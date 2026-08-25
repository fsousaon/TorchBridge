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


# Slots da roda que abrem painéis laterais do jogo, por lateral da tela:
# índice 0 = lado esquerdo (Personagem 'C' e Pet 'P'); índice 1 = lado direito (Inventário 'I',
# Habilidades 'S', Missões 'Q' e Diário 'J').
PANEL_SIDE: dict[str, int] = {
    "C": 0,
    "P": 0,
    "I": 1,
    "S": 1,
    "Q": 1,
    "J": 1,
}


# Alterna o painel 'slot' na lateral correspondente e devolve o novo estado de active_panels
# ([esquerdo, direito], "" = fechado). Selecionar o painel já aberto o fecha; qualquer outro
# abre ou substitui o painel daquela lateral. Slots sem lateral definida não alteram o estado.
def toggle_panel(active_panels: list[str], slot: str) -> list[str]:
    key = slot.strip().upper()
    side = PANEL_SIDE.get(key)
    if side is None:
        return active_panels
    result = list(active_panels)
    result[side] = "" if result[side] == key else key
    return result


# Fração da largura da tela (12,5%) que a roda de atalhos e a âncora de movimento deslocam
# quando um único painel lateral está aberto, mantendo-as na área visível do lado oposto.
PANEL_X_SHIFT = 0.125


# Deslocamento horizontal (fração da largura; positivo = direita) conforme os painéis
# laterais abertos: +12,5% com só o lado esquerdo (índice 0) aberto; -12,5% com só o
# direito (índice 1); 0 quando ambos estão abertos ou ambos fechados.
def panels_x_shift(active_panels: list[str]) -> float:
    left, right = (list(active_panels) + ["", ""])[:2]
    if left and not right:
        return PANEL_X_SHIFT
    if right and not left:
        return -PANEL_X_SHIFT
    return 0.0


# Com os dois painéis laterais abertos de uma vez, a tela útil fica pequena demais para o
# movimento direto: o analógico esquerdo vira cursor livre (sem o clique do click-to-move).
def both_panels_open(active_panels: list[str]) -> bool:
    left, right = (list(active_panels) + ["", ""])[:2]
    return bool(left and right)


# Largura do painel do jogo medida na própria janela: ela escala com a ALTURA da janela
# (7/15 ≈ 46,6%), não com a largura — o jogo mantém a interface sem distorção em qualquer
# proporção de tela (224px em 480 de altura, 280px em 600, 468px em ~1003, sempre 7/15).
PANEL_WIDTH_FRACTION_OF_HEIGHT = 7 / 15


# Botão de fechar (X) do painel: a forma que o JOGO hit-testa não é uma caixa, é uma
# aba com base reta na borda interna do painel e ponta voltada para o interior
# (medidos pixel a pixel no botão VERMELHO REAL do jogo em docs/proporcao/
# calibracao1.png: y 132–155 de 486 → altura ~0.05 da janela; ponta x 207 vs base
# x 220 → protrusão ~13px = 0.057 da largura do painel; topo/base estreitos ~3px
# = 0.015 do painel; base reta na borda interna). Ajuste aqui.
CLOSE_TAB_TOP_FRACTION = 0.27      # topo da aba, em fração da ALTURA da janela
CLOSE_TAB_BOTTOM_FRACTION = 0.32   # base da aba, em fração da altura da janela
CLOSE_TAB_EDGE_WIDTH = 0.015       # largura no topo e na base, em fração da largura do painel
CLOSE_TAB_TIP_WIDTH = 0.06         # largura máxima (ponta, na altura do meio), em fração do painel


# Largura do painel em pixels, a partir da altura da janela.
def panel_width(rect: Rect) -> int:
    return int(round(rect.height * PANEL_WIDTH_FRACTION_OF_HEIGHT))


# Retângulos de calibração (x, y, w, h) em coordenadas absolutas — fonte única usada pelo
# click_zone (lógica) e pelo overlay (desenho das zonas em modo de calibração).
def panel_regions(rect: Rect) -> dict[str, tuple[int, int, int, int]]:
    w = panel_width(rect)
    return {
        "panel_left": (rect.left, rect.top, w, rect.height),
        "panel_right": (rect.right - w, rect.top, w, rect.height),
        "center": (rect.left + w, rect.top, rect.width - 2 * w, rect.height),
    }


# Vértices (x, y) absolutos da aba do botão fechar, em ordem para o hit-test de
# polígono e para o QPainter. side 'left' = aba do painel esquerdo (borda interna na
# direita do painel); 'right' = espelho no painel direito (borda interna na esquerda).
# Forma: base RETA na borda interna (topo→base), estreita no topo/na base, alargando
# até a ponta na altura do meio — como a aba real do Torchlight.
def close_tab_vertices(rect: Rect, side: str) -> list[tuple[float, float]]:
    w = panel_width(rect)
    edge_w = w * CLOSE_TAB_EDGE_WIDTH
    tip_w = w * CLOSE_TAB_TIP_WIDTH
    top = rect.top + rect.height * CLOSE_TAB_TOP_FRACTION
    bottom = rect.top + rect.height * CLOSE_TAB_BOTTOM_FRACTION
    mid = (top + bottom) / 2
    if side == "left":
        inner = rect.left + w
        return [
            (inner, top),            # canto interno superior (na borda do painel)
            (inner - edge_w, top),   # canto externo superior
            (inner - tip_w, mid),    # ponta, voltada para o interior do painel
            (inner - edge_w, bottom),  # canto externo inferior
            (inner, bottom),         # canto interno inferior (base reta na borda)
        ]
    inner = rect.right - w
    return [
        (inner, top),
        (inner + edge_w, top),
        (inner + tip_w, mid),
        (inner + edge_w, bottom),
        (inner, bottom),
    ]


# Ponto dentro do polígono (ray-casting): dispara um raio horizontal à direita e
# conta interseções com as arestas — ímpar = dentro.
def point_in_polygon(x: float, y: float, polygon: list[tuple[float, float]]) -> bool:
    inside = False
    n = len(polygon)
    for i in range(n):
        x1, y1 = polygon[i]
        x2, y2 = polygon[(i + 1) % n]
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            inside = not inside
    return inside


# Região de um clique em relação aos painéis: "close_left" (aba fechar do painel
# esquerdo), "left", "center", "right" ou "close_right" (aba do painel direito).
# A aba do botão fecha só aquele lado; o resto do painel não fecha nada.
def click_zone(rect: Rect, x: int, y: int) -> str:
    if not rect.contains(x, y):
        return "outside"
    for name, side in (("close_left", "left"), ("close_right", "right")):
        if point_in_polygon(x, y, close_tab_vertices(rect, side)):
            return name
    regions = panel_regions(rect)
    for name, label in (("panel_left", "left"), ("panel_right", "right")):
        bx, by, bw, bh = regions[name]
        if bx <= x < bx + bw and by <= y < by + bh:
            return label
    return "center"


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
    radial_active: bool = False
    radial_selection: int | None = None
    # Painéis laterais abertos pela roda: índice 0 = esquerdo (C/P), 1 = direito (I/S/Q/J); "" = fechado.
    active_panels: list[str] = field(default_factory=lambda: ["", ""])
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

