"""Where iphone-mcp keeps its config, build output and logs (never inside the repo)."""

from __future__ import annotations

import json
import os
import sys
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

STATE_FILE = CONFIG_DIR / "state.json"  # runtime state: last Wi-Fi IP, xcodebuild pid
WDA_FILE = CONFIG_DIR / "wda.json"  # result of `iphone-mcp setup`
WDA_SRC = DATA_DIR / "WebDriverAgent"
WDA_DERIVED = DATA_DIR / "DerivedData"
WDA_LOG = LOG_DIR / "wda.log"
BUILD_LOG = LOG_DIR / "build.log"


def load(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


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


def register_client(pid: int) -> None:
    """Remember a running MCP server process (several agent windows can share one iPhone)."""
    clients = [p for p in load(STATE_FILE).get("clients", []) if p != pid and pid_alive(p)]
    update(STATE_FILE, clients=[*clients, pid])


def unregister_client(pid: int) -> list[int]:
    """Forget a server process; returns the other servers that are still running."""
    others = [p for p in load(STATE_FILE).get("clients", []) if p != pid and pid_alive(p)]
    update(STATE_FILE, clients=others)
    return others


def update(path: Path, **values) -> None:
    data = load(path)
    data.update(values)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2))
