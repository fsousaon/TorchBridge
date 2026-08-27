# Testes do rastreador de painéis (toggle_panel): abertura, troca de lateral e fechamento por repetição.
import unittest

from torchbridge.models import (
    PANEL_SIDE,
    CLOSE_TAB_ANCHOR_FRACTION_OF_HEIGHT,
    CLOSE_TAB_ASPECT,
    CLOSE_TAB_BOTTOM_FRACTION,
    CLOSE_TAB_TOP_FRACTION,
    Rect,
    both_panels_open,
    click_zone,
    close_tab_vertices,
    hud_mask_hit,
    hud_target_rect,
    load_hud_mask,
    point_in_polygon,
    toggle_panel,
    panels_x_shift,
)


class PanelToggleTests(unittest.TestCase):
    # Estado inicial do rastreador: as duas posições vazias (nenhum painel aberto).
    def test_default_state_is_empty(self):
        self.assertEqual(toggle_panel(["", ""], "Z"), ["", ""])

    # Selecionar C abre o painel esquerdo (índice 0).
    def test_select_c_opens_left_panel(self):
        self.assertEqual(toggle_panel(["", ""], "C"), ["C", ""])

    # Selecionar P depois de C troca o painel esquerdo, sem tocar no direito.
    def test_select_p_replaces_left_panel(self):
        self.assertEqual(toggle_panel(["C", ""], "P"), ["P", ""])

    # Repetir P fecha o painel esquerdo — as duas posições voltam a vazias.
    def test_select_same_panel_closes_it(self):
        self.assertEqual(toggle_panel(["P", ""], "P"), ["", ""])

    # Selecionar I abre o painel direito (índice 1).
    def test_select_i_opens_right_panel(self):
        self.assertEqual(toggle_panel(["", ""], "I"), ["", "I"])

    # Repetir S fecha o painel direito.
    def test_select_same_right_panel_closes_it(self):
        self.assertEqual(toggle_panel(["", "S"], "S"), ["", ""])

    # Trocar I por Q mantém o direito aberto com o novo painel.
    def test_switch_right_panel(self):
        self.assertEqual(toggle_panel(["", "I"], "Q"), ["", "Q"])

    # As laterais são independentes: esquerda aberta não interfere no direito e vice-versa.
    def test_sides_are_independent(self):
        self.assertEqual(toggle_panel(["C", "I"], "J"), ["C", "J"])
        self.assertEqual(toggle_panel(["C", "I"], "C"), ["", "I"])
        self.assertEqual(toggle_panel(["C", "I"], "S"), ["C", "S"])

    # Minúscula normaliza: 'c' abre o mesmo painel de 'C'.
    def test_lowercase_normalizes(self):
        self.assertEqual(toggle_panel(["", ""], "c"), ["C", ""])
        self.assertEqual(toggle_panel(["", ""], " s "), ["", "S"])

    # Slot sem painel definido (outro atalho da roda) não altera o estado (mesma lista).
    def test_non_panel_slot_is_ignored(self):
        panels = ["C", "I"]
        self.assertIs(toggle_panel(panels, "M"), panels)


class PanelsXShiftTests(unittest.TestCase):
    # Só o lado esquerdo (índice 0) aberto → desloca para a direita (+12,5% da largura).
    def test_left_panel_only_shifts_positive(self):
        self.assertEqual(panels_x_shift(["C", ""]), 0.125)
        self.assertEqual(panels_x_shift(["P", ""]), 0.125)

    # Só o lado direito (índice 1) aberto → desloca para a esquerda (-12,5% da largura).
    def test_right_panel_only_shifts_negative(self):
        self.assertEqual(panels_x_shift(["", "I"]), -0.125)
        self.assertEqual(panels_x_shift(["", "Q"]), -0.125)

    # Ambos fechados ou ambos abertos → sem deslocamento (centro).
    def test_no_shift_when_both_or_none(self):
        self.assertEqual(panels_x_shift(["", ""]), 0.0)
        self.assertEqual(panels_x_shift(["C", "J"]), 0.0)

    # Listas curtas são toleradas como lado vazio.
    def test_short_list_is_tolerated(self):
        self.assertEqual(panels_x_shift(["C"]), 0.125)
        self.assertEqual(panels_x_shift([]), 0.0)


