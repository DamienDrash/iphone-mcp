"""Tool behaviour against a simulated WebDriverAgent (no iPhone needed)."""

import asyncio
import base64
import io
import json

import httpx
import pytest
from PIL import Image

from iphone_mcp import server
from iphone_mcp.wda import WDAClient, WDAConnectionError

SID = "S1"


class FakeWDA:
    """Answers the WebDriverAgent endpoints iphone-mcp uses and records what it was asked to do."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, object]] = []
        self.elements: dict[str, dict] = {}  # id -> rect
        self.element_rounds: list[list[str]] = []  # ids returned per /elements call (last one repeats)
        self.locked = False
        self.focused: str | None = None  # element with keyboard focus

    def handler(self, request: httpx.Request) -> httpx.Response:
        path, method = request.url.path, request.method
        body = json.loads(request.content) if request.content else None
        self.calls.append((method, path, body))
        s = f"/session/{SID}"
        if path == "/status":
            return httpx.Response(200, json={"value": {"ready": True}, "sessionId": SID})
        if path == f"{s}/window/size":
            return self._v({"width": 430, "height": 932})
        if path == f"{s}/element/active":
            return self._v({"ELEMENT": self.focused} if self.focused else None)
        if path == "/wda/locked":
            return self._v(self.locked)
        if path == f"{s}/elements":
            ids = self.element_rounds.pop(0) if len(self.element_rounds) > 1 else (self.element_rounds or [[]])[0]
            return self._v([{"ELEMENT": i} for i in ids])
        if path.startswith(f"{s}/element/") and path.endswith("/rect"):
            return self._v(self.elements[path.split("/")[4]])
        if path == "/screenshot":
            buf = io.BytesIO()
            Image.new("RGB", (1290, 2796), "white").save(buf, format="PNG")
            return self._v(base64.b64encode(buf.getvalue()).decode())
        return self._v(None)

    @staticmethod
    def _v(value) -> httpx.Response:
        return httpx.Response(200, json={"value": value})

    def posted(self, suffix: str) -> list:
        return [b for m, p, b in self.calls if m == "POST" and p.endswith(suffix)]


@pytest.fixture
def wda(monkeypatch):
    fake = FakeWDA()
    client = WDAClient("http://wda")
    client._http = httpx.AsyncClient(base_url="http://wda", transport=httpx.MockTransport(fake.handler))

    async def get():
        return client

    monkeypatch.setattr(server.conn, "get", get)
    return fake


def run(coro):
    return asyncio.run(coro)


def pointer_steps(body) -> list[dict]:
    return body["actions"][0]["actions"]


def test_tap_single(wda):
    assert run(server.iphone_tap(100, 200)).startswith("Tapped at (100, 200)")
    steps = pointer_steps(wda.posted("/actions")[0])
    assert steps[0] == {"type": "pointerMove", "duration": 0, "x": 100, "y": 200}
    assert [s["type"] for s in steps] == ["pointerMove", "pointerDown", "pause", "pointerUp"]


def test_tap_double_and_long(wda):
    run(server.iphone_tap(10, 20, gesture="double"))
    run(server.iphone_tap(10, 20, gesture="long", hold_seconds=2))
    double, long = wda.posted("/actions")
    assert sum(s["type"] == "pointerDown" for s in pointer_steps(double)) == 2
    assert {"type": "pause", "duration": 2000} in pointer_steps(long)


def test_pinch_uses_two_fingers_moving_apart(wda):
    run(server.iphone_pinch(2.0))
    fingers = wda.posted("/actions")[0]["actions"]
    assert len(fingers) == 2
    start = abs(fingers[0]["actions"][0]["x"] - fingers[1]["actions"][0]["x"])
    end = abs(fingers[0]["actions"][3]["x"] - fingers[1]["actions"][3]["x"])
    assert end == pytest.approx(2 * start)
    assert fingers[0]["actions"][0]["y"] == 466  # default: screen center


def test_tap_text_picks_topmost_and_excludes_app_window(wda):
    wda.elements = {
        "low": {"x": 0, "y": 500, "width": 100, "height": 40},
        "high": {"x": 0, "y": 100, "width": 100, "height": 40},
    }
    wda.element_rounds = [["low", "high"]]
    assert "match 1 of 2" in run(server.iphone_tap_text("General"))
    assert "XCUIElementTypeApplication" in wda.posted("/elements")[0]["value"]
    assert pointer_steps(wda.posted("/actions")[0])[0]["y"] == 120  # center of the higher element


def test_tap_text_without_match_taps_nothing(wda):
    wda.element_rounds = [[]]
    assert "nothing was tapped" in run(server.iphone_tap_text("Missing"))
    assert wda.posted("/actions") == []


def test_wait_for_appears_after_polling(wda):
    wda.elements = {"e": {"x": 0, "y": 0, "width": 50, "height": 50}}
    wda.element_rounds = [[], [], ["e"]]
    assert run(server.iphone_wait_for("Done", timeout=5)) == "“Done” is visible at (25, 25)."


def test_wait_for_gone_and_timeout(wda):
    wda.elements = {"e": {"x": 0, "y": 0, "width": 50, "height": 50}}
    wda.element_rounds = [["e"], []]
    assert "no longer on screen" in run(server.iphone_wait_for("Loading", timeout=5, gone=True))
    wda.element_rounds = [[]]
    assert run(server.iphone_wait_for("Never", timeout=1)).startswith("Timeout after 1s")


def test_type_submit_appends_return(wda):
    assert run(server.iphone_type("hi", submit=True)) == "Typed 2 characters and pressed Return."
    assert wda.posted("/wda/keys")[0]["value"] == ["h", "i", "\n"]


def test_home_button_and_lock_note(wda):
    wda.locked = True
    result = run(server.iphone_press_button("home"))
    assert result.startswith("Pressed home.") and "locked" in result
    assert wda.posted("/wda/homescreen")


def test_launch_app_by_name(wda):
    assert run(server.iphone_launch_app("Einstellungen")).startswith("Launched com.apple.Preferences")
    assert wda.posted("/wda/apps/launch")[0] == {"bundleId": "com.apple.Preferences"}


def test_screenshot_is_scaled_to_points(wda):
    image, size = run(server.iphone_screenshot())
    assert size.startswith("430×932 points")
    assert Image.open(io.BytesIO(image.data)).size == (430, 932)


def test_recovering_restarts_agent_once(monkeypatch, wda):
    real_get = server.conn.get
    calls = {"get": 0, "recover": 0}

    async def flaky_get():
        calls["get"] += 1
        if calls["get"] == 1:
            raise WDAConnectionError("lost")
        return await real_get()

    async def recover():
        calls["recover"] += 1
        return "restarted"

    monkeypatch.setattr(server.conn, "get", flaky_get)
    monkeypatch.setattr(server.conn, "recover", recover)
    monkeypatch.setattr(server.conn, "auto_recover", True)
    result = run(server.iphone_tap(1, 2))
    assert calls["recover"] == 1
    assert result.startswith("(WebDriverAgent had stopped and was restarted.) Tapped")


def test_recovering_reports_failed_restart(monkeypatch, wda):
    async def lost():
        raise WDAConnectionError("lost")

    async def recover():
        from iphone_mcp.wda import WDAError

        raise WDAError("The iPhone is locked")

    monkeypatch.setattr(server.conn, "get", lost)
    monkeypatch.setattr(server.conn, "recover", recover)
    monkeypatch.setattr(server.conn, "auto_recover", True)
    result = run(server.iphone_tap(1, 2))
    assert result.startswith("Error: lost") and "The iPhone is locked" in result


def test_search_retries_when_screen_is_changing(monkeypatch, wda):
    original = wda.handler
    state = {"n": 0}

    def flaky(request):
        if request.url.path.endswith("/elements") and state["n"] < 2:
            state["n"] += 1
            return httpx.Response(404, json={"value": {"error": "stale element reference", "message": "gone"}})
        return original(request)

    wda.handler = flaky
    client = WDAClient("http://wda")
    client._http = httpx.AsyncClient(base_url="http://wda", transport=httpx.MockTransport(lambda r: wda.handler(r)))

    async def get():
        return client

    monkeypatch.setattr(server.conn, "get", get)
    wda.element_rounds = [[]]
    assert "no longer on screen" in run(server.iphone_wait_for("General", timeout=2, gone=True))
    assert state["n"] == 2


def test_type_replace_clears_focused_field_first(wda):
    wda.focused = "field"
    assert run(server.iphone_type("new", replace=True)) == "Cleared the field and typed 3 characters."
    assert wda.posted("/element/field/clear")
    assert wda.posted("/wda/keys")[0]["value"] == ["n", "e", "w"]
    assert run(server.iphone_type("", replace=True)) == "Cleared the field."
    assert len(wda.posted("/wda/keys")) == 1


def test_type_replace_without_focus_types_nothing(wda):
    assert "no text field has keyboard focus" in run(server.iphone_type("x", replace=True))
    assert wda.posted("/wda/keys") == []


@pytest.mark.parametrize(
    ("panel", "start_y", "hold"),
    [("notification_center", 2, False), ("control_center", 2, False)],
)
def test_swipe_opens_system_panel(wda, panel, start_y, hold):
    wda.elements = {"cc": {"x": 0, "y": 0, "width": 10, "height": 10}}
    wda.element_rounds = [["cc"]]
    assert run(server.iphone_swipe(system=panel)).startswith("Opened")
    steps = pointer_steps(wda.posted("/actions")[0])
    assert steps[0]["y"] == start_y
    assert (steps[-2]["type"] == "pause") is hold


def test_control_center_swipe_is_repeated_until_it_opens(wda):
    wda.elements = {"cc": {"x": 0, "y": 0, "width": 10, "height": 10}}
    wda.element_rounds = [[], ["cc"]]
    assert run(server.iphone_swipe(system="control_center")).startswith("Opened control center")
    assert len(wda.posted("/actions")) == 2
    wda.element_rounds = [[]]
    assert "did not open after 3 swipes" in run(server.iphone_swipe(system="control_center"))


def test_notification_center_is_not_reported_as_locked(wda):
    wda.locked = True  # iOS reports the cover sheet as locked
    assert "locked" not in run(server.iphone_swipe(system="notification_center"))


def test_swipe_without_coordinates_or_panel_fails(wda):
    assert run(server.iphone_swipe(x1=1, y1=2)).startswith("Error: give x1")
    assert wda.posted("/actions") == []


def test_pinch_rotation_turns_fingers_along_the_arc(wda):
    run(server.iphone_pinch(rotation=90))
    fingers = wda.posted("/actions")[0]["actions"]
    moves = [a for a in fingers[1]["actions"] if a["type"] == "pointerMove"][1:]
    assert len(moves) == 6  # 90° in steps of at most 15°
    first, last = fingers[1]["actions"][0], moves[-1]
    assert (first["x"], first["y"]) == pytest.approx((275, 466))  # right of the center
    assert (last["x"], last["y"]) == pytest.approx((215, 526))  # below it: turned clockwise
