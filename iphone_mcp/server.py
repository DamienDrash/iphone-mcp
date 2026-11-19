"""MCP server: control a real iPhone over USB or Wi-Fi through WebDriverAgent."""

from __future__ import annotations

import asyncio
import contextvars
import functools
import io
import logging
import os
import signal
import sys
from typing import Annotated, Literal

from mcp.server.mcpserver import Image, MCPServer
from mcp.types import ToolAnnotations
from PIL import Image as PILImage
from pydantic import Field

from . import paths
from .connection import Connection
from .wda import WDAClient, WDAConnectionError, WDAError

for _name in ("pymobiledevice3", "httpx", "httpcore", "asyncio"):
    logging.getLogger(_name).setLevel(logging.CRITICAL)

mcp = MCPServer(
    "iphone",
    instructions=(
        "Controls a real, physical iPhone. Coordinates are iOS points, not pixels: iphone_screenshot returns an "
        "image scaled to points, so a position in the image can be passed straight to iphone_tap. "
        "Typical loop: look (iphone_ui_tree is cheaper and more precise, iphone_screenshot for visuals) → act "
        "(iphone_tap_text / iphone_tap / iphone_swipe / iphone_type) → iphone_wait_for or look again. "
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


_recovering = contextvars.ContextVar("iphone_mcp_recovering", default=False)


def _err(e: Exception) -> str:
    # Inside a @recovering tool, a lost connection is handed up so the agent can be restarted.
    if _recovering.get() and isinstance(e, WDAConnectionError):
        raise e
    return f"Error: {e}"


def recovering(fn):
    """Retry a device tool once after restarting WebDriverAgent when the connection was lost (crash, unplug)."""
    returns_list = fn.__annotations__.get("return") in ("list", list)

    def shaped(text: str):
        return [text] if returns_list else text

    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        token = _recovering.set(True)
        try:
            return await fn(*args, **kwargs)
        except WDAConnectionError as lost:
            if not conn.auto_recover:
                return shaped(f"Error: {lost}")
            try:
                await conn.recover()
            except WDAError as e:
                return shaped(f"Error: {lost}\nRestarting WebDriverAgent failed: {e}")
        finally:
            _recovering.reset(token)
        result = await fn(*args, **kwargs)  # second and last attempt
        note = "(WebDriverAgent had stopped and was restarted.) "
        if isinstance(result, list):
            return [*result, note.strip()]
        return note + result

    return wrapper


# ---------------------------------------------------------------- shared parameter types
X = Annotated[
    float,
    Field(
        description="Horizontal position in iOS points, from the left edge (0) to the screen width shown by iphone_status / iphone_screenshot.",
        ge=0,
    ),
]
Y = Annotated[
    float,
    Field(
        description="Vertical position in iOS points, from the top edge (0) to the screen height shown by iphone_status / iphone_screenshot.",
        ge=0,
    ),
]
APP = Annotated[
    str,
    Field(
        description="App name as shown on the home screen (e.g. 'Settings', 'Einstellungen', 'Safari') or bundle ID (e.g. 'com.apple.Preferences').",
        min_length=1,
    ),
]


def _ann(title: str, *, read_only: bool, destructive: bool = False, idempotent: bool = False) -> ToolAnnotations:
    # open_world: every tool acts on a real, external device
    return ToolAnnotations(
        title=title,
        read_only_hint=read_only,
        destructive_hint=destructive,
        idempotent_hint=idempotent,
        open_world_hint=True,
    )


# ---------------------------------------------------------------- connection
@mcp.tool(annotations=_ann("iPhone status", read_only=True, idempotent=True))
async def iphone_status() -> str:
    """Report the iPhone connection and state: device and iOS version, connection route (USB or Wi-Fi),
    screen size in points, whether it is locked, whether keep-awake is on, and the foreground app.

    Use it first in a session and whenever an action fails, to decide whether to call iphone_start_agent
    (no connection) or ask the user to unlock (locked). Reads only; changes nothing on the phone."""
    lines = []
    info = await conn.coredevice_info()
    if info:
        transport = str(info.get("transportType") or "-")
        via = {"wired": "cable", "localNetwork": "Wi-Fi"}.get(transport, transport)
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


@mcp.tool(annotations=_ann("Start iPhone agent", read_only=False, idempotent=True))
async def iphone_start_agent() -> str:
    """Start WebDriverAgent on the iPhone and connect to it – required before any other iphone_* action.

    Call it when iphone_status or another tool reports no connection. It is safe to call again: if the
    agent already runs it just reports the connection. Starting takes 10–90 s, works over cable or Wi-Fi
    and needs the iPhone unlocked; if it is locked, the result says so and the user must unlock it.
    Returns the connection route on success or a concrete reason on failure."""
    try:
        return await conn.start_wda()
    except WDAError as e:
        return _err(e)


@mcp.tool(annotations=_ann("Stop iPhone agent", read_only=False, idempotent=True))
async def iphone_stop_agent() -> str:
    """Stop WebDriverAgent on the iPhone and close the connection.

    Use it when the session is finished, so the automation port on the phone is no longer open. Afterwards
    every other iphone_* tool fails until iphone_start_agent is called again. Leaves apps and data untouched."""
    return await conn.stop_wda()


@mcp.tool(annotations=_ann("Keep screen awake", read_only=False, idempotent=True))
async def iphone_keep_awake(
    enabled: Annotated[
        bool,
        Field(
            description="true keeps the screen from auto-locking during this session (default); false lets it lock normally."
        ),
    ],
) -> str:
    """Turn the no-auto-lock behaviour on or off for this session. It is on by default.

    While on, an invisible touch just outside the screen is sent every 10 s, which iOS counts as activity;
    no settings are changed and it ends with the session. Turn it off if the user wants the phone to lock.
    It cannot unlock a phone that is already locked – use iphone_lock for that."""
    conn.set_keep_awake(enabled)
    return "Keep-awake is on: the screen will not auto-lock during this session." if enabled else "Keep-awake is off."


# ---------------------------------------------------------------- looking
@mcp.tool(annotations=_ann("Take screenshot", read_only=True, idempotent=True))
@recovering
async def iphone_screenshot(
    quality: Annotated[
        int, Field(description="JPEG quality from 20 to 95; lower is smaller and cheaper.", ge=20, le=95)
    ] = 70,
) -> list:
    """Capture the current iPhone screen as a JPEG image scaled to iOS points, plus its size.

    Because the image is in points, any pixel position in it can be passed unchanged to iphone_tap or
    iphone_swipe. Use it to see visual content (images, colours, layout, games). To find buttons or read
    text, iphone_ui_tree is cheaper and gives exact positions. Reads only; adds a note if the phone is locked."""
    try:
        w = await _wda()
        png = await w.screenshot_png()
        pw, ph = await w.window_size()
    except WDAError as e:
        return [_err(e)]
    img = PILImage.open(io.BytesIO(png)).convert("RGB")
    landscape = img.width > img.height
    target = (max(pw, ph), min(pw, ph)) if landscape else (min(pw, ph), max(pw, ph))
    img = img.resize(target, PILImage.Resampling.LANCZOS)
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


@mcp.tool(annotations=_ann("Read UI tree", read_only=True, idempotent=True))
@recovering
async def iphone_ui_tree(
    max_lines: Annotated[
        int,
        Field(
            description="Maximum number of elements to return (10–1000); the rest is cut off with a count.",
            ge=10,
            le=1000,
        ),
    ] = 250,
) -> str:
    """List the visible UI elements of the current screen as text, one per line:
    [Type] "label" value=… @(center x,center y) width×height, indented by nesting, all in iOS points.

    Prefer this over iphone_screenshot to find buttons, fields and text and to read values: it is cheaper
    and positions are exact. Pass the center point to iphone_tap, or use iphone_tap_text with the label.
    Reads only; adds a note if the phone is locked."""
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


def predicate_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace("'", "\\'")


async def _visible_matches(w: WDAClient, text: str, exact: bool) -> list[dict]:
    """Rects of on-screen elements whose label, name or value matches `text`, topmost first."""
    q = predicate_escape(text)
    op = "==[c]" if exact else "CONTAINS[c]"
    # The app's own window carries the app name as label – never treat it as a match.
    predicate = f"type != 'XCUIElementTypeApplication' AND (label {op} '{q}' OR name {op} '{q}' OR value {op} '{q}')"
    for attempt in range(3):
        try:
            ids = await w.find_elements("predicate string", predicate)
            break
        except WDAError as e:
            # While the screen changes, WebDriverAgent can report its own cached elements as stale.
            if "stale element" not in str(e).lower() or attempt == 2:
                raise
            await asyncio.sleep(0.3)
    sw, sh = await w.window_size()
    hits = []
    for eid in ids[:40]:
        try:
            r = await w.element_rect(eid)
        except WDAError:
            continue  # element vanished between search and read (screen changed) – skip it
        cx, cy = r["x"] + r["width"] / 2, r["y"] + r["height"] / 2
        if r["width"] > 0 and r["height"] > 0 and 0 <= cx <= sw and 0 <= cy <= sh:
            hits.append(r)
    hits.sort(key=lambda r: (round(r["y"]), r["width"] * r["height"]))
    return hits


@mcp.tool(annotations=_ann("Wait for text", read_only=True, idempotent=True))
@recovering
async def iphone_wait_for(
    text: Annotated[
        str,
        Field(
            description="Text to wait for, matched case-insensitively against element labels, names and values.",
            min_length=1,
        ),
    ],
    timeout: Annotated[float, Field(description="Maximum seconds to wait (1–60).", ge=1, le=60)] = 10,
    gone: Annotated[
        bool,
        Field(
            description="false waits until the text appears; true waits until it disappears (e.g. a spinner or 'Loading')."
        ),
    ] = False,
    exact: Annotated[
        bool, Field(description="true requires the whole label to equal the text instead of containing it.")
    ] = False,
) -> str:
    """Wait until an element with the given text appears on screen (or disappears with gone=true).

    Use it after an action that triggers loading, navigation or an animation, instead of guessing a delay
    or taking repeated screenshots. Polls about twice a second and returns as soon as the condition holds,
    with the element's position, or reports a timeout. Reads only."""
    try:
        w = await _wda()
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            hits = await _visible_matches(w, text, exact)
            if gone and not hits:
                return f"“{text}” is no longer on screen."
            if hits and not gone:
                r = hits[0]
                return f"“{text}” is visible at ({r['x'] + r['width'] / 2:.0f}, {r['y'] + r['height'] / 2:.0f})."
            if loop.time() >= deadline:
                state = "still visible" if gone else "not visible"
                return f"Timeout after {timeout:.0f}s: “{text}” is {state}." + await _lock_note(w)
            await asyncio.sleep(0.5)
    except WDAError as e:
        return _err(e)


# ---------------------------------------------------------------- gestures
@mcp.tool(annotations=_ann("Tap", read_only=False))
@recovering
async def iphone_tap(
    x: X,
    y: Y,
    gesture: Annotated[
        Literal["tap", "double", "long"],
        Field(
            description="'tap' = single tap (buttons, links, fields); 'double' = double tap (zoom, select a word); 'long' = touch and hold (context menus, moving icons)."
        ),
    ] = "tap",
    hold_seconds: Annotated[
        float, Field(description="How long to hold for gesture='long' (0.3–10 s).", ge=0.3, le=10)
    ] = 1.0,
) -> str:
    """Touch the screen at a coordinate: single tap, double tap or long press.

    Use it when you know the position – from iphone_ui_tree (center point) or iphone_screenshot (pixel =
    point). To hit an element by its visible text, iphone_tap_text is more robust. The touch acts on
    whatever is at that point, like a finger would; it does not wait for the app to finish reacting, so
    look again (or call iphone_wait_for) before the next step."""
    try:
        w = await _wda()
        if gesture == "double":
            await w.double_tap(x, y)
            done = "Double-tapped"
        elif gesture == "long":
            await w.tap(x, y, hold_ms=int(hold_seconds * 1000))
            done = f"Long-pressed ({hold_seconds:.1f}s)"
        else:
            await w.tap(x, y)
            done = "Tapped"
        return f"{done} at ({x:.0f}, {y:.0f})." + await _lock_note(w)
    except WDAError as e:
        return _err(e)


@mcp.tool(annotations=_ann("Tap element by text", read_only=False))
@recovering
async def iphone_tap_text(
    text: Annotated[
        str,
        Field(
            description="Visible text of the element to tap, matched case-insensitively against its label, name and value.",
            min_length=1,
        ),
    ],
    index: Annotated[
        int, Field(description="Which match to tap when several elements match, 0 = topmost on screen.", ge=0)
    ] = 0,
    exact: Annotated[
        bool, Field(description="true requires the whole label to equal the text instead of containing it.")
    ] = False,
) -> str:
    """Find a visible element by its text and tap its center.

    Prefer this over iphone_tap for buttons, cells and links with a label: it does not depend on layout or
    screen size. If nothing matches, it says so and taps nothing; with several matches it reports how many
    there are so you can pick another with `index`. Like a finger tap, the effect depends on the element."""
    try:
        w = await _wda()
        hits = await _visible_matches(w, text, exact)
        if not hits:
            return f"No visible element matching “{text}”; nothing was tapped." + await _lock_note(w)
        if index >= len(hits):
            return f"Only {len(hits)} matches for “{text}”; nothing was tapped."
        r = hits[index]
        cx, cy = r["x"] + r["width"] / 2, r["y"] + r["height"] / 2
        await w.tap(cx, cy)
        return f"Tapped “{text}” at ({cx:.0f}, {cy:.0f}) — match {index + 1} of {len(hits)}."
    except WDAError as e:
        return _err(e)


@mcp.tool(annotations=_ann("Swipe", read_only=False))
@recovering
async def iphone_swipe(
    x1: Annotated[float, Field(description="Start x in iOS points.", ge=0)],
    y1: Annotated[float, Field(description="Start y in iOS points.", ge=0)],
    x2: Annotated[float, Field(description="End x in iOS points.", ge=0)],
    y2: Annotated[float, Field(description="End y in iOS points.", ge=0)],
    duration_ms: Annotated[
        int,
        Field(
            description="Duration of the movement in milliseconds (50–5000); short is a flick, long is a drag.",
            ge=50,
            le=5000,
        ),
    ] = 300,
) -> str:
    """Drag one finger from a start point to an end point.

    Use it for precise drags: sliders, reordering, swiping a cell to reveal actions, pulling down the
    notification area or a sheet. For plain scrolling of a list or page, iphone_scroll is simpler.
    Swipes starting at the very bottom or top edge trigger iOS system gestures (home, notifications)."""
    try:
        await (await _wda()).swipe(x1, y1, x2, y2, duration_ms)
        return f"Swiped ({x1:.0f},{y1:.0f}) → ({x2:.0f},{y2:.0f}) in {duration_ms} ms."
    except WDAError as e:
        return _err(e)


def scroll_vector(direction: str, amount: float, size: tuple[int, int]) -> tuple[float, float, float, float]:
    """Start and end point of a swipe that scrolls content in `direction` ('down' = reveal content below)."""
    sw, sh = size
    a = max(0.1, min(amount, 0.9))
    dx, dy = {"down": (0, -1), "up": (0, 1), "left": (1, 0), "right": (-1, 0)}[direction]
    cx, cy = sw / 2, sh / 2
    return cx - dx * sw * a / 2, cy - dy * sh * a / 2, cx + dx * sw * a / 2, cy + dy * sh * a / 2


@mcp.tool(annotations=_ann("Scroll", read_only=False))
@recovering
async def iphone_scroll(
    direction: Annotated[
        Literal["up", "down", "left", "right"],
        Field(
            description="Where to move the view: 'down' reveals content further down (finger moves up), 'right' reveals content to the right."
        ),
    ],
    amount: Annotated[
        float, Field(description="Distance as a share of the screen size, 0.1–0.9.", ge=0.1, le=0.9)
    ] = 0.5,
) -> str:
    """Scroll the content in the middle of the screen in a direction.

    Use it to reach items outside the visible area of lists, pages and carousels, then call iphone_ui_tree
    again. It starts in the screen center, so it never triggers system edge gestures; for exact drags use
    iphone_swipe."""
    try:
        w = await _wda()
        await w.swipe(*scroll_vector(direction, amount, await w.window_size()), 400)
        return f"Scrolled {direction}." + await _lock_note(w)
    except WDAError as e:
        return _err(e)


@mcp.tool(annotations=_ann("Pinch to zoom", read_only=False))
@recovering
async def iphone_pinch(
    scale: Annotated[
        float,
        Field(
            description="Zoom factor: above 1 zooms in (fingers apart, e.g. 2.0), below 1 zooms out (fingers together, e.g. 0.5). Range 0.2–5.",
            ge=0.2,
            le=5,
        ),
    ],
    x: Annotated[float | None, Field(description="Center x in iOS points; default is the screen center.", ge=0)] = None,
    y: Annotated[float | None, Field(description="Center y in iOS points; default is the screen center.", ge=0)] = None,
) -> str:
    """Two-finger pinch gesture to zoom in or out, e.g. in Maps, Photos or a web page.

    Use it only where content is zoomable; elsewhere it usually has no effect. Single-finger gestures are
    covered by iphone_tap, iphone_swipe and iphone_scroll."""
    try:
        w = await _wda()
        sw, sh = await w.window_size()
        cx, cy = (x if x is not None else sw / 2), (y if y is not None else sh / 2)
        await w.pinch(cx, cy, scale)
        return f"Pinched ×{scale:g} around ({cx:.0f}, {cy:.0f})." + await _lock_note(w)
    except WDAError as e:
        return _err(e)


# ---------------------------------------------------------------- input & buttons
@mcp.tool(annotations=_ann("Type text", read_only=False))
@recovering
async def iphone_type(
    text: Annotated[str, Field(description="Text to type, exactly as it should appear. Use \\n for a line break.")],
    submit: Annotated[
        bool, Field(description="true presses Return after typing (send a search, confirm a form).")
    ] = False,
) -> str:
    """Type text into the text field that currently has keyboard focus.

    Tap the field first (iphone_tap_text or iphone_tap) so the keyboard is open; without a focused field
    the call fails. Text is appended at the cursor, not replacing existing content. Never use it for
    passwords or passcodes – ask the user to enter those."""
    try:
        await (await _wda()).type_text(text + ("\n" if submit else ""))
        return f"Typed {len(text)} characters" + (" and pressed Return." if submit else ".")
    except WDAError as e:
        return _err(e)


@mcp.tool(annotations=_ann("Press hardware button", read_only=False))
@recovering
async def iphone_press_button(
    button: Annotated[
        Literal["home", "volumeUp", "volumeDown"],
        Field(
            description="'home' returns to the home screen (leaves the current app running in the background); 'volumeUp' / 'volumeDown' change the volume by one step."
        ),
    ],
) -> str:
    """Press a hardware button of the iPhone.

    iPhones have no back button: to go back inside an app, tap its back button (often top left, see
    iphone_ui_tree). To leave an app use 'home'; to quit it completely use iphone_close_app; to lock the
    screen use iphone_lock."""
    try:
        w = await _wda()
        if button == "home":
            await w.home()
        else:
            await w.press_button(button)
        return f"Pressed {button}." + await _lock_note(w)
    except WDAError as e:
        return _err(e)


@mcp.tool(annotations=_ann("Lock or unlock", read_only=False, idempotent=True))
@recovering
async def iphone_lock(
    action: Annotated[
        Literal["lock", "unlock"],
        Field(description="'lock' turns the screen off and locks; 'unlock' wakes the screen and swipes to unlock."),
    ],
) -> str:
    """Lock the iPhone, or wake and unlock it.

    'unlock' only completes on phones without a passcode; with a passcode or Face ID it wakes the screen
    and reports that the user must unlock – never try to enter a passcode. Use iphone_status to see the
    current lock state and iphone_keep_awake to prevent auto-lock."""
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


@mcp.tool(annotations=_ann("List apps", read_only=True, idempotent=True))
async def iphone_list_apps(
    filter: Annotated[
        str | None, Field(description="Only list apps whose name or bundle ID contains this text (case-insensitive).")
    ] = None,
) -> str:
    """List the apps installed on the iPhone as 'Name — bundle ID' lines, sorted by name.

    Use it to find the exact bundle ID or name for iphone_launch_app / iphone_close_app when a name is
    ambiguous or not found. Needs the cable or Wi-Fi sync; otherwise it lists the built-in Apple apps it
    knows. Reads only."""
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


@mcp.tool(annotations=_ann("Launch app", read_only=False, idempotent=True))
@recovering
async def iphone_launch_app(app: APP) -> str:
    """Open an app, or bring it to the front if it is already running (its state is kept).

    Faster and more reliable than finding and tapping its icon on the home screen. To start an app fresh,
    call iphone_close_app first. To open a specific page or link inside an app, use iphone_open_url."""
    try:
        bid = await resolve_app(app)
        w = await _wda()
        await w.launch_app(bid)
        return f"Launched {bid}." + await _lock_note(w)
    except WDAError as e:
        return _err(e)


@mcp.tool(annotations=_ann("Close app", read_only=False, destructive=True, idempotent=True))
@recovering
async def iphone_close_app(app: APP) -> str:
    """Force-quit an app, like swiping it away in the app switcher.

    Use it to reset an app to a fresh start or to stop something running. Unsaved input in that app (for
    example a half-written message) can be lost, so prefer iphone_press_button('home') to just leave it."""
    try:
        bid = await resolve_app(app)
        await (await _wda()).terminate_app(bid)
        return f"Terminated {bid}."
    except WDAError as e:
        return _err(e)


@mcp.tool(annotations=_ann("Open URL or deep link", read_only=False))
@recovering
async def iphone_open_url(
    url: Annotated[
        str,
        Field(
            description="Web URL (https://…) or deep link with an app scheme (e.g. maps://?q=Berlin, mailto:…, or an app's own scheme).",
            min_length=3,
        ),
    ],
) -> str:
    """Open a web page in Safari or a deep link in the app that handles it.

    Use it to jump straight to a page or screen instead of navigating by taps. Links with tel:, sms:,
    mailto: or payment schemes start real actions or drafts on the phone – ask the user first. iOS may show
    a confirmation dialog; check with iphone_ui_tree afterwards."""
    try:
        w = await _wda()
        await w.open_url(url)
        return f"Opened {url}." + await _lock_note(w)
    except WDAError as e:
        return _err(e)


def _cleanup_on_exit() -> None:
    """Stop WebDriverAgent when the last iphone-mcp server ends, so port 8100 on the iPhone is not left open.

    Not if other agent windows still use it, if it was started by hand (`iphone-mcp start`), or with
    IPHONE_MCP_STOP_ON_EXIT=0. A runner launched without Xcode ends together with this process anyway."""
    others = paths.unregister_client(os.getpid())
    stop = os.environ.get("IPHONE_MCP_STOP_ON_EXIT", "1").lower() not in ("0", "false", "no", "off")
    state = paths.load(paths.STATE_FILE)
    if stop and not others and state.get("wda_pid") and state.get("wda_owner", "server") == "server":
        try:
            asyncio.run(Connection().stop_wda())
        except Exception:  # noqa: BLE001 - best effort while shutting down
            pass


def serve() -> None:
    paths.register_client(os.getpid())
    # Clients usually end a stdio server by closing stdin or with SIGTERM; both must run the cleanup.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    try:
        mcp.run()
    finally:
        _cleanup_on_exit()
