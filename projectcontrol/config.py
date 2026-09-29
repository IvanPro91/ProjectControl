from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "ProjectControl"


def _data_dir() -> Path:
    override = os.environ.get("PROJECTCONTROL_HOME")
    if override:
        base = Path(override).expanduser()
    elif sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming"))
        base = base / APP_NAME
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / APP_NAME
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")) / APP_NAME
    base.mkdir(parents=True, exist_ok=True)
    return base


def resource_dir() -> Path:
    return Path(__file__).resolve().parent


DATA_DIR = _data_dir()
DB_PATH = DATA_DIR / "projects.json"
STATIC_DIR = resource_dir() / "static"

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8770
DEFAULT_LOG_LINES = 2000
MIN_LOG_LINES = 100
MAX_LOG_LINES = 200000
