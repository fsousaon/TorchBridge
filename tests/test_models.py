# Testes do rastreador de painéis (toggle_panel): abertura, troca de lateral e fechamento por repetição.
import unittest

from torchbridge.models import PANEL_SIDE, toggle_panel


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


if __name__ == "__main__":
    unittest.main()