class BothPanelsOpenTests(unittest.TestCase):
    # Só uma lateral aberta → False (movimento direto segue normal).
    def test_single_panel_is_not_both(self):
        self.assertFalse(both_panels_open(["C", ""]))
        self.assertFalse(both_panels_open(["", "I"]))

    # Ambas abertas → True (esquerdo vira cursor livre, sem click-to-move).
    def test_both_panels_is_true(self):
        self.assertTrue(both_panels_open(["C", "I"]))
        self.assertTrue(both_panels_open(["P", "Q"]))

    # Nenhuma aberta → False; listas curtas tratadas como lado vazio.
    def test_empty_and_short_lists(self):
        self.assertFalse(both_panels_open(["", ""]))
        self.assertFalse(both_panels_open([]))
        self.assertFalse(both_panels_open(["C"]))


class ClickZoneTests(unittest.TestCase):
    # 1920x1080 → painel 504px; aba (octógono do SVG) com borda interna em x=504 (esq.) /
    # 1416 (dir.), y 281.5–339.8 (altura 58.3px, centro y≈310.7). A "ponta" (aresta
    # vertical) existe entre y 289.1 e 331.3, profundidade 32.8px (aspecto 41/73):
    # x≈471.2 (esq.) / ≈1448.8 (dir.). Center x504–1415.
    def test_zones_on_1920x1080(self):
        rect = Rect(0, 0, 1920, 1080)
        self.assertEqual(click_zone(rect, 100, 100), "left")         # painel esq.
        self.assertEqual(click_zone(rect, 300, 310), "left")          # painel esq., longe da aba
        self.assertEqual(click_zone(rect, 498, 200), "left")          # acima da aba
        self.assertEqual(click_zone(rect, 498, 420), "left")          # abaixo da aba
        self.assertEqual(click_zone(rect, 500, 310), "close_left")    # dentro da aba esq.
        self.assertEqual(click_zone(rect, 478, 310), "close_left")    # perto da ponta esq.
        self.assertEqual(click_zone(rect, 960, 540), "center")
        self.assertEqual(click_zone(rect, 600, 540), "center")
        self.assertEqual(click_zone(rect, 1900, 100), "right")       # painel dir.
        self.assertEqual(click_zone(rect, 1480, 310), "right")       # painel dir., fora da aba
        self.assertEqual(click_zone(rect, 1420, 310), "close_right") # dentro da aba dir.
        self.assertEqual(click_zone(rect, 1444, 310), "close_right") # perto da ponta dir.

    # A aba é estreita no topo/base e larga no meio: ponto dentro na altura da ponta
    # (x=491, y=310) fica FORA acima do topo da aba (y=250), que começa em y≈281.5.
    def test_tab_narrower_at_top(self):
        rect = Rect(0, 0, 1920, 1080)
        self.assertEqual(click_zone(rect, 491, 310), "close_left")  # na altura da ponta
        self.assertEqual(click_zone(rect, 491, 250), "left")        # acima do topo: fora

    # Fora da janela → 'outside'.
    def test_outside_window(self):
        rect = Rect(0, 0, 1920, 1080)
        self.assertEqual(click_zone(rect, 2000, 540), "outside")
        self.assertEqual(click_zone(rect, 960, -5), "outside")
        self.assertEqual(click_zone(rect, 960, 1100), "outside")

    # 4:3 (640x480): painel 224px, aba y 125.1–151.0 (altura 25.9px), borda interna esq. x=224, dir. x=416.
    # Profundidade no meio (aspecto 41/73) ≈ 14.6px: ponta esq. x≈209.4, dir. x≈430.6.
    def test_43_resolution_close_tab(self):
        rect = Rect(0, 0, 640, 480)
        self.assertEqual(click_zone(rect, 100, 100), "left")
        self.assertEqual(click_zone(rect, 218, 138), "close_left")   # dentro da aba esq.
        self.assertEqual(click_zone(rect, 205, 138), "left")         # fora (esq.) da aba
        self.assertEqual(click_zone(rect, 300, 200), "center")
        self.assertEqual(click_zone(rect, 422, 138), "close_right")  # dentro da aba dir.
        self.assertEqual(click_zone(rect, 435, 138), "right")        # fora (dir.) da aba

    # Janela deslocada na tela: zonas acompanham o retângulo (não a origem).
    # 1080p deslocado (100,50): aba esq. y 331.5–389.8, x 571.2–604; centro y≈360.7.
    def test_offset_window(self):
        rect = Rect(100, 50, 1920, 1080)
        self.assertEqual(click_zone(rect, 596, 360), "close_left")
        self.assertEqual(click_zone(rect, 100 + 960, 50 + 540), "center")
        self.assertEqual(click_zone(rect, 1524, 360), "close_right")


