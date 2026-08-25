# Testes do rastreador de painéis (toggle_panel): abertura, troca de lateral e fechamento por repetição.
import unittest

from torchbridge.models import (
    PANEL_SIDE,
    CLOSE_TAB_ASPECT,
    CLOSE_TAB_BOTTOM_FRACTION,
    CLOSE_TAB_TOP_FRACTION,
    Rect,
    both_panels_open,
    click_zone,
    close_tab_vertices,
    panel_width,
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
        w = panel_width(rect)
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
        inner = rect.left + panel_width(rect)
        depth = inner - min(x for x, _ in left)
        self.assertAlmostEqual(depth / height, CLOSE_TAB_ASPECT, places=6)

    # Hit-test do polígono: centro da aba dentro, canto externo fora.
    def test_point_in_polygon(self):
        rect = Rect(0, 0, 1920, 1080)
        left = close_tab_vertices(rect, "left")
        self.assertTrue(point_in_polygon(495, 318.6, left))   # perto da borda interna, no meio
        self.assertFalse(point_in_polygon(100, 318.6, left))  # longe, dentro do painel


if __name__ == "__main__":
    unittest.main()
