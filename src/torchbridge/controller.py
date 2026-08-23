# Leitura do controle via SDL2 (pygame-ce).
# 
# Caminho 1 — SDL GameController: usa o banco de mapas do SDL para expor nomes lógicos
# (A/B/X/Y, lb, lx...) independentes da marca. É o caminho dos controles conhecidos.
# Caminho 2 — RAW/Joystick: sem mapa SDL (controle genérico), lê eixos/botões crus
# pelo pygame.joystick usando a calibração salva no perfil (indexada pelo GUID).
from __future__ import annotations

import os
import time
from typing import Any

# SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS=1: permite ler o controle mesmo sem foco na
# nossa janela (quem está em primeiro plano é o Torchlight, não o TorchBridge).
# HIDAPI_PS5/PS4: ativa os drivers HIDAPI do SDL (DualSense/DualShock via USB/Bluetooth),
# em vez de depender de XInput/DirectInput. Devem ser setadas antes do import do pygame.
os.environ.setdefault("SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS", "1")
os.environ.setdefault("SDL_JOYSTICK_HIDAPI_PS5", "1")
os.environ.setdefault("SDL_JOYSTICK_HIDAPI_PS4", "1")

import pygame
from pygame._sdl2 import controller as sdl_controller

from .mathutils import clamp, trigger_value
from .models import ControllerState


# Eixos lógicos do GameController; getattr com fallback numérico preserva a ordem do SDL.
AXES = {
    "lx": getattr(pygame, "CONTROLLER_AXIS_LEFTX", 0),
    "ly": getattr(pygame, "CONTROLLER_AXIS_LEFTY", 1),
    "rx": getattr(pygame, "CONTROLLER_AXIS_RIGHTX", 2),
    "ry": getattr(pygame, "CONTROLLER_AXIS_RIGHTY", 3),
    "lt": getattr(pygame, "CONTROLLER_AXIS_TRIGGERLEFT", 4),
    "rt": getattr(pygame, "CONTROLLER_AXIS_TRIGGERRIGHT", 5),
}

# Botões lógicos do GameController. Atenção: A/B/X/Y são posições físicas
# (inferior/direita/esquerda/superior), não as letras gravadas na carcaça do controle.
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