class CloseTabShapeTests(unittest.TestCase):
    # A aba é o octógono exato do close-button-shape.svg: 8 vértices, base reta
    # na borda interna, ponta em aresta reta, topo/base com extremidades chanfradas.
    def test_vertices_count_and_edges(self):
        rect = Rect(0, 0, 1920, 1080)
        # A aba ancora na borda ORIGINAL do painel (CLOSE_TAB_ANCHOR...), não na
        # largura atual (que ganhou 20px): o botão do jogo fica onde o jogo desenha.
        w = rect.height * CLOSE_TAB_ANCHOR_FRACTION_OF_HEIGHT
        left = close_tab_vertices(rect, "left")
        right = close_tab_vertices(rect, "right")
        self.assertEqual(len(left), 8)
        self.assertEqual(len(right), 8)
        # Base reta na borda interna: os dois cantos da base têm x == borda interna.
        self.assertEqual(left[1][0], rect.left + w)
        self.assertEqual(left[2][0], rect.left + w)
        # Ponta da seta: aresta vertical (dois vértices com o x mais interno do painel).
        depth = (CLOSE_TAB_BOTTOM_FRACTION - CLOSE_TAB_TOP_FRACTION) * rect.height * CLOSE_TAB_ASPECT
        self.assertAlmostEqual(left[5][0], rect.left + w - depth)
        self.assertAlmostEqual(left[6][0], rect.left + w - depth)
        self.assertEqual(left[5][0], left[6][0])
        # Espelhamento: borda interna do direito = right - width; ponta no +depth.
        self.assertEqual(right[1][0], rect.right - w)
        self.assertEqual(right[2][0], rect.right - w)
        self.assertAlmostEqual(right[5][0], rect.right - w + depth)
        self.assertAlmostEqual(right[6][0], rect.right - w + depth)

    # A profundidade acompanha o aspecto do SVG (41/73) — forma real do botão.
    def test_svg_aspect_ratio(self):
        rect = Rect(0, 0, 1920, 1080)
        left = close_tab_vertices(rect, "left")
        height = max(y for _, y in left) - min(y for _, y in left)
        inner = rect.left + rect.height * CLOSE_TAB_ANCHOR_FRACTION_OF_HEIGHT
        depth = inner - min(x for x, _ in left)
        self.assertAlmostEqual(depth / height, CLOSE_TAB_ASPECT, places=6)

    # Hit-test do polígono: centro da aba dentro, canto externo fora.
    def test_point_in_polygon(self):
        rect = Rect(0, 0, 1920, 1080)
        left = close_tab_vertices(rect, "left")
        self.assertTrue(point_in_polygon(495, 318.6, left))   # perto da borda interna, no meio
        self.assertFalse(point_in_polygon(100, 318.6, left))  # longe, dentro do painel


