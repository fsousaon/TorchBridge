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


class DirectMovementCircleTests(unittest.TestCase):
    # Raio do movimento direto referenciado à ALTURA nos dois eixos: em 1920x1080 com
    # raio 16%, o alcance máximo é 173 px da âncora (960, 507.6) em qualquer direção — círculo,
    # não elipse. Sem painel aberto o modo direto fica ativo.
    def test_max_reach_is_circular_based_on_height(self):
        with tempfile.TemporaryDirectory() as directory:
            config = ConfigManager(Path(directory) / "perfil.json")
            engine = BridgeEngine(config, SharedOverlayState())
            engine._mode = "direct"
            injector = FakeInjector()
            engine.injector = injector

            cfg = config.get()
            rect = Rect(0, 0, 1920, 1080)
            radius = 1080 * float(cfg["movement"]["movement_radius_percent"])
            anchor_x = 1920 * float(cfg["movement"]["anchor_x"])
            anchor_y = 1080 * float(cfg["movement"]["anchor_y"])

            def reach(direction: tuple[float, float]) -> tuple[int, int]:
                state = ControllerState(connected=True, lx=direction[0], ly=direction[1])
                engine._process_active(FakeHub(), state, rect, cfg, 0.0, 0.05)
                return injector.moved[-1]

            # Direita, esquerda, baixo e cima com stick cheio: a distância da âncora é a mesma.
            # Tolerância de 1 px: âncora y (507.6) e raio decimal não caem em inteiro.
            for dx, dy in ((1.0, 0.0), (-1.0, 0.0), (0.0, 1.0), (0.0, -1.0)):
                x, y = reach((dx, dy))
                dist = ((x - anchor_x) ** 2 + (y - anchor_y) ** 2) ** 0.5
                self.assertLessEqual(
                    abs(dist - radius), 1.0,
                    msg=f"Alcance na direção {(dx, dy)} = {dist:.1f} não bate com o raio circular {radius:.1f}",
                )
            # Diagonal (0.7071/0.7071 = unidade): mesmo raio, sem ovalizar.
            x, y = reach((0.7071, 0.7071))
            dist = ((x - anchor_x) ** 2 + (y - anchor_y) ** 2) ** 0.5
            self.assertLessEqual(
                abs(dist - radius), 1.0,
                msg=f"Alcance diagonal = {dist:.1f} não bate com o raio circular {radius:.1f}",
            )


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


class CenterClickPanelResetTests(unittest.TestCase):
    # Monta um engine com painel esquerdo+direito abertos e um injector fake.
    def _make_engine(self, directory: str, cursor: tuple[int, int]) -> tuple[BridgeEngine, SharedOverlayState, FakeInjector]:
        config = ConfigManager(Path(directory) / "perfil.json")
        shared = SharedOverlayState()
        engine = BridgeEngine(config, shared)
        engine._mode = "direct"
        engine._active_panels = ["C", "I"]
        injector = FakeInjector()
        injector.cursor = cursor
        engine.injector = injector
        return engine, shared, injector

    def _tick(self, engine: BridgeEngine, rt: float, directory: str) -> FakeInjector:
        cfg = engine.config.get()
        rect = Rect(0, 0, 1920, 1080)
        state = ControllerState(connected=True, rt=rt)
        engine._process_active(FakeHub(), state, rect, cfg, 0.0, 0.05)
        return engine.injector  # type: ignore[return-value]

    # Clique esquerdo (RT) na zona central com os dois painéis 'abertos' no estado: o jogo
    # fechou o menu, então o estado é esvaziado — o terceiro caminho de sincronização.
    def test_center_click_resets_panels(self):
        with tempfile.TemporaryDirectory() as directory:
            engine, shared, _ = self._make_engine(directory, cursor=(960, 540))
            self._tick(engine, rt=1.0, directory=directory)
            self.assertEqual(engine._active_panels, ["", ""])
            self.assertEqual(shared.get().active_panels, ["", ""])

    # Clique DENTRO do painel esquerdo (fora da caixa de fechar) não zera o estado
    # (o menu seguiu aberto no jogo).
    def test_panel_click_keeps_panels(self):
        with tempfile.TemporaryDirectory() as directory:
            engine, _, _ = self._make_engine(directory, cursor=(100, 100))
            self._tick(engine, rt=1.0, directory=directory)
            self.assertEqual(engine._active_panels, ["C", "I"])

    # Clique na aba de fechar ESQUERDA: fecha só o painel esquerdo, o direito permanece.
    # 1920x1080: aba esq. x≈473.76–504, y 291.6–345.6 → ponto (490, 318).
    def test_close_left_box_closes_only_left(self):
        with tempfile.TemporaryDirectory() as directory:
            engine, shared, _ = self._make_engine(directory, cursor=(490, 318))
            self._tick(engine, rt=1.0, directory=directory)
            self.assertEqual(engine._active_panels, ["", "I"])
            self.assertEqual(shared.get().active_panels, ["", "I"])

    # Clique na aba de fechar DIREITA: espelho — fecha só o painel direito.
    # Aba dir. x 1416–≈1446.24 → ponto (1430, 318).
    def test_close_right_box_closes_only_right(self):
        with tempfile.TemporaryDirectory() as directory:
            engine, _, _ = self._make_engine(directory, cursor=(1430, 318))
            self._tick(engine, rt=1.0, directory=directory)
            self.assertEqual(engine._active_panels, ["C", ""])

    # Clique na aba de fechar sem o correspondente painel aberto: nada muda.
    def test_close_box_noop_when_panel_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            engine, _, _ = self._make_engine(directory, cursor=(490, 318))
            engine._active_panels = ["", "I"]
            self._tick(engine, rt=1.0, directory=directory)
            self.assertEqual(engine._active_panels, ["", "I"])

    # Clique retido (sem nova borda de subida) não dispara o reset de novo: só o primeiro toque conta.
    def test_held_click_does_not_repeat_reset(self):
        with tempfile.TemporaryDirectory() as directory:
            engine, _, injector = self._make_engine(directory, cursor=(960, 540))
            # Primeiro toque no centro: reseta (estado já fica limpo).
            self._tick(engine, rt=1.0, directory=directory)
            self.assertEqual(engine._active_panels, ["", ""])
            # Simula um painel aberto de novo durante o clique ainda retido...
            engine._active_panels = ["I", ""]
            # ...e continua segurando RT no centro: sem borda, o estado NÃO é tocado.
            self._tick(engine, rt=1.0, directory=directory)
            self.assertEqual(engine._active_panels, ["I", ""])
            self.assertTrue(injector.buttons.get("left", False))
