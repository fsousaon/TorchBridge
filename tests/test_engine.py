# Testes do BridgeEngine: reset do estado da roda na borda de detecção do jogo.
from pathlib import Path
import tempfile
import unittest

from torchbridge.config import ConfigManager
from torchbridge.engine import BridgeEngine
from torchbridge.mathutils import cursor_delta, radial_deadzone
from torchbridge.models import ControllerState, Rect, SharedOverlayState


class FakeInjector:
    # Substituto do InputInjector: grava os eventos em vez de enviá-los ao Windows.
    def __init__(self) -> None:
        self.moved: list[tuple[int, int]] = []
        self.cursor = (100, 100)
        self.buttons: dict[str, bool] = {}
        self.tapped: list[str] = []

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

    def tap(self, name: str) -> bool:
        self.tapped.append(name)
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

    # Perto da deadzone o cursor fica junto da âncora (clique central do click-to-move):
    # o alcance em fração do raio deve ser ~click_center_fraction, não 55% como era antes.
    # Assim o clique cai perto do herói, longe de NPCs/inimigos, e o cursor só cresce
    # até o raio cheio quando o stick é empurrado até o fim.
    def test_low_stick_keeps_cursor_near_anchor(self):
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
            center_frac = float(cfg["movement"]["click_center_fraction"])
            dz = float(cfg["input"]["deadzone"])
            curve = float(cfg["input"]["response_curve"])

            # Stick levemente inclinado (mag 0.3, direção +x): o lmag vem da mesma função do engine.
            _, _, lmag = radial_deadzone(0.3, 0.0, dz, curve)
            self.assertGreater(lmag, 0.0)  # fora da deadzone, senão o teste é vazio
            expected_reach = center_frac + (1.0 - center_frac) * lmag

            state = ControllerState(connected=True, lx=0.3, ly=0.0)
            engine._process_active(FakeHub(), state, rect, cfg, 0.0, 0.05)
            x, y = injector.moved[-1]
            dist = ((x - anchor_x) ** 2 + (y - anchor_y) ** 2) ** 0.5

            # O cursor está na fração esperada do raio (1 px de folga p/ arredondamento).
            self.assertLessEqual(
                abs(dist - radius * expected_reach), 1.0,
                msg=f"Alcance {dist:.1f} não bate com o esperado {radius * expected_reach:.1f} (reach {expected_reach:.2f})",
            )
            # E, o que importa: bem mais perto do que o antigo salto de 55% do raio.
            self.assertLess(dist, radius * 0.55)

    # Ao soltar o stick (voltar para a deadzone), o cursor é devolvido à âncora do herói:
    # o próximo click-to-move nasce no centro e não cai em cima de um NPC/inimigo.
    def test_releasing_stick_returns_cursor_to_anchor(self):
        with tempfile.TemporaryDirectory() as directory:
            config = ConfigManager(Path(directory) / "perfil.json")
            engine = BridgeEngine(config, SharedOverlayState())
            engine._mode = "direct"
            injector = FakeInjector()
            engine.injector = injector

            cfg = config.get()
            rect = Rect(0, 0, 1920, 1080)
            anchor = (round(1920 * float(cfg["movement"]["anchor_x"])),
                      round(1080 * float(cfg["movement"]["anchor_y"])))

            # Tick 1: stick cheio para a direita -> movimento direto ativo, cursor no raio cheio.
            engine._process_active(FakeHub(), ControllerState(connected=True, lx=1.0, ly=0.0), rect, cfg, 0.0, 0.05)
            self.assertNotEqual(injector.moved[-1], anchor)  # cursor saiu da âncora
            self.assertTrue(engine._direct_move_active)

            # Tick 2: stick de volta ao centro (deadzone) -> nada move o cursor -> retorno à âncora.
            engine._process_active(FakeHub(), ControllerState(connected=True, lx=0.0, ly=0.0), rect, cfg, 0.0, 0.05)
            self.assertEqual(injector.moved[-1], anchor)
            self.assertFalse(engine._direct_move_active)

    # O retorno não deve sequestrar o cursor quando outro eixo está o movendo (cursor livre/direito).
    def test_return_does_not_override_active_cursor(self):
        with tempfile.TemporaryDirectory() as directory:
            config = ConfigManager(Path(directory) / "perfil.json")
            engine = BridgeEngine(config, SharedOverlayState())
            engine._mode = "direct"
            injector = FakeInjector()
            engine.injector = injector

            cfg = config.get()
            rect = Rect(0, 0, 1920, 1080)
            anchor = (round(1920 * float(cfg["movement"]["anchor_x"])),
                      round(1080 * float(cfg["movement"]["anchor_y"])))

            # Tick 1: movimento direto ativo.
            engine._process_active(FakeHub(), ControllerState(connected=True, lx=1.0, ly=0.0), rect, cfg, 0.0, 0.05)
            # Tick 2: esquerdo solto, DIREITO inclinado -> cursor livre ativo; o retorno deve ceder.
            engine._process_active(FakeHub(), ControllerState(connected=True, lx=0.0, ly=0.0, rx=1.0, ry=0.0), rect, cfg, 0.0, 0.05)
            self.assertNotEqual(injector.moved[-1], anchor)  # o cursor seguiu o direito, não voltou
            self.assertEqual(engine._direct_move_active, False)

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


