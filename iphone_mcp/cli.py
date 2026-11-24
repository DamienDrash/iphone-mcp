"""Command line: `iphone-mcp [serve [--http]|setup|doctor|selftest|start|stop]`."""

from __future__ import annotations

import argparse
import asyncio
import shutil
import subprocess
import sys

from . import __version__, paths


def _doctor() -> int:
    from .connection import Connection, coredevice_devices
    from .setup_wda import find_teams

    ok = True

    def line(good: bool | None, text: str) -> None:
        nonlocal ok
        mark = "✓" if good else ("•" if good is None else "✗")
        ok = ok and good is not False
        print(f"{mark} {text}")

    line(True, f"iphone-mcp {__version__} on {sys.platform}")
    if paths.IS_MAC:
        xb = shutil.which("xcodebuild")
        ver = (
            subprocess.run(["xcodebuild", "-version"], capture_output=True, text=True).stdout.split("\n")[0]
            if xb
            else ""
        )
        line(bool(ver), ver or "Xcode not found (needed for setup/start)")
        teams = find_teams()
        line(
            bool(teams),
            f"Signing teams: {', '.join(teams)}"
            if teams
            else "No 'Apple Development' certificate (Xcode → Settings → Accounts)",
        )
        devices = asyncio.run(coredevice_devices())
        if not devices:
            line(False, "No iPhone known to Xcode – connect it by cable once and tap 'Trust'")
        for d in devices:
            reach = d.get("tunnelState") != "unavailable"
            transport = str(d.get("transportType") or "-")
            via = {"wired": "cable", "localNetwork": "Wi-Fi"}.get(transport, transport)
            line(
                reach,
                f"{d['name']} ({d.get('model')}, iOS {d.get('os')}): {d.get('tunnelState')} via {via}, developer mode {d.get('developerMode')}",
            )
    else:
        line(None, "Not macOS: WebDriverAgent is launched without Xcode (pymobiledevice3)")
    conn = Connection()
    for dev in asyncio.run(conn.usbmux_devices()):
        line(True, f"usbmux sees {dev.serial} via {dev.connection_type}")

    cfg = paths.load(paths.WDA_FILE)
    launcher = conn.launcher
    if launcher == "xcodebuild":
        built = bool(list(paths.WDA_DERIVED.glob("Build/Products/*.xctestrun")))
        line(
            built,
            f"WebDriverAgent built with Xcode for {cfg.get('udid')} (team {cfg.get('team')})"
            if built
            else "WebDriverAgent not built yet – run: iphone-mcp setup",
        )
    else:
        runner = cfg.get("runner_bundle_id")
        line(
            bool(runner) or None,
            f"Launch without Xcode, runner {runner}"
            if runner
            else "Launch without Xcode – install WebDriverAgent first: iphone-mcp setup --prebuilt, then --ipa",
        )
    line(True, f"Keep-awake during sessions: {'on' if conn.keep_awake else 'off'}")

    async def probe() -> str:
        try:
            await conn.get()
            return f"WebDriverAgent reachable via {conn.transport}"
        except Exception as e:  # noqa: BLE001
            return f"WebDriverAgent not running ({str(e).split('.')[0]}) – the agent starts it with iphone_start_agent"

    msg = asyncio.run(probe())
    line(True if msg.startswith("WebDriverAgent reachable") else None, msg)
    return 0 if ok else 1


def _start() -> int:
    from .connection import Connection

    async def go() -> str:
        conn = Connection()
        conn.owner = "cli"  # started by hand: keep it running when MCP servers exit
        return await conn.start_wda()

    try:
        print(asyncio.run(go()))
        print("xcodebuild keeps running in the background; stop it with: iphone-mcp stop")
        return 0
    except Exception as e:  # noqa: BLE001
        print(f"✗ {e}", file=sys.stderr)
        return 1


def _stop() -> int:
    from .connection import Connection

    print(asyncio.run(Connection().stop_wda()))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="iphone-mcp",
        description="Control a real iPhone from MCP clients.",
        epilog="Docs: https://github.com/DamienDrash/iphone-mcp · Sponsor: https://github.com/sponsors/DamienDrash",
    )
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command")
    p_serve = sub.add_parser("serve", help="run the MCP server on stdio (default), or over HTTP for remote access")
    p_serve.add_argument(
        "--http",
        action="store_true",
        help="serve Streamable HTTP at /mcp with a bearer token, for agents on other machines (README 'Remote access')",
    )
    p_serve.add_argument("--host", help="address to listen on with --http (default 127.0.0.1; 0.0.0.0 = all networks)")
    p_serve.add_argument("--port", type=int, help="port for --http (default 8765)")
    p_serve.add_argument("--new-token", action="store_true", help="with --http: replace the stored access token")
    p_setup = sub.add_parser("setup", help="build and install WebDriverAgent on your iPhone (macOS)")
    p_setup.add_argument("--udid", help="device UDID if several iPhones are connected")
    p_setup.add_argument("--team", help="Apple team ID used for signing")
    p_setup.add_argument("--bundle-id", help="bundle ID for the WebDriverAgent runner")
    p_setup.add_argument(
        "--prebuilt",
        action="store_true",
        help="no Xcode: download Appium's prebuilt WebDriverAgent for you to sign (default on Linux/Windows)",
    )
    p_setup.add_argument("--ipa", help="no Xcode: install this signed WebDriverAgent .ipa over USB")
    sub.add_parser("doctor", help="check Xcode, signing, iPhone and connection")
    sub.add_parser("selftest", help="exercise the tools on the iPhone with harmless actions")
    sub.add_parser("start", help="start WebDriverAgent without an MCP client")
    sub.add_parser("stop", help="stop WebDriverAgent")
    args = parser.parse_args(argv)

    if args.command == "setup":
        from .setup_wda import run

        return run(args.udid, args.team, args.bundle_id, prebuilt=args.prebuilt, ipa=args.ipa)
    if args.command == "doctor":
        return _doctor()
    if args.command == "selftest":
        from .selftest import run as selftest

        return selftest()
    if args.command == "start":
        return _start()
    if args.command == "stop":
        return _stop()
    if args.command == "serve" and args.http:
        from .remote import serve_http

        return serve_http(host=args.host or "127.0.0.1", port=args.port or 8765, new_token=args.new_token)
    if args.command == "serve" and (args.host or args.port or args.new_token):
        parser.error("--host, --port and --new-token need --http")

    from .server import serve

    serve()
    return 0


if __name__ == "__main__":
    sys.exit(main())
