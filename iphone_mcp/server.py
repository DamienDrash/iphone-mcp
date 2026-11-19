"""MCP server: control a real iPhone over USB or Wi-Fi through WebDriverAgent."""

from __future__ import annotations

import io
import logging
from typing import Literal

from mcp.server.mcpserver import Image, MCPServer
from PIL import Image as PILImage

from .connection import Connection
from .wda import WDAClient, WDAError

for _name in ("pymobiledevice3", "httpx", "httpcore", "asyncio"):
    logging.getLogger(_name).setLevel(logging.CRITICAL)

mcp = MCPServer(
    "iphone",
    instructions=(
        "Controls a real, physical iPhone. Coordinates are iOS points, not pixels: iphone_screenshot returns an "
        "image scaled to points, so a position in the image can be passed straight to iphone_tap. "
        "Typical loop: look (iphone_ui_tree is cheaper and more precise, iphone_screenshot for visuals) → act "
        "(iphone_tap_text / iphone_tap / iphone_swipe / iphone_type) → look again. "
        "If there is no connection, call iphone_start_agent first. While connected, the screen is kept from "
        "auto-locking (iphone_keep_awake). If the iPhone is locked, a passcode must be entered by the user. Ask the user before anything with real-world effect "
        "(sending messages, purchases, payments, deleting data, changing account or security settings)."
    ),
)
conn = Connection()

KNOWN_APPS = {
    # English and German display names of common Apple apps
    "settings": "com.apple.Preferences",
    "einstellungen": "com.apple.Preferences",
    "safari": "com.apple.mobilesafari",
    "photos": "com.apple.mobileslideshow",
    "fotos": "com.apple.mobileslideshow",
    "camera": "com.apple.camera",
    "kamera": "com.apple.camera",
    "messages": "com.apple.MobileSMS",
    "nachrichten": "com.apple.MobileSMS",
    "mail": "com.apple.mobilemail",
    "maps": "com.apple.Maps",
    "karten": "com.apple.Maps",
    "notes": "com.apple.mobilenotes",
    "notizen": "com.apple.mobilenotes",
    "calendar": "com.apple.mobilecal",
    "kalender": "com.apple.mobilecal",
    "app store": "com.apple.AppStore",
    "music": "com.apple.Music",
    "musik": "com.apple.Music",
    "phone": "com.apple.mobilephone",
    "telefon": "com.apple.mobilephone",
    "clock": "com.apple.mobiletimer",
    "uhr": "com.apple.mobiletimer",
    "weather": "com.apple.weather",
    "wetter": "com.apple.weather",
    "files": "com.apple.DocumentsApp",
    "dateien": "com.apple.DocumentsApp",
    "reminders": "com.apple.reminders",
    "erinnerungen": "com.apple.reminders",
    "contacts": "com.apple.MobileAddressBook",
    "kontakte": "com.apple.MobileAddressBook",
    "health": "com.apple.Health",
    "wallet": "com.apple.Passbook",
    "shortcuts": "com.apple.shortcuts",
    "kurzbefehle": "com.apple.shortcuts",
    "testflight": "com.apple.TestFlight",
}

# Element types shown in the UI tree even without a label
INTERACTIVE = {
    "Button",
    "Link",
    "Cell",
    "TextField",
    "SecureTextField",
    "SearchField",
    "TextView",
    "Switch",
    "Slider",
    "Toggle",
    "Tab",
    "Icon",
    "MenuItem",
    "SegmentedControl",
    "Picker",
    "PickerWheel",
    "Key",
    "Stepper",
}

LOCKED_NOTE = (
    "\nNote: the iPhone is locked (screen off or lock screen). Call iphone_lock(action='unlock'); "
    "if a passcode is required, ask the user to unlock it."
)


async def _wda() -> WDAClient:
    return await conn.get()


async def _lock_note(w: WDAClient) -> str:
    try:
        return LOCKED_NOTE if await w.is_locked() else ""
    except WDAError:
        return ""


def _err(e: Exception) -> str:
    return f"Error: {e}"


