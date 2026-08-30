# Testes do BridgeEngine: reset do estado da roda na borda de detecção do jogo.
import json
from pathlib import Path
import tempfile
import unittest

from torchbridge.config import ConfigManager
from torchbridge.engine import BridgeEngine
from torchbridge.mathutils import cursor_delta, radial_deadzone
from torchbridge.models import ControllerState, Rect, SharedOverlayState, pet_click_point


class FakeInjector:
    # Substituto do InputInjector: grava os eventos em vez de enviá-los ao Windows.
    # `events` registra a ORDEM cronológica de tudo (move, mouse, tap) — as asserts
    # de sequência (clique do pet: ida → down → up → retorno) usam essa lista.
    # O estado físico das teclas (`buttons`) modela GetAsyncKeyState: um KEYUP
    # "perdido" (drop_next_key_up) sai do SendInput mas NUNCA aplica a solta —
    # é a simulação do bug real do Shift residual (ago/2026).
    def __init__(self) -> None:
        self.moved: list[tuple[int, int]] = []
        self.cursor = (100, 100)
        self.buttons: dict[str, bool] = {}
        self.tapped: list[str] = []
        self.events: list[tuple] = []
        # Teclas cujo PRÓXIMO KEYUP é engolido (o OS não aplica a solta).
        self.drop_next_key_up: set[str] = set()
        # Teclas fisicamente presas (o OS engole TODO KEYUP — driver quebrado).
        self.stuck_keys: set[str] = set()

    def move(self, x: int, y: int) -> bool:
        self.moved.append((x, y))
        self.events.append(("move", x, y))
        return True

    def cursor_position(self) -> tuple[int, int]:
        return self.cursor

    def key(self, name: str, down: bool) -> bool:
        if not down and name in self.drop_next_key_up:
            # KEYUP perdido: o SendInput "aceitou" mas a solta nunca chegou.
            self.drop_next_key_up.discard(name)
            self.events.append(("key_dropped", name,))
            return True
        self.buttons[name] = down
        self.events.append(("key", name, down))
        return True

    # Estado físico da tecla (equivalente do GetAsyncKeyState no engine).
    def key_pressed(self, name: str) -> bool:
        if name in self.stuck_keys:
            return True
        return self.buttons.get(name, False)

    def mouse_button(self, button: str, down: bool) -> bool:
        self.buttons[button] = down
        self.events.append(("mouse", button, down))
        return True

    def tap(self, name: str) -> bool:
        self.tapped.append(name)
        self.events.append(("tap", name))
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

    def _tick(self, engine: BridgeEngine, a: bool, directory: str) -> FakeInjector:
        cfg = engine.config.get()
        rect = Rect(0, 0, 1920, 1080)
        state = ControllerState(connected=True, buttons=frozenset({"a"} if a else ()))
        engine._process_active(FakeHub(), state, rect, cfg, 0.0, 0.05)
        return engine.injector  # type: ignore[return-value]

    # Clique esquerdo (botão A) na zona central com os dois painéis 'abertos' no estado:
    # o jogo fechou o menu, então o estado é esvaziado — o terceiro caminho de sincronização.
    def test_center_click_resets_panels(self):
        with tempfile.TemporaryDirectory() as directory:
            engine, shared, _ = self._make_engine(directory, cursor=(960, 540))
            self._tick(engine, a=True, directory=directory)
            self.assertEqual(engine._active_panels, ["", ""])
            self.assertEqual(shared.get().active_panels, ["", ""])

    # Clique DENTRO do painel esquerdo (fora da caixa de fechar) não zera o estado
    # (o menu seguiu aberto no jogo).
    def test_panel_click_keeps_panels(self):
        with tempfile.TemporaryDirectory() as directory:
            engine, _, _ = self._make_engine(directory, cursor=(100, 100))
            self._tick(engine, a=True, directory=directory)
            self.assertEqual(engine._active_panels, ["C", "I"])

    # Clique na aba de fechar ESQUERDA: fecha só o painel esquerdo, o direito permanece.
    # 1920x1080: aba esq. x≈473.76–504, y 291.6–345.6 → ponto (490, 318).
    def test_close_left_box_closes_only_left(self):
        with tempfile.TemporaryDirectory() as directory:
            engine, shared, _ = self._make_engine(directory, cursor=(490, 318))
            self._tick(engine, a=True, directory=directory)
            self.assertEqual(engine._active_panels, ["", "I"])
            self.assertEqual(shared.get().active_panels, ["", "I"])

    # Clique na aba de fechar DIREITA: espelho — fecha só o painel direito.
    # Aba dir. x 1416–≈1446.24 → ponto (1430, 318).
    def test_close_right_box_closes_only_right(self):
        with tempfile.TemporaryDirectory() as directory:
            engine, _, _ = self._make_engine(directory, cursor=(1430, 318))
            self._tick(engine, a=True, directory=directory)
            self.assertEqual(engine._active_panels, ["C", ""])

    # Clique na aba de fechar sem o correspondente painel aberto: nada muda.
    def test_close_box_noop_when_panel_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            engine, _, _ = self._make_engine(directory, cursor=(490, 318))
            engine._active_panels = ["", "I"]
            self._tick(engine, a=True, directory=directory)
            self.assertEqual(engine._active_panels, ["", "I"])

    # Clique retido (sem nova borda de subida) não dispara o reset de novo: só o primeiro toque conta.
    def test_held_click_does_not_repeat_reset(self):
        with tempfile.TemporaryDirectory() as directory:
            engine, _, injector = self._make_engine(directory, cursor=(960, 540))
            # Primeiro toque no centro: reseta (estado já fica limpo).
            self._tick(engine, a=True, directory=directory)
            self.assertEqual(engine._active_panels, ["", ""])
            # Simula um painel aberto de novo durante o clique ainda retido...
            engine._active_panels = ["I", ""]
            # ...e continua segurando o A no centro: sem borda, o estado NÃO é tocado.
            self._tick(engine, a=True, directory=directory)
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
        state = ControllerState(connected=True, buttons=frozenset({"a"}))
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

    def _tick(
        self,
        engine: BridgeEngine,
        directory: str,
        state: ControllerState,
        now: float = 0.0,
        rect: "Rect | None" = None,
    ) -> None:
        cfg = engine.config.get()
        if rect is None:
            rect = Rect(0, 0, 1920, 1080)
        engine._process_active(FakeHub(), state, rect, cfg, now, 0.05)
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

    # D-pad BAIXO com a roda fechada NÃO dispara nenhum toque: o d-pad está sem
    # binding no overworld (o mapa antigo 5-8 morreu no remap — docs/REMAP-BOTOES).
    def test_dpad_noop_without_wheel(self):
        with tempfile.TemporaryDirectory() as directory:
            engine, _ = self._engine(directory)
            self._tick(engine, directory, self._state(("dpad_down",)))
            self.assertEqual(engine.injector.tapped, [])

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

    # Soltar LB fecha a roda SEM disparar nada (confirmação é do A): sublinha aberta,
    # soltar LB = desistência pura.
    def test_releasing_lb_closes_without_firing(self):
        with tempfile.TemporaryDirectory() as directory:
            engine, shared = self._engine(directory)
            self._tick(engine, directory, self._state(("lb",), rx=-0.6, ry=0.8))
            self._tick(engine, directory, self._state(("lb", "dpad_down"), rx=-0.6, ry=0.8))
            self.assertTrue(shared.get().pet_submenu_open)
            # Solta LB no setor P com a sublinha aberta: fecha tudo, NÃO dispara o atalho.
            self._tick(engine, directory, self._state(rx=-0.6, ry=0.8))
            self.assertNotIn("P", engine.injector.tapped)
            self.assertFalse(shared.get().pet_submenu_open)
            self.assertFalse(shared.get().radial_active)

    # Botão A com a roda aberta (sem sublinha) confirma: dispara o atalho do setor,
    # alterna o painel e fecha a roda — com o LB AINDA segurado (latch).
    def test_a_confirms_slot_and_closes_wheel(self):
        with tempfile.TemporaryDirectory() as directory:
            engine, shared = self._engine(directory)
            # Setor 1 (topo) = I: rx=0, ry negativo.
            self._tick(engine, directory, self._state(("lb",), rx=0.0, ry=-0.6))
            self.assertEqual(shared.get().radial_selection, 1)
            # A com o LB ainda segurado: confirma o I.
            self._tick(engine, directory, self._state(("lb", "a"), rx=0.0, ry=-0.6))
            self.assertIn("I", engine.injector.tapped)
            self.assertIn("I", shared.get().active_panels)
            # Roda fechou (latch) mesmo com o LB pressionado.
            self.assertFalse(shared.get().radial_active)
            self.assertIsNone(shared.get().radial_selection)
            # E o clique esquerdo NÃO seguiu junto (A confirma, não clica).
            self.assertFalse(engine.injector.buttons.get("left", False))

    # A com a sublinha ABERTA: fecha a sublinha E a roda, sem disparar o atalho do P.
    def test_a_with_submenu_closes_wheel_without_firing(self):
        with tempfile.TemporaryDirectory() as directory:
            engine, shared = self._engine(directory)
            self._tick(engine, directory, self._state(("lb",), rx=-0.6, ry=0.8))
            self._tick(engine, directory, self._state(("lb", "dpad_down"), rx=-0.6, ry=0.8))
            self.assertTrue(shared.get().pet_submenu_open)
            self._tick(engine, directory, self._state(("lb", "a"), rx=-0.6, ry=0.8))
            self.assertNotIn("P", engine.injector.tapped)
            self.assertFalse(shared.get().pet_submenu_open)
            self.assertFalse(shared.get().radial_active)
            self.assertNotIn("1", engine.injector.tapped)

    # A com a sublinha aberta E painel ESQUERDO aberto: a letra do painel dispara NA
    # HORA da confirmação (fecha o painel no jogo) e o clique do pet só sai DEPOIS dos
    # 500 ms de espera da animação (testado em PetClickSequenceTests). O índice 0 já
    # zera na confirmação.
    def test_a_with_submenu_closes_open_left_panel_first(self):
        with tempfile.TemporaryDirectory() as directory:
            engine, shared = self._engine(directory)
            self._tick(engine, directory, self._state(("lb",), rx=-0.6, ry=0.8), now=0.0)
            self._tick(engine, directory, self._state(("lb", "dpad_down"), rx=-0.6, ry=0.8), now=0.05)
            # Painel esquerdo (C) está aberto antes da confirmação.
            engine._active_panels = ["C", ""]
            shared.update(active_panels=["C", ""])
            # A confirma: a sequência arma, o índice 0 zera e a letra "C" sai NA HORA.
            self._tick(engine, directory, self._state(("lb", "a"), rx=-0.6, ry=0.8), now=0.10)
            self.assertEqual(engine._active_panels[0], "")
            self.assertEqual(shared.get().active_panels[0], "")
            self.assertNotIn("P", engine.injector.tapped)
            self.assertFalse(shared.get().pet_submenu_open)
            self.assertFalse(shared.get().radial_active)
            self.assertIn("C", engine.injector.tapped)
            # A sequência segue viva (fase de espera de 500 ms) e o cursor não se mexe
            # até a animação de fechamento terminar.
            self.assertIsNotNone(engine._pet_click_seq)
            self.assertEqual(engine.injector.moved, [])
            # Encerra a sequência (fim do caminho com painel em t=0,74): retorno ao centro.
            self._tick(engine, directory, self._state(), now=0.85)
            self.assertIsNone(engine._pet_click_seq)

    # A com a sublinha aberta E painel esquerdo FECHADO: nenhum toque extra, só fecha tudo.
    def test_a_with_submenu_no_left_panel_no_extra_tap(self):
        with tempfile.TemporaryDirectory() as directory:
            engine, shared = self._engine(directory)
            self._tick(engine, directory, self._state(("lb",), rx=-0.6, ry=0.8))
            self._tick(engine, directory, self._state(("lb", "dpad_down"), rx=-0.6, ry=0.8))
            self.assertEqual(engine._active_panels, ["", ""])
            self._tick(engine, directory, self._state(("lb", "a"), rx=-0.6, ry=0.8))
            # Só o A confirmou; nada extra foi disparado.
            self.assertEqual(engine.injector.tapped, [])
            self.assertEqual(engine._active_panels, ["", ""])
            # E a sequência termina sem tocar em tecla (painel pendente = None).
            self._tick(engine, directory, self._state(), now=0.41)
            self.assertEqual(engine.injector.tapped, [])
            self.assertIsNone(engine._pet_click_seq)

    # A com a roda aberta NÃO segura o clique esquerdo: o toque é da confirmação.
    def test_a_key_tap_suppressed_when_wheel_open(self):
        with tempfile.TemporaryDirectory() as directory:
            engine, _ = self._engine(directory)
            self._tick(engine, directory, self._state(("lb",), rx=0.0, ry=-0.6))
            self._tick(engine, directory, self._state(("lb", "a"), rx=0.0, ry=-0.6))
            self.assertFalse(engine.injector.buttons.get("left", False))

    # Após confirmar com o A (roda fechada via latch, LB ainda segurado), o stick NÃO
    # ressuscita a seleção; soltar o LB e apertar de novo reabre a roda do zero.
    def test_wheel_stays_closed_while_lb_held_after_confirm(self):
        with tempfile.TemporaryDirectory() as directory:
            engine, shared = self._engine(directory)
            self._tick(engine, directory, self._state(("lb",), rx=0.0, ry=-0.6))
            self._tick(engine, directory, self._state(("lb", "a"), rx=0.0, ry=-0.6))
            # LB continua segurado apontando no setor I: roda segue fechada, sem seleção.
            self._tick(engine, directory, self._state(("lb",), rx=0.0, ry=-0.6))
            self.assertFalse(shared.get().radial_active)
            self.assertIsNone(shared.get().radial_selection)
            # Solta e aperta de novo: reabre normalmente.
            self._tick(engine, directory, self._state(rx=0.0, ry=-0.6))
            self._tick(engine, directory, self._state(("lb",), rx=0.0, ry=-0.6))
            self.assertTrue(shared.get().radial_active)
            self.assertEqual(shared.get().radial_selection, 1)

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

    # Regressão: com a alavanca de volta ao CENTRO (radial_slot → None), o setor persistido
    # segue valendo — d-pad BAIXO abre a sublinha sem manter o stick inclinado no P.
    def test_stick_at_center_still_opens_submenu(self):
        with tempfile.TemporaryDirectory() as directory:
            engine, shared = self._engine(directory)
            self._tick(engine, directory, self._state(("lb",), rx=-0.6, ry=0.8))
            # Alavanca relaxa para o centro (rx=ry=0): o P segue selecionado.
            self._tick(engine, directory, self._state(("lb",), rx=0.0, ry=0.0))
            self.assertEqual(shared.get().radial_selection, 5)
            self._tick(engine, directory, self._state(("lb", "dpad_down"), rx=0.0, ry=0.0))
            self.assertTrue(shared.get().pet_submenu_open)

    # Com a sublinha aberta e stick no centro, d-pad CIMA continua fechando.
    def test_stick_at_center_still_closes_submenu(self):
        with tempfile.TemporaryDirectory() as directory:
            engine, shared = self._engine(directory)
            self._tick(engine, directory, self._state(("lb",), rx=-0.6, ry=0.8))
            self._tick(engine, directory, self._state(("lb",), rx=0.0, ry=0.0))
            self._tick(engine, directory, self._state(("lb", "dpad_down"), rx=0.0, ry=0.0))
            self.assertTrue(shared.get().pet_submenu_open)
            self._tick(engine, directory, self._state(("lb", "dpad_up"), rx=0.0, ry=0.0))
            self.assertFalse(shared.get().pet_submenu_open)

    # Ao abrir, o marcador nasce no quadrado 4 (default, embaixo do ícone P).
    def test_marker_starts_on_square_4(self):
        with tempfile.TemporaryDirectory() as directory:
            engine, shared = self._engine(directory)
            self._tick(engine, directory, self._state(("lb",), rx=-0.6, ry=0.8))
            self._tick(engine, directory, self._state(("lb", "dpad_down"), rx=-0.6, ry=0.8))
            self.assertTrue(shared.get().pet_submenu_open)
            self.assertEqual(shared.get().pet_submenu_selection, 4)

    # D-pad DIRITO anda 1 quadrado à direita com wrap: 4 → 1 → 2.
    def test_dpad_right_moves_marker_with_wrap(self):
        with tempfile.TemporaryDirectory() as directory:
            engine, shared = self._engine(directory)
            self._tick(engine, directory, self._state(("lb",), rx=-0.6, ry=0.8))
            self._tick(engine, directory, self._state(("lb", "dpad_down"), rx=-0.6, ry=0.8))
            # Do 4º, direita dá a volta e cai no 1º. Toque = borda: pressiona e solta.
            self._tick(engine, directory, self._state(("lb", "dpad_right"), rx=-0.6, ry=0.8))
            self._tick(engine, directory, self._state(("lb",), rx=-0.6, ry=0.8))
            self.assertEqual(shared.get().pet_submenu_selection, 1)
            self._tick(engine, directory, self._state(("lb", "dpad_right"), rx=-0.6, ry=0.8))
            self.assertEqual(shared.get().pet_submenu_selection, 2)

    # D-pad ESQUERDO anda 1 quadrado à esquerda com wrap: 4 → 3 → 2 → 1 → 4.
    def test_dpad_left_moves_marker_with_wrap(self):
        with tempfile.TemporaryDirectory() as directory:
            engine, shared = self._engine(directory)
            self._tick(engine, directory, self._state(("lb",), rx=-0.6, ry=0.8))
            self._tick(engine, directory, self._state(("lb", "dpad_down"), rx=-0.6, ry=0.8))
            for expected in (3, 2, 1, 4):
                self._tick(engine, directory, self._state(("lb", "dpad_left"), rx=-0.6, ry=0.8))
                self._tick(engine, directory, self._state(("lb",), rx=-0.6, ry=0.8))
                self.assertEqual(shared.get().pet_submenu_selection, expected)

    # Sublinha FECHADA: d-pad ESQUERDO/DIRITO NÃO move marcador (None) e também não
    # dispara as teclas 6/8 (roda aberta suprime o d-pad inteiro).
    def test_dpad_horizontal_no_effect_when_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            engine, shared = self._engine(directory)
            self._tick(engine, directory, self._state(("lb",), rx=-0.6, ry=0.8))
            self._tick(engine, directory, self._state(("lb", "dpad_right"), rx=-0.6, ry=0.8))
            self.assertIsNone(shared.get().pet_submenu_selection)
            self.assertFalse(shared.get().pet_submenu_open)
            self.assertNotIn("6", engine.injector.tapped)
            self.assertNotIn("8", engine.injector.tapped)

    # Fechar (d-pad cima) e reabrir: o marcador volta pro 4, não gruda onde parou.
    def test_reopen_resets_marker_to_default(self):
        with tempfile.TemporaryDirectory() as directory:
            engine, shared = self._engine(directory)
            self._tick(engine, directory, self._state(("lb",), rx=-0.6, ry=0.8))
            self._tick(engine, directory, self._state(("lb", "dpad_down"), rx=-0.6, ry=0.8))
            self._tick(engine, directory, self._state(("lb", "dpad_right"), rx=-0.6, ry=0.8))
            self.assertEqual(shared.get().pet_submenu_selection, 1)
            self._tick(engine, directory, self._state(("lb", "dpad_up"), rx=-0.6, ry=0.8))
            self.assertIsNone(shared.get().pet_submenu_selection)
            self._tick(engine, directory, self._state(("lb", "dpad_down"), rx=-0.6, ry=0.8))
            self.assertEqual(shared.get().pet_submenu_selection, 4)

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


