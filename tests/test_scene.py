# Testes da identificação de cena: movimento entre grades, máquina de estados
# com histerese e guardas contra captura falha — tudo sem hardware (grades puras).
import unittest

from torchbridge.scene import (
    SceneAnalyzer,
    SceneKind,
    grid_color_features,
    grid_motion,
    grid_stats,
    ignore_cells,
    is_blank_grid,
)


def _grid(value: int, cols: int = 8, rows: int = 6) -> list[list[int]]:
    """Grade sintética preenchida com um único valor de luminância."""
    return [[value] * cols for _ in range(rows)]


def _noisy_grid(base: int, noise: int, cols: int = 8, rows: int = 6, seed: int = 0) -> list[list[int]]:
    """Grade com ruído determinístico (diferente por linha/coluna, sem imports)."""
    grid = []
    for row in range(rows):
        line = []
        for col in range(cols):
            jitter = ((row * 7 + col * 13 + seed * 31) % (noise * 2 + 1)) - noise
            line.append(max(0, min(255, base + jitter)))
        grid.append(line)
    return grid


class GridMotionTests(unittest.TestCase):
    # Grades idênticas: movimento zero (tela estática = menu).
    def test_identical_grids_have_zero_motion(self):
        self.assertEqual(grid_motion(_grid(120), _grid(120)), 0.0)

    # Grades diferentes: movimento proporcional ao deslocamento de luminância.
    def test_different_grids_have_positive_motion(self):
        motion = grid_motion(_grid(100), _grid(130))
        self.assertAlmostEqual(motion, 30.0)

    # Tamanhos diferentes não quebram: conta apenas os pares existentes.
    def test_mismatched_grids_compare_overlapping_cells(self):
        motion = grid_motion(_grid(100, cols=8, rows=6), _grid(100, cols=4, rows=3))
        self.assertEqual(motion, 0.0)

    # Ruído leve (ex.: cursor do mouse sobre o menu) ainda deixa o menu estático.
    # Célula única alterada em 20 níveis numa grade 8×6 → 20/48 ≈ 0.42 < 0.5.
    def test_light_noise_stays_below_menu_threshold(self):
        previous = _grid(90)
        current = _grid(90)
        current[3][4] = 110
        self.assertLess(grid_motion(previous, current), 0.5)

    # Células mascaradas não contam movimento nem no divisor (denominador só
    # com as células analisadas). Mudança só na célula (1,0) de 2×2 → 40/3.
    def test_masked_cells_are_ignored_in_motion(self):
        previous = [[10, 10], [10, 10]]
        current = [[10, 50], [50, 10]]
        self.assertAlmostEqual(grid_motion(previous, current), 20.0)
        self.assertAlmostEqual(grid_motion(previous, current, mask={(1, 0)}), 40.0 / 3.0)
        self.assertEqual(grid_motion(previous, current, mask={(0, 1), (1, 0)}), 0.0)

    # Tudo mascarado ⇒ movimento 0 (voto de menu) — nunca estoura divisão.
    def test_fully_masked_grids_have_zero_motion(self):
        previous = [[10, 10], [10, 10]]
        current = [[10, 50], [50, 10]]
        all_cells = {(0, 0), (0, 1), (1, 0), (1, 1)}
        self.assertEqual(grid_motion(previous, current, mask=all_cells), 0.0)


class GridStatsTests(unittest.TestCase):
    # Média e desvio de grade uniforme.
    def test_stats_of_uniform_grid(self):
        mean, std = grid_stats(_grid(200))
        self.assertEqual(mean, 200.0)
        self.assertEqual(std, 0.0)

    # Grade mista tem desvio positivo e média coerente.
    def test_stats_of_mixed_grid(self):
        mean, std = grid_stats([[0, 10, 10, 20]])
        self.assertAlmostEqual(mean, 10.0)
        self.assertGreater(std, 0.0)


class BlankGridTests(unittest.TestCase):
    # Captura toda preta (falha típica de fullscreen exclusivo) é tratada como vazia.
    def test_black_grid_is_blank(self):
        self.assertTrue(is_blank_grid(_grid(0)))

    # Conteúdo real, mesmo escuro, não é considerado captura falha.
    def test_dark_content_is_not_blank(self):
        self.assertFalse(is_blank_grid(_grid(12)))
        self.assertFalse(is_blank_grid(_noisy_grid(6, 3)))


