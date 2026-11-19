import asyncio

import pytest

from iphone_mcp import server
from iphone_mcp.connection import url_host
from iphone_mcp.wda import WDAError


def test_walk_tree_keeps_visible_meaningful_elements():
    tree = {
        "type": "Application",
        "label": "Settings",
        "rect": {"x": 0, "y": 0, "width": 430, "height": 932},
        "children": [
            {
                "type": "Button",
                "label": "Wi-Fi",
                "value": "Home",
                "rect": {"x": 0, "y": 200, "width": 430, "height": 44},
            },
            {"type": "Button", "label": "Offscreen", "rect": {"x": 0, "y": 2000, "width": 430, "height": 44}},
            {"type": "Other", "rect": {"x": 0, "y": 0, "width": 10, "height": 10}},
            {
                "type": "XCUIElementTypeSwitch",
                "label": "Airplane Mode",
                "value": "0",
                "isEnabled": "0",
                "rect": {"x": 350, "y": 150, "width": 51, "height": 31},
            },
        ],
    }
    out: list[str] = []
    server.walk_tree(tree, out, (430, 932))
    assert out == [
        '[Application] "Settings" @(215,466) 430×932',
        "  [Button] \"Wi-Fi\" value='Home' @(215,222) 430×44",
        "  [Switch] \"Airplane Mode\" value='0' (disabled) @(375,165) 51×31",
    ]


def test_predicate_escape():
    assert server.predicate_escape("it's") == "it\\'s"
    assert server.predicate_escape("a\\b") == "a\\\\b"


@pytest.mark.parametrize(
    "direction,expected",
    [
        ("down", (215.0, 699.0, 215.0, 233.0)),
        ("up", (215.0, 233.0, 215.0, 699.0)),
        ("left", (107.5, 466.0, 322.5, 466.0)),
        ("right", (322.5, 466.0, 107.5, 466.0)),
    ],
)
def test_scroll_vector(direction, expected):
    assert server.scroll_vector(direction, 0.5, (430, 932)) == expected


def test_resolve_app_known_names_and_bundle_ids():
    assert asyncio.run(server.resolve_app("Einstellungen")) == "com.apple.Preferences"
    assert asyncio.run(server.resolve_app("Settings")) == "com.apple.Preferences"
    assert asyncio.run(server.resolve_app("com.example.app")) == "com.example.app"


def test_resolve_app_unknown(monkeypatch):
    async def no_apps():
        return {}

    monkeypatch.setattr(server, "installed_apps", no_apps)
    with pytest.raises(WDAError):
        asyncio.run(server.resolve_app("Definitely Not An App"))


def test_url_host_brackets_ipv6():
    assert url_host("fd23::1") == "[fd23::1]"
    assert url_host("192.168.1.2") == "192.168.1.2"