class HudMaskGeometryTests(unittest.TestCase):
    # hud_target_rect: em 1080p padrão, HUD centralizada na base, 942*1.07 x 137*1.06
    # (SVG ampliado; largura escala com a ALTURA da janela, como os painéis).
    def test_target_rect_1080p(self):
        rect = Rect(0, 0, 1920, 1080)
        left, top, width, height = hud_target_rect(rect)
        self.assertAlmostEqual(width, 942.0 * 1.07, places=3)   # 1007.94
        self.assertAlmostEqual(height, 137.0 * 1.06, places=3)  # 145.22
        # 1080p: centro em 1920*0.501525 e base em 1080*0.996293 → nudge de ~3px
        # à direita e ~8px para cima em relação à posição original (0.5 / 1.0).
        self.assertAlmostEqual(left, 1920.0 * 0.501525 - 942.0 * 1.07 / 2.0, places=3)  # 458.958
        self.assertAlmostEqual(top, 1080.0 * 0.996293 - 137.0 * 1.06, places=3)          # 930.78

    # Janela deslocada: a HUD acompanha o retângulo (não a origem).
    def test_target_rect_offset(self):
        rect = Rect(100, 50, 1920, 1080)
        left, top, width, height = hud_target_rect(rect)
        self.assertAlmostEqual(left, 100 + 1920.0 * 0.501525 - 942.0 * 1.07 / 2.0, places=3)
        self.assertAlmostEqual(top, 50 + 1080.0 * 0.996293 - 137.0 * 1.06, places=3)

    # hud_mask_hit: sem máscara → sempre False (comportamento antigo preservado).
    def test_no_mask_never_hits(self):
        rect = Rect(0, 0, 1920, 1080)
        self.assertFalse(hud_mask_hit(None, rect, 960, 1045))
        self.assertFalse(hud_mask_hit(None, rect, 500, 500))

    # hud_mask_hit com máscara sintética 2x2: mapeia o ponto para a grade da HUD.
    def test_mask_hit_synthetic_grid(self):
        # Grade 2x2 com apenas o canto superior-direito preenchido (data = [0,1,0,0]).
        rows, cols = 2, 2
        data = [0, 1, 0, 0]
        mask = (rows, cols, list(data))
        rect = Rect(0, 0, 1920, 1080)
        # hud_target_rect(1080p) = (458.958, 930.78, 1007.94, 145.22). Canto sup-dir da HUD
        # cai em px=1, py=0 → data[0*2+1] = 1.
        self.assertTrue(hud_mask_hit(mask, rect, 458.958 + 1007.94 / 2 + 1, 930.78 + 72.61 - 1))
        # Canto sup-esq → data[0] = 0.
        self.assertFalse(hud_mask_hit(mask, rect, 458.958 + 10, 930.78 + 10))
        # Fora da região da HUD → False.
        self.assertFalse(hud_mask_hit(mask, rect, 960, 540))


class HudMaskAssetTests(unittest.TestCase):
    # Carrega a máscara real do SVG (precisa de Qt offscreen). Se o asset não existir
    # neste ambiente, os testes são pulados — a funcionalidade de hit-test já é coberta
    # pelas classes acima com máscara sintética.
    @classmethod
    def setUpClass(cls):
        try:
            cls.mask = load_hud_mask()
        except Exception:
            cls.mask = None

    def _require_mask(self):
        if self.mask is None:
            self.skipTest("asset da HUD / Qt Svg indisponível neste ambiente")

    def test_mask_dimensions_match_svg(self):
        self._require_mask()
        rows, cols, data = self.mask
        self.assertEqual((rows, cols), (137, 942))
        self.assertEqual(len(data), rows * cols)

    def test_mask_has_filled_pixels(self):
        self._require_mask()
        # O SVG verde tem ~84 mil pixels preenchidos — nunca zero.
        self.assertGreater(sum(self.mask[2]), 1000)

    def test_click_zone_hud_green_returns_hud(self):
        self._require_mask()
        rect = Rect(0, 0, 1920, 1080)
        # Ponto verde conhecido: barra de vida no centro da base (960, 1070).
        self.assertEqual(click_zone(rect, 960, 1070, self.mask), "hud")

    def test_click_zone_center_gap_stays_center(self):
        self._require_mask()
        rect = Rect(0, 0, 1920, 1080)
        # No meio da HUD, no vão entre o disco central e a barra de vida (960, 1058)
        # → continua "center".
        self.assertEqual(click_zone(rect, 960, 1058, self.mask), "center")

    def test_close_tab_takes_priority_over_hud(self):
        self._require_mask()
        rect = Rect(0, 0, 1920, 1080)
        # A aba de fechar (490, 318) tem prioridade sobre a HUD (que só existe no rodapé).
        self.assertEqual(click_zone(rect, 490, 318, self.mask), "close_left")

    def test_without_mask_center_is_unchanged(self):
        # Sem máscara (asset ausente), o mesmo ponto verde volta "center" — o
        # comportamento antigo (clique central zera os painéis) é preservado.
        rect = Rect(0, 0, 1920, 1080)
        self.assertEqual(click_zone(rect, 960, 1070), "center")


if __name__ == "__main__":
    unittest.main()
