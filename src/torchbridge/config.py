from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
from threading import RLock
from typing import Any

from .mathutils import clamp


DEFAULT_CONFIG: dict[str, Any] = {
    "version": 1,
    "target": {
        "process_names": ["Torchlight.exe"],
        "window_titles": ["Torchlight"],
    },
    "input": {
        "poll_hz": 120,
        "deadzone": 0.18,
        "response_curve": 1.6,
        "trigger_threshold": 0.32,
    },
    "movement": {
        "initial_mode": "direct",
        "anchor_x": 0.50,
        "anchor_y": 0.47,
        "radius_x_percent": 0.16,
        "radius_y_percent": 0.13,
    },
    "cursor": {
        "speed_pixels_per_second": 1450,
    },
    "overlay": {
        "enabled": True,
        "scale": 1.0,
        "show_aim_marker": True,
        "show_mode_badge": True,
    },
    "bindings": {
        "a": "1",
        "b": "2",
        "x": "3",
        "y": "4",
        "dpad_up": "5",
        "dpad_right": "6",
        "dpad_down": "7",
        "dpad_left": "8",
        "r3": "TAB",
        "start": "ESC",
        "rb_hold": "SHIFT",
        "l3_hold": "ALT",
        "radial_slots": ["i", "c", "s", "p", "j", "q"],
    },
    "raw_controller": {
        "force_raw": False,
        "axes": {
            "left_x": {"index": 0, "invert": False},
            "left_y": {"index": 1, "invert": False},
            "right_x": {"index": 2, "invert": False},
            "right_y": {"index": 3, "invert": False},
        },
        "triggers": {
            "left": {"type": "axis", "index": 4, "rest": -1.0, "active": 1.0},
            "right": {"type": "axis", "index": 5, "rest": -1.0, "active": 1.0},
        },
        "buttons": {
            "a": {"type": "button", "index": 0},
            "b": {"type": "button", "index": 1},
            "x": {"type": "button", "index": 2},
            "y": {"type": "button", "index": 3},
            "lb": {"type": "button", "index": 4},
            "rb": {"type": "button", "index": 5},
            "back": {"type": "button", "index": 6},
            "start": {"type": "button", "index": 7},
            "l3": {"type": "button", "index": 8},
            "r3": {"type": "button", "index": 9},
            "dpad_up": {"type": "hat", "index": 0, "value": [0, 1]},
            "dpad_right": {"type": "hat", "index": 0, "value": [1, 0]},
            "dpad_down": {"type": "hat", "index": 0, "value": [0, -1]},
            "dpad_left": {"type": "hat", "index": 0, "value": [-1, 0]},
        },
    },
}


def _deep_merge(base: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(base)
    for key, value in incoming.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def user_config_dir() -> Path:
    if os.name == "nt":
        root = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    else:
        root = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return root / "TorchBridge"


class ConfigManager:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or user_config_dir() / "perfil.json"
        self._lock = RLock()
        self._data = deepcopy(DEFAULT_CONFIG)
        self._mtime_ns = 0
        self.ensure_exists()
        self.reload(force=True)

    def ensure_exists(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._write(DEFAULT_CONFIG)

    def _write(self, data: dict[str, Any]) -> None:
        temporary = self.path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.path)

    def _validate(self, data: dict[str, Any]) -> dict[str, Any]:
        for section in (
            "target",
            "input",
            "movement",
            "cursor",
            "overlay",
            "bindings",
            "raw_controller",
        ):
            if not isinstance(data.get(section), dict):
                data[section] = deepcopy(DEFAULT_CONFIG[section])
        for target_key in ("process_names", "window_titles"):
            values = data["target"].get(target_key)
            if not isinstance(values, list) or not all(isinstance(item, str) for item in values):
                data["target"][target_key] = deepcopy(DEFAULT_CONFIG["target"][target_key])
        data["input"]["poll_hz"] = int(clamp(float(data["input"]["poll_hz"]), 30, 240))
        data["input"]["deadzone"] = clamp(float(data["input"]["deadzone"]), 0.02, 0.60)
        data["input"]["response_curve"] = clamp(float(data["input"]["response_curve"]), 0.2, 4.0)
        data["input"]["trigger_threshold"] = clamp(
            float(data["input"]["trigger_threshold"]), 0.05, 0.95
        )
        data["movement"]["anchor_x"] = clamp(float(data["movement"]["anchor_x"]), 0.05, 0.95)
        data["movement"]["anchor_y"] = clamp(float(data["movement"]["anchor_y"]), 0.05, 0.95)
        data["movement"]["radius_x_percent"] = clamp(
            float(data["movement"]["radius_x_percent"]), 0.03, 0.45
        )
        data["movement"]["radius_y_percent"] = clamp(
            float(data["movement"]["radius_y_percent"]), 0.03, 0.45
        )
        if data["movement"].get("initial_mode") not in {"direct", "cursor"}:
            data["movement"]["initial_mode"] = "direct"
        data["cursor"]["speed_pixels_per_second"] = int(
            clamp(float(data["cursor"]["speed_pixels_per_second"]), 150, 4000)
        )
        data["overlay"]["scale"] = clamp(float(data["overlay"]["scale"]), 0.6, 2.0)
        radial_slots = data["bindings"].get("radial_slots")
        if not isinstance(radial_slots, list) or not all(isinstance(item, str) for item in radial_slots):
            data["bindings"]["radial_slots"] = deepcopy(
                DEFAULT_CONFIG["bindings"]["radial_slots"]
            )
        return data

    def reload(self, force: bool = False) -> bool:
        try:
            mtime_ns = self.path.stat().st_mtime_ns
            if not force and mtime_ns == self._mtime_ns:
                return False
            incoming = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(incoming, dict):
                raise ValueError("A raiz do perfil precisa ser um objeto JSON.")
            merged = self._validate(_deep_merge(DEFAULT_CONFIG, incoming))
        except (OSError, TypeError, KeyError, ValueError, json.JSONDecodeError):
            return False
        with self._lock:
            self._data = merged
            self._mtime_ns = mtime_ns
        return True

    def get(self) -> dict[str, Any]:
        with self._lock:
            return deepcopy(self._data)

    def update_anchor(self, x: float, y: float) -> None:
        with self._lock:
            data = deepcopy(self._data)
            data["movement"]["anchor_x"] = round(clamp(x, 0.05, 0.95), 4)
            data["movement"]["anchor_y"] = round(clamp(y, 0.05, 0.95), 4)
            self._write(data)
        self.reload(force=True)

    def update_raw_mapping(self, mapping: dict[str, Any]) -> None:
        with self._lock:
            data = deepcopy(self._data)
            data["raw_controller"] = mapping
            self._write(data)
        self.reload(force=True)
