import asyncio

from iphone_mcp import connection, paths


def test_launcher_defaults_to_pymobiledevice3_off_macos(monkeypatch):
    monkeypatch.setattr(paths, "IS_MAC", False)
    monkeypatch.delenv("IPHONE_MCP_LAUNCHER", raising=False)
    assert connection.Connection().launcher == "pymobiledevice3"


def test_launcher_env_override(monkeypatch):
    monkeypatch.setenv("IPHONE_MCP_LAUNCHER", "pymobiledevice3")
    assert connection.Connection().launcher == "pymobiledevice3"


def test_keep_awake_env(monkeypatch):
    monkeypatch.setenv("IPHONE_MCP_KEEP_AWAKE", "0")
    assert connection.Connection().keep_awake is False
    monkeypatch.delenv("IPHONE_MCP_KEEP_AWAKE")
    assert connection.Connection().keep_awake is True


class FakeClient:
    def __init__(self, locked):
        self.locked = locked
        self.taps = []

    async def is_locked(self):
        return self.locked

    async def tap(self, x, y, hold_ms=50):
        self.taps.append((x, y, hold_ms))


def test_keep_awake_taps_off_screen_only_when_unlocked(monkeypatch):
    monkeypatch.setattr(connection, "KEEP_AWAKE_INTERVAL", 0.01)

    async def run(locked):
        conn = connection.Connection()
        conn.client = FakeClient(locked)
        task = asyncio.create_task(conn._keep_awake_loop())
        await asyncio.sleep(0.05)
        conn.set_keep_awake(False)
        task.cancel()
        return conn.client.taps

    taps = asyncio.run(run(False))
    assert taps and all(x < 0 and y < 0 and hold == 1 for x, y, hold in taps)
    assert asyncio.run(run(True)) == []
