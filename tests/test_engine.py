# Testes do BridgeEngine: reset do estado da roda na borda de detecção do jogo.
from pathlib import Path
import tempfile
import unittest

from torchbridge.config import ConfigManager
from torchbridge.engine import BridgeEngine
from torchbridge.mathutils import cursor_delta
from torchbridge.models import ControllerState, Rect, SharedOverlayState


class FakeInjector:
    # Substituto do InputInjector: grava os eventos em vez de enviá-los ao Windows.
    def __init__(self) -> None:
        self.moved: list[tuple[int, int]] = []
        self.cursor = (100, 100)
        self.buttons: dict[str, bool] = {}

    def move(self, x: int, y: int) -> bool:
        self.moved.append((x, y))
        return True

    def cursor_position(self) -> tuple[int, int]:
        return self.cursor

    def key(self, name: str, down: bool) -> bool:
        self.buttons[name] = down
        return True

    def mouse_button(self, button: str, down: bool) -> bool:
        self.buttons[button] = down
        return True


class FakeHub:
    def rumble(self, *args: object) -> None:
        pass


class RadialSessionResetTests(unittest.TestCase):
    # Monta um engine sem subir a thread: ConfigManager em perfil temporário + estado compartilhado.
    def _make_engine(self, directory: str) -> tuple[BridgeEngine, SharedOverlayState]:
        config = ConfigManager(Path(directory) / "perfil.json")
        shared = SharedOverlayState()
        return BridgeEngine(config, shared), shared

    # Painel fantasma (sobrevivente de um Alt+F4) é limpo ao redetectar o jogo.
    def test_reset_clears_phantom_panels_and_selection(self):
        with tempfile.TemporaryDirectory() as directory:
            engine, shared = self._make_engine(directory)
            # Simula o estado de antes do encerramento forçado: painel direito aberto + setor 2.
            engine._active_panels = ["", "I"]
            engine._radial_selection = 2
            engine._reset_radial_session()
            self.assertEqual(engine._active_panels, ["", ""])
            self.assertIsNone(engine._radial_selection)
            # O overlay também precisa ver o estado limpo.
            self.assertEqual(shared.get().active_panels, ["", ""])
            self.assertIsNone(shared.get().radial_selection)

    # Sem painel aberto o reset é inócuo (seguro de rodar a cada detecção, inclusive na inicialização).
    def test_reset_is_noop_when_already_clean(self):
        with tempfile.TemporaryDirectory() as directory:
            engine, _ = self._make_engine(directory)
            engine._reset_radial_session()
            self.assertEqual(engine._active_panels, ["", ""])
            self.assertIsNone(engine._radial_selection)

    # ESC (binding de start) fecha os menus: todos os painéis abertos são esquecidos, não só um.
    def test_esc_resets_all_open_panels(self):
        with tempfile.TemporaryDirectory() as directory:
            engine, shared = self._make_engine(directory)
            engine._active_panels = ["C", "I"]
            engine._reset_active_panels()
            self.assertEqual(engine._active_panels, ["", ""])
            self.assertEqual(shared.get().active_panels, ["", ""])

    # ESC sem painel aberto não altera nada.
    def test_esc_reset_is_noop_when_no_panels_open(self):
        with tempfile.TemporaryDirectory() as directory:
            engine, shared = self._make_engine(directory)
            engine._reset_active_panels()
            self.assertEqual(engine._active_panels, ["", ""])
            self.assertEqual(shared.get().active_panels, ["", ""])


class LeftStickFreeCursorTests(unittest.TestCase):
    # Com os dois painéis abertos, o analógico esquerdo move o cursor livremente
    # (deslocamento relativo) e NÃO segura o clique do click-to-move.
    def test_both_panels_open_moves_cursor_without_click(self):
        with tempfile.TemporaryDirectory() as directory:
            config = ConfigManager(Path(directory) / "perfil.json")
            engine = BridgeEngine(config, SharedOverlayState())
            engine._mode = "direct"
            engine._active_panels = ["C", "I"]
            injector = FakeInjector()
            engine.injector = injector

            cfg = config.get()
            rect = Rect(0, 0, 1920, 1080)
            state = ControllerState(connected=True, lx=1.0, ly=0.0)

            engine._process_active(FakeHub(), state, rect, cfg, 0.0, 0.05)

            # O cursor andou a partir da posição atual (100, 100) em direção a +x...
            expected_dx, _ = cursor_delta(
                1.0, 0.0, float(cfg["cursor"]["speed_pixels_per_second"]), 0.05
            )
            self.assertEqual(injector.moved, [(100 + int(expected_dx), 100)])
            # ...e o clique esquerdo NÃO ficou retido (click-to-move desligado).
            self.assertFalse(injector.buttons.get("left", False))

    # Com só um painel aberto, o comportamento clássico permanece: movimento direto segura o clique.
    def test_single_panel_keeps_click_to_move(self):
        with tempfile.TemporaryDirectory() as directory:
            config = ConfigManager(Path(directory) / "perfil.json")
            engine = BridgeEngine(config, SharedOverlayState())
            engine._mode = "direct"
            engine._active_panels = ["I", ""]
            injector = FakeInjector()
            engine.injector = injector

            cfg = config.get()
            rect = Rect(0, 0, 1920, 1080)
            state = ControllerState(connected=True, lx=1.0, ly=0.0)

            engine._process_active(FakeHub(), state, rect, cfg, 0.0, 0.05)

            # Movimento direto saltou para o ponto-âncora e reteve o clique esquerdo.
            self.assertEqual(len(injector.moved), 1)
            self.assertTrue(injector.buttons.get("left", False))
