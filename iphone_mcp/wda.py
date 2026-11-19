"""Small async client for WebDriverAgent (github.com/appium/WebDriverAgent)."""

from __future__ import annotations

import base64
from typing import Any

import httpx

# Attributes that are expensive to compute; fetching them on big screens
# (e.g. the home screen) can make WebDriverAgent crash.
EXCLUDED_ATTRIBUTES = "customActions,visible,accessible,placeholderValue,traits"


class WDAError(RuntimeError):
    pass


class WDAClient:
    def __init__(self, base_url: str, timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self._http = httpx.AsyncClient(base_url=self.base_url, timeout=timeout)
        self._session: str | None = None

    async def close(self) -> None:
        await self._http.aclose()

    # ---------- low level ----------
    async def _request(self, method: str, path: str, json: Any = None) -> Any:
        try:
            r = await self._http.request(method, path, json=json)
        except httpx.HTTPError as e:
            raise WDAError(f"WebDriverAgent at {self.base_url} is not reachable: {e}") from e
        try:
            data = r.json()
        except ValueError:
            raise WDAError(f"Unexpected response ({r.status_code}): {r.text[:200]}") from None
        value = data.get("value") if isinstance(data, dict) else data
        if r.status_code >= 400 or (isinstance(value, dict) and "error" in value):
            err = value.get("error") if isinstance(value, dict) else r.status_code
            msg = value.get("message") if isinstance(value, dict) else r.text
            raise WDAError(f"{err}: {msg}")
        return value

    async def status(self) -> dict:
        return await self._request("GET", "/status")

    async def session(self) -> str:
        if self._session:
            return self._session
        value = await self._request(
            "POST", "/session", {"capabilities": {"alwaysMatch": {"shouldWaitForQuiescence": False}}}
        )
        self._session = value.get("sessionId") if isinstance(value, dict) else None
        if not self._session:
            raise WDAError("WebDriverAgent did not return a session id")
        return self._session

    async def s(self, method: str, path: str, json: Any = None) -> Any:
        """Request inside the session; recreates the session once if it expired."""
        for attempt in range(2):
            sid = await self.session()
            try:
                return await self._request(method, f"/session/{sid}{path}", json)
            except WDAError as e:
                text = str(e).lower()
                if attempt == 0 and ("invalid session" in text or "session does not exist" in text):
                    self._session = None
                    continue
                raise

    # ---------- device ----------
    async def window_size(self) -> tuple[int, int]:
        v = await self.s("GET", "/window/size")
        return int(v["width"]), int(v["height"])

    async def screenshot_png(self) -> bytes:
        return base64.b64decode(await self._request("GET", "/screenshot"))

    async def source_json(self) -> dict:
        return await self._request("GET", f"/source?format=json&excluded_attributes={EXCLUDED_ATTRIBUTES}")

    async def active_app(self) -> dict:
        return await self._request("GET", "/wda/activeAppInfo")

    async def is_locked(self) -> bool:
        return bool(await self._request("GET", "/wda/locked"))

    # ---------- gestures ----------
    async def _actions(self, steps: list[dict]) -> None:
        await self.s(
            "POST",
            "/actions",
            {
                "actions": [
                    {"type": "pointer", "id": "finger1", "parameters": {"pointerType": "touch"}, "actions": steps}
                ]
            },
        )

    async def tap(self, x: float, y: float, hold_ms: int = 50) -> None:
        await self._actions(
            [
                {"type": "pointerMove", "duration": 0, "x": x, "y": y},
                {"type": "pointerDown", "button": 0},
                {"type": "pause", "duration": hold_ms},
                {"type": "pointerUp", "button": 0},
            ]
        )

    async def double_tap(self, x: float, y: float) -> None:
        down_up = [
            {"type": "pointerDown", "button": 0},
            {"type": "pause", "duration": 40},
            {"type": "pointerUp", "button": 0},
        ]
        await self._actions(
            [
                {"type": "pointerMove", "duration": 0, "x": x, "y": y},
                *down_up,
                {"type": "pause", "duration": 80},
                *down_up,
            ]
        )

    async def swipe(self, x1: float, y1: float, x2: float, y2: float, duration_ms: int = 300) -> None:
        await self._actions(
            [
                {"type": "pointerMove", "duration": 0, "x": x1, "y": y1},
                {"type": "pointerDown", "button": 0},
                {"type": "pause", "duration": 30},
                {"type": "pointerMove", "duration": duration_ms, "x": x2, "y": y2},
                {"type": "pointerUp", "button": 0},
            ]
        )

    async def find_elements(self, using: str, value: str) -> list[str]:
        v = await self.s("POST", "/elements", {"using": using, "value": value})
        return [e.get("ELEMENT") or e.get("element-6066-11e4-a52e-4f735466cecf") for e in v]

    async def element_rect(self, eid: str) -> dict:
        return await self.s("GET", f"/element/{eid}/rect")

    # ---------- input / buttons / apps ----------
    async def type_text(self, text: str) -> None:
        await self.s("POST", "/wda/keys", {"value": list(text)})

    async def press_button(self, name: str) -> None:
        await self.s("POST", "/wda/pressButton", {"name": name})

    async def home(self) -> None:
        await self._request("POST", "/wda/homescreen", {})

    async def launch_app(self, bundle_id: str) -> None:
        await self.s("POST", "/wda/apps/launch", {"bundleId": bundle_id})

    async def terminate_app(self, bundle_id: str) -> None:
        await self.s("POST", "/wda/apps/terminate", {"bundleId": bundle_id})

    async def open_url(self, url: str) -> None:
        await self.s("POST", "/url", {"url": url})

    async def lock(self) -> None:
        await self.s("POST", "/wda/lock", {})

    async def unlock(self) -> None:
        await self.s("POST", "/wda/unlock", {})