# ---------------------------------------------------------------- connection
@mcp.tool()
async def iphone_status() -> str:
    """Connection (USB / Wi-Fi), screen size, lock state and the app in the foreground."""
    lines = []
    info = await conn.coredevice_info()
    if info:
        via = {"wired": "cable", "localNetwork": "Wi-Fi"}.get(info.get("transportType"), info.get("transportType"))
        lines.append(
            f"Device: {info.get('name')} ({info.get('model')}, iOS {info.get('os')}), Xcode link: {info.get('tunnelState')} via {via}"
        )
    try:
        w = await _wda()
        size = await w.window_size()
        app = await w.active_app()
        lines += [
            f"Connected via: {conn.transport}",
            f"Screen: {size[0]}×{size[1]} points",
            f"Locked: {'yes' if await w.is_locked() else 'no'}",
            f"Keep-awake: {'on' if conn.keep_awake else 'off'}",
            f"Foreground app: {app.get('name') or app.get('bundleId', '?')} ({app.get('bundleId', '?')})",
        ]
    except WDAError as e:
        lines.append(str(e))
    return "\n".join(lines)


@mcp.tool()
async def iphone_start_agent() -> str:
    """Start WebDriverAgent on the iPhone (via xcodebuild on macOS) and connect to it.
    Works over cable or Wi-Fi. Can take up to two minutes."""
    try:
        return await conn.start_wda()
    except WDAError as e:
        return _err(e)


@mcp.tool()
async def iphone_stop_agent() -> str:
    """Stop WebDriverAgent and drop the connection."""
    return await conn.stop_wda()


@mcp.tool()
async def iphone_keep_awake(enabled: bool) -> str:
    """Keep the iPhone screen from auto-locking while this server is connected (on by default).
    Works by sending an invisible touch just outside the screen every few seconds; it does not change
    any settings and stops when the session ends. It cannot unlock a phone that is already locked."""
    conn.set_keep_awake(enabled)
    return "Keep-awake is on: the screen will not auto-lock during this session." if enabled else "Keep-awake is off."


# ---------------------------------------------------------------- looking
@mcp.tool()
async def iphone_screenshot(quality: int = 70) -> list:
    """Screenshot of the iPhone screen, scaled to iOS points:
    a position (x, y) in the image can be passed directly to iphone_tap."""
    try:
        w = await _wda()
        png = await w.screenshot_png()
        pw, ph = await w.window_size()
    except WDAError as e:
        return [_err(e)]
    img = PILImage.open(io.BytesIO(png)).convert("RGB")
    landscape = img.width > img.height
    target = (max(pw, ph), min(pw, ph)) if landscape else (min(pw, ph), max(pw, ph))
    img = img.resize(target, PILImage.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=max(20, min(quality, 95)))
    return [Image(data=buf.getvalue(), format="jpeg"), f"{img.width}×{img.height} points" + await _lock_note(w)]


def walk_tree(node: dict, out: list[str], screen: tuple[int, int], depth: int = 0) -> None:
    """Flatten WebDriverAgent's JSON source into one line per visible, meaningful element."""
    t = (node.get("type") or "").replace("XCUIElementType", "")
    rect = node.get("rect") or {}
    x, y, w, h = (rect.get(k, 0) or 0 for k in ("x", "y", "width", "height"))
    visible = str(node.get("isVisible", "1")) not in ("0", "false", "False")
    on_screen = w > 0 and h > 0 and x + w > 0 and y + h > 0 and x < screen[0] and y < screen[1]
    text = node.get("label") or node.get("name") or ""
    value = node.get("value")
    if visible and on_screen and (text or value not in (None, "") or t in INTERACTIVE):
        parts = [f"[{t}]"]
        if text:
            parts.append(f'"{text}"')
        if value not in (None, "", text):
            parts.append(f"value={value!r}")
        if str(node.get("isEnabled", "1")) in ("0", "false"):
            parts.append("(disabled)")
        parts.append(f"@({int(x + w / 2)},{int(y + h / 2)}) {int(w)}×{int(h)}")
        out.append("  " * min(depth, 6) + " ".join(parts))
        depth += 1
    for child in node.get("children") or []:
        walk_tree(child, out, screen, depth)


@mcp.tool()
async def iphone_ui_tree(max_lines: int = 250) -> str:
    """Visible UI elements with type, text, center point (in points) and size.
    Cheaper and more precise than a screenshot for finding buttons and text fields."""
    try:
        w = await _wda()
        src = await w.source_json()
        screen = await w.window_size()
    except WDAError as e:
        return _err(e)
    out: list[str] = []
    walk_tree(src, out, screen)
    if len(out) > max_lines:
        out = out[:max_lines] + [f"… {len(out) - max_lines} more elements cut off"]
    return ("\n".join(out) or "No visible elements found.") + await _lock_note(w)


