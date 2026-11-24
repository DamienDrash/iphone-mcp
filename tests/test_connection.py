import asyncio
import json
import os

import pytest

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


# ---------------------------------------------------------------- several iPhones, one server each
UDID_A = "00008130-000A0A0A0A0A0A0A"
UDID_B = "00008130-000B0B0B0B0B0B0B"


@pytest.fixture
def config(monkeypatch, tmp_path):
    """Point state.json / wda.json / logs at a temporary directory."""
    monkeypatch.setattr(paths, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(paths, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(paths, "WDA_FILE", tmp_path / "wda.json")
    monkeypatch.setattr(paths, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(paths, "WDA_LOG", tmp_path / "logs" / "wda.log")
    monkeypatch.delenv("IPHONE_MCP_UDID", raising=False)
    monkeypatch.delenv("IPHONE_MCP_LAUNCHER", raising=False)
    return tmp_path


def conn_for(monkeypatch, udid):
    monkeypatch.setenv("IPHONE_MCP_UDID", udid)
    return connection.Connection()


def test_runtime_state_is_kept_per_iphone(config, monkeypatch):
    me = os.getpid()
    paths.update_device_state(UDID_A, wda_pid=me, wda_owner="server", last_ip="10.0.0.1")
    paths.register_client(me, UDID_A)
    paths.update_device_state(UDID_B, last_ip="10.0.0.2")

    a, b = conn_for(monkeypatch, UDID_A), conn_for(monkeypatch, UDID_B)
    assert a.wda_running_pid() == me
    assert b.wda_running_pid() is None  # A's xcodebuild is not "foreign" to B: B never waits for or kills it
    assert paths.device_state(UDID_A)["last_ip"] == "10.0.0.1"
    assert paths.device_state(UDID_B)["last_ip"] == "10.0.0.2"
    assert paths.unregister_client(me, UDID_B) == []
    assert paths.device_state(UDID_A)["clients"] == [me]  # B's server ending leaves A's clients alone


def test_stop_wda_only_touches_its_own_iphone(config, monkeypatch):
    killed = []
    monkeypatch.setattr(connection.os, "killpg", lambda pid, sig: killed.append(pid), raising=False)
    monkeypatch.setattr(connection.os, "kill", lambda pid, sig: killed.append(pid))
    monkeypatch.setattr(paths, "pid_alive", lambda pid: True)
    paths.update_device_state(UDID_A, wda_pid=111)
    paths.update_device_state(UDID_B, wda_pid=222)

    asyncio.run(conn_for(monkeypatch, UDID_B).stop_wda())
    assert killed == [222]
    assert paths.device_state(UDID_A)["wda_pid"] == 111
    assert paths.device_state(UDID_B)["wda_pid"] is None


def test_start_wda_launches_for_its_own_udid_despite_other_iphone(config, monkeypatch):
    """B's start must not treat A's running xcodebuild as a leftover of its own and must launch for B."""
    derived = config / "dd"
    (derived / "Build" / "Products").mkdir(parents=True)
    (derived / "Build" / "Products" / "WDA.xctestrun").write_text("")
    paths.save_device_setup(UDID_A, derived_data=str(derived), launcher="xcodebuild")
    paths.save_device_setup(UDID_B, derived_data=str(derived), launcher="xcodebuild")
    monkeypatch.setattr(paths, "pid_alive", lambda pid: pid == 111)
    paths.update_device_state(UDID_A, wda_pid=111)

    launched = []

    class FakeProc:
        pid = 222

        def __init__(self, cmd, **kw):
            launched.append(cmd)

        def poll(self):
            return None

    monkeypatch.setattr(connection.subprocess, "Popen", FakeProc)
    b = conn_for(monkeypatch, UDID_B)
    b.launcher = "xcodebuild"
    calls = 0

    async def fake_get():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise connection.WDAConnectionError("not yet")
        return object()

    monkeypatch.setattr(b, "get", fake_get)
    asyncio.run(b.start_wda(timeout=10))
    assert len(launched) == 1 and f"id={UDID_B}" in launched[0]
    assert paths.device_state(UDID_A)["wda_pid"] == 111
    assert paths.device_state(UDID_B)["wda_pid"] == 222
    assert paths.wda_log(UDID_B).exists() and not paths.wda_log(UDID_A).exists()


def test_legacy_single_iphone_files_still_work(config, monkeypatch):
    """A 1.0 state.json / wda.json (no "devices") belongs to the iPhone in wda.json."""
    (config / "wda.json").write_text(json.dumps({"udid": UDID_A, "team": "ABCDE12345", "launcher": "xcodebuild"}))
    (config / "state.json").write_text(json.dumps({"wda_pid": 4242, "wda_owner": "cli", "last_ip": "10.0.0.9"}))

    assert connection.Connection().udid == UDID_A  # no IPHONE_MCP_UDID: the set-up iPhone, as in 1.0
    assert paths.device_state(UDID_A)["wda_pid"] == 4242
    assert paths.device_state(UDID_B) == {}
    assert paths.wda_config(UDID_B)["team"] == "ABCDE12345"  # the same signed build is reused

    paths.update_device_state(UDID_B, last_ip="10.0.0.2")  # first write moves the 1.0 state under its iPhone
    data = json.loads((config / "state.json").read_text())
    assert "wda_pid" not in data
    assert data["devices"][UDID_A] == {"wda_pid": 4242, "wda_owner": "cli", "last_ip": "10.0.0.9"}


def test_setup_registers_several_iphones(config):
    (config / "wda.json").write_text(json.dumps({"udid": UDID_A, "team": "ABCDE12345", "launcher": "xcodebuild"}))
    paths.save_device_setup(UDID_B, name="Phone B", team="ABCDE12345", launcher="pymobiledevice3")

    assert set(paths.configured_devices()) == {UDID_A, UDID_B}
    assert paths.load(paths.WDA_FILE)["udid"] == UDID_B  # the last set-up iPhone is the default, as in 1.0
    assert paths.wda_config(UDID_A)["launcher"] == "xcodebuild"
    assert paths.wda_config(UDID_B)["launcher"] == "pymobiledevice3"