# Leitor do controle com troca a quente: reconecta sozinho ao plugar/desplugar o dispositivo.
class ControllerHub:
    """Hot-pluggable SDL controller reader with a raw joystick fallback."""

    def __init__(self) -> None:
        # Inicializa o display (exigido pelo joystick/eventos do SDL em várias plataformas).
        pygame.display.init()
        # Liga o subsistema de joystick do SDL.
        pygame.joystick.init()
        # Liga o GameController (banco de mapas SDL).
        sdl_controller.init()
        # _controller = dispositivo com mapa SDL; _joystick = dispositivo cru (fallback).
        self._controller: Any | None = None
        self._joystick: Any | None = None
        self._name = ""
        self._mode = ""
        self._last_scan = 0.0
        self._last_force_raw: bool | None = None
        self._last_raw_guid = ""

    # Encerra tudo limpo: libera o dispositivo e desliga os subsistemas SDL.
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

    # Solta o dispositivo atual sem desligar o SDL (para reconectar do zero na troca a quente).
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

    # O dispositivo ainda está fisicamente conectado?
    def _device_healthy(self) -> bool:
        try:
            if self._controller is not None:
                # No SDL, attached() informa se o controle ainda está presente (get_init() no caminho raw).
                return bool(self._controller.attached())
            if self._joystick is not None:
                return bool(self._joystick.get_init())
        except pygame.error:
            return False
        return False

    # Garante um dispositivo atual: reconecta quando necessário, com varredura limitada a cada 0.8 s.
    def _scan(self, raw_config: dict[str, Any]) -> None:
        # Lê a configuração atual do perfil (forçar caminho raw? qual GUID calibrado?).
        now = time.monotonic()
        force_raw = bool(raw_config.get("force_raw", False))
        raw_guid = str(raw_config.get("device_guid", ""))
        # A configuração desejada mudou → reconecta o dispositivo do zero.
        if force_raw != self._last_force_raw or raw_guid != self._last_raw_guid:
            self._drop_device()
            self._last_force_raw = force_raw
            self._last_raw_guid = raw_guid
        # Já temos dispositivo bom (ou acabamos de procurar): evita trabalho repetido a cada tick.
        if self._device_healthy() or now - self._last_scan < 0.8:
            return
        # Marca a varredura e recomeça: o dispositivo anterior saiu ou ficou obsoleto.
        self._last_scan = now
        self._drop_device()

        # Quantos dispositivos o SDL enxerga agora.
        count = pygame.joystick.get_count()
        # Tentativa 1: caminho SDL — algum dispositivo é reconhecido pelo banco de mapas?
        if not force_raw:
            for index in range(count):
                try:
                    # Este joystick tem mapa SDL → vira um GameController com nomes lógicos.
                    if sdl_controller.is_controller(index):
                        self._controller = sdl_controller.Controller(index)
                        # O controller SDL também é um Joystick: serve para nome e rumble.
                        joystick = self._controller.as_joystick()
                        self._name = joystick.get_name() or sdl_controller.name_forindex(index) or "Controle"
                        self._mode = "SDL"
                        return
                except pygame.error:
                    continue

        # Tentativa 2 (fallback): caminho RAW com o joystick cru.
        if count:
            candidates = list(range(count))
            if raw_guid:
                # Ordena para colocar o dispositivo calibrado no perfil (device_guid) em primeiro.
                candidates.sort(
                    key=lambda index: pygame.joystick.Joystick(index).get_guid() != raw_guid
                )
            for index in candidates:
                try:
                    candidate = pygame.joystick.Joystick(index)
                    # Com GUID calibrado, outros dispositivos são ignorados (não tomam o lugar do calibrado).
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
    # Eixos do GameController vêm como int16 (-32768..32767); normaliza para -1..1.
    def _normalized_axis(controller: Any, axis: int) -> float:
        raw = int(controller.get_axis(axis))
        # Divisões assimétricas: int16 vai até -32768, mas o positivo máximo real é 32767.
        divisor = 32768.0 if raw < 0 else 32767.0
        return clamp(raw / divisor, -1.0, 1.0)

    # Leitura completa pelo caminho SDL: botões ∈ nomes lógicos; gatilhos 0..1.
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

    # Lê um eixo cru pelo índice do descritor, aplicando inversão se o perfil pedir.
    def _raw_axis(self, descriptor: dict[str, Any]) -> float:
        assert self._joystick is not None
        index = int(descriptor.get("index", -1))
        # Índice fora da faixa do dispositivo (descritor inválido): trata como neutro.
        if not 0 <= index < self._joystick.get_numaxes():
            return 0.0
        value = float(self._joystick.get_axis(index))
        if descriptor.get("invert", False):
            value = -value
        return clamp(value, -1.0, 1.0)

    # Descritor cru → booleano (botão, hat ou eixo).
    def _raw_control(self, descriptor: dict[str, Any]) -> bool:
        assert self._joystick is not None
        kind = descriptor.get("type", "button")
        index = int(descriptor.get("index", -1))
        # Botão digital: índice dentro da faixa e estado atual.
        if kind == "button":
            return 0 <= index < self._joystick.get_numbuttons() and bool(
                self._joystick.get_button(index)
            )
        # Direcional (hat): exige (x, y) igual ao esperado; valor 0 no descritor aceita qualquer lado.
        if kind == "hat" and 0 <= index < self._joystick.get_numhats():
            actual = self._joystick.get_hat(index)
            expected = descriptor.get("value", [0, 0])
            need_x, need_y = int(expected[0]), int(expected[1])
            return (need_x == 0 or actual[0] == need_x) and (
                need_y == 0 or actual[1] == need_y
            )
        # Eixo como botão (ex.: gatilho): normaliza repouso/ativo e compara com o limiar.
        if kind == "axis" and 0 <= index < self._joystick.get_numaxes():
            normalized = trigger_value(
                float(self._joystick.get_axis(index)),
                float(descriptor.get("rest", -1.0)),
                float(descriptor.get("active", 1.0)),
            )
            return normalized >= float(descriptor.get("threshold", 0.5))
        return False

    # Valor 0..1 de um gatilho cru: eixo normalizado ou botão digital.
    def _raw_trigger(self, descriptor: dict[str, Any]) -> float:
        assert self._joystick is not None
        kind = descriptor.get("type", "axis")
        index = int(descriptor.get("index", -1))
        # Botão digital usado como gatilho: 1.0 pressionado, 0.0 solto.
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

    # Monta o ControllerState a partir dos descritores crus do perfil.
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

    # Chamado a cada tick: processa eventos SDL (obrigatório para o estado atualizar) e devolve o estado.
    def poll(self, raw_config: dict[str, Any]) -> ControllerState:
        # Processa a fila de eventos do SDL sem bloquear: é o que faz conexões novas aparecerem.
        pygame.event.pump()
        force_raw = bool(raw_config.get("force_raw", False))
        self._scan(raw_config)
        try:
            if self._controller is not None:
                return self._poll_sdl()
            if self._joystick is not None:
                return self._poll_raw(raw_config)
        # Dispositivo sumiu/mudou no meio da leitura: solta e reporta desconectado neste tick.
        except (pygame.error, OSError, IndexError, TypeError, ValueError):
            self._drop_device()
        return ControllerState()

    # Vibração de feedback (troca de setor, conexão): low/high em intensidade 0..1 e duração em ms.
    def rumble(self, low: float = 0.15, high: float = 0.35, duration_ms: int = 120) -> bool:
        try:
            if self._controller is not None:
                # Rumble disponível no caminho SDL e no raw (quando o hardware suporta).
                return bool(self._controller.rumble(low, high, duration_ms))
            if self._joystick is not None:
                return bool(self._joystick.rumble(low, high, duration_ms))
        except pygame.error:
            pass
        return False
