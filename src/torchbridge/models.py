# Modelos de dados compartilhados entre a thread do motor (entrada) e a UI Qt (overlay).
from __future__ import annotations

from dataclasses import dataclass, field, replace
from threading import Lock
import time


@dataclass(frozen=True)
# Área retangular útil (cliente) da janela do jogo, em coordenadas absolutas de tela.
class Rect:
    left: int = 0
    top: int = 0
    width: int = 0
    height: int = 0

    @property
    # Borda direita (exclusiva), derivada de left + width.
    def right(self) -> int:
        return self.left + self.width

    @property
    # Borda inferior (exclusiva), derivada de top + height.
    def bottom(self) -> int:
        return self.top + self.height

    @property
    # A janela só é utilizável com área positiva; retângulo vazio = 'sem jogo'.
    def valid(self) -> bool:
        return self.width > 0 and self.height > 0

    # Teste de ponto dentro da área [left, right) × [top, bottom).
    def contains(self, x: int, y: int) -> bool:
        return self.left <= x < self.right and self.top <= y < self.bottom


@dataclass(frozen=True)
# Fotografia imutável do controle em um tick: eixos (-1..1), gatilhos (0..1) e botões.
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
    # Botões pressionados como frozenset de nomes lógicos ('a', 'lb', 'dpad_up'...) — imutável e barato de comparar.
    buttons: frozenset[str] = field(default_factory=frozenset)

    # Atalho: o botão lógico está pressionado neste tick?
    def pressed(self, button: str) -> bool:
        return button in self.buttons


@dataclass(frozen=True)
# Estado visual imutável que o motor publica para o overlay Qt desenhar.
class OverlaySnapshot:
    enabled: bool = True
    game_found: bool = False
    game_active: bool = False
    game_rect: Rect = field(default_factory=Rect)
    controller_connected: bool = False
    controller_name: str = ""
    controller_mapping: str = ""
    mode: str = "direct"
    # Cena atual identificada pelo SceneDetector ('gameplay', 'menu' ou 'unknown').
    scene_kind: str = "unknown"
    radial_active: bool = False
    radial_selection: int | None = None
    aim_x: int | None = None
    aim_y: int | None = None
    toast_text: str = ""
    toast_until: float = 0.0


# Ponte thread-safe entre o motor (thread 'TorchBridgeInput') e a thread da UI (Qt).
class SharedOverlayState:
    def __init__(self) -> None:
        self._lock = Lock()
        self._snapshot = OverlaySnapshot()

    # Leitura atômica do último snapshot publicado.
    def get(self) -> OverlaySnapshot:
        with self._lock:
            return self._snapshot

    # Publica um novo snapshot com apenas os campos alterados (dataclasses.replace).
    def update(self, **changes: object) -> None:
        with self._lock:
            self._snapshot = replace(self._snapshot, **changes)

    # Publica uma mensagem temporária com validade em segundos; o overlay apaga sozinho.
    def toast(self, text: str, seconds: float = 2.2) -> None:
        self.update(toast_text=text, toast_until=time.monotonic() + seconds)

