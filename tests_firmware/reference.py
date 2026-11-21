"""Independent Python versions of what the node firmware must produce."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

REPO = Path(__file__).resolve().parent.parent
COMMON = REPO / "esphome" / "common"


def anon_id(addr: int, salt: str, day: int) -> str:
    """32-bit FNV-1a over salt, day (little-endian) and the 6 address bytes."""
    h = 2166136261
    data = salt.encode() + day.to_bytes(4, "little") + (addr & 0xFFFFFFFFFFFF).to_bytes(6, "little")
    for byte in data:
        h ^= byte
        h = (h * 16777619) & 0xFFFFFFFF
    return f"{h:08x}"


def _load_parser():
    # Load tracker.py by path: importing the package would pull in Home
    # Assistant, which these tests do not need.
    path = REPO / "custom_components" / "ble_footfall" / "tracker.py"
    spec = importlib.util.spec_from_file_location("ble_footfall_tracker", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.parse_sightings


parse_sightings = _load_parser()
