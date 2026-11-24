import asyncio
import json
import os
import socket
import threading
import time

import httpx2
import pytest
import uvicorn
from mcp.client.client import Client
from mcp.client.streamable_http import streamable_http_client
from starlette.testclient import TestClient

from iphone_mcp import cli, remote

TOKEN = "test-token-0123456789abcdef"
INIT = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}},
}
ACCEPT = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


@pytest.fixture
def audit(tmp_path):
    return tmp_path / "remote.log"


@pytest.fixture
def client(audit):
    with TestClient(remote.build_app(TOKEN, audit)) as c:
        yield c


def test_missing_token_rejected(client, audit):
    r = client.post("/mcp", json=INIT, headers=ACCEPT)
    assert r.status_code == 401
    assert r.headers["www-authenticate"].startswith("Bearer")
    assert "rejected" in audit.read_text()


def test_wrong_token_rejected(client):
    r = client.post("/mcp", json=INIT, headers={**ACCEPT, "Authorization": "Bearer nope"})
    assert r.status_code == 401
    r = client.post("/mcp", json=INIT, headers={**ACCEPT, "Authorization": f"Basic {TOKEN}"})
    assert r.status_code == 401


def test_right_token_accepted(client):
    r = client.post("/mcp", json=INIT, headers={**ACCEPT, "Authorization": f"Bearer {TOKEN}"})
    assert r.status_code == 200
    assert "mcp-session-id" in r.headers


def test_tool_calls_logs_names_only():
    body = json.dumps(
        [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "iphone_type", "arguments": {"text": "secret"}},
            },
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        ]
    ).encode()
    assert remote.tool_calls(body) == ["iphone_type"]
    assert remote.tool_calls(b"not json") == []


def test_token_generated_once_with_private_mode(tmp_path, monkeypatch):
    monkeypatch.delenv(remote.TOKEN_ENV, raising=False)
    file = tmp_path / "cfg" / "http-token"
    token, created = remote.load_token(file=file)
    assert created and len(token) >= remote.MIN_TOKEN_LENGTH
    if os.name != "nt":
        assert file.stat().st_mode & 0o777 == 0o600
    assert remote.load_token(file=file) == (token, False)
    rotated, created = remote.load_token(new=True, file=file)
    assert created and rotated != token


def test_token_from_env(tmp_path, monkeypatch):
    monkeypatch.setenv(remote.TOKEN_ENV, TOKEN)
    assert remote.load_token(file=tmp_path / "t") == (TOKEN, False)
    monkeypatch.setenv(remote.TOKEN_ENV, "short")
    with pytest.raises(remote.TokenError):
        remote.load_token(file=tmp_path / "t")


def test_is_loopback():
    assert remote.is_loopback("127.0.0.1") and remote.is_loopback("::1") and remote.is_loopback("localhost")
    assert not remote.is_loopback("0.0.0.0")


def test_http_options_need_http_flag():
    with pytest.raises(SystemExit):
        cli.main(["serve", "--port", "9000"])


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_http_server_starts_and_lists_tools(audit):
    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(remote.build_app(TOKEN, audit), host="127.0.0.1", port=port, log_level="error")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 15
        while not server.started:
            assert time.monotonic() < deadline, "HTTP server did not start"
            time.sleep(0.05)

        async def list_tools() -> list[str]:
            async with httpx2.AsyncClient(headers={"Authorization": f"Bearer {TOKEN}"}) as http:
                async with Client(streamable_http_client(f"http://127.0.0.1:{port}/mcp", http_client=http)) as c:
                    return [t.name for t in (await c.list_tools()).tools]

        names = asyncio.run(list_tools())
        assert "iphone_tap" in names and "iphone_screenshot" in names
    finally:
        server.should_exit = True
        thread.join(timeout=10)


def test_tool_call_is_audited_without_arguments(client, audit):
    call = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {"name": "iphone_type", "arguments": {"text": "pin 1234"}},
    }
    client.post("/mcp", json=call, headers={**ACCEPT, "Authorization": f"Bearer {TOKEN}"})
    log = audit.read_text()
    assert "tool=iphone_type client=testclient" in log
    assert "1234" not in log