class IgnoreCellsTests(unittest.TestCase):
    # Sem retângulos (None ou vazio): nenhuma célula é ignorada.
    def test_no_rects_means_no_mask(self):
        self.assertEqual(ignore_cells(8, 6, None), frozenset())
        self.assertEqual(ignore_cells(8, 6, []), frozenset())

    # Retângulo central numa grade 4×4: só as 4 células do meio (centros 0.375/0.625).
    def test_center_rect_selects_center_cells(self):
        cells = ignore_cells(4, 4, [[0.25, 0.25, 0.75, 0.75]])
        self.assertEqual(cells, {(1, 1), (1, 2), (2, 1), (2, 2)})

    # Faixa superior (como a área do logo): apenas a primeira linha.
    def test_top_band_rect_selects_top_rows(self):
        cells = ignore_cells(4, 4, [[0.0, 0.0, 1.0, 0.2]])
        self.assertEqual(cells, {(0, col) for col in range(4)})

    # Vários retângulos acumulam (ex.: colunas laterais à esquerda e à direita).
    def test_multiple_rects_accumulate(self):
        cells = ignore_cells(4, 4, [[0.0, 0.0, 0.2, 1.0], [0.8, 0.0, 1.0, 1.0]])
        self.assertEqual(cells, {(row, 0) for row in range(4)} | {(row, 3) for row in range(4)})

    # Tela inteira mascarada: todas as células ignoradas.
    def test_full_screen_rect_ignores_everything(self):
        cells = ignore_cells(4, 4, [[0.0, 0.0, 1.0, 1.0]])
        self.assertEqual(len(cells), 16)

    # A máscara medida do print de referência do usuário (área verde) cobre a
    # cena central e preserva o topo (logo) e a base (barra de menu): na grade
    # 48×27 ficam de fora as linhas 0-4 e 24-26 (a fronteira exata da linha 4
    # fica no fio do arredondamento do retângulo — célula de borda, inofensiva).
    def test_reference_mask_keeps_logo_and_menu_bands(self):
        cells = ignore_cells(48, 27, [[0.0104, 0.1667, 0.9901, 0.8713]])
        rows_with_cells = {row for row, _ in cells}
        self.assertEqual(rows_with_cells, set(range(5, 24)))
        kept = [
            (row, col)
            for row in range(27)
            for col in range(48)
            if (row, col) not in cells
        ]
        kept_rows = {row for row, _ in kept}
        self.assertEqual(kept_rows, {0, 1, 2, 3, 4, 24, 25, 26})


class GridColorFeaturesTests(unittest.TestCase):
    def _rgb_grid(self, rows: int = 27, cols: int = 48, color=(0, 0, 255)) -> list[list[tuple[int, int, int]]]:
        """Grade sintética preenchida com uma única cor (b, g, r)."""
        return [[color] * cols for _ in range(rows)]

    # Faixa do topo (HUD) azul-dominante → top_blue alto; faixa do painel fria.
    # Cores em (b, g, r) — ordem do DIB da captura (BGR), não RGB.
    def test_blue_top_band_means_hud_present(self):
        grid = self._rgb_grid(color=(10, 10, 10))
        for row in range(5):  # topo (centros < 0.2)
            grid[row] = [(200, 40, 60)] * 48
        top, panel = grid_color_features(grid)
        self.assertGreaterEqual(top, 0.9)
        self.assertLess(panel, 0.4)

    # Painel do pause (pergaminho/botões QUENTES) na faixa central → panel alto.
    def test_warm_panel_band_means_pause(self):
        grid = self._rgb_grid(color=(10, 10, 10))
        for row in range(10, 16):  # centros 0.39..0.60
            for col in range(10, 38):  # colunas centrais (0.20..0.80)
                grid[row][col] = (60, 130, 200)  # laranja (r dominante)
        top, panel = grid_color_features(grid)
        self.assertLess(top, 0.5)
        self.assertGreaterEqual(panel, 0.9)

    # Mundo frio (azul) na faixa central não acusa painel — o caso medido ao
    # vivo (rocha azul-ardósia) que a antiga faixa da base errava.
    def test_cold_world_has_no_panel_signal(self):
        grid = self._rgb_grid(color=(10, 10, 10))
        for row in range(10, 16):
            for col in range(10, 38):
                grid[row][col] = (200, 140, 60)  # azul (b dominante)
        top, panel = grid_color_features(grid)
        self.assertLess(top, 0.5)
        self.assertLess(panel, 0.5)

    # Células quentes fora das colunas centrais (bordas = mundo) não contam.
    def test_warm_cells_at_screen_edges_do_not_vote(self):
        grid = self._rgb_grid(color=(10, 10, 10))
        for row in range(10, 16):
            for col in range(0, 10):  # borda esquerda
                grid[row][col] = (60, 130, 200)
            for col in range(38, 48):  # borda direita
                grid[row][col] = (60, 130, 200)
        top, panel = grid_color_features(grid)
        self.assertEqual(panel, 0.0)

    # Faixa escura demais não opina (0.0): evita razão instável com 1 pixel quente.
    def test_dark_band_does_not_vote(self):
        grid = self._rgb_grid(color=(3, 3, 3))
        grid[12][10] = (60, 130, 200)
        top, panel = grid_color_features(grid)
        self.assertEqual(panel, 0.0)


