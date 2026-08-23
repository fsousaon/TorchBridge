from __future__ import annotations

import os
import time
from typing import Any

os.environ.setdefault("SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS", "1")
os.environ.setdefault("SDL_JOYSTICK_HIDAPI_PS5", "1")
os.environ.setdefault("SDL_JOYSTICK_HIDAPI_PS4", "1")

import pygame
from pygame._sdl2 import controller as sdl_controller

from .mathutils import clamp, trigger_value
from .models import ControllerState


AXES = {
    "lx": getattr(pygame, "CONTROLLER_AXIS_LEFTX", 0),
    "ly": getattr(pygame, "CONTROLLER_AXIS_LEFTY", 1),
    "rx": getattr(pygame, "CONTROLLER_AXIS_RIGHTX", 2),
    "ry": getattr(pygame, "CONTROLLER_AXIS_RIGHTY", 3),
    "lt": getattr(pygame, "CONTROLLER_AXIS_TRIGGERLEFT", 4),
    "rt": getattr(pygame, "CONTROLLER_AXIS_TRIGGERRIGHT", 5),
}

BUTTONS = {
    "a": getattr(pygame, "CONTROLLER_BUTTON_A", 0),
    "b": getattr(pygame, "CONTROLLER_BUTTON_B", 1),
    "x": getattr(pygame, "CONTROLLER_BUTTON_X", 2),
    "y": getattr(pygame, "CONTROLLER_BUTTON_Y", 3),
    "back": getattr(pygame, "CONTROLLER_BUTTON_BACK", 4),
    "guide": getattr(pygame, "CONTROLLER_BUTTON_GUIDE", 5),
    "start": getattr(pygame, "CONTROLLER_BUTTON_START", 6),
    "l3": getattr(pygame, "CONTROLLER_BUTTON_LEFTSTICK", 7),
    "r3": getattr(pygame, "CONTROLLER_BUTTON_RIGHTSTICK", 8),
    "lb": getattr(pygame, "CONTROLLER_BUTTON_LEFTSHOULDER", 9),
    "rb": getattr(pygame, "CONTROLLER_BUTTON_RIGHTSHOULDER", 10),
    "dpad_up": getattr(pygame, "CONTROLLER_BUTTON_DPAD_UP", 11),
    "dpad_down": getattr(pygame, "CONTROLLER_BUTTON_DPAD_DOWN", 12),
    "dpad_left": getattr(pygame, "CONTROLLER_BUTTON_DPAD_LEFT", 13),
    "dpad_right": getattr(pygame, "CONTROLLER_BUTTON_DPAD_RIGHT", 14),
}


