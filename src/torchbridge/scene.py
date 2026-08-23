# Identificação de cena: thread dedicada que captura a área do jogo em baixa
# resolução e decide se a tela atual é de "menu" (título, pausa, carregamento,
# cinemáticas — onde a roda de habilidades não faz sentido) ou de "gameplay".
#
# Principio: telas de menu do Torchlight são estáticas (arte fixa ou mundo
# congelado); o gameplay tem o mundo vivo em movimento. Medimos o movimento
# médio entre capturas consecutivas — fora das regiões ignoradas do perfil
# (a cena 3D central anima até em menus: personagem, fogo, lanternas).
#
# Só movimento não basta: o jogador PARADO no gameplay também fica estático
# (medido ao vivo: 0.17–0.35, dentro da faixa de menu). Por isso juntamos um
# segundo sinal, de COR nas faixas fora da máscara:
#   * topo (0..0.2 da altura): o HUD do Torchlight tem retrato/mana/minimapa
#     azul-dominantes — presente no gameplay e no pause, ausente no título;
#   * meio (0.39..0.60 da altura, colunas centrais): o painel "Options" do
#     pause — pergaminho bege + botões vermelhos, tons QUENTES sobre o mundo
#     frio (medido ao vivo: painel 0.69 x mundo 0.06–0.17 de células quentes).
#     A antiga faixa da base media a última linha da grade: o painel não
#     chega até lá (medido 0.00 no centro) e o azul vinha das rochas do mundo
#     nos cantos da tela — piscava 0↔1 durante o gameplay e forjava MENU.
# Regra por amostra: painel presente => menu; senão HUD presente => gameplay
# (mesmo parado); senão decide por movimento. A histerese (várias amostras
# seguidas) evita trocas por ruído, e captura falhou ⇒ UNKNOWN ⇒ roda
# liberada, porque bloquear à toa no meio do combate seria pior que mostrar
# a roda uma vez num menu.
from __future__ import annotations

import logging
import threading
import time
from typing import Any

from .win32 import WindowLocator, capture_client_grid

log = logging.getLogger(__name__)


# Rótulos de cena publicados no estado compartilhado (string simples, como 'mode').
class SceneKind:
    # Mundo vivo (HUD + movimento): roda liberada.
    GAMEPLAY = "gameplay"
    # Tela de menu detectada (título, pausa, loading, cinemática): roda bloqueada.
    MENU = "menu"
    # Indeterminado (captura falhou, jogo ausente, aquecimento): permissivo.
    UNKNOWN = "unknown"


# Conjunto das cenas em que a roda é liberada — regra única consultada pelo motor.
RADIAL_ALLOWED = frozenset({SceneKind.GAMEPLAY, SceneKind.UNKNOWN})


# Movimento médio entre duas capturas: média dos |Δ| por célula (0..255 por célula).
# Células em `mask` (linha, coluna) são ignoradas — regiões que o usuário marcou
# como irrelevantes no perfil (scenes.ignore_rects). Tudo mascarado ⇒ 0.0 (menu).
def grid_motion(
    current: list[list[int]],
    previous: list[list[int]],
    mask: set[tuple[int, int]] | frozenset[tuple[int, int]] | None = None,
) -> float:
    """Mean absolute luminance delta per unmasked cell between two grids."""
    total = 0
    count = 0
    for row_index, (row_cur, row_prev) in enumerate(zip(current, previous)):
        for col_index, (value_cur, value_prev) in enumerate(zip(row_cur, row_prev)):
            if mask and (row_index, col_index) in mask:
                continue
            total += abs(value_cur - value_prev)
            count += 1
    return total / max(1, count)


# Células da grade cobertas por retângulos normalizados [x0, y0, x1, y1] (0..1):
# uma célula é ignorada quando o CENTRO dela cai dentro de algum retângulo.
def ignore_cells(
    cols: int,
    rows: int,
    rects: list[list[float]] | None,
) -> frozenset[tuple[int, int]]:
    """Return the grid cells whose centers fall inside any normalized rect."""
    if not rects:
        return frozenset()
    cells: set[tuple[int, int]] = set()
    for x0, y0, x1, y1 in rects:
        for row in range(rows):
            center_y = (row + 0.5) / rows
            if not (y0 <= center_y <= y1):
                continue
            for col in range(cols):
                center_x = (col + 0.5) / cols
                if x0 <= center_x <= x1:
                    cells.add((row, col))
    return frozenset(cells)


