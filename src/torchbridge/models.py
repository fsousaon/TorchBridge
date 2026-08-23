from __future__ import annotations

from dataclasses import dataclass, field, replace
from threading import Lock
import time


@dataclass(frozen=True)
class Rect:
    left: int = 0
    top: int = 0
    width: int = 0
    height: int = 0

    @property
    def right(self) -> int:
        return self.left + self.width

    @property
    def bottom(self) -> int:
        return self.top + self.height

    @property
    def valid(self) -> bool:
        return self.width > 0 and self.height > 0

    def contains(self, x: int, y: int) -> bool:
        return self.left <= x < self.right and self.top <= y < self.bottom


@dataclass(frozen=True)
class ControllerState:
    connected: bool = False
    name: str = ""
    mapping: str = ""
    lx: float = 0.0
    ly: float = 0.0
    rx: float = 0.0
    ry: float = 0.0
    lt: float = 0.0
    rt: float = 0.0
    buttons: frozenset[str] = field(default_factory=frozenset)

    def pressed(self, button: str) -> bool:
        return button in self.buttons


@dataclass(frozen=True)
class OverlaySnapshot:
    enabled: bool = True
    game_found: bool = False
    game_active: bool = False
    game_rect: Rect = field(default_factory=Rect)
    controller_connected: bool = False
    controller_name: str = ""
    controller_mapping: str = ""
    mode: str = "direct"
    radial_active: bool = False
    radial_selection: int | None = None
    aim_x: int | None = None
    aim_y: int | None = None
    toast_text: str = ""
    toast_until: float = 0.0


class SharedOverlayState:
    def __init__(self) -> None:
        self._lock = Lock()
        self._snapshot = OverlaySnapshot()

    def get(self) -> OverlaySnapshot:
        with self._lock:
            return self._snapshot

    def update(self, **changes: object) -> None:
        with self._lock:
            self._snapshot = replace(self._snapshot, **changes)

    def toast(self, text: str, seconds: float = 2.2) -> None:
        self.update(toast_text=text, toast_until=time.monotonic() + seconds)

