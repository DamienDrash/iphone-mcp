"""`iphone-mcp selftest`: exercise the tools on the real iPhone with harmless actions.

It only reads the screen, opens the Settings app, scrolls, opens "General" and goes back. It uses Apple's
locale-independent element identifiers (e.g. ``com.apple.settings.general``), so it works in any language.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any

from . import server

SETTINGS = "com.apple.Preferences"
GENERAL = "com.apple.settings.general"

FAIL_MARKERS = ("Error:", "Timeout", "No visible element", "Only ", "not found")


def _text(result: Any) -> str:
    if isinstance(result, list):
        return " | ".join(r if isinstance(r, str) else f"<{type(r).__name__}>" for r in result)
    return str(result)


def _ok(result: Any) -> bool:
    text = _text(result)
    return not any(text.startswith(m) or f"\n{m}" in text for m in FAIL_MARKERS)


async def _run() -> int:
    steps: list[tuple[str, Callable[[], Awaitable[Any]], Callable[[Any], bool] | None]] = [
        ("Start / connect agent", server.iphone_start_agent, lambda r: "running" in _text(r)),
        ("Status: unlocked", server.iphone_status, lambda r: "Locked: no" in _text(r)),
        ("Screenshot", lambda: server.iphone_screenshot(quality=40), lambda r: isinstance(r, list) and len(r) >= 2),
        ("Home button", lambda: server.iphone_press_button("home"), None),
        ("UI tree", server.iphone_ui_tree, lambda r: "[" in _text(r)),
        ("Close Settings (fresh start)", lambda: server.iphone_close_app(SETTINGS), None),
        ("Launch Settings", lambda: server.iphone_launch_app(SETTINGS), None),
        ("Wait for 'General'", lambda: server.iphone_wait_for(GENERAL, timeout=10, exact=True), None),
        ("Scroll down", lambda: server.iphone_scroll("down", 0.3), None),
        ("Scroll up", lambda: server.iphone_scroll("up", 0.3), None),
        ("Tap 'General' by identifier", lambda: server.iphone_tap_text(GENERAL, exact=True), None),
        ("Wait until list is left", lambda: server.iphone_wait_for(GENERAL, timeout=10, gone=True, exact=True), None),
        ("Tap back button", lambda: server.iphone_tap_text("BackButton", exact=True), None),
        ("Wait for 'General' again", lambda: server.iphone_wait_for(GENERAL, timeout=10, exact=True), None),
        ("Keep-awake on", lambda: server.iphone_keep_awake(True), None),
        ("Close Settings", lambda: server.iphone_close_app(SETTINGS), None),
        ("Home button", lambda: server.iphone_press_button("home"), None),
    ]
    optional = [("App list (needs cable or Wi-Fi sync)", lambda: server.iphone_list_apps(SETTINGS))]

    started_here = False
    try:
        await server.conn.get()
    except Exception:  # noqa: BLE001
        started_here = True
    server.conn.owner = "cli"

    failed = 0
    print("iphone-mcp selftest – harmless actions only (Settings app, scrolling, back navigation)\n")
    for name, call, check in steps:
        t0 = time.monotonic()
        try:
            result = await call()
            ok = check(result) if check else _ok(result)
            detail = _text(result).replace("\n", " / ")
        except Exception as e:  # noqa: BLE001
            ok, detail = False, f"{type(e).__name__}: {e}"
        mark = "✓" if ok else "✗"
        print(f"{mark} {name:<34} {time.monotonic() - t0:5.1f}s  {detail[:110]}")
        if "is locked" in detail or "SleepLockScreen" in detail or "could not be, unlocked" in detail:
            failed += 1  # an interrupted run never counts as passed
            print("\nStopped: the iPhone locked itself during the test. Unlock it, keep it awake and run again.")
            break
        if not ok:
            failed += 1
            if name.startswith(("Start", "Status")):
                print("\nStopped: the agent is not usable yet (see the message above).")
                break
    else:
        for name, call in optional:
            result = await call()
            mark = "✓" if _ok(result) and "unavailable" not in _text(result) else "•"
            print(f"{mark} {name:<34}         {_text(result).splitlines()[0][:110]}")

    if started_here:
        await server.conn.stop_wda()
    print(f"\n{'All checks passed.' if not failed else f'{failed} check(s) failed.'}")
    print("Not exercised (they change things on purpose): iphone_type, iphone_open_url, iphone_lock, iphone_pinch,")
    print("double tap / long press, iphone_swipe at arbitrary positions.")
    return 1 if failed else 0


def run() -> int:
    return asyncio.run(_run())