class ControllerHub:
    """Hot-pluggable SDL controller reader with a raw joystick fallback."""

    def __init__(self) -> None:
        pygame.display.init()
        pygame.joystick.init()
        sdl_controller.init()
        self._controller: Any | None = None
        self._joystick: Any | None = None
        self._name = ""
        self._mode = ""
        self._last_scan = 0.0
        self._last_force_raw: bool | None = None
        self._last_raw_guid = ""

    def close(self) -> None:
        try:
            if self._controller is not None:
                self._controller.quit()
            if self._joystick is not None:
                self._joystick.quit()
        finally:
            self._controller = None
            self._joystick = None
            sdl_controller.quit()
            pygame.joystick.quit()
            pygame.display.quit()

    def _drop_device(self) -> None:
        try:
            if self._controller is not None:
                self._controller.quit()
            if self._joystick is not None:
                self._joystick.quit()
        except pygame.error:
            pass
        self._controller = None
        self._joystick = None
        self._name = ""
        self._mode = ""

    def _device_healthy(self) -> bool:
        try:
            if self._controller is not None:
                return bool(self._controller.attached())
            if self._joystick is not None:
                return bool(self._joystick.get_init())
        except pygame.error:
            return False
        return False

    def _scan(self, raw_config: dict[str, Any]) -> None:
        now = time.monotonic()
        force_raw = bool(raw_config.get("force_raw", False))
        raw_guid = str(raw_config.get("device_guid", ""))
        if force_raw != self._last_force_raw or raw_guid != self._last_raw_guid:
            self._drop_device()
            self._last_force_raw = force_raw
            self._last_raw_guid = raw_guid
        if self._device_healthy() or now - self._last_scan < 0.8:
            return
        self._last_scan = now
        self._drop_device()

        count = pygame.joystick.get_count()
        if not force_raw:
            for index in range(count):
                try:
                    if sdl_controller.is_controller(index):
                        self._controller = sdl_controller.Controller(index)
                        joystick = self._controller.as_joystick()
                        self._name = joystick.get_name() or sdl_controller.name_forindex(index) or "Controle"
                        self._mode = "SDL"
                        return
                except pygame.error:
                    continue

        if count:
            candidates = list(range(count))
            if raw_guid:
                candidates.sort(
                    key=lambda index: pygame.joystick.Joystick(index).get_guid() != raw_guid
                )
            for index in candidates:
                try:
                    candidate = pygame.joystick.Joystick(index)
                    if raw_guid and candidate.get_guid() != raw_guid:
                        candidate.quit()
                        continue
                    self._joystick = candidate
                    self._name = self._joystick.get_name() or "Controle genérico"
                    self._mode = "RAW"
                    return
                except pygame.error:
                    continue
            self._drop_device()

    @staticmethod
    def _normalized_axis(controller: Any, axis: int) -> float:
        raw = int(controller.get_axis(axis))
        divisor = 32768.0 if raw < 0 else 32767.0
        return clamp(raw / divisor, -1.0, 1.0)

    def _poll_sdl(self) -> ControllerState:
        assert self._controller is not None
        pressed = frozenset(
            name for name, constant in BUTTONS.items() if self._controller.get_button(constant)
        )
        return ControllerState(
            connected=True,
            name=self._name,
            mapping="SDL normalizado",
            lx=self._normalized_axis(self._controller, AXES["lx"]),
            ly=self._normalized_axis(self._controller, AXES["ly"]),
            rx=self._normalized_axis(self._controller, AXES["rx"]),
            ry=self._normalized_axis(self._controller, AXES["ry"]),
            lt=clamp(self._controller.get_axis(AXES["lt"]) / 32768.0, 0.0, 1.0),
            rt=clamp(self._controller.get_axis(AXES["rt"]) / 32768.0, 0.0, 1.0),
            buttons=pressed,
        )

    def _raw_axis(self, descriptor: dict[str, Any]) -> float:
        assert self._joystick is not None
        index = int(descriptor.get("index", -1))
        if not 0 <= index < self._joystick.get_numaxes():
            return 0.0
        value = float(self._joystick.get_axis(index))
        if descriptor.get("invert", False):
            value = -value
        return clamp(value, -1.0, 1.0)

    def _raw_control(self, descriptor: dict[str, Any]) -> bool:
        assert self._joystick is not None
        kind = descriptor.get("type", "button")
        index = int(descriptor.get("index", -1))
        if kind == "button":
            return 0 <= index < self._joystick.get_numbuttons() and bool(
                self._joystick.get_button(index)
            )
        if kind == "hat" and 0 <= index < self._joystick.get_numhats():
            actual = self._joystick.get_hat(index)
            expected = descriptor.get("value", [0, 0])
            need_x, need_y = int(expected[0]), int(expected[1])
            return (need_x == 0 or actual[0] == need_x) and (
                need_y == 0 or actual[1] == need_y
            )
        if kind == "axis" and 0 <= index < self._joystick.get_numaxes():
            normalized = trigger_value(
                float(self._joystick.get_axis(index)),
                float(descriptor.get("rest", -1.0)),
                float(descriptor.get("active", 1.0)),
            )
            return normalized >= float(descriptor.get("threshold", 0.5))
        return False

    def _raw_trigger(self, descriptor: dict[str, Any]) -> float:
        assert self._joystick is not None
        kind = descriptor.get("type", "axis")
        index = int(descriptor.get("index", -1))
        if kind == "button":
            if 0 <= index < self._joystick.get_numbuttons():
                return 1.0 if self._joystick.get_button(index) else 0.0
            return 0.0
        if kind == "axis" and 0 <= index < self._joystick.get_numaxes():
            return trigger_value(
                float(self._joystick.get_axis(index)),
                float(descriptor.get("rest", -1.0)),
                float(descriptor.get("active", 1.0)),
            )
        return 0.0

    def _poll_raw(self, raw: dict[str, Any]) -> ControllerState:
        assert self._joystick is not None
        axes = raw.get("axes", {})
        triggers = raw.get("triggers", {})
        button_map = raw.get("buttons", {})
        pressed = frozenset(
            name
            for name, descriptor in button_map.items()
            if isinstance(descriptor, dict) and self._raw_control(descriptor)
        )
        return ControllerState(
            connected=True,
            name=self._name,
            mapping="genérico calibrado" if raw.get("force_raw") else "genérico padrão",
            lx=self._raw_axis(axes.get("left_x", {})),
            ly=self._raw_axis(axes.get("left_y", {})),
            rx=self._raw_axis(axes.get("right_x", {})),
            ry=self._raw_axis(axes.get("right_y", {})),
            lt=self._raw_trigger(triggers.get("left", {})),
            rt=self._raw_trigger(triggers.get("right", {})),
            buttons=pressed,
        )

    def poll(self, raw_config: dict[str, Any]) -> ControllerState:
        pygame.event.pump()
        force_raw = bool(raw_config.get("force_raw", False))
        self._scan(raw_config)
        try:
            if self._controller is not None:
                return self._poll_sdl()
            if self._joystick is not None:
                return self._poll_raw(raw_config)
        except (pygame.error, OSError, IndexError, TypeError, ValueError):
            self._drop_device()
        return ControllerState()

    def rumble(self, low: float = 0.15, high: float = 0.35, duration_ms: int = 120) -> bool:
        try:
            if self._controller is not None:
                return bool(self._controller.rumble(low, high, duration_ms))
            if self._joystick is not None:
                return bool(self._joystick.rumble(low, high, duration_ms))
        except pygame.error:
            pass
        return False
