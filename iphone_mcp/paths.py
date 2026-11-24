"""Where iphone-mcp keeps its config, build output and logs (never inside the repo)."""

from __future__ import annotations

import contextlib
import json
import os
import sys
from collections.abc import Iterator
from pathlib import Path

IS_MAC = sys.platform == "darwin"

CONFIG_DIR = Path(os.environ.get("IPHONE_MCP_CONFIG_DIR") or Path.home() / ".config" / "iphone-mcp")
if IS_MAC:
    _default_data = Path.home() / "Library" / "Application Support" / "iphone-mcp"
    _default_logs = Path.home() / "Library" / "Logs" / "iphone-mcp"
else:
    _default_data = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "iphone-mcp"
    _default_logs = _default_data / "logs"
DATA_DIR = Path(os.environ.get("IPHONE_MCP_DATA_DIR") or _default_data)
LOG_DIR = _default_logs

STATE_FILE = CONFIG_DIR / "state.json"  # runtime state per iPhone: last Wi-Fi IP, xcodebuild pid, servers
WDA_FILE = CONFIG_DIR / "wda.json"  # result of `iphone-mcp setup`, per iPhone
WDA_SRC = DATA_DIR / "WebDriverAgent"
WDA_DERIVED = DATA_DIR / "DerivedData"
WDA_LOG = LOG_DIR / "wda.log"  # runtime log while no UDID is known; see wda_log()
BUILD_LOG = LOG_DIR / "build.log"


def load(path: Path) -> dict:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def pid_alive(pid: int) -> bool:
    """Whether a process exists. Never use os.kill(pid, 0) on Windows: there it terminates the process."""
    if os.name == "nt":
        import ctypes

        kernel32 = getattr(ctypes, "windll").kernel32  # noqa: B009 - windll exists only on Windows
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_ulong()
        ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        kernel32.CloseHandle(handle)
        return bool(ok) and code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ValueError):
        return False


# ---------------------------------------------------------------- shared JSON files
@contextlib.contextmanager
def _locked(path: Path) -> Iterator[None]:
    """Serialise read-modify-write of a shared file between processes (best effort)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path.with_name(path.name + ".lock"), "a+b") as fh:
        locked = False
        try:
            if sys.platform == "win32":
                import msvcrt

                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl

                fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            locked = True
        except OSError:
            pass  # locking unavailable (e.g. some network file systems): carry on unlocked
        try:
            yield
        finally:
            if locked:
                with contextlib.suppress(OSError):
                    if sys.platform == "win32":
                        import msvcrt

                        fh.seek(0)
                        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=2))
    os.replace(tmp, path)


def update(path: Path, **values) -> None:
    with _locked(path):
        data = load(path)
        data.update(values)
        _write(path, data)


# ---------------------------------------------------------------- per-iPhone state
# One MCP server process controls one iPhone; several servers (one per iPhone) share these files.
#   state.json  {"devices": {"<udid>": {"wda_pid", "wda_owner", "last_ip", "clients"}}}
#   wda.json    the last `setup` at the top level (its "udid" is the default iPhone), plus
#               {"devices": {"<udid>": {...that iPhone's setup result...}}}
# iphone-mcp 1.0 kept one iPhone's runtime state at the top level of state.json. That is read as the state of
# the default iPhone (wda.json "udid") and moved under "devices" on the next write.
DEVICE_STATE_KEYS = ("wda_pid", "wda_owner", "last_ip", "clients")
DEFAULT_KEY = "default"  # state key while no UDID is known (e.g. IPHONE_MCP_WDA_URL without setup)


def _key(udid: str | None) -> str:
    return udid or DEFAULT_KEY


def _legacy_key() -> str:
    """The iPhone that top-level (1.0) runtime state in state.json belongs to."""
    return _key(load(WDA_FILE).get("udid"))


def _legacy_state(data: dict) -> dict:
    return {k: data[k] for k in DEVICE_STATE_KEYS if k in data}


def wda_log(udid: str | None) -> Path:
    """Runtime log of the xcodebuild that runs WebDriverAgent on this iPhone (one log per iPhone)."""
    return LOG_DIR / f"wda-{udid}.log" if udid else WDA_LOG


def device_state(udid: str | None) -> dict:
    """Runtime state of one iPhone: wda_pid, wda_owner, last_ip, clients. Never another iPhone's."""
    data = load(STATE_FILE)
    entry = (data.get("devices") or {}).get(_key(udid))
    if entry is not None:
        return dict(entry)
    legacy = _legacy_state(data)
    return legacy if legacy and _legacy_key() == _key(udid) else {}


def all_device_states() -> dict[str, dict]:
    data = load(STATE_FILE)
    out = {k: dict(v) for k, v in (data.get("devices") or {}).items()}
    if legacy := _legacy_state(data):
        out.setdefault(_legacy_key(), legacy)
    return out


def update_device_state(udid: str | None, **values) -> None:
    with _locked(STATE_FILE):
        data = load(STATE_FILE)
        devices = data.setdefault("devices", {})
        if legacy := _legacy_state(data):  # move 1.0 state under its iPhone
            for k in legacy:
                data.pop(k)
            devices.setdefault(_legacy_key(), legacy)
        devices.setdefault(_key(udid), {}).update(values)
        _write(STATE_FILE, data)


def register_client(pid: int, udid: str | None = None) -> None:
    """Remember a running MCP server process (several agent windows can share one iPhone)."""
    with _locked(CONFIG_DIR / "clients"):
        clients = [p for p in device_state(udid).get("clients", []) if p != pid and pid_alive(p)]
        update_device_state(udid, clients=[*clients, pid])


def unregister_client(pid: int, udid: str | None = None) -> list[int]:
    """Forget a server process; returns the other servers for the same iPhone that are still running."""
    with _locked(CONFIG_DIR / "clients"):
        others = [p for p in device_state(udid).get("clients", []) if p != pid and pid_alive(p)]
        update_device_state(udid, clients=others)
    return others


def wda_config(udid: str | None) -> dict:
    """Setup result for one iPhone: the top level of wda.json, overlaid with that iPhone's own entry.

    An iPhone without an entry of its own gets the top level (a 1.0 wda.json, or the last build)."""
    data = load(WDA_FILE)
    cfg = {k: v for k, v in data.items() if k != "devices"}
    if udid:
        cfg.update((data.get("devices") or {}).get(udid) or {})
    return cfg


def configured_devices() -> dict[str, dict]:
    """Every iPhone `iphone-mcp setup` was run for, by UDID (including a 1.0 setup)."""
    data = load(WDA_FILE)
    out = {k: dict(v) for k, v in (data.get("devices") or {}).items()}
    if data.get("udid"):
        out.setdefault(data["udid"], {k: v for k, v in data.items() if k != "devices"})
    return out


def save_device_setup(udid: str, **values) -> None:
    """Record `iphone-mcp setup` for one iPhone. It also becomes the default iPhone (top-level "udid"),
    used by servers without IPHONE_MCP_UDID, as in 1.0. Entries of other iPhones are kept."""
    with _locked(WDA_FILE):
        data = load(WDA_FILE)
        devices = data.setdefault("devices", {})
        if data.get("udid") and data["udid"] not in devices:  # keep a 1.0 setup as that iPhone's entry
            devices[data["udid"]] = {k: v for k, v in data.items() if k != "devices"}
        devices[udid] = {**devices.get(udid, {}), **values, "udid": udid}
        data.update(values, udid=udid)
        _write(WDA_FILE, data)