class SceneAnalyzerTests(unittest.TestCase):
    def _analyzer(self, **kwargs) -> SceneAnalyzer:
        return SceneAnalyzer(motion_gameplay=1.0, motion_menu=0.5, **kwargs)

    # Sem captura (None): cena indeterminada e permissiva — nunca bloqueia na dúvida.
    def test_no_capture_means_unknown(self):
        analyzer = self._analyzer()
        self.assertEqual(analyzer.feed(None), SceneKind.UNKNOWN)

    # Tela estática (título/pausa/loading): menu confirmado após N amostras iguais.
    def test_static_scene_becomes_menu_after_confirmation(self):
        analyzer = self._analyzer()
        self.assertEqual(analyzer.feed(0.0), SceneKind.UNKNOWN)  # 1ª votação
        self.assertEqual(analyzer.feed(0.0), SceneKind.UNKNOWN)  # 2ª
        self.assertEqual(analyzer.feed(0.0), SceneKind.MENU)  # 3ª confirmou
        self.assertEqual(analyzer.feed(0.0), SceneKind.MENU)

    # Mundo vivo: gameplay confirmado após N amostras com movimento suficiente.
    def test_dynamic_scene_becomes_gameplay_after_confirmation(self):
        analyzer = self._analyzer()
        self.assertEqual(analyzer.feed(5.0), SceneKind.UNKNOWN)
        self.assertEqual(analyzer.feed(5.0), SceneKind.UNKNOWN)
        self.assertEqual(analyzer.feed(5.0), SceneKind.GAMEPLAY)

    # Zona de histerese (entre os limiares): não troca de cena nem acumula votos.
    def test_hysteresis_zone_keeps_state_and_resets_votes(self):
        analyzer = self._analyzer()
        analyzer.feed(5.0)
        analyzer.feed(5.0)
        analyzer.feed(5.0)  # gameplay confirmado
        # Dois votos 'menu' e um neutro no meio: sem confirmação, continua gameplay.
        self.assertEqual(analyzer.feed(0.0), SceneKind.GAMEPLAY)
        self.assertEqual(analyzer.feed(0.0), SceneKind.GAMEPLAY)
        self.assertEqual(analyzer.feed(0.7), SceneKind.GAMEPLAY)  # neutro zera votos
        self.assertEqual(analyzer.feed(0.0), SceneKind.GAMEPLAY)  # recomeçou a contagem
        self.assertEqual(analyzer.feed(0.0), SceneKind.GAMEPLAY)
        self.assertEqual(analyzer.feed(0.0), SceneKind.MENU)

    # Menu → gameplay e gameplay → menu: transições completas em ambos os sentidos.
    def test_transitions_between_scenes(self):
        analyzer = self._analyzer()
        for _ in range(3):
            analyzer.feed(0.0)
        self.assertEqual(analyzer.kind, SceneKind.MENU)
        for _ in range(3):
            analyzer.feed(8.0)
        self.assertEqual(analyzer.kind, SceneKind.GAMEPLAY)
        for _ in range(3):
            analyzer.feed(0.0)
        self.assertEqual(analyzer.kind, SceneKind.MENU)

    # Sem captura no meio do caminho: volta a UNKNOWN e a nova cena recomeça do zero.
    def test_capture_loss_resets_to_unknown_and_requires_fresh_confirmation(self):
        analyzer = self._analyzer()
        for _ in range(3):
            analyzer.feed(8.0)
        self.assertEqual(analyzer.kind, SceneKind.GAMEPLAY)
        self.assertEqual(analyzer.feed(None), SceneKind.UNKNOWN)
        self.assertEqual(analyzer.feed(8.0), SceneKind.UNKNOWN)  # votos zerados
        self.assertEqual(analyzer.feed(8.0), SceneKind.UNKNOWN)
        self.assertEqual(analyzer.feed(8.0), SceneKind.GAMEPLAY)

    # Confirm = 1: troca imediata (útil para teste/ajuste fino do usuário).
    def test_single_confirmation_switches_immediately(self):
        analyzer = self._analyzer(confirm_samples=1)
        self.assertEqual(analyzer.feed(0.0), SceneKind.MENU)
        self.assertEqual(analyzer.feed(5.0), SceneKind.GAMEPLAY)

    # Jogador PARADO no gameplay (movimento ~0.2, dentro da faixa de menu):
    # o HUD azul no topo vota gameplay — o caso que o movimento sozinho errava.
    def test_still_player_with_hud_is_gameplay(self):
        analyzer = self._analyzer()
        for _ in range(3):
            analyzer.feed(0.2, top_blue=0.84, panel_warm=0.0)
        self.assertEqual(analyzer.kind, SceneKind.GAMEPLAY)

    # Pausa: o painel QUENTE no centro vota MENU mesmo com o HUD visível no topo.
    def test_pause_panel_with_hud_is_menu(self):
        analyzer = self._analyzer()
        for _ in range(3):
            analyzer.feed(0.0, top_blue=0.73, panel_warm=0.9)
        self.assertEqual(analyzer.kind, SceneKind.MENU)

    # Regressão medida ao vivo: mundo com rocha AZUL nas bordas da base (o
    # Torchlight do usuário é azul-ardósia) não pode forjar MENU — a antiga
    # feature "base_blue" votava 0.82–1.00 durante o gameplay e bloqueava a
    # roda com o jogador correndo; o painel quente (0.06–0.17 no mundo) não.
    def test_blue_corner_world_does_not_vote_menu(self):
        analyzer = self._analyzer()
        for _ in range(3):
            analyzer.feed(8.0, top_blue=0.65, panel_warm=0.1)
        self.assertEqual(analyzer.kind, SceneKind.GAMEPLAY)
        analyzer.reset()
        for _ in range(3):
            analyzer.feed(0.2, top_blue=0.56, panel_warm=0.15)  # parado, mundo azul
        self.assertEqual(analyzer.kind, SceneKind.GAMEPLAY)

    # Título (topo quente, sem HUD): decide pelo movimento baixo (menu).
    def test_title_screen_without_hud_is_menu(self):
        analyzer = self._analyzer()
        for _ in range(3):
            analyzer.feed(0.4, top_blue=0.10, panel_warm=0.09)
        self.assertEqual(analyzer.kind, SceneKind.MENU)

    # Gameplay andando (movimento alto + HUD): gameplay, como antes.
    def test_walking_player_with_hud_is_gameplay(self):
        analyzer = self._analyzer()
        for _ in range(3):
            analyzer.feed(8.0, top_blue=0.84, panel_warm=0.0)
        self.assertEqual(analyzer.kind, SceneKind.GAMEPLAY)

    # Sem features de cor (None): comportamento antigo só por movimento.
    def test_color_features_optional_preserve_motion_only_votes(self):
        analyzer = self._analyzer()
        for _ in range(3):
            analyzer.feed(0.0)
        self.assertEqual(analyzer.kind, SceneKind.MENU)
        for _ in range(3):
            analyzer.feed(5.0)
        self.assertEqual(analyzer.kind, SceneKind.GAMEPLAY)

    # O painel do pause vence até o movimento alto (ex.: mundo vivo atrás).
    def test_pause_panel_beats_high_motion(self):
        analyzer = self._analyzer()
        for _ in range(3):
            analyzer.feed(9.0, top_blue=0.6, panel_warm=0.9)
        self.assertEqual(analyzer.kind, SceneKind.MENU)


if __name__ == "__main__":
    unittest.main()