class PetClickSequenceTests(unittest.TestCase):
    # Sequência de clique das ações do pet: A com a sublinha aberta arma o cursor,
    # que vai até o botão na caixinha do pet, clica (left down/up) e volta ao centro —
    # com os sticks BLOQUEADOS durante todo o trajeto. A ordem cronológica dos eventos
    # (movimento, descida, subida, retorno, letra do painel) é o contrato testado aqui.
    # Vetor do setor P (radial_slot): rx=-0.6, ry=0.8 → setor 5.
    #
    # Cronograma das fases (t = now - t0, com t0 = 0,10 no armar):
    #   t < 0,12 (now < 0,22)   -> movimento até o botão (fase de ida)
    #   0,12..0,21 (0,22..0,31) -> mouse LEFT DOWN
    #   0,21..0,24 (0,31..0,34) -> mouse LEFT UP
    #   0,24..0,30 (0,34..0,40) -> letra do painel pendente
    #   t >= 0,30 (now >= 0,40) -> fim: garante tudo + retorno ao centro
    # Os testes usam now no MEIO de cada fase: 0,15 / 0,25 / 0,32 / 0,36 / 0,41.

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

    def _tick(
        self,
        engine: BridgeEngine,
        state: ControllerState,
        now: float,
        rect: "Rect | None" = None,
    ) -> None:
        cfg = engine.config.get()
        if rect is None:
            rect = Rect(0, 0, 1920, 1080)
        engine._process_active(FakeHub(), state, rect, cfg, now, 0.05)
        engine._previous = state

    # Prepara: roda aberta no P, sublinha aberta, âncora conhecida.
    def _prep(self, engine, directory, square: int = 4, anchor=(960, 500)):
        self._tick(engine, self._state(("lb",), rx=-0.6, ry=0.8), 0.0)
        self._tick(engine, self._state(("lb", "dpad_down"), rx=-0.6, ry=0.8), 0.05)
        self.assertTrue(engine._pet_submenu)
        if square != 4:
            # Navega o marcador até o quadrado pedido (direito = +1 com wrap):
            # do 4, 1 passo→1, 2→2, 3→3 (4 passos voltam ao 4).
            for _ in range(square):
                self._tick(engine, self._state(("lb", "dpad_right"), rx=-0.6, ry=0.8), 0.06)
                self._tick(engine, self._state(("lb",), rx=-0.6, ry=0.8), 0.07)
        engine._last_anchor = anchor
        return engine

    # A confirma com o quadrado 4 (vendedor): o cursor vai ao ponto do quadrado amarelo
    # (pet_click_point 4), clica e volta à âncora — nessa ordem.
    def test_confirm_moves_clicks_returns(self):
        with tempfile.TemporaryDirectory() as directory:
            engine, shared = self._engine(directory)
            anchor = (960, 500)
            self._prep(engine, directory, square=4, anchor=anchor)
            rect = Rect(0, 0, 1920, 1080)
            target = pet_click_point(rect, 4)
            # A confirma (t0=0,10): movimento até o botão já sai neste tick.
            self._tick(engine, self._state(("lb", "a"), rx=-0.6, ry=0.8), 0.10)
            self.assertEqual(engine.injector.moved[0], target)
            # Desce o clique (fase down, now=0,25).
            self._tick(engine, self._state(), 0.25)
            self.assertIn(("mouse", "left", True), engine.injector.events)
            # Sobe (fase up, now=0,32) — o retorno AINDA não aconteceu.
            self._tick(engine, self._state(), 0.32)
            self.assertIn(("mouse", "left", False), engine.injector.events)
            self.assertNotIn(anchor, engine.injector.moved)
            # Fim (now=0,41): retorno ao centro e fim da sequência.
            self._tick(engine, self._state(), 0.41)
            self.assertEqual(engine.injector.moved[-1], anchor)
            self.assertIsNone(engine._pet_click_seq)
            # Nada disparou o atalho do P.
            self.assertNotIn("P", engine.injector.tapped)
            # Ordem cronológica (na MESMA lista de eventos): ida → down → up → retorno.
            events = engine.injector.events
            down = next(i for i, e in enumerate(events) if e == ("mouse", "left", True))
            up = next(i for i, e in enumerate(events) if e == ("mouse", "left", False))
            first_go = events.index(("move",) + tuple(target))
            last_return = events.index(("move",) + tuple(anchor))
            self.assertLess(first_go, down)
            self.assertLess(down, up)
            self.assertGreaterEqual(last_return, up)

    # Cada quadrado leva o cursor ao ponto CERTO (1=vermelho, 2=azul, 3=branco, 4=amarelo).
    def test_each_square_targets_its_button(self):
        rect = Rect(0, 0, 1920, 1080)
        for square in (1, 2, 3, 4):
            with tempfile.TemporaryDirectory() as directory:
                engine, _ = self._engine(directory)
                self._prep(engine, directory, square=square, anchor=(960, 500))
                self._tick(engine, self._state(("lb", "a"), rx=-0.6, ry=0.8), 0.10)
                self.assertEqual(engine.injector.moved[0], pet_click_point(rect, square))

    # Durante a sequência os sticks NÃO movem o cursor: o único movimento até o fim é o
    # da sequência (ida + retorno) — nenhum movimento intermediário do analógico.
    def test_sticks_blocked_during_sequence(self):
        with tempfile.TemporaryDirectory() as directory:
            engine, _ = self._engine(directory)
            anchor = (960, 500)
            self._prep(engine, directory, square=4, anchor=anchor)
            rect = Rect(0, 0, 1920, 1080)
            target = pet_click_point(rect, 4)
            self._tick(engine, self._state(("lb", "a"), rx=-0.6, ry=0.8), 0.10)
            # Nos ticks seguintes o stick esquerdo está inclinado (lmag > 0) — se o
            # bloqueio falhasse, o movimento direto moveria o cursor de novo.
            for t in (0.15, 0.18, 0.25, 0.32):
                self._tick(engine, self._state(lx=0.5, ly=0.0), t)
            # Fim da sequência (t=0,26) com o stick já SOLTO: o retorno ao centro é o
            # último movimento (com o stick ainda inclinado, o controle volta ao
            # jogador e o movimento direto retoma — o que é o comportamento correto).
            self._tick(engine, self._state(), 0.36)
            # Movimentos: ida no armar (target) + reafirmação na fase de ida (target, x2)
            # + retorno final (anchor). NENHUM do stick.
            self.assertEqual(engine.injector.moved, [target, target, target, anchor])

    # Painel esquerdo aberto: a letra dispara NA HORA (fecha o painel no jogo) e o
    # clique só sai DEPOIS dos 500 ms de espera da animação (PET_PANEL_CLOSE_DELAY) —
    # com o painel ainda abrindo na tela o botão do pet não recebe o clique.
    def test_pending_panel_closes_first_then_clicks_after_delay(self):
        with tempfile.TemporaryDirectory() as directory:
            engine, _ = self._engine(directory)
            self._prep(engine, directory, square=4, anchor=(960, 500))
            rect = Rect(0, 0, 1920, 1080)
            target = pet_click_point(rect, 4)
            engine._active_panels = ["C", ""]
            self._tick(engine, self._state(("lb", "a"), rx=-0.6, ry=0.8), 0.10)
            # A letra "C" saiu NA HORA e zerou o índice 0 (o jogo fecha o painel)...
            self.assertIn("C", engine.injector.tapped)
            self.assertEqual(engine._active_panels[0], "")
            # ...mas o cursor AINDA não se mexeu (fase de espera de 500 ms).
            self.assertEqual(engine.injector.moved, [])
            self._tick(engine, self._state(), 0.30)  # t=0,20: ainda esperando
            self._tick(engine, self._state(), 0.40)  # t=0,30: ainda esperando
            self.assertEqual(engine.injector.moved, [])
            # t=0,50: fim da espera — o cursor vai até o botão (ida).
            self._tick(engine, self._state(), 0.60)
            self.assertEqual(engine.injector.moved, [target])
            # t=0,63: down (fase de descida começa em t=0,62).
            self._tick(engine, self._state(), 0.73)
            self.assertIn(("mouse", "left", True), engine.injector.events)
            # t=0,71: up (fase de subida começa em t=0,71).
            self._tick(engine, self._state(), 0.82)
            self.assertIn(("mouse", "left", False), engine.injector.events)
            # t=0,75: fim (começa em t=0,74) — retorno ao centro.
            self._tick(engine, self._state(), 0.85)
            self.assertEqual(engine.injector.moved[-1], (960, 500))
            self.assertIsNone(engine._pet_click_seq)
            # Ordem cronológica: letra → ida → down → up → retorno.
            events = engine.injector.events
            tap = events.index(("tap", "C"))
            first_go = events.index(("move",) + tuple(target))
            down = next(i for i, e in enumerate(events) if e == ("mouse", "left", True))
            up = next(i for i, e in enumerate(events) if e == ("mouse", "left", False))
            self.assertLess(tap, first_go)
            self.assertLess(first_go, down)
            self.assertLess(down, up)
            self.assertGreaterEqual(events.index(("move",) + tuple((960, 500))), up)

    # Sem painel aberto o caminho rápido continua ~300 ms (a espera de 500 ms não se aplica).
    def test_no_panel_keeps_fast_path(self):
        with tempfile.TemporaryDirectory() as directory:
            engine, _ = self._engine(directory)
            self._prep(engine, directory, square=4, anchor=(960, 500))
            rect = Rect(0, 0, 1920, 1080)
            target = pet_click_point(rect, 4)
            self._tick(engine, self._state(("lb", "a"), rx=-0.6, ry=0.8), 0.10)
            self.assertEqual(engine.injector.moved[0], target)  # ida já no armar
            self._tick(engine, self._state(), 0.22)  # t=0,12: down
            self.assertIn(("mouse", "left", True), engine.injector.events)
            self._tick(engine, self._state(), 0.32)  # t=0,22: up
            self.assertIn(("mouse", "left", False), engine.injector.events)
            self._tick(engine, self._state(), 0.42)  # t=0,32: fim
            self.assertIsNone(engine._pet_click_seq)
            # Total ~320 ms (arma em 0,10, fim em 0,42) — longe dos ~820 ms do caminho com painel.

    # Interrupção (_release_all) no meio da sequência: solta o botão esquerdo e descarta.
    def test_release_all_cancels_in_flight(self):
        with tempfile.TemporaryDirectory() as directory:
            engine, _ = self._engine(directory)
            self._prep(engine, directory, square=4, anchor=(960, 500))
            self._tick(engine, self._state(("lb", "a"), rx=-0.6, ry=0.8), 0.10)
            self._tick(engine, self._state(), 0.25)  # botão já baixado
            self.assertTrue(engine._pet_click_down)
            engine._release_all()
            self.assertIsNone(engine._pet_click_seq)
            self.assertFalse(engine._pet_click_down)
            # O último evento de mouse é o soltar (nada fica retido).
            mouse_events = [e for e in engine.injector.events if e[0] == "mouse"]
            self.assertEqual(mouse_events[-1], ("mouse", "left", False))

    # Sequência terminada: o próximo A com sublinha reaberta arma de novo do zero (estado
    # de fase rearmado — não herda o "down" ou "panel_done" da anterior).
    def test_second_sequence_is_fresh(self):
        with tempfile.TemporaryDirectory() as directory:
            engine, shared = self._engine(directory)
            self._prep(engine, directory, square=4, anchor=(960, 500))
            self._tick(engine, self._state(("lb", "a"), rx=-0.6, ry=0.8), 0.10)
            self._tick(engine, self._state(), 0.25)  # down
            self._tick(engine, self._state(), 0.41)  # fim
            self.assertIsNone(engine._pet_click_seq)
            # Reabre roda + sublinha e confirma de novo (t0=0,55): a sequência roda outra vez.
            self._tick(engine, self._state(("lb",), rx=-0.6, ry=0.8), 0.45)
            self._tick(engine, self._state(("lb", "dpad_down"), rx=-0.6, ry=0.8), 0.50)
            events_before = len(engine.injector.events)
            self._tick(engine, self._state(("lb", "a"), rx=-0.6, ry=0.8), 0.55)
            self._tick(engine, self._state(), 0.70)  # down da segunda sequência
            self._tick(engine, self._state(), 0.86)  # fim da segunda
            # Houve mais um ciclo completo de clique (down + up) depois do primeiro.
            downs = [i for i, e in enumerate(engine.injector.events) if e == ("mouse", "left", True)]
            self.assertEqual(len(downs), 2)
            self.assertGreater(downs[1], events_before)
            ups = [i for i, e in enumerate(engine.injector.events) if e == ("mouse", "left", False)]
            self.assertEqual(len(ups), 2)
            self.assertIsNone(engine._pet_click_seq)