class HudClickPanelResetTests(unittest.TestCase):
    # Clique na área verde da HUD (barra/ícones do rodapé) com os dois painéis abertos:
    # o jogador apertou um botão do jogo ali, então os painéis NÃO são zerados —
    # diferente do clique no centro vazio (que o jogo usa para fechar tudo).
    # Precisa da máscara real do SVG; sem o asset, os testes são pulados.
    @classmethod
    def setUpClass(cls):
        from torchbridge.models import load_hud_mask
        cls.mask = load_hud_mask()

    def _require_mask(self):
        if self.mask is None:
            self.skipTest("asset da HUD / Qt Svg indisponível neste ambiente")

    def _make_engine(self, directory: str, cursor: tuple[int, int]) -> tuple[BridgeEngine, SharedOverlayState, FakeInjector]:
        config = ConfigManager(Path(directory) / "perfil.json")
        shared = SharedOverlayState()
        engine = BridgeEngine(config, shared)
        engine._mode = "direct"
        engine._active_panels = ["C", "I"]
        # Publica o estado inicial (ambos abertos) no overlay: assim a assert de
        # shared verifica que o reset NUNCA publicou ['',''] (se o reset disparasse,
        # o engine chamaria shared.update(active_panels=['','']) e a assert pegaria).
        shared.update(active_panels=list(engine._active_panels))
        injector = FakeInjector()
        injector.cursor = cursor
        engine.injector = injector
        return engine, shared, injector

    def _tick(self, engine: BridgeEngine) -> None:
        cfg = engine.config.get()
        rect = Rect(0, 0, 1920, 1080)
        state = ControllerState(connected=True, rt=1.0)
        engine._process_active(FakeHub(), state, rect, cfg, 0.0, 0.05)

    # 1080p: barra de vida no centro da base (960, 1070) é verde no SVG → zona "hud".
    def test_hud_click_does_not_reset_panels(self):
        self._require_mask()
        with tempfile.TemporaryDirectory() as directory:
            engine, shared, _ = self._make_engine(directory, cursor=(960, 1070))
            self._tick(engine)
            self.assertEqual(engine._active_panels, ["C", "I"])
            self.assertEqual(shared.get().active_panels, ["C", "I"])

    # Ícone de habilidade no disco central (930, 1015 — dentro da metade esquerda do
    # círculo, verde no SVG) também é zona "hud" → não reseta. O centro exato (960) cai
    # na fenda vertical do disco (gap), que volta "center" (coberto no test_models).
    def test_hud_icon_click_does_not_reset_panels(self):
        self._require_mask()
        with tempfile.TemporaryDirectory() as directory:
            engine, _, _ = self._make_engine(directory, cursor=(930, 1015))
            self._tick(engine)
            self.assertEqual(engine._active_panels, ["C", "I"])

    # Clique no centro VAZIO (960, 540 — longe da HUD) com ambos abertos continua zerando.
    def test_center_click_still_resets(self):
        self._require_mask()
        with tempfile.TemporaryDirectory() as directory:
            engine, _, _ = self._make_engine(directory, cursor=(960, 540))
            self._tick(engine)
            self.assertEqual(engine._active_panels, ["", ""])


