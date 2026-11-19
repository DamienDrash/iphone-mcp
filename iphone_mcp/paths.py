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


def update(path: Path, **values) -> None:
    data = load(path)
    data.update(values)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2))
