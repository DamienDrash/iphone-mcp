"""Remote mode: serve the MCP server over Streamable HTTP, guarded by a bearer token.

For support-style remote control: the computer next to the iPhone runs `iphone-mcp serve --http`, and an
agent elsewhere connects to it through a private network (Tailscale) or a tunnel (Cloudflare Tunnel).
WebDriverAgent itself (port 8100 on the iPhone) has no authentication and is never exposed: only this
server talks to it.
"""

from __future__ import annotations

import hmac
import ipaddress
import json
import logging
import os
import secrets
import sys
from collections.abc import Awaitable, Callable, MutableMapping
from datetime import datetime
from pathlib import Path
from typing import Any

from . import paths

TOKEN_ENV = "IPHONE_MCP_TOKEN"
TOKEN_FILE = paths.CONFIG_DIR / "http-token"
AUDIT_LOG = paths.LOG_DIR / "remote.log"
MIN_TOKEN_LENGTH = 16
MAX_BODY = 4 * 1024 * 1024  # a tools/call request is tiny; anything bigger is not read for the audit log

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]


class TokenError(Exception):
    pass


def load_token(new: bool = False, file: Path | None = None) -> tuple[str, bool]:
    """The bearer token clients must send. Returns (token, freshly_created).

    IPHONE_MCP_TOKEN wins; otherwise the token stored in the config dir (mode 0600) is used, and one is
    generated on first use or with `new=True` (rotating it locks out everyone who had the old one)."""
    file = file or TOKEN_FILE
    env = os.environ.get(TOKEN_ENV, "").strip()
    if env:
        if len(env) < MIN_TOKEN_LENGTH:
            raise TokenError(f"{TOKEN_ENV} is too short: use at least {MIN_TOKEN_LENGTH} random characters")
        return env, False
    if not new:
        try:
            stored = file.read_text().strip()
            if len(stored) >= MIN_TOKEN_LENGTH:
                return stored, False
        except OSError:
            pass
    token = secrets.token_urlsafe(32)
    file.parent.mkdir(parents=True, exist_ok=True)
    file.unlink(missing_ok=True)
    fd = os.open(file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(token + "\n")
    return token, True


def _header(scope: Scope, name: bytes) -> str:
    for key, value in scope.get("headers", []):
        if key.lower() == name:
            return value.decode("latin-1")
    return ""


def client_address(scope: Scope) -> str:
    """The peer address, plus what a tunnel in front says about the real client (only logged, never trusted)."""
    peer = (scope.get("client") or ("?", 0))[0]
    for name in (b"tailscale-user-login", b"cf-connecting-ip", b"x-forwarded-for"):
        if value := _header(scope, name):
            return f"{peer} ({name.decode()}: {value.split(',')[0].strip()})"
    return str(peer)


def tool_calls(body: bytes) -> list[str]:
    """Names of the tools called in a JSON-RPC request body (single message or batch). Arguments are ignored."""
    try:
        data = json.loads(body)
    except ValueError:
        return []
    messages = data if isinstance(data, list) else [data]
    names = []
    for m in messages:
        if isinstance(m, dict) and m.get("method") == "tools/call":
            params = m.get("params")
            name = params.get("name") if isinstance(params, dict) else None
            names.append(str(name) if name else "?")
    return names


class BearerAuth:
    """ASGI wrapper: rejects every HTTP request without the right `Authorization: Bearer …` header, and writes
    one audit line per tool call (time, tool, client – no arguments, so no typed text and no screenshots)."""

    def __init__(self, app: ASGIApp, token: str, log_file: Path) -> None:
        self.app = app
        self.token = token.encode()
        self.log_file = log_file

    def audit(self, event: str, scope: Scope) -> None:
        line = f"{datetime.now().astimezone().isoformat(timespec='seconds')} {event} client={client_address(scope)}\n"
        try:
            self.log_file.parent.mkdir(parents=True, exist_ok=True)
            with self.log_file.open("a", encoding="utf-8") as f:
                f.write(line)
        except OSError:
            pass

    def authorized(self, scope: Scope) -> bool:
        scheme, _, given = _header(scope, b"authorization").partition(" ")
        return scheme.lower() == "bearer" and hmac.compare_digest(given.strip().encode(), self.token)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":  # lifespan: starts the session manager
            await self.app(scope, receive, send)
            return
        if not self.authorized(scope):
            self.audit(f"rejected path={scope.get('path')}", scope)
            body = b'{"error":"unauthorized","detail":"missing or wrong bearer token"}'
            await send(
                {
                    "type": "http.response.start",
                    "status": 401,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(body)).encode()),
                        (b"www-authenticate", b'Bearer realm="iphone-mcp"'),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": body})
            return
        if scope.get("method") != "POST":
            await self.app(scope, receive, send)
            return

        # Read the request to log tool calls, then hand the same bytes to the MCP app.
        chunks: list[bytes] = []
        size = 0
        more = True
        while more:
            message = await receive()
            if message["type"] != "http.request":
                break
            chunks.append(message.get("body", b""))
            size += len(chunks[-1])
            more = message.get("more_body", False) and size <= MAX_BODY
        body = b"".join(chunks)
        if not more:
            for name in tool_calls(body):
                self.audit(f"tool={name}", scope)
        replayed = False

        async def replay() -> Message:
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": body, "more_body": more}
            return await receive()

        await self.app(scope, replay, send)


def build_app(token: str, log_file: Path | None = None) -> ASGIApp:
    from mcp.server.transport_security import TransportSecuritySettings

    from .server import mcp

    # DNS-rebinding protection checks the Host header against localhost, which would reject requests that
    # Tailscale Serve or Cloudflare Tunnel forward with the public host name. The token already stops a
    # rebinding attack: a browser on another site cannot send a header it does not know.
    inner = mcp.streamable_http_app(
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )
    return BearerAuth(inner, token, log_file or AUDIT_LOG)


def is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def serve_http(host: str = "127.0.0.1", port: int = 8765, new_token: bool = False) -> int:
    import uvicorn

    from .server import _cleanup_on_exit

    try:
        token, created = load_token(new=new_token)
    except TokenError as e:
        print(f"✗ {e}", file=sys.stderr)
        return 2

    shown = f"[{host}]" if ":" in host else host
    url = f"http://{shown}:{port}/mcp"
    print(f"iphone-mcp remote mode: {url} (Streamable HTTP)")
    if created:
        print(f"New access token (stored in {TOKEN_FILE}, shown only now):\n\n    {token}\n")
        print("Connect from another machine, e.g. through Tailscale (see README 'Remote access'):")
        print(f'    claude mcp add --transport http iphone https://<host>/mcp --header "Authorization: Bearer {token}"')
    elif os.environ.get(TOKEN_ENV):
        print(f"Access token: from {TOKEN_ENV}")
    else:
        print(f"Access token: stored in {TOKEN_FILE} (new one: --new-token)")
    if not is_loopback(host):
        print(f"! Listening on {host}: reachable from every network this computer is in. Prefer the default")
        print("  127.0.0.1 behind Tailscale Serve or Cloudflare Tunnel. Never expose port 8100 (WebDriverAgent).")
    print(f"Tool calls are logged to {AUDIT_LOG}. Stop with Ctrl+C.")
    sys.stdout.flush()

    # Session IDs in the SDK's info lines work like short-lived credentials: keep them out of the terminal.
    for name in ("mcp.server.streamable_http", "mcp.server.streamable_http_manager"):
        logging.getLogger(name).setLevel(logging.WARNING)

    paths.register_client(os.getpid())
    try:
        uvicorn.run(build_app(token), host=host, port=port, log_level="warning")
    finally:
        _cleanup_on_exit()
    return 0
