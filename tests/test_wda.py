import asyncio

import httpx

from iphone_mcp import server
from iphone_mcp.wda import WDAClient, WDAError


def _client(handler) -> WDAClient:
    c = WDAClient("http://wda")
    c._http = httpx.AsyncClient(base_url="http://wda", transport=httpx.MockTransport(handler))
    return c


def test_session_joins_existing_session_instead_of_creating_one():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.url.path == "/status":
            return httpx.Response(200, json={"value": {"ready": True}, "sessionId": "ABC"})
        return httpx.Response(200, json={"value": {"sessionId": "NEW"}})

    sid = asyncio.run(_client(handler).session())
    assert sid == "ABC"
    assert ("POST", "/session") not in calls


def test_session_created_when_none_exists():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/status":
            return httpx.Response(200, json={"value": {"ready": True}})
        return httpx.Response(200, json={"value": {"sessionId": "NEW"}})

    assert asyncio.run(_client(handler).session()) == "NEW"


class StaleClient:
    async def find_elements(self, using, value):
        return ["gone", "ok"]

    async def element_rect(self, eid):
        if eid == "gone":
            raise WDAError("stale element reference")
        return {"x": 10, "y": 20, "width": 100, "height": 40}

    async def window_size(self):
        return (430, 932)


def test_visible_matches_skips_vanished_elements():
    hits = asyncio.run(server._visible_matches(StaleClient(), "Settings", exact=False))
    assert hits == [{"x": 10, "y": 20, "width": 100, "height": 40}]