class FaceButtonsMouseTests(unittest.TestCase):
    # A/X viraram os botões do mouse (ago/2026): A = clique ESQUERDO, X = clique
    # DIREITO. As três famílias (cruz/A/A-nintendo e quadrado/X/X-nintendo) chegam
    # já normalizadas como "a"/"x" pelo SDL (BUTTONS em controller.py), então os
    # testes usam os nomes lógicos. Enquanto LB está segurado (roda de atalhos)
    # nenhum dos dois pode vazar clique de mouse no jogo.

    @staticmethod
    def _state(buttons: tuple[str, ...] = (), **kw) -> ControllerState:
        return ControllerState(connected=True, buttons=frozenset(buttons), **kw)

    def _engine(self, directory: str) -> tuple[BridgeEngine, SharedOverlayState, FakeInjector]:
        config = ConfigManager(Path(directory) / "perfil.json")
        shared = SharedOverlayState()
        engine = BridgeEngine(config, shared)
        engine._mode = "direct"
        engine._active_panels = ["", ""]
        injector = FakeInjector()
        injector.cursor = (960, 540)  # centro da janela: zona "center"
        engine.injector = injector
        return engine, shared, injector

    def _tick(self, engine: BridgeEngine, state: ControllerState, now: float = 0.0) -> None:
        cfg = engine.config.get()
        rect = Rect(0, 0, 1920, 1080)
        engine._process_active(FakeHub(), state, rect, cfg, now, 0.05)
        engine._previous = state

    # A apertado: clique esquerdo retido; soltar A: libera (e nada do direito).
    def test_a_maps_to_left_click(self):
        with tempfile.TemporaryDirectory() as directory:
            engine, _, injector = self._engine(directory)
            self._tick(engine, self._state(("a",)))
            self.assertTrue(injector.buttons.get("left", False))
            self.assertFalse(injector.buttons.get("right", False))
            self._tick(engine, self._state())
            self.assertFalse(injector.buttons.get("left", False))

    # X apertado: clique direito retido; soltar X: libera (e nada do esquerdo).
    def test_x_maps_to_right_click(self):
        with tempfile.TemporaryDirectory() as directory:
            engine, _, injector = self._engine(directory)
            self._tick(engine, self._state(("x",)))
            self.assertTrue(injector.buttons.get("right", False))
            self.assertFalse(injector.buttons.get("left", False))
            self._tick(engine, self._state())
            self.assertFalse(injector.buttons.get("right", False))

    # R2/LT não clicam mais em nada (o mapeamento gatilho→mouse morreu junto com
    # trigger_threshold).
    def test_triggers_do_not_click(self):
        with tempfile.TemporaryDirectory() as directory:
            engine, _, injector = self._engine(directory)
            for rt, lt in ((1.0, 0.0), (0.0, 1.0), (1.0, 1.0)):
                self._tick(engine, self._state(rt=rt, lt=lt))
            self.assertFalse(injector.buttons.get("left", False))
            self.assertFalse(injector.buttons.get("right", False))

    # A com a roda aberta no setor P: confirma o slot (tapa "P", abre o painel) e
    # NÃO aperta o botão esquerdo do mouse.
    def test_a_confirms_slot_without_left_click(self):
        with tempfile.TemporaryDirectory() as directory:
            engine, _, injector = self._engine(directory)
            # Setor P (slot 5, "P" = painel esquerdo): vetor rx=-0.6, ry=0.8.
            self._tick(engine, self._state(("lb",), rx=-0.6, ry=0.8))
            self._tick(engine, self._state(("lb", "a"), rx=-0.6, ry=0.8))
            self.assertEqual(injector.tapped, ["P"])
            self.assertEqual(engine._active_panels, ["P", ""])
            self.assertFalse(injector.buttons.get("left", False))
            self.assertFalse(injector.buttons.get("right", False))

    # A confirmando o quadrado do pet: o ÚNICO clique esquerdo é o da sequência no
    # botão do pet — o botão esquerdo do cursor nunca é retido (LB ainda segurado
    # na confirmação e a sequência injeta o clique direto, fora de _held_mouse).
    def test_pet_confirm_does_not_leak_cursor_click(self):
        with tempfile.TemporaryDirectory() as directory:
            engine, _, injector = self._engine(directory)
            self._tick(engine, self._state(("lb",), rx=-0.6, ry=0.8))
            self._tick(engine, self._state(("lb", "dpad_down"), rx=-0.6, ry=0.8))
            self.assertTrue(engine._pet_submenu)
            events_before = len(injector.events)
            # Confirma o quadrado com o A (t0=0,10).
            self._tick(engine, self._state(("lb", "a"), rx=-0.6, ry=0.8), 0.10)
            self.assertFalse(injector.buttons.get("left", False))
            self.assertFalse(injector.buttons.get("right", False))
            # Fase down (0,22..0,31) e fim (>= 0,40).
            self._tick(engine, self._state(), 0.25)
            self._tick(engine, self._state(), 0.41)
            self.assertIsNone(engine._pet_click_seq)
            downs = [i for i, e in enumerate(injector.events) if e == ("mouse", "left", True) and i >= events_before]
            ups = [i for i, e in enumerate(injector.events) if e == ("mouse", "left", False) and i >= events_before]
            self.assertEqual(len(downs), 1)
            self.assertEqual(len(ups), 1)

    # X com LB segurado: nenhum clique direito vaza.
    def test_x_suppressed_while_lb_held(self):
        with tempfile.TemporaryDirectory() as directory:
            engine, _, injector = self._engine(directory)
            self._tick(engine, self._state(("lb", "x"), rx=-0.6, ry=0.8))
            self.assertFalse(injector.buttons.get("right", False))