# ---------------------------------------------------------------- gestures
@mcp.tool()
async def iphone_tap(x: float, y: float) -> str:
    """Tap at (x, y) in iOS points."""
    try:
        w = await _wda()
        await w.tap(x, y)
        return f"Tapped ({x:.0f}, {y:.0f})." + await _lock_note(w)
    except WDAError as e:
        return _err(e)


@mcp.tool()
async def iphone_double_tap(x: float, y: float) -> str:
    """Double-tap at (x, y) in iOS points."""
    try:
        w = await _wda()
        await w.double_tap(x, y)
        return f"Double-tapped ({x:.0f}, {y:.0f})." + await _lock_note(w)
    except WDAError as e:
        return _err(e)


@mcp.tool()
async def iphone_long_press(x: float, y: float, seconds: float = 1.0) -> str:
    """Touch and hold at (x, y), e.g. for context menus or moving app icons."""
    try:
        w = await _wda()
        await w.tap(x, y, hold_ms=int(seconds * 1000))
        return f"Long-pressed ({x:.0f}, {y:.0f}) for {seconds:.1f}s." + await _lock_note(w)
    except WDAError as e:
        return _err(e)


def predicate_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace("'", "\\'")


@mcp.tool()
async def iphone_tap_text(text: str, index: int = 0, exact: bool = False) -> str:
    """Tap the visible element whose label, name or value contains `text` (case-insensitive).
    With several matches, `index` picks one (0 = topmost). exact=True requires a full match."""
    try:
        w = await _wda()
        q = predicate_escape(text)
        op = "==[c]" if exact else "CONTAINS[c]"
        ids = await w.find_elements("predicate string", f"label {op} '{q}' OR name {op} '{q}' OR value {op} '{q}'")
        sw, sh = await w.window_size()
        hits = []
        for eid in ids[:40]:
            r = await w.element_rect(eid)
            cx, cy = r["x"] + r["width"] / 2, r["y"] + r["height"] / 2
            if r["width"] > 0 and r["height"] > 0 and 0 <= cx <= sw and 0 <= cy <= sh:
                hits.append(r)
        if not hits:
            return f"No visible element matching “{text}”." + await _lock_note(w)
        hits.sort(key=lambda r: (round(r["y"]), r["width"] * r["height"]))
        if index >= len(hits):
            return f"Only {len(hits)} matches for “{text}”."
        r = hits[index]
        cx, cy = r["x"] + r["width"] / 2, r["y"] + r["height"] / 2
        await w.tap(cx, cy)
        return f"Tapped “{text}” at ({cx:.0f}, {cy:.0f}) — match {index + 1} of {len(hits)}."
    except WDAError as e:
        return _err(e)


@mcp.tool()
async def iphone_swipe(x1: float, y1: float, x2: float, y2: float, duration_ms: int = 300) -> str:
    """Swipe from (x1, y1) to (x2, y2) in iOS points."""
    try:
        await (await _wda()).swipe(x1, y1, x2, y2, duration_ms)
        return f"Swiped ({x1:.0f},{y1:.0f}) → ({x2:.0f},{y2:.0f})."
    except WDAError as e:
        return _err(e)


def scroll_vector(direction: str, amount: float, size: tuple[int, int]) -> tuple[float, float, float, float]:
    """Start and end point of a swipe that scrolls content in `direction` ('down' = reveal content below)."""
    sw, sh = size
    a = max(0.1, min(amount, 0.9))
    dx, dy = {"down": (0, -1), "up": (0, 1), "left": (1, 0), "right": (-1, 0)}[direction]
    cx, cy = sw / 2, sh / 2
    return cx - dx * sw * a / 2, cy - dy * sh * a / 2, cx + dx * sw * a / 2, cy + dy * sh * a / 2


@mcp.tool()
async def iphone_scroll(direction: Literal["up", "down", "left", "right"], amount: float = 0.5) -> str:
    """Scroll the content. 'down' reveals content further down. amount = share of the screen (0.1–0.9)."""
    try:
        w = await _wda()
        await w.swipe(*scroll_vector(direction, amount, await w.window_size()), 400)
        return f"Scrolled {direction}." + await _lock_note(w)
    except WDAError as e:
        return _err(e)


