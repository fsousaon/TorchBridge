from __future__ import annotations

import logging
import threading
import time
from typing import Any

from .config import ConfigManager
from .controller import ControllerHub
from .mathutils import clamp, cursor_delta, radial_deadzone, radial_slot
from .models import ControllerState, Rect, SharedOverlayState
from .win32 import (
    InputInjector,
    WindowLocator,
    begin_high_resolution_timer,
    end_high_resolution_timer,
)


log = logging.getLogger(__name__)


class BridgeEngine(threading.Thread):
    """120 Hz controller-to-keyboard/mouse translation loop."""

    def __init__(self, config: ConfigManager, shared: SharedOverlayState) -> None:
        super().__init__(name="TorchBridgeInput", daemon=True)
        self.config = config
        self.shared = shared
        initial = config.get()
        target = initial["target"]
        self.locator = WindowLocator(target["process_names"], target["window_titles"])
        self.injector = InputInjector()
        self._stop_event = threading.Event()
        self._enabled_lock = threading.Lock()
        self._enabled = True
        self._mode = initial["movement"]["initial_mode"]
        self._previous = ControllerState()
        self._held_keys: set[str] = set()
        self._held_mouse: set[str] = set()
        self._last_reload = 0.0
        self._last_controller_name = ""
        self._game_was_found = False
        self._radial_selection: int | None = None
        self._center_combo_seen = False
        self._center_combo_started: float | None = None
        self._center_combo_triggered = False

    def stop(self) -> None:
        self._stop_event.set()

    def set_enabled(self, enabled: bool) -> None:
        with self._enabled_lock:
            self._enabled = enabled
        if not enabled:
            self.shared.toast("TorchBridge pausado")
        else:
            self.shared.toast("TorchBridge ativado")

    def is_enabled(self) -> bool:
        with self._enabled_lock:
            return self._enabled

    def _set_key(self, name: str, desired: bool) -> None:
        if not isinstance(name, str) or not name.strip():
            return
        normalized = name.strip().upper()
        try:
            if desired and normalized not in self._held_keys:
                if self.injector.key(normalized, True):
                    self._held_keys.add(normalized)
            elif not desired and normalized in self._held_keys:
                self.injector.key(normalized, False)
                self._held_keys.discard(normalized)
        except ValueError as exc:
            log.warning("Binding de retenção ignorado: %s", exc)

    def _set_mouse(self, button: str, desired: bool) -> None:
        if desired and button not in self._held_mouse:
            if self.injector.mouse_button(button, True):
                self._held_mouse.add(button)
        elif not desired and button in self._held_mouse:
            self.injector.mouse_button(button, False)
            self._held_mouse.discard(button)

    def _release_all(self) -> None:
        for key in tuple(self._held_keys):
            self.injector.key(key, False)
        for button in tuple(self._held_mouse):
            self.injector.mouse_button(button, False)
        self._held_keys.clear()
        self._held_mouse.clear()
        self.shared.update(
            radial_active=False,
            radial_selection=None,
            aim_x=None,
            aim_y=None,
        )

    def _tap_binding(self, value: Any) -> None:
        if not isinstance(value, str) or not value.strip():
            return
        normalized = value.strip().upper()
        temporarily_released: list[str] = []
        modifiers = ["ALT"]
        if normalized in {"TAB", "ESC", "ESCAPE"}:
            modifiers.append("SHIFT")
        try:
            for modifier in modifiers:
                if modifier in self._held_keys:
                    self.injector.key(modifier, False)
                    self._held_keys.discard(modifier)
                    temporarily_released.append(modifier)
            self.injector.tap(normalized)
        except ValueError as exc:
            log.warning("Binding ignorado: %s", exc)
        finally:
            for modifier in temporarily_released:
                if self.injector.key(modifier, True):
                    self._held_keys.add(modifier)

    def _handle_center_buttons(
        self,
        state: ControllerState,
        rect: Rect,
        bindings: dict[str, Any],
        now: float,
    ) -> None:
        back = state.pressed("back")
        start = state.pressed("start")
        both = back and start

        if both:
            if not self._center_combo_seen:
                self._center_combo_started = now
            self._center_combo_seen = True
            if (
                not self._center_combo_triggered
                and self._center_combo_started is not None
                and now - self._center_combo_started >= 0.8
            ):
                x, y = self.injector.cursor_position()
                if rect.contains(x, y):
                    self.config.update_anchor(
                        (x - rect.left) / rect.width,
                        (y - rect.top) / rect.height,
                    )
                    self.shared.toast("Centro do personagem calibrado", 2.8)
                else:
                    self.shared.toast("Coloque o cursor sobre o personagem", 2.8)
                self._center_combo_triggered = True
            return

        if not self._center_combo_seen:
            if self._previous.pressed("back") and not back:
                self._mode = "cursor" if self._mode == "direct" else "direct"
                label = "CURSOR / MENUS" if self._mode == "cursor" else "MOVIMENTO DIRETO"
                self.shared.toast(f"Modo: {label}")
            if self._previous.pressed("start") and not start:
                self._tap_binding(bindings.get("start", "ESC"))

        if not back and not start:
            self._center_combo_seen = False
            self._center_combo_started = None
            self._center_combo_triggered = False

    def _handle_discrete_bindings(
        self,
        state: ControllerState,
        bindings: dict[str, Any],
    ) -> None:
        for button in (
            "a",
            "b",
            "x",
            "y",
            "dpad_up",
            "dpad_right",
            "dpad_down",
            "dpad_left",
            "r3",
        ):
            if state.pressed(button) and not self._previous.pressed(button):
                self._tap_binding(bindings.get(button))

    def _handle_radial(
        self,
        hub: ControllerHub,
        state: ControllerState,
        bindings: dict[str, Any],
    ) -> None:
        active = state.pressed("lb")
        selection = radial_slot(state.rx, state.ry) if active else None
        if active and selection is not None and selection != self._radial_selection:
            self._radial_selection = selection
            hub.rumble(0.04, 0.10, 35)

        if self._previous.pressed("lb") and not active:
            slots = bindings.get("radial_slots", [])
            if self._radial_selection is not None and self._radial_selection <= len(slots):
                self._tap_binding(slots[self._radial_selection - 1])
            self._radial_selection = None

        self.shared.update(
            radial_active=active,
            radial_selection=self._radial_selection if active else None,
        )

    def _move_pointer(
        self,
        state: ControllerState,
        rect: Rect,
        cfg: dict[str, Any],
        dt: float,
    ) -> bool:
        input_cfg = cfg["input"]
        movement = cfg["movement"]
        deadzone = float(input_cfg["deadzone"])
        curve = float(input_cfg["response_curve"])
        lx, ly, lmag = radial_deadzone(state.lx, state.ly, deadzone, curve)
        rx, ry, rmag = radial_deadzone(state.rx, state.ry, deadzone, curve)
        radial_active = state.pressed("lb")
        auto_move = False
        aim_local: tuple[int, int] | None = None

        if self._mode == "direct" and lmag > 0 and rmag == 0 and not radial_active:
            anchor_x = rect.left + rect.width * float(movement["anchor_x"])
            anchor_y = rect.top + rect.height * float(movement["anchor_y"])
            radius_x = rect.width * float(movement["radius_x_percent"])
            radius_y = rect.height * float(movement["radius_y_percent"])
            target_x = round(anchor_x + (lx / max(lmag, 1e-6)) * radius_x * (0.55 + 0.45 * lmag))
            target_y = round(anchor_y + (ly / max(lmag, 1e-6)) * radius_y * (0.55 + 0.45 * lmag))
            target_x = int(clamp(target_x, rect.left + 2, rect.right - 2))
            target_y = int(clamp(target_y, rect.top + 2, rect.bottom - 2))
            self.injector.move(target_x, target_y)
            aim_local = (target_x - rect.left, target_y - rect.top)
            auto_move = True
        else:
            cursor_x = cursor_y = cursor_mag = 0.0
            if not radial_active and rmag > 0:
                cursor_x, cursor_y, cursor_mag = rx, ry, rmag
            elif self._mode == "cursor" and lmag > 0:
                cursor_x, cursor_y, cursor_mag = lx, ly, lmag

            if cursor_mag > 0:
                current_x, current_y = self.injector.cursor_position()
                speed = float(cfg["cursor"]["speed_pixels_per_second"])
                dx, dy = cursor_delta(cursor_x, cursor_y, speed, min(dt, 0.05))
                target_x = int(clamp(round(current_x + dx), rect.left + 2, rect.right - 2))
                target_y = int(clamp(round(current_y + dy), rect.top + 2, rect.bottom - 2))
                self.injector.move(target_x, target_y)
                aim_local = (target_x - rect.left, target_y - rect.top)

        if cfg["overlay"].get("show_aim_marker", True) and aim_local:
            self.shared.update(aim_x=aim_local[0], aim_y=aim_local[1])
        else:
            self.shared.update(aim_x=None, aim_y=None)
        return auto_move

    def _process_active(
        self,
        hub: ControllerHub,
        state: ControllerState,
        rect: Rect,
        cfg: dict[str, Any],
        now: float,
        dt: float,
    ) -> None:
        bindings = cfg["bindings"]
        self._handle_center_buttons(state, rect, bindings, now)
        self._handle_discrete_bindings(state, bindings)
        self._handle_radial(hub, state, bindings)

        center_combo = state.pressed("back") and state.pressed("start")
        self._set_key(bindings.get("rb_hold", "SHIFT"), state.pressed("rb") and not center_combo)
        self._set_key(bindings.get("l3_hold", "ALT"), state.pressed("l3") and not center_combo)

        auto_move = self._move_pointer(state, rect, cfg, dt)
        threshold = float(cfg["input"]["trigger_threshold"])
        self._set_mouse("left", auto_move or state.rt >= threshold)
        self._set_mouse("right", state.lt >= threshold)

    def run(self) -> None:
        begin_high_resolution_timer()
        hub: ControllerHub | None = None
        last_tick = time.perf_counter()
        try:
            hub = ControllerHub()
            while not self._stop_event.is_set():
                tick_start = time.perf_counter()
                dt = tick_start - last_tick
                last_tick = tick_start
                now = time.monotonic()

                if now - self._last_reload >= 1.0:
                    if self.config.reload():
                        self.shared.toast("Perfil recarregado")
                    self._last_reload = now
                cfg = self.config.get()
                state = hub.poll(cfg["raw_controller"])
                hwnd = self.locator.find()
                rect = self.locator.client_rect(hwnd) if hwnd else Rect()
                game_found = bool(hwnd and rect.valid)
                game_active = bool(game_found and self.locator.is_foreground(hwnd))
                enabled = self.is_enabled()

                if state.connected and state.name != self._last_controller_name:
                    self._last_controller_name = state.name
                    self.shared.toast(f"Controle conectado: {state.name}", 3.0)
                    hub.rumble()
                    self._previous = state
                elif not state.connected and self._last_controller_name:
                    self.shared.toast("Controle desconectado", 2.5)
                    self._last_controller_name = ""

                if game_found and not self._game_was_found:
                    self.shared.toast("Torchlight detectado", 2.5)
                self._game_was_found = game_found

                self.shared.update(
                    enabled=enabled and bool(cfg["overlay"].get("enabled", True)),
                    game_found=game_found,
                    game_active=game_active,
                    game_rect=rect,
                    controller_connected=state.connected,
                    controller_name=state.name,
                    controller_mapping=state.mapping,
                    mode=self._mode,
                )

                if enabled and game_active and state.connected:
                    self._process_active(hub, state, rect, cfg, now, dt)
                else:
                    self._release_all()

                self._previous = state
                period = 1.0 / float(cfg["input"]["poll_hz"])
                remaining = period - (time.perf_counter() - tick_start)
                if remaining > 0:
                    self._stop_event.wait(remaining)
        except Exception:
            log.exception("Falha fatal no loop de entrada")
            self.shared.toast("Erro no motor; consulte torchbridge.log", 8.0)
        finally:
            self._release_all()
            if hub is not None:
                hub.close()
            end_high_resolution_timer()
