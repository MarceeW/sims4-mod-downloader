"""Optional user configuration loaded from ``config.json``.

The file holds the entry (download) URLs and, optionally, default values for the
other run settings. It is read at startup; nothing is hard-coded in the app.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def config_path() -> Path:
    """``config.json`` next to the executable when frozen (PyInstaller), else in
    the current working directory."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent / "config.json"
    return Path("config.json")


def load_config() -> dict:
    """Read ``config.json`` into a dict; return ``{}`` if missing/invalid."""
    try:
        data = json.loads(config_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def entry_urls(config: dict) -> list[str]:
    """Extract the configured entry URLs (accepts ``entry_urls`` or ``urls``;
    a single string is allowed too)."""
    urls = config.get("entry_urls") or config.get("urls") or []
    if isinstance(urls, str):
        urls = [urls]
    return [u for u in (str(x).strip() for x in urls) if u]