# ---------------------------------------------------------------- input & buttons
@mcp.tool()
async def iphone_type(text: str, submit: bool = False) -> str:
    """Type text into the focused text field (tap the field first). submit=True presses Return afterwards."""
    try:
        await (await _wda()).type_text(text + ("\n" if submit else ""))
        return "Text typed."
    except WDAError as e:
        return _err(e)


@mcp.tool()
async def iphone_press_button(button: Literal["home", "volumeUp", "volumeDown"]) -> str:
    """Press a hardware button. 'home' goes to the home screen."""
    try:
        w = await _wda()
        if button == "home":
            await w.home()
        else:
            await w.press_button(button)
        return f"Pressed {button}." + await _lock_note(w)
    except WDAError as e:
        return _err(e)


@mcp.tool()
async def iphone_lock(action: Literal["lock", "unlock"]) -> str:
    """Lock the iPhone, or wake it up and unlock it. Unlocking only works without a passcode;
    otherwise the user has to enter it."""
    try:
        w = await _wda()
        if action == "lock":
            await w.lock()
            return "Locked."
        await w.unlock()
        if await w.is_locked():
            return "Screen is on, but a passcode / Face ID is required — ask the user to unlock the iPhone."
        return "Unlocked."
    except WDAError as e:
        return _err(e)


# ---------------------------------------------------------------- apps
async def installed_apps() -> dict[str, str]:
    """Display name → bundle ID, via usbmux/lockdown (cable or Wi-Fi sync)."""
    from pymobiledevice3.lockdown import create_using_usbmux
    from pymobiledevice3.services.installation_proxy import InstallationProxyService

    devices = await conn.usbmux_devices()
    if not devices:
        return {}
    lockdown = await create_using_usbmux(devices[0].serial, connection_type=devices[0].connection_type)
    apps = await InstallationProxyService(lockdown).get_apps(application_type="Any")
    result = {}
    for bid, info in apps.items():
        name = (info.get("CFBundleDisplayName") or info.get("CFBundleName") or "").strip("‎‏ ")
        if name and "hidden" not in (info.get("SBAppTags") or []) and info.get("ApplicationType") in ("User", "System"):
            result[name] = bid
    return result


@mcp.tool()
async def iphone_list_apps(filter: str | None = None) -> str:
    """List installed apps (name → bundle ID), optionally filtered by name."""
    try:
        apps = await installed_apps()
    except Exception as e:  # noqa: BLE001
        return _err(e)
    if not apps:
        return "App list unavailable (needs the cable or Wi-Fi sync). Known Apple apps: " + ", ".join(
            sorted(KNOWN_APPS)
        )
    items = sorted(apps.items(), key=lambda kv: kv[0].lower())
    if filter:
        f = filter.lower()
        items = [kv for kv in items if f in kv[0].lower() or f in kv[1].lower()]
    return "\n".join(f"{n} — {b}" for n, b in items) or "No matching app."


async def resolve_app(app: str) -> str:
    if "." in app and " " not in app:
        return app
    key = app.strip().lower()
    if key in KNOWN_APPS:
        return KNOWN_APPS[key]
    try:
        apps = await installed_apps()
    except Exception:  # noqa: BLE001
        apps = {}
    for name, bid in apps.items():
        if name.lower() == key:
            return bid
    for name, bid in apps.items():
        if key in name.lower():
            return bid
    raise WDAError(f"App “{app}” not found. iphone_list_apps shows all bundle IDs.")


@mcp.tool()
async def iphone_launch_app(app: str) -> str:
    """Launch (or bring to front) an app by bundle ID (e.g. com.apple.Preferences) or name (e.g. 'Settings')."""
    try:
        bid = await resolve_app(app)
        w = await _wda()
        await w.launch_app(bid)
        return f"Launched {bid}." + await _lock_note(w)
    except WDAError as e:
        return _err(e)


@mcp.tool()
async def iphone_close_app(app: str) -> str:
    """Terminate an app by bundle ID or name."""
    try:
        bid = await resolve_app(app)
        await (await _wda()).terminate_app(bid)
        return f"Terminated {bid}."
    except WDAError as e:
        return _err(e)


@mcp.tool()
async def iphone_open_url(url: str) -> str:
    """Open a URL or deep link (https://…, maps:, tel:, custom app schemes)."""
    try:
        w = await _wda()
        await w.open_url(url)
        return f"Opened {url}." + await _lock_note(w)
    except WDAError as e:
        return _err(e)


def serve() -> None:
    mcp.run()