class OverworldRemapTests(unittest.TestCase):
    # Mapa do overworld (docs/REMAP-BOTOES): Y/B/RB/RT tocam 1/2/3/4 na borda de subida;
    # LT+botão toca 5..0; d-pad sem ação; RB/L3 não seguram mais Shift/Alt.
    # Gatilhos: limiar TRIGGER_ACTIVE_THRESHOLD (0.50) separa "segurado" de "solto".
    # Borda de RT/LT: histerese — o motor atualiza as bordas a cada tick (o _tick aqui
    # imita o run: estado do tick anterior alimenta a borda do atual).

    @staticmethod
    def _state(buttons: tuple[str, ...] = (), **kw) -> ControllerState:
        return ControllerState(connected=True, buttons=frozenset(buttons), **kw)

    def _engine(self, directory: str) -> tuple[BridgeEngine, SharedOverlayState, FakeInjector]:
        config = ConfigManager(Path(directory) / "perfil.json")
        shared = SharedOverlayState()
        engine = BridgeEngine(config, shared)
        engine._mode = "direct"
        injector = FakeInjector()
        injector.cursor = (960, 540)  # centro da janela: zona "center"
        engine.injector = injector
        return engine, shared, injector

    def _tick(self, engine: BridgeEngine, state: ControllerState, now: float = 0.0) -> None:
        cfg = engine.config.get()
        rect = Rect(0, 0, 1920, 1080)
        engine._process_active(FakeHub(), state, rect, cfg, now, 0.05)
        engine._previous = state

    # Y na borda (painéis fechados): toca o 1.
    def test_y_taps_1_when_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            engine, _, _ = self._engine(directory)
            self._tick(engine, self._state(("y",)))
            self.assertIn("1", engine.injector.tapped)

    # B na borda (painéis fechados): toca o 2 (e não abre ESC — estado anterior fechado).
    def test_b_taps_2_when_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            engine, _, _ = self._engine(directory)
            self._tick(engine, self._state(("b",)))
            self.assertIn("2", engine.injector.tapped)
            self.assertNotIn("ESC", engine.injector.tapped)

    # RB na borda: toca o 3 (o antigo hold SHIFT morreu no remap).
    def test_rb_taps_3(self):
        with tempfile.TemporaryDirectory() as directory:
            engine, _, injector = self._engine(directory)
            self._tick(engine, self._state(("rb",)))
            self.assertIn("3", engine.injector.tapped)
            keys = [e[1] for e in injector.events if e[0] == "key"]
            self.assertNotIn("SHIFT", keys)

    # RT cruza o limiar: toca o 4 (borda com histerese no limiar 0.5).
    def test_rt_taps_4(self):
        with tempfile.TemporaryDirectory() as directory:
            engine, _, _ = self._engine(directory)
            self._tick(engine, self._state())
            self._tick(engine, self._state(rt=1.0))
            self.assertIn("4", engine.injector.tapped)
            # Segurar o RT não repete (sem auto-repeat).
            engine.injector.tapped.clear()
            self._tick(engine, self._state(rt=1.0))
            self.assertNotIn("4", engine.injector.tapped)

    # RB segurando não repete o 3 (borda de subida).
    def test_rb_held_does_not_repeat(self):
        with tempfile.TemporaryDirectory() as directory:
            engine, _, _ = self._engine(directory)
            self._tick(engine, self._state(("rb",)))
            self._tick(engine, self._state(("rb",)))
            self.assertEqual(engine.injector.tapped.count("3"), 1)

    # LT segurado + A na borda: toca o 5 (painéis fechados) e NÃO segura o clique comum.
    def test_lt_a_taps_5_when_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            engine, _, injector = self._engine(directory)
            self._tick(engine, self._state(lt=1.0))
            self._tick(engine, self._state(("a",), lt=1.0))
            self.assertIn("5", engine.injector.tapped)
            # O A com LT ativo NÃO segura o clique esquerdo comum.
            self.assertFalse(injector.buttons.get("left", False))

    # LT + X na borda: toca o 6 (e não segura o clique direito comum).
    def test_lt_x_taps_6(self):
        with tempfile.TemporaryDirectory() as directory:
            engine, _, injector = self._engine(directory)
            self._tick(engine, self._state(lt=1.0))
            self._tick(engine, self._state(("x",), lt=1.0))
            self.assertIn("6", engine.injector.tapped)
            self.assertFalse(injector.buttons.get("right", False))

    # LT + Y na borda: toca o 7 (mesmo com painel aberto — LT+ tem prioridade sobre o
    # remap contextual de Y=Shift+clique).
    def test_lt_y_taps_7_with_panel(self):
        with tempfile.TemporaryDirectory() as directory:
            engine, _, injector = self._engine(directory)
            engine._active_panels = ["", "I"]
            engine._previous_panels = ("", "I")
            self._tick(engine, self._state(lt=1.0))
            self._tick(engine, self._state(("y",), lt=1.0))
            self.assertIn("7", engine.injector.tapped)
            # Não injetou Shift+clique (o 7 é toque puro de tecla).
            self.assertNotIn("SHIFT", [e[1] for e in injector.events if e[0] == "key"])

    # LT + B na borda: toca o 8 SEMPRE (não a ESC do remap de B, mesmo com painel).
    def test_lt_b_taps_8_not_esc(self):
        with tempfile.TemporaryDirectory() as directory:
            engine, _, injector = self._engine(directory)
            engine._active_panels = ["", "I"]
            engine._previous_panels = ("", "I")
            self._tick(engine, self._state(lt=1.0))
            self._tick(engine, self._state(("b",), lt=1.0))
            self.assertIn("8", engine.injector.tapped)
            self.assertNotIn("ESC", injector.tapped)

    # LT + RB na borda: toca o 9 (prioridade sobre o 3 do RB sozinho).
    def test_lt_rb_taps_9(self):
        with tempfile.TemporaryDirectory() as directory:
            engine, _, _ = self._engine(directory)
            self._tick(engine, self._state(lt=1.0))
            self._tick(engine, self._state(("rb",), lt=1.0))
            self.assertIn("9", engine.injector.tapped)
            self.assertNotIn("3", engine.injector.tapped)

    # LT + RT na borda: toca o 0 (prioridade sobre o 4 do RT sozinho).
    def test_lt_rt_taps_0(self):
        with tempfile.TemporaryDirectory() as directory:
            engine, _, _ = self._engine(directory)
            self._tick(engine, self._state(lt=1.0))
            self._tick(engine, self._state(lt=1.0, rt=1.0))
            self.assertIn("0", engine.injector.tapped)
            self.assertNotIn("4", engine.injector.tapped)

    # RT abaixo do limiar (0.3 < 0.5) não conta como segurado: não dispara o 4.
    def test_rt_below_threshold_no_tap(self):
        with tempfile.TemporaryDirectory() as directory:
            engine, _, _ = self._engine(directory)
            self._tick(engine, self._state())
            self._tick(engine, self._state(rt=0.3))
            self.assertNotIn("4", engine.injector.tapped)

    # A/B/X/Y sem LT tocam o clique comum / tecla comum (não o combo).
    def test_no_lt_no_combo(self):
        with tempfile.TemporaryDirectory() as directory:
            engine, _, injector = self._engine(directory)
            self._tick(engine, self._state(("a",)))
            self.assertTrue(injector.buttons.get("left", False))
            self.assertEqual(engine.injector.tapped, [])

    # ---------- Remap contextual (painel aberto no tick anterior) ----------

    # B com painel direito aberto no tick anterior: abre ESC e reseta o rastreador.
    def test_b_with_panel_open_sends_esc_and_resets(self):
        with tempfile.TemporaryDirectory() as directory:
            engine, shared, injector = self._engine(directory)
            engine._active_panels = ["", "I"]
            engine._previous_panels = ("", "I")
            shared.update(active_panels=["", "I"])
            self._tick(engine, self._state(("b",)))
            self.assertIn("ESC", injector.tapped)
            self.assertNotIn("2", injector.tapped)
            self.assertEqual(engine._active_panels, ["", ""])
            self.assertEqual(shared.get().active_panels, ["", ""])

    # B com o MESMO tick da abertura da roda: NÃO abre ESC (estado anterior fechado —
    # a borda do B pertence ao overworld fechado).
    def test_b_on_opening_tick_taps_2_not_esc(self):
        with tempfile.TemporaryDirectory() as directory:
            engine, _, injector = self._engine(directory)
            # Painel ainda fechado no tick anterior (_previous_panels = ("", "")).
            # Roda abre o painel neste tick (simulando o A confirmando o slot).
            engine._active_panels = ["", "I"]  # o handler da roda já abriu
            self._tick(engine, self._state(("b",)))
            # B tocou o 2 (estado anterior estava fechado) — não ESC.
            self.assertIn("2", injector.tapped)
            self.assertNotIn("ESC", injector.tapped)

    # Y com painel aberto no tick anterior + cursor na região do ABERTO (direito):
    # injeta Shift+clique E o rastreador abre o outro painel (esquerdo "P").
    def test_y_shift_click_opens_other_panel(self):
        with tempfile.TemporaryDirectory() as directory:
            engine, shared, injector = self._engine(directory)
            # Painel direito aberto; cursor na região DIREITA (painel aberto).
            engine._active_panels = ["", "I"]
            engine._previous_panels = ("", "I")
            shared.update(active_panels=["", "I"])
            # Região direita em 1920x1080: painel direito começa em rect.right - w.
            # w = panel_width = 1080 * (7/15 + 20/1080) ≈ 504.24 + 20 ≈ 524.24 → direita x>1395.76.
            injector.cursor = (1500, 540)
            self._tick(engine, self._state(("y",)), now=0.0)
            # Shift+clique foi armado (Shift pressionado + left down) e fecha na
            # liberação (200 ms): o jogo viu o par Shift+clique e abriu o outro.
            self._tick(engine, self._state(), now=0.20)
            keys = [e[1] for e in injector.events if e[0] == "key"]
            self.assertIn("SHIFT", keys)
            mice = [e for e in injector.events if e[0] == "mouse"]
            self.assertIn(("mouse", "left", True), mice)
            self.assertIn(("mouse", "left", False), mice)
            # O rastreador abriu o painel esquerdo ("P") — o jogo abriu o outro.
            self.assertEqual(engine._active_panels, ["P", "I"])
            self.assertEqual(shared.get().active_panels, ["P", "I"])

    # Y com painel aberto + cursor na região do FECHADO (centro): injeta Shift+clique
    # mas NÃO abre outro painel (o clique não caiu no lado do aberto).
    def test_y_shift_click_center_no_panel_change(self):
        with tempfile.TemporaryDirectory() as directory:
            engine, shared, injector = self._engine(directory)
            engine._active_panels = ["", "I"]
            engine._previous_panels = ("", "I")
            shared.update(active_panels=["", "I"])
            injector.cursor = (960, 540)  # centro: zona "center"
            self._tick(engine, self._state(("y",)))
            # Shift+clique saiu...
            keys = [e[1] for e in injector.events if e[0] == "key"]
            self.assertIn("SHIFT", keys)
            # ...mas nenhum painel novo abriu (clique no centro, não no lado aberto).
            self.assertEqual(engine._active_panels, ["", "I"])

    # LT + A com painel aberto no tick anterior: Ctrl+clique (não o 5).
    def test_lt_a_with_panel_sends_ctrl_click(self):
        with tempfile.TemporaryDirectory() as directory:
            engine, _, injector = self._engine(directory)
            engine._active_panels = ["", "I"]
            engine._previous_panels = ("", "I")
            self._tick(engine, self._state(lt=1.0))
            self._tick(engine, self._state(("a",), lt=1.0))
            # Ctrl+clique: CTRL down+up, left down+up.
            keys = [e[1] for e in injector.events if e[0] == "key"]
            self.assertIn("CTRL", keys)
            mice = [e for e in injector.events if e[0] == "mouse"]
            self.assertIn(("mouse", "left", True), mice)
            self.assertIn(("mouse", "left", False), mice)
            # Não tocou o 5 (o combo virou o Ctrl+clique contextual).
            self.assertNotIn("5", injector.tapped)

    # Y com painel aberto: Shift+clique SEQUENCIADO — o modificador fica FÍSICAMENTE
    # pressionado nos frames do clique (o jogo amostra a tecla por frame; a forma
    # antiga, down/up no mesmo tick, saía como clique simples — bug real, ago/2026):
    # SHIFT down + left down no tick do Y, left up em 120 ms, SHIFT up (garantido)
    # em 180 ms.
    def test_y_shift_click_hold_sequence(self):
        with tempfile.TemporaryDirectory() as directory:
            engine, shared, injector = self._engine(directory)
            engine._active_panels = ["", "I"]
            engine._previous_panels = ("", "I")
            shared.update(active_panels=["", "I"])
            injector.cursor = (1500, 540)  # região direita: jogo abre o outro painel
            self._tick(engine, self._state(("y",)), now=0.0)
            # Tick do Y: Shift e left baixados (o cursor NÃO se moveu — o jogador
            # apontou antes).
            self.assertEqual(injector.events[0], ("key", "SHIFT", True))
            self.assertEqual(injector.events[1], ("mouse", "left", True))
            self.assertTrue(injector.key_pressed("SHIFT"))
            self.assertEqual(injector.moved, [])
            self.assertIsNotNone(engine._modifier_seq)
            # Tick intermediário (50 ms): o jogo está lendo Shift PRESSIONADO com o
            # left ainda segurado — é isso que o torna Shift+clique.
            self._tick(engine, self._state(), now=0.05)
            self.assertTrue(injector.key_pressed("SHIFT"))
            self.assertTrue(injector.buttons.get("left"))
            self.assertIsNotNone(engine._modifier_seq)  # ainda em curso (50 ms < 180 ms)
            self.assertEqual(injector.moved, [])  # cursor/botões suprimidos no gate
            # Tick de 150 ms: left up (120 ms) já saiu, mas o Shift AINDA está
            # pressionado (soltou em 180 ms) — a janela humana do clique modificado.
            self._tick(engine, self._state(), now=0.15)
            self.assertFalse(injector.buttons.get("left"))
            self.assertTrue(injector.key_pressed("SHIFT"))
            # Tick de liberação (200 ms): SHIFT up garantido pelo estado físico.
            self._tick(engine, self._state(), now=0.20)
            self.assertFalse(injector.key_pressed("SHIFT"))
            self.assertIsNone(engine._modifier_seq)
            self.assertNotIn("SHIFT", engine._held_keys)
            # A borda de subida do left (fora de _held_mouse) NÃO disparou a
            # click_zone de fechamento: o painel segue aberto.
            self.assertEqual(engine._active_panels, ["P", "I"])
            # Ordem cronológica completa: SHIFT down → left down → left up → SHIFT up.
            kinds = [
                (e[0], e[1], e[2]) for e in injector.events if e[0] in ("key", "mouse")
            ]
            self.assertEqual(kinds, [
                ("key", "SHIFT", True),
                ("mouse", "left", True),
                ("mouse", "left", False),
                ("key", "SHIFT", False),
            ])

    # O mesmo risco do KEYUP perdido agora vive na FASE DE SOLTA da sequência:
    # o engine reenvia a solta até a tecla confirmar solta (GetAsyncKeyState).
    def test_y_shift_click_release_retries_when_keyup_dropped(self):
        with tempfile.TemporaryDirectory() as directory:
            engine, shared, injector = self._engine(directory)
            engine._active_panels = ["", "I"]
            engine._previous_panels = ("", "I")
            shared.update(active_panels=["", "I"])
            self._tick(engine, self._state(("y",)), now=0.0)
            # A solta de 180 ms (tick a 0.20) some na fila de input (solta não aplicada).
            injector.drop_next_key_up = {"SHIFT"}
            self._tick(engine, self._state(), now=0.20)
            self.assertIn(("key_dropped", "SHIFT"), injector.events)
            # O retry reenviou o KEYUP dentro do próprio tick: Shift fisicamente solto.
            self.assertFalse(injector.key_pressed("SHIFT"))
            self.assertNotIn("SHIFT", engine._held_keys)
            # Ordem mantida: o retry veio DEPOIS do left up.
            events = injector.events
            lup = next(i for i, e in enumerate(events) if e == ("mouse", "left", False))
            retry = next(i for i, e in enumerate(events) if e == ("key", "SHIFT", False))
            self.assertGreater(retry, lup)

    # LT + A com painel aberto no tick anterior: Ctrl+clique (não o 5).
    def test_lt_a_with_panel_sends_ctrl_click(self):
        with tempfile.TemporaryDirectory() as directory:
            engine, _, injector = self._engine(directory)
            engine._active_panels = ["", "I"]
            engine._previous_panels = ("", "I")
            self._tick(engine, self._state(lt=1.0))
            self._tick(engine, self._state(("a",), lt=1.0), now=0.0)
            # Ctrl+clique: CTRL down, left down (sequência armada)...
            keys = [e[1] for e in injector.events if e[0] == "key"]
            self.assertIn("CTRL", keys)
            mice = [e for e in injector.events if e[0] == "mouse"]
            self.assertIn(("mouse", "left", True), mice)
            self.assertNotIn("5", injector.tapped)
            # ...e o par fecha nos ticks seguintes: left up (150 ms, já passou dos
            # 120 ms) + CTRL up garantido (200 ms, já passou dos 180 ms).
            self._tick(engine, self._state(), now=0.15)
            self.assertIn(("mouse", "left", False), [
                e for e in injector.events if e[0] == "mouse"
            ])
            self.assertTrue(injector.key_pressed("CTRL"))  # ainda segurando (150 < 180)
            self._tick(engine, self._state(), now=0.20)
            self.assertFalse(injector.key_pressed("CTRL"))
            self.assertFalse(injector.buttons.get("CTRL"))
            self.assertNotIn("CTRL", engine._held_keys)

    # Tecla fisicamente presa (driver/OS engole TODO KEYUP): o retry estoura o
    # timeout na liberação (180 ms) e desiste logando — não trava o tick.
    def test_modifier_click_stuck_key_gives_up_after_timeout(self):
        with tempfile.TemporaryDirectory() as directory:
            engine, shared, injector = self._engine(directory)
            engine._active_panels = ["", "I"]
            engine._previous_panels = ("", "I")
            shared.update(active_panels=["", "I"])
            injector.stuck_keys = {"SHIFT"}
            engine._MODIFIER_RELEASE_TIMEOUT = 0.01  # encurta o loop do teste
            engine._MODIFIER_RELEASE_RETRY_INTERVAL = 0.001
            self._tick(engine, self._state(("y",)), now=0.0)
            self._tick(engine, self._state(), now=0.20)  # liberação (180 ms) vencida
            # O clique saiu (down + up de left) e o engine desistiu sem segurar a
            # tecla no registro interno.
            mice = [e for e in injector.events if e[0] == "mouse"]
            self.assertIn(("mouse", "left", True), mice)
            self.assertIn(("mouse", "left", False), mice)
            self.assertNotIn("SHIFT", engine._held_keys)
            self.assertIsNone(engine._modifier_seq)

    # Left JÁ retido (click-to-move em curso): a sequência não toca no botão (o
    # left up mataria o movimento) — mas a liberação do modificador continua
    # garantida pelo estado físico.
    def test_y_shift_click_with_held_left_skips_mouse_but_guarantees_release(self):
        with tempfile.TemporaryDirectory() as directory:
            engine, shared, injector = self._engine(directory)
            engine._active_panels = ["", "I"]
            engine._previous_panels = ("", "I")
            shared.update(active_panels=["", "I"])
            # Click-to-move em curso: o left está retido desde antes do Y.
            engine.injector.mouse_button("left", True)
            engine._held_mouse.add("left")
            self._tick(engine, self._state(("y",)), now=0.0)
            # Nenhum down/up de left da sequência (só o retido de antes)...
            mice = [e for e in injector.events if e[0] == "mouse"]
            self.assertEqual(mice, [("mouse", "left", True)])
            # ...o Shift acompanhou o down e será solto garantido na liberação (200 ms).
            self.assertTrue(injector.key_pressed("SHIFT"))
            # O click-to-move continua: stick ativo mantém o left retido via auto_move
            # (sem isso o _set_mouse soltaria o retido na descida do A do tick seguinte).
            self._tick(engine, self._state(lx=0.8), now=0.20)
            self.assertFalse(injector.key_pressed("SHIFT"))
            self.assertNotIn("SHIFT", engine._held_keys)
            # O left retido seguiu retido (a sequência não o tocou) e volta a ser
            # do click-to-move: soltando o stick, o _set_mouse devolve a descida.
            self.assertTrue(injector.buttons.get("left"))
            self.assertIn("left", engine._held_mouse)
            self._tick(engine, self._state(), now=0.25)
            self.assertFalse(injector.buttons.get("left"))
            self.assertNotIn("left", engine._held_mouse)

    # Durante a sequência o _process_active pula cursor/botões: stick direito
    # inclinado nos ticks intermediários não move o cursor (o clique não salta
    # no meio) e nenhum toque de tecla vaza do remap (Y de novo = um só par).
    def test_modifier_sequence_suppresses_pointer_and_retrigger(self):
        with tempfile.TemporaryDirectory() as directory:
            engine, shared, injector = self._engine(directory)
            engine._active_panels = ["", "I"]
            engine._previous_panels = ("", "I")
            shared.update(active_panels=["", "I"])
            self._tick(engine, self._state(("y",)), now=0.0)
            # Tick intermediário com stick direito inclinado (cursor livre): o
            # cursor fica parado e o Y segurado não rearma a sequência.
            self._tick(engine, self._state(("y",), rx=0.8, ry=0.0), now=0.05)
            self.assertEqual(injector.moved, [])
            downs = [e for e in injector.events if e == ("key", "SHIFT", True)]
            self.assertEqual(len(downs), 1)
            self._tick(engine, self._state(), now=0.20)  # liberação (180 ms) vencida
            self.assertIsNone(engine._modifier_seq)

    # Pausa/foco perdido no MEIO da sequência: o left da sequência é solto na hora
    # (ele vive fora de _held_mouse) e a sequência morre junto com a interrupção.
    def test_release_all_cancels_inflight_modifier_click(self):
        with tempfile.TemporaryDirectory() as directory:
            engine, shared, injector = self._engine(directory)
            engine._active_panels = ["", "I"]
            engine._previous_panels = ("", "I")
            shared.update(active_panels=["", "I"])
            self._tick(engine, self._state(("y",)), now=0.0)
            self.assertTrue(injector.buttons.get("left"))
            engine._release_all()
            self.assertIsNone(engine._modifier_seq)
            self.assertFalse(injector.buttons.get("left"))
            self.assertFalse(injector.key_pressed("SHIFT"))
            self.assertNotIn("SHIFT", engine._held_keys)

    # D-pad continua sem ação no overworld (o mapa antigo 5-8 morreu).
    def test_dpad_still_noop(self):
        with tempfile.TemporaryDirectory() as directory:
            engine, _, _ = self._engine(directory)
            for btn in ("dpad_up", "dpad_right", "dpad_down", "dpad_left"):
                self._tick(engine, self._state((btn,)))
            self.assertEqual(engine.injector.tapped, [])

    # RB segurado NÃO segura mais Shift (o hold SHIFT morreu no remap).
    def test_rb_held_no_shift(self):
        with tempfile.TemporaryDirectory() as directory:
            engine, _, injector = self._engine(directory)
            self._tick(engine, self._state(("rb",)))
            self._tick(engine, self._state(("rb",)))
            keys = [e[1] for e in injector.events if e[0] == "key"]
            self.assertNotIn("SHIFT", keys)

    # L3 segurado NÃO segura mais Alt (o hold ALT morreu no remap).
    def test_l3_held_no_alt(self):
        with tempfile.TemporaryDirectory() as directory:
            engine, _, injector = self._engine(directory)
            self._tick(engine, self._state(("l3",)))
            keys = [e[1] for e in injector.events if e[0] == "key"]
            self.assertNotIn("ALT", keys)


