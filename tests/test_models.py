# Testes do rastreador de painéis (toggle_panel): abertura, troca de lateral e fechamento por repetição.
import unittest

from torchbridge.models import PANEL_SIDE, toggle_panel, panels_x_shift


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


if __name__ == "__main__":
    unittest.main()
