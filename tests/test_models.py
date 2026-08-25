# Testes do rastreador de painéis (toggle_panel): abertura, troca de lateral e fechamento por repetição.
import unittest

from torchbridge.models import (
    PANEL_SIDE,
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
    # 1920x1080 → painel 504px; aba (pentágono) com borda interna em x=504 (esq.) / 1416
    # (dir.), y 291.6–345.6. Ponta no meio (y=318.6) em x≈473.76 (esq.) / ≈1446.24
    # (dir.), topo/base estreitos (x≈496.4 / ≈1423.6). Center x504–1415.
    def test_zones_on_1920x1080(self):
        rect = Rect(0, 0, 1920, 1080)
        self.assertEqual(click_zone(rect, 100, 100), "left")         # painel esq.
        self.assertEqual(click_zone(rect, 300, 318), "left")          # painel esq., longe da aba
        self.assertEqual(click_zone(rect, 498, 200), "left")          # acima da aba
        self.assertEqual(click_zone(rect, 498, 420), "left")          # abaixo da aba
        self.assertEqual(click_zone(rect, 500, 318), "close_left")    # dentro da aba esq.
        self.assertEqual(click_zone(rect, 478, 318), "close_left")    # perto da ponta esq.
        self.assertEqual(click_zone(rect, 960, 540), "center")
        self.assertEqual(click_zone(rect, 600, 540), "center")
        self.assertEqual(click_zone(rect, 1900, 100), "right")       # painel dir.
        self.assertEqual(click_zone(rect, 1480, 318), "right")       # painel dir., fora da aba
        self.assertEqual(click_zone(rect, 1420, 318), "close_right") # dentro da aba dir.
        self.assertEqual(click_zone(rect, 1444, 318), "close_right") # perto da ponta dir.

    # A aba é estreita no topo/base e larga no meio: ponto dentro na ponta (x=478,
    # y=318) fica FORA perto do topo (y=295), onde a borda estreita começa em x≈493.
    def test_tab_narrower_at_top(self):
        rect = Rect(0, 0, 1920, 1080)
        self.assertEqual(click_zone(rect, 478, 318), "close_left")  # na altura da ponta
        self.assertEqual(click_zone(rect, 478, 295), "left")        # perto do topo: fora

    # Fora da janela → 'outside'.
    def test_outside_window(self):
        rect = Rect(0, 0, 1920, 1080)
        self.assertEqual(click_zone(rect, 2000, 540), "outside")
        self.assertEqual(click_zone(rect, 960, -5), "outside")
        self.assertEqual(click_zone(rect, 960, 1100), "outside")

    # 4:3 (640x480): painel 224px, aba y 129.6–153.6, borda interna esq. x=224, dir. x=416.
    # Ponta esq. x≈210.6 (meio), dir. x≈429.4.
    def test_43_resolution_close_tab(self):
        rect = Rect(0, 0, 640, 480)
        self.assertEqual(click_zone(rect, 100, 100), "left")
        self.assertEqual(click_zone(rect, 218, 141), "close_left")   # dentro da aba esq.
        self.assertEqual(click_zone(rect, 205, 141), "left")         # fora (esq.) da aba
        self.assertEqual(click_zone(rect, 300, 200), "center")
        self.assertEqual(click_zone(rect, 422, 141), "close_right")  # dentro da aba dir.
        self.assertEqual(click_zone(rect, 435, 141), "right")        # fora (dir.) da aba

    # Janela deslocada na tela: zonas acompanham o retângulo (não a origem).
    def test_offset_window(self):
        rect = Rect(100, 50, 1920, 1080)
        self.assertEqual(click_zone(rect, 596, 368), "close_left")
        self.assertEqual(click_zone(rect, 100 + 960, 50 + 540), "center")
        self.assertEqual(click_zone(rect, 1524, 368), "close_right")


class CloseTabShapeTests(unittest.TestCase):
    # A aba é um pentágono: 5 vértices, base reta na borda interna, ponta no interior.
    def test_vertices_count_and_edges(self):
        rect = Rect(0, 0, 1920, 1080)
        left = close_tab_vertices(rect, "left")
        right = close_tab_vertices(rect, "right")
        self.assertEqual(len(left), 5)
        self.assertEqual(len(right), 5)
        # Base reta na borda interna: os dois cantos internos têm x == borda interna (504).
        self.assertEqual(left[0][0], rect.left + panel_width(rect))
        self.assertEqual(left[4][0], rect.left + panel_width(rect))
        # A ponta (vértice 2) fica no interior do painel (x menor que a borda interna).
        self.assertLess(left[2][0], rect.left + panel_width(rect))
        # Espelhamento: borda interna do direito = right - width.
        self.assertEqual(right[0][0], rect.right - panel_width(rect))
        self.assertGreater(right[2][0], rect.right - panel_width(rect))

    # Hit-test do polígono: centro da aba dentro, canto externo fora.
    def test_point_in_polygon(self):
        rect = Rect(0, 0, 1920, 1080)
        left = close_tab_vertices(rect, "left")
        self.assertTrue(point_in_polygon(495, 318.6, left))   # perto da borda interna, no meio
        self.assertFalse(point_in_polygon(100, 318.6, left))  # longe, dentro do painel


if __name__ == "__main__":
    unittest.main()