class LegacyProfileMigrationTests(unittest.TestCase):
    # Perfil escrito ANTES do remap (tem rb_hold/l3_hold, Y="4", d-pad 5-8) migra para o
    # novo mapa no reload: Y="1", B="2", RB="3", RT="4", LT+ combos 5..0, d-pad "".
    # Chaves que sobrevivem (start, r3, radial_slots) preservam o valor do perfil.
    def test_legacy_profile_migrates_to_new_map(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "perfil.json"
            legacy = {
                "bindings": {
                    "a": "",
                    "b": "2",
                    "x": "",
                    "y": "4",
                    "dpad_up": "5",
                    "dpad_right": "6",
                    "dpad_down": "7",
                    "dpad_left": "8",
                    "r3": "TAB",
                    "start": "F9",  # customizado: PRECISA sobreviver à migração
                    "rb_hold": "SHIFT",
                    "l3_hold": "ALT",
                    "radial_slots": ["I", "S", "Q", "J", "P", "C", "A"],
                }
            }
            path.write_text(json.dumps(legacy), encoding="utf-8")
            config = ConfigManager(path)
            bindings = config.get()["bindings"]
            self.assertEqual(bindings["y"], "1")
            self.assertEqual(bindings["b"], "2")
            self.assertEqual(bindings["rb"], "3")
            self.assertEqual(bindings["rt"], "4")
            self.assertEqual(bindings["lt_a"], "5")
            self.assertEqual(bindings["lt_rt"], "0")
            self.assertEqual(bindings["dpad_up"], "")
            self.assertEqual(bindings["dpad_down"], "")
            self.assertNotIn("rb_hold", bindings)
            self.assertNotIn("l3_hold", bindings)
            # Customização sobrevivente preservada.
            self.assertEqual(bindings["start"], "F9")
            self.assertEqual(bindings["r3"], "TAB")

    # Perfil novo (sem chaves antigas) NÃO é considerado legado: um "y": "4"
    # customizado a posteriori não é pisado.
    def test_new_profile_custom_y_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "perfil.json"
            path.write_text(json.dumps({"bindings": {"y": "4"}}), encoding="utf-8")
            config = ConfigManager(path)
            self.assertEqual(config.get()["bindings"]["y"], "4")