class PetSubmenuTests(unittest.TestCase):
    # Sublinha de pet actions (4 quadradinhos sob o slot 'P'): d-pad BAIXO abre, CIMA fecha,
    # e o d-pad inteiro deixa de disparar toques de tecla enquanto a roda (LB) está aberta.
    # Slots do perfil: ["I", "S", "Q", "J", "P", "C", "A"] — setor 5 = P, setor 6 = C.
    # Vetores que resolvem em cada setor (radial_slot: atan2(rx, -ry) a partir do topo):
    #   P (centro 205,7°): rx=-0.6, ry=0.8  → 216,9° → setor 5
    #   C (centro 257,1°): rx=-0.9, ry=0.2  → 257,4° → setor 6

    @staticmethod
    def _state(buttons: tuple[str, ...] = (), **kw) -> ControllerState:
        return ControllerState(connected=True, buttons=frozenset(buttons), **kw)

    def _engine(self, directory: str):
        config = ConfigManager(Path(directory) / "perfil.json")
        shared = SharedOverlayState()
        engine = BridgeEngine(config, shared)
        engine._mode = "direct"
        engine.injector = FakeInjector()
        return engine, shared

    def _tick(self, engine: BridgeEngine, directory: str, state: ControllerState) -> None:
        cfg = engine.config.get()
        rect = Rect(0, 0, 1920, 1080)
        engine._process_active(FakeHub(), state, rect, cfg, 0.0, 0.05)
        # O loop real (run()) registra o estado do tick em _previous no fim de cada ciclo;
        # aqui fazemos o mesmo para as bordas (subida/descida) funcionarem nos ticks seguintes.
        engine._previous = state

    # Roda aberta no setor P + d-pad BAIXO na borda: a sublinha abre e publica True.
    def test_dpad_down_on_pet_opens_submenu(self):
        with tempfile.TemporaryDirectory() as directory:
            engine, shared = self._engine(directory)
            self._tick(engine, directory, self._state(("lb",), rx=-0.6, ry=0.8))
            self._tick(engine, directory, self._state(("lb", "dpad_down"), rx=-0.6, ry=0.8))
            snap = shared.get()
            self.assertEqual(snap.radial_selection, 5)
            self.assertTrue(snap.pet_submenu_open)

    # Com a sublinha aberta, d-pad BAIXO não dispara o binding de tecla (7): o d-pad é da sublinha.
    def test_dpad_down_suppressed_tap_when_open(self):
        with tempfile.TemporaryDirectory() as directory:
            engine, _ = self._engine(directory)
            self._tick(engine, directory, self._state(("lb",), rx=-0.6, ry=0.8))
            self._tick(engine, directory, self._state(("lb", "dpad_down"), rx=-0.6, ry=0.8))
            self.assertNotIn("7", engine.injector.tapped)

    # Sem roda aberta o d-pad continua disparando a tecla configurada (comportamento antigo).
    def test_dpad_tap_still_works_without_wheel(self):
        with tempfile.TemporaryDirectory() as directory:
            engine, _ = self._engine(directory)
            self._tick(engine, directory, self._state(("dpad_down",)))
            self.assertIn("7", engine.injector.tapped)

    # Com a roda aberta em OUTRO setor (C = 6), d-pad BAIXO NÃO abre a sublinha do pet.
    def test_dpad_down_on_other_sector_does_not_open(self):
        with tempfile.TemporaryDirectory() as directory:
            engine, shared = self._engine(directory)
            self._tick(engine, directory, self._state(("lb",), rx=-0.9, ry=0.2))
            self._tick(engine, directory, self._state(("lb", "dpad_down"), rx=-0.9, ry=0.2))
            self.assertEqual(shared.get().radial_selection, 6)
            self.assertFalse(shared.get().pet_submenu_open)
            # E o toque de tecla do d-pad continua suprimido com a roda aberta.
            self.assertNotIn("7", engine.injector.tapped)

    # D-pad CIMA na borda fecha a sublinha aberta.
    def test_dpad_up_closes_submenu(self):
        with tempfile.TemporaryDirectory() as directory:
            engine, shared = self._engine(directory)
            self._tick(engine, directory, self._state(("lb",), rx=-0.6, ry=0.8))
            self._tick(engine, directory, self._state(("lb", "dpad_down"), rx=-0.6, ry=0.8))
            self.assertTrue(shared.get().pet_submenu_open)
            self._tick(engine, directory, self._state(("lb", "dpad_up"), rx=-0.6, ry=0.8))
            self.assertFalse(shared.get().pet_submenu_open)

    # Segurar o d-pad (sem nova borda) não reabre: o toggle só responde a toques.
    def test_held_dpad_does_not_retrigger(self):
        with tempfile.TemporaryDirectory() as directory:
            engine, shared = self._engine(directory)
            self._tick(engine, directory, self._state(("lb",), rx=-0.6, ry=0.8))
            self._tick(engine, directory, self._state(("lb", "dpad_down"), rx=-0.6, ry=0.8))
            self.assertTrue(shared.get().pet_submenu_open)
            # Segue segurando baixo nos ticks seguintes: nada muda.
            self._tick(engine, directory, self._state(("lb", "dpad_down"), rx=-0.6, ry=0.8))
            self._tick(engine, directory, self._state(("lb", "dpad_down"), rx=-0.6, ry=0.8))
            self.assertTrue(shared.get().pet_submenu_open)

    # Desmarcar o setor P (girar para C) esconde a sublinha sozinha.
    def test_deselecting_pet_hides_submenu(self):
        with tempfile.TemporaryDirectory() as directory:
            engine, shared = self._engine(directory)
            self._tick(engine, directory, self._state(("lb",), rx=-0.6, ry=0.8))
            self._tick(engine, directory, self._state(("lb", "dpad_down"), rx=-0.6, ry=0.8))
            self.assertTrue(shared.get().pet_submenu_open)
            # Gira para o setor C (índice 6): a sublinha some.
            self._tick(engine, directory, self._state(("lb",), rx=-0.9, ry=0.2))
            self.assertEqual(shared.get().radial_selection, 6)
            self.assertFalse(shared.get().pet_submenu_open)

    # Soltar LB (confirma o slot P) esconde a sublinha: roda fechada = sublinha inexistente.
    def test_releasing_lb_hides_submenu_and_confirms_slot(self):
        with tempfile.TemporaryDirectory() as directory:
            engine, shared = self._engine(directory)
            self._tick(engine, directory, self._state(("lb",), rx=-0.6, ry=0.8))
            self._tick(engine, directory, self._state(("lb", "dpad_down"), rx=-0.6, ry=0.8))
            self.assertTrue(shared.get().pet_submenu_open)
            # Solta LB no setor P: dispara o atalho 'P' e esconde a sublinha.
            self._tick(engine, directory, self._state(rx=-0.6, ry=0.8))
            self.assertIn("P", engine.injector.tapped)
            self.assertFalse(shared.get().pet_submenu_open)
            self.assertFalse(shared.get().radial_active)

    # Reabrir a roda no P depois de fechada: a sublinha começa FECHADA (nada gruda).
    def test_reopening_pet_starts_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            engine, shared = self._engine(directory)
            self._tick(engine, directory, self._state(("lb",), rx=-0.6, ry=0.8))
            self._tick(engine, directory, self._state(("lb", "dpad_down"), rx=-0.6, ry=0.8))
            self._tick(engine, directory, self._state(rx=-0.6, ry=0.8))  # fecha a roda
            self._tick(engine, directory, self._state(("lb",), rx=-0.6, ry=0.8))  # reabre no P
            self.assertEqual(shared.get().radial_selection, 5)
            self.assertFalse(shared.get().pet_submenu_open)

    # Pausa/interrupção (_release_all) zera a sublinha aberta.
    def test_release_all_closes_submenu(self):
        with tempfile.TemporaryDirectory() as directory:
            engine, shared = self._engine(directory)
            self._tick(engine, directory, self._state(("lb",), rx=-0.6, ry=0.8))
            self._tick(engine, directory, self._state(("lb", "dpad_down"), rx=-0.6, ry=0.8))
            self.assertTrue(shared.get().pet_submenu_open)
            engine._release_all()
            self.assertFalse(shared.get().pet_submenu_open)
            self.assertFalse(engine._pet_submenu)

    # Roda aberta sem seleção (stick no centro): d-pad não abre nada e não dispara teclas.
    def test_no_selection_no_submenu(self):
        with tempfile.TemporaryDirectory() as directory:
            engine, shared = self._engine(directory)
            self._tick(engine, directory, self._state(("lb", "dpad_down")))
            self.assertIsNone(shared.get().radial_selection)
            self.assertFalse(shared.get().pet_submenu_open)
            self.assertNotIn("7", engine.injector.tapped)