# Média e desvio padrão da luminância da captura (escala 0..255 por célula).
def grid_stats(grid: list[list[int]]) -> tuple[float, float]:
    """Return (mean, standard deviation) of grid luminance."""
    values = [value for row in grid for value in row]
    mean = sum(values) / max(1, len(values))
    variance = sum((value - mean) ** 2 for value in values) / max(1, len(values))
    return mean, variance ** 0.5


# Features de cor por faixas fixas (em frações 0..1 da altura): o azul do
# topo indica HUD presente (retrato/mana/minimapa) e o QUENTE do meio indica
# o painel do pause (pergaminho bege + botões vermelhos) — o mundo do
# Torchlight deste usuário é frio (azul-ardósia), então "quente" separa bem.
# As duas faixas olham só as colunas centrais (0.20..0.80 da largura): as
# bordas mostram o mundo (a hotbar é centralizada) e não devem opinar.
# Devolve (top_blue, panel_warm) como fração de células da cor alvo entre as
# saturadas da faixa (0.0 quando a faixa não tem cor utilizável — escura
# demais para opinar).
def grid_color_features(
    grid: list[list[tuple[int, int, int]]],
    top_max: float = 0.2,
    panel_min: float = 0.39,
    panel_max: float = 0.60,
    center_min: float = 0.20,
    center_max: float = 0.80,
) -> tuple[float, float]:
    """Return (top_band_blue, panel_band_warm) fractions of target cells."""
    rows = len(grid)
    if not rows:
        return 0.0, 0.0
    cols = len(grid[0]) or 1

    def band_score(is_in_band, is_target) -> float:
        saturated = 0
        hit = 0
        for row_index, row in enumerate(grid):
            center_y = (row_index + 0.5) / rows
            if not is_in_band(center_y):
                continue
            for col_index, value in enumerate(row):
                center_x = (col_index + 0.5) / cols
                if center_x < center_min or center_x > center_max:
                    continue
                b, g, r = value
                level = (b + g + r) // 3
                if level < 14:
                    continue
                if max(b, g, r) - min(b, g, r) < 22:
                    continue
                saturated += 1
                if is_target(b, g, r):
                    hit += 1
        # Faixa sem cor suficiente não opina (0.0 em vez de razão instável).
        if saturated < max(2, cols // 10):
            return 0.0
        return hit / saturated

    # Azul-dominante (b >= r e r bem menor que b): HUD/Mundo azul do jogo.
    top = band_score(
        lambda y: y < top_max,
        lambda b, g, r: b >= r and b >= g and r <= b * 0.6,
    )
    # Quente (r bem maior que b): pergaminho/vermelho do painel do pause.
    panel = band_score(
        lambda y: panel_min <= y <= panel_max,
        lambda b, g, r: r >= b * 1.25 and r >= g,
    )
    return top, panel


# Captura toda preta/plana = quase certamente falha de captura (ex.: exclusive
# fullscreen) e não uma cena real — devolve True para o detector tratar como erro.
def is_blank_grid(grid: list[list[int]], max_mean: float = 3.0, max_std: float = 3.0) -> bool:
    """Return True when the grid looks like a failed (empty/black) capture."""
    mean, std = grid_stats(grid)
    return mean <= max_mean and std <= max_std


# Máquina de estados pura (sem I/O): vota por amostra e só troca de cena após
# N confirmações consecutivas — histerese contra ruído e transições rápidas.
class SceneAnalyzer:
    def __init__(
        self,
        motion_gameplay: float = 1.0,
        motion_menu: float = 0.5,
        confirm_samples: int = 3,
        top_blue_menu: float = 0.5,
        panel_warm_menu: float = 0.4,
    ) -> None:
        self.motion_gameplay = motion_gameplay
        self.motion_menu = motion_menu
        self.confirm_samples = max(1, confirm_samples)
        # Fração de cor nas faixas que revelam menu mesmo com o jogador
        # parado: topo com HUD (gameplay) x células QUENTES do painel no
        # meio da tela (pause).
        self.top_blue_menu = top_blue_menu
        self.panel_warm_menu = panel_warm_menu
        self.kind = SceneKind.UNKNOWN
        self._votes = 0

    # Alimenta uma amostra (motion pode ser None = sem captura) e devolve a cena atual.
    # top_blue/panel_warm (frações 0..1 das features de cor) são opcionais; com
    # None (teste/uso antigo) o voto continua sendo só por movimento.
    def feed(
        self,
        motion: float | None,
        top_blue: float | None = None,
        panel_warm: float | None = None,
    ) -> str:
        """Feed one sample; returns the current scene kind after voting."""
        # Sem dado (jogo ausente, captura falhou): cena indeterminada e votos zerados —
        # ao voltar a capturar, a nova cena precisa ser confirmada do zero.
        if motion is None:
            self.kind = SceneKind.UNKNOWN
            self._votes = 0
            return self.kind

        # Voto desta amostra. Ordem importa: o painel do pause (quente no
        # meio da tela) é o sinal mais forte — mesmo com HUD no topo e
        # movimento, manda fechar.
        if panel_warm is not None and panel_warm >= self.panel_warm_menu:
            target = SceneKind.MENU
        # HUD presente (topo azul) => gameplay mesmo com o jogador parado
        # (movimento quase zero); é o caso que o movimento sozinho errava.
        elif top_blue is not None and top_blue >= self.top_blue_menu:
            target = SceneKind.GAMEPLAY
        elif motion >= self.motion_gameplay:
            target = SceneKind.GAMEPLAY
        elif motion <= self.motion_menu:
            target = SceneKind.MENU
        else:
            self._votes = 0  # zona de histerese: anula confirmações acumuladas
            return self.kind

        # Cena já confirmada: contagem zerada para a próxima transição.
        if target == self.kind:
            self._votes = 0
            return self.kind
        # Rumo a uma cena nova: acumula votos até a confirmação (histerese).
        self._votes += 1
        if self._votes >= self.confirm_samples:
            self.kind = target
            self._votes = 0
        return self.kind

    # Novas amostras de uma cena diferente da atual reiniciam a contagem
    # (tratado dentro de feed pelo 'target == self.kind'); este método é usado
    # pelo detector ao trocar de janela/ausência de jogo para re-sincronizar.
    def reset(self) -> None:
        self.kind = SceneKind.UNKNOWN
        self._votes = 0


# Thread daemon: amostra o jogo a cada sample_interval_s e publica a cena no overlay.
class SceneDetector(threading.Thread):
    """Daemon thread that samples the game window and publishes the scene kind."""

    def __init__(self, config, shared, locator: WindowLocator) -> None:
        super().__init__(name="TorchBridgeScene", daemon=True)
        self.config = config
        self.shared = shared
        self.locator = locator
        self._stop_event = threading.Event()
        self._analyzer = SceneAnalyzer()
        self._last_grid: list[list[int]] | None = None
        self._last_kind = SceneKind.UNKNOWN

    # Pede a parada; a thread encerra no próximo ciclo.
    def stop(self) -> None:
        self._stop_event.set()

    # Publica a cena; avisa o usuário apenas nas transições (menu → bloqueio etc.).
    def _publish(self, kind: str) -> None:
        if kind == self._last_kind:
            return
        self._last_kind = kind
        self.shared.update(scene_kind=kind)
        # Feedback visual só quando o estado muda de fato (sem spam a cada amostra).
        if kind == SceneKind.MENU:
            self.shared.toast("Tela de menu — roda desativada", 2.2)
            log.info("Cena detectada como menu — roda radial bloqueada")
        elif kind == SceneKind.GAMEPLAY:
            self.shared.toast("Jogo em andamento — roda liberada", 1.8)
            log.info("Cena detectada como gameplay — roda radial liberada")

    # Um ciclo: captura, analisa e publica. Chamado pelo loop; separado para teste.
    def _tick(self) -> None:
        cfg = self.config.get()
        scenes = cfg.get("scenes", {})
        if not scenes.get("enabled", True):
            # Desligado no perfil: cena indeterminada e sem captura.
            self._last_grid = None
            self._analyzer.reset()
            self._publish(SceneKind.UNKNOWN)
            return

        hwnd = self.locator.find()
        # Só captura com a janela do jogo presente, em primeiro plano e com área
        # utilizável. A captura lê o desktop composto na região do jogo; com o
        # jogo coberto, leria a janela de cima (dado errado) — sem dado é melhor.
        if not hwnd or not self.locator.is_foreground(hwnd):
            self._last_grid = None
            self._analyzer.reset()
            self._publish(SceneKind.UNKNOWN)
            return
        grids = capture_client_grid(hwnd, int(scenes.get("grid_cols", 48)), int(scenes.get("grid_rows", 27)))
        if grids is None:
            self._last_grid = None
            self._analyzer.reset()
            self._publish(SceneKind.UNKNOWN)
            return
        grid, color_grid = grids
        if is_blank_grid(grid):
            # Falha de captura: não temos informação — permissivo, sem ruído de log.
            self._last_grid = None
            self._analyzer.reset()
            self._publish(SceneKind.UNKNOWN)
            return

        # Primeira captura útil: só guarda como referência (sem movimento medido).
        if self._last_grid is None:
            self._last_grid = grid
            self._publish(SceneKind.UNKNOWN)
            return

        motion = grid_motion(
            grid,
            self._last_grid,
            # Regiões irrelevantes do perfil (ex.: cena central animada do título).
            ignore_cells(
                int(scenes.get("grid_cols", 48)),
                int(scenes.get("grid_rows", 27)),
                scenes.get("ignore_rects", []),
            ),
        )
        self._last_grid = grid
        # Sinal de cor: HUD no topo (gameplay parado) x painel QUENTE no meio (pause).
        top_blue, panel_warm = grid_color_features(color_grid)
        self._publish(self._analyzer.feed(motion, top_blue, panel_warm))

    # Máquina de estados conforme o perfil (método separado para reconfiguração).
    @staticmethod
    def _build_analyzer(scenes: dict[str, Any]) -> SceneAnalyzer:
        return SceneAnalyzer(
            motion_gameplay=float(scenes.get("motion_gameplay", 1.0)),
            motion_menu=float(scenes.get("motion_menu", 0.5)),
            confirm_samples=int(scenes.get("confirm_samples", 3)),
            top_blue_menu=float(scenes.get("top_blue_menu", 0.5)),
            panel_warm_menu=float(scenes.get("panel_warm_menu", 0.4)),
        )

    # Loop principal da thread: amostra no intervalo configurado, dorme o restante.
    def run(self) -> None:
        interval = 0.25
        try:
            while not self._stop_event.is_set():
                tick_start = time.perf_counter()
                # Perfil recarregado (ou primeira passada): aplica os parâmetros novos.
                cfg = self.config.get()
                scenes = cfg.get("scenes", {})
                interval = float(scenes.get("sample_interval_s", 0.25))
                new_analyzer = self._build_analyzer(scenes)
                # Só re-cria a máquina quando os parâmetros mudaram de fato; trocar
                # a cada tick zeraria os votos de confirmação (histerese).
                if (
                    self._analyzer.motion_gameplay != new_analyzer.motion_gameplay
                    or self._analyzer.motion_menu != new_analyzer.motion_menu
                    or self._analyzer.confirm_samples != new_analyzer.confirm_samples
                    or self._analyzer.top_blue_menu != new_analyzer.top_blue_menu
                    or self._analyzer.panel_warm_menu != new_analyzer.panel_warm_menu
                ):
                    self._analyzer = new_analyzer
                    self._analyzer.reset()
                try:
                    self._tick()
                except Exception:
                    # Uma amostra ruim nunca derruba a thread; segue no próximo ciclo.
                    log.exception("Falha ao analisar a cena")
                remaining = interval - (time.perf_counter() - tick_start)
                if remaining > 0:
                    self._stop_event.wait(remaining)
        finally:
            # Ao sair, devolve o estado para o padrão permissivo (sem cena).
            self.shared.update(scene_kind=SceneKind.UNKNOWN)
