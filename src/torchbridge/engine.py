# Motor principal: thread dedicada que, a 120 Hz (padrão), converte o estado do controle
# em teclado/mouse reais via SendInput. Só atua com o jogo em primeiro plano.
from __future__ import annotations

import logging
import threading
import time
from typing import Any

from .config import ConfigManager
from .controller import ControllerHub
from .mathutils import clamp, cursor_delta, radial_deadzone, radial_slot
from .models import (
    PET_SUBMENU_COUNT,
    PET_SUBMENU_DEFAULT,
    ControllerState,
    Rect,
    SharedOverlayState,
    both_panels_open,
    click_zone,
    load_hud_mask,
    panels_x_shift,
    pet_click_point,
    pet_submenu_open,
    toggle_panel,
)
from .win32 import (
    InputInjector,
    WindowLocator,
    begin_high_resolution_timer,
    end_high_resolution_timer,
)


log = logging.getLogger(__name__)


# Thread daemon: encerra junto com o processo; o loop vive em run().
class BridgeEngine(threading.Thread):
    """120 Hz controller-to-keyboard/mouse translation loop."""

    def __init__(self, config: ConfigManager, shared: SharedOverlayState) -> None:
        super().__init__(name="TorchBridgeInput", daemon=True)
        self.config = config
        self.shared = shared
        initial = config.get()
        target = initial["target"]
        # Localizador da janela do Torchlight + injetor de entrada do Windows.
        self.locator = WindowLocator(target["process_names"], target["window_titles"])
        self.injector = InputInjector()
        # Parada pedida pela UI; locks protegem estado lido por outra thread.
        self._stop_event = threading.Event()
        self._enabled_lock = threading.Lock()
        self._enabled = True
        # Modo atual: 'direct' (movimento direto) ou 'cursor' (menus/inventário).
        self._mode = initial["movement"]["initial_mode"]
        # Estado do tick anterior: detecta bordas (transições), nunca auto-repeat.
        self._previous = ControllerState()
        # Clique esquerdo retido no tick anterior (borda de subida do clique).
        self._previous_left_pressed = False
        # Movimento direto ativo no tick anterior (borda de descida -> devolver o cursor ao centro).
        self._direct_move_active = False
        # Âncora (x, y em px) do último movimento direto: pra onde o cursor volta ao soltar o stick.
        self._last_anchor: tuple[int, int] | None = None
        # Teclas e botões de mouse retidos agora (liberados todos na perda de foco/saída).
        self._held_keys: set[str] = set()
        self._held_mouse: set[str] = set()
        # Nome do último controle visto — detecta troca/conexão/desconexão.
        # Última recarga do perfil (no mínimo 1 s entre tentativas).
        self._last_reload = 0.0
        self._last_controller_name = ""
        # Borda da detecção do jogo (aviso único de 'Torchlight detectado').
        self._game_was_found = False
        # Estado do combo Back+Start (calibração): visto?, desde quando?, já disparou?
        # Setor da roda selecionado enquanto LB estiver segurado.
        self._radial_selection: int | None = None
        # Latch de "roda confirmada pelo A": o jogador aperta A com o polegar esquerdo
        # ainda segurando o LB, então a roda precisa ficar fechada mesmo com o LB
        # pressionado. Rearma na borda de subida do LB (próximo aperto reabre).
        self._radial_dismissed: bool = False
        # Toggle interno da sublinha de pet actions (4 quadradinhos sob o slot 'P'): d-pad
        # para BAIXO abre, para CIMA fecha. Desmarca o setor 'P' ou fecha a roda: a sublinha
        # some automaticamente (estado interno volta a False).
        self._pet_submenu: bool = False
        # Quadrado (1..4) com o marcador na sublinha; ao abrir, nasce em PET_SUBMENU_DEFAULT
        # (o 4º, embaixo do ícone P). D-pad ESQUERDO/DIRITO navega com wrap enquanto aberta.
        self._pet_submenu_selection: int = PET_SUBMENU_DEFAULT
        # Painéis laterais abertos pela roda: índice 0 = esquerdo (C/P), 1 = direito (I/S/Q/J); "" = fechado.
        self._active_panels: list[str] = ["", ""]
        # Painéis no tick ativo anterior: o remap contextual de B/Y só vale com o
        # estado ESTÁVEL — abrir/fechar o painel no mesmo tick (roda/ESC) não conta
        # como "aberto" para a borda do B (senão o B abriria a ESC em vez do 2).
        self._previous_panels: tuple[str, str] = ("", "")
        # Gatilhos no tick anterior (histerese em TRIGGER_ACTIVE_THRESHOLD): bordas de
        # RT (toque 4) e do combo LT+RT (toque 0). ATUALIZADAS A CADA TICK EM run() —
        # também com o jogo sem foco — para um gatilho segurado através de uma perda
        # de foco não vazar um toque falso ao retomar.
        self._previous_lt_active = False
        self._previous_rt_active = False
        # Estado corrente/bordas calculado em _update_trigger_edges (todo tick).
        self._lt_current = False
        self._lt_edge_up = False
        self._rt_edge_up = False
        # O Shift+clique (Y com painel aberto) injeta o clique direto no meio do tick e
        # consome a parte de cursor/botões segurados: ela só roda no tick seguinte.
        self._skip_pointer_frame = False
        # Sequência de clique do pet em andamento: (x, y do alvo, retorno_x, retorno_y,
        # letra do painel esquerdo pendente, t de início). Enquanto existe, o cursor é
        # exclusivo da sequência — o stick não move o mouse — e o clique esquerdo
        # retido pelo click-to-move não é tocado. None = nada em curso.
        self._pet_click_seq: tuple[int, int, int, int, str | None, float] | None = None
        # Flags de fase da sequência (rearmadas ao armar uma nova): botão esquerdo
        # já foi baixado? a letra do painel pendente já disparou?
        self._pet_click_down = False
        self._pet_click_panel_done = False
        self._center_combo_seen = False
        self._center_combo_started: float | None = None
        self._center_combo_triggered = False
        # Silhueta verde da HUD inferior (área que não fecha painéis): rasterizada uma
        # vez aqui e passada ao click_zone em cada borda de clique. None = sem asset
        # (comportamento antigo: clique central com ambos abertos fecha tudo).
        self._hud_mask = load_hud_mask()

    # Esquece os painéis que a roda acompanhava (ESC fechou os menus do jogo ou sessão nova).
    def _reset_active_panels(self) -> None:
        self._active_panels = ["", ""]
        self.shared.update(active_panels=list(self._active_panels))

    # Nova sessão do jogo (borda de subida): reseta o estado da roda. Um encerramento
    # forçado (Alt+F4) não limpa o estado, e sem esse reset o primeiro toque na roda
    # depois de reabrir o jogo fariam um toggle no estado fantasma (painel 'fechando'
    # em vez de 'abrindo') — o deslocamento do overlay não apareceria.
    def _reset_radial_session(self) -> None:
        self._reset_active_panels()
        self._radial_selection = None
        self._radial_dismissed = False
        self.shared.update(radial_selection=None)

    # Pede a parada; a thread encerra no próximo ciclo.
    def stop(self) -> None:
        self._stop_event.set()

    # Liga/desliga o envio de comandos (checkbox do menu da bandeja) com toast.
    def set_enabled(self, enabled: bool) -> None:
        with self._enabled_lock:
            # Escrita protegida: a UI e a thread leem este valor.
            self._enabled = enabled
        if not enabled:
            self.shared.toast("TorchBridge pausado")
        else:
            self.shared.toast("TorchBridge ativado")

    # Leitura protegida do estado 'habilitado'.
    def is_enabled(self) -> bool:
        with self._enabled_lock:
            return self._enabled

    # Mantém ou libera uma tecla, enviando ao Windows só na transição (nunca repete evento).
    def _set_key(self, name: str, desired: bool) -> None:
        # Binding vazio/ausente no perfil: ignora silenciosamente.
        if not isinstance(name, str) or not name.strip():
            return
        normalized = name.strip().upper()
        try:
            # Borda de subida: tecla ainda não retida → pressiona e registra.
            if desired and normalized not in self._held_keys:
                if self.injector.key(normalized, True):
                    self._held_keys.add(normalized)
            # Borda de descida: tecla retida → libera e esquece.
            elif not desired and normalized in self._held_keys:
                self.injector.key(normalized, False)
                self._held_keys.discard(normalized)
        # Tecla não suportada no perfil: avisa no log e segue o jogo.
        except ValueError as exc:
            log.warning("Binding de retenção ignorado: %s", exc)

    # Mesma lógica de borda para botões de mouse (left/right).
    def _set_mouse(self, button: str, desired: bool) -> None:
        if desired and button not in self._held_mouse:
            if self.injector.mouse_button(button, True):
                self._held_mouse.add(button)
        elif not desired and button in self._held_mouse:
            self.injector.mouse_button(button, False)
            self._held_mouse.discard(button)

    # Segurança: solta todo clique/tecla retidos (perda de foco, pausa, troca de modo ou saída) e limpa o overlay.
    def _release_all(self) -> None:
        for key in tuple(self._held_keys):
            self.injector.key(key, False)
        for button in tuple(self._held_mouse):
            self.injector.mouse_button(button, False)
        self._held_keys.clear()
        self._held_mouse.clear()
        # Interrupção no meio da sequência do pet: solta o botão esquerdo se o clique
        # estava no ar e descarta o restante (o cursor não volta ao centro — quem
        # deteve o foco detém o cursor; a sequência não sobrevive à pausa).
        if self._pet_click_seq is not None:
            if self._pet_click_down:
                self.injector.mouse_button("left", False)
                self._pet_click_down = False
            self._pet_click_seq = None
            self._pet_click_panel_done = False
        # Interrompe o "movimento direto ativo" para o retorno ao centro não disparar no tick de volta.
        self._direct_move_active = False
        # A roda fechou (interrupção): a sublinha de pet actions não pode sobreviver aberta.
        self._pet_submenu = False
        self._pet_submenu_selection = PET_SUBMENU_DEFAULT
        # O latch de confirmação pelo A também não pode sobreviver à interrupção.
        self._radial_dismissed = False
        # Pausa/interrupção também não pode deixar o tick do Shift+clique 'pendurado'.
        self._skip_pointer_frame = False
        self.shared.update(
            radial_active=False,
            radial_selection=None,
            pet_submenu_open=False,
            pet_submenu_selection=None,
            aim_x=None,
            aim_y=None,
        )

    # Toque único de tecla (aperta e solta), usado por botões de ação e slots da roda.
    def _tap_binding(self, value: Any) -> None:
        if not isinstance(value, str) or not value.strip():
            return
        normalized = value.strip().upper()
        temporarily_released: list[str] = []
        modifiers = ["ALT"]
        # Para TAB/ESC, libera também o SHIFT retido: senão o toque viraria Shift+Tab ou Shift+Esc.
        if normalized in {"TAB", "ESC", "ESCAPE"}:
            modifiers.append("SHIFT")
        try:
            # Solta temporariamente os modificadores retidos (ALT; + SHIFT para TAB/ESC)...
            for modifier in modifiers:
                if modifier in self._held_keys:
                    self.injector.key(modifier, False)
                    self._held_keys.discard(modifier)
                    temporarily_released.append(modifier)
            # ...envia o toque e, no finally, religa os modificadores que foram soltos.
            self.injector.tap(normalized)
        except ValueError as exc:
            log.warning("Binding ignorado: %s", exc)
        finally:
            for modifier in temporarily_released:
                if self.injector.key(modifier, True):
                    self._held_keys.add(modifier)

    # Botões centrais: Back+Start por 0,8 s calibra o centro do herói; Back alterna modo; Start dispara seu binding.
    def _handle_center_buttons(
        self,
        state: ControllerState,
        rect: Rect,
        bindings: dict[str, Any],
        now: float,
    ) -> None:
        # Lê os dois botões centrais uma vez por chamada.
        back = state.pressed("back")
        start = state.pressed("start")
        both = back and start

        # Combo (Back+Start) pressionado: inicia a contagem para a calibração.
        if both:
            if not self._center_combo_seen:
                # Primeiro tick do combo: guarda quando começou.
                self._center_combo_started = now
            self._center_combo_seen = True
            # Combo mantido por 0,8 s: dispara a calibração do centro uma única vez por combo.
            if (
                not self._center_combo_triggered
                and self._center_combo_started is not None
                and now - self._center_combo_started >= 0.8
            ):
                # A âncora nova é a posição atual do cursor — aponte o herói antes.
                x, y = self.injector.cursor_position()
                # Só calibra com o cursor dentro da janela do jogo; senão, orienta o usuário.
                if rect.contains(x, y):
                    self.config.update_anchor(
                        (x - rect.left) / rect.width,
                        (y - rect.top) / rect.height,
                    )
                    self.shared.toast("Centro do personagem calibrado", 2.8)
                else:
                    self.shared.toast("Coloque o cursor sobre o personagem", 2.8)
                # Marca para não repetir enquanto o combo continuar pressionado.
                self._center_combo_triggered = True
            return

        if not self._center_combo_seen:
            # Combo inativo: borda de descida do Back → alterna o modo.
            if self._previous.pressed("back") and not back:
                # Alterna entre movimento direto e modo cursor/menus.
                self._mode = "cursor" if self._mode == "direct" else "direct"
                label = "CURSOR / MENUS" if self._mode == "cursor" else "MOVIMENTO DIRETO"
                # Informa o modo atual no overlay.
                self.shared.toast(f"Modo: {label}")
            # Borda de descida do Start (sem combo) → dispara o binding de Start (ESC por padrão).
            if self._previous.pressed("start") and not start:
                self._tap_binding(bindings.get("start", "ESC"))
                # O ESC fechou os menus do jogo: esquece os painéis que a roda acompanhava.
                self._reset_active_panels()

        # Nenhum botão central pressionado: zera o estado do combo para a próxima tentativa.
        if not back and not start:
            self._center_combo_seen = False
            self._center_combo_started = None
            self._center_combo_triggered = False

    # Botões discretos do overworld (mapa docs/REMAP-BOTOES): R3 continua tocando seu
    # binding (TAB). O d-pad NÃO dispara no overworld (vazio por padrão; a tela inicial
    # usará, mas o rastreamento dela ainda não existe). B/Y e os gatilhos têm tratamento
    # próprio — ver _handle_overworld_remap e _handle_trigger_combos. Com a roda ABERTA
    # (LB segurado) a supressão inteira vale: nenhum toque de tecla pode vazar.
    def _handle_discrete_bindings(
        self,
        state: ControllerState,
        bindings: dict[str, Any],
    ) -> None:
        # Roda aberta: a roda é o mundo — nenhum toque de tecla dispara enquanto ela
        # estiver de pé (o d-pad controla a sublinha de pet actions lá dentro).
        if state.pressed("lb") and not self._radial_dismissed:
            return
        # R3: borda de subida, um toque por pressionamento, sem auto-repeat.
        if state.pressed("r3") and not self._previous.pressed("r3"):
            self._tap_binding(bindings.get("r3"))

    # Limiar (0..1) que o gatilho precisa passar para contar como "segurado" (RT=4,
    # LT+RT=0, e LT como modificador dos combos). O SDL normaliza gatilhos analógicos
    # para 0..1; o mesmo limiar vale no caminho raw (trigger_value devolve 0..1).
    TRIGGER_ACTIVE_THRESHOLD = 0.50

    # Sincroniza as bordas dos gatilhos com o estado do tick (chamado no COMEÇO do
    # _process_active e na ramada inativa do run — os flags precisam acompanhar o
    # hardware mesmo quando nada é enviado, para um gatilho segurado através de uma
    # perda de foco não vazar um toque falso ao retomar o foco).
    def _update_trigger_edges(self, state: ControllerState) -> None:
        self._lt_current = state.lt >= self.TRIGGER_ACTIVE_THRESHOLD
        self._lt_edge_up = self._lt_current and not self._previous_lt_active
        self._rt_edge_up = state.rt >= self.TRIGGER_ACTIVE_THRESHOLD and not self._previous_rt_active
        self._previous_lt_active = self._lt_current
        self._previous_rt_active = state.rt >= self.TRIGGER_ACTIVE_THRESHOLD

    # Clique modificado injetado no lugar (Shift/Ctrl + clique esquerdo): mesma ordem de
    # um clique físico modificado — mod down → left down → left up → mod up, tudo no
    # mesmo tick (o gap entre down/up ≈ 0; o jogo lê o par como um clique único). O
    # clique NÃO entra em _held_mouse de propósito: a borda de subida do left no tick
    # seguinte dispararia a lógica de click_zone de fechamento de painéis. O cursor não
    # se mexe aqui — o jogador apontou antes com o stick.
    def _modifier_click(self, modifier: str) -> None:
        if self.injector.key(modifier, True):
            self._held_keys.add(modifier)
        # Botão esquerdo JÁ retido (click-to-move em curso): injetar o clique soltaria
        # o clique retido antes da hora (o left up mataria o movimento) — o clique já
        # "aconteceu" na descida do A; o modificador vem e vai sem tocar no botão.
        if "left" not in self._held_mouse:
            self.injector.mouse_button("left", True)
            self.injector.mouse_button("left", False)
        if self._held_keys.discard(modifier):
            self.injector.key(modifier, False)

    # Combos de gatilho do overworld (docs/REMAP-BOTOES), disparados na BORDA de
    # subida do BOTÃO — o gatilho é o modificador, o botão é o gesto:
    #   RB sozinho       → toque 3      RT sozinho  → toque 4
    #   LT + A/X/Y/B     → toques 5/6/7/8
    #   LT + RB          → toque 9      LT + RT     → toque 0
    # O "LT ativo" é o LT corrente do tick (limiar em TRIGGER_ACTIVE_THRESHOLD):
    # segurar LT e DEPOIS apertar o botão dispara o combo; apertar o botão e DEPOIS
    # o LT não (a borda do botão já foi consumida como ação comum). Com a roda ABERTA
    # nada dispara aqui — a roda é o mundo (A confirma slot, os demais estão
    # suprimidos, mesma regra da supressão clássica do d-pad/face).
    def _handle_trigger_combos(
        self,
        state: ControllerState,
        bindings: dict[str, Any],
    ) -> None:
        # Roda aberta: o A pertence à confirmação de slot; nada de combo pode vazar.
        if state.pressed("lb") and not self._radial_dismissed:
            return
        # Sequência do pet em curso: o cursor/click é exclusivo dela — nenhum combo
        # pode vazar no meio do clique (o A do jogador pertence à confirmação).
        if self._pet_click_seq is not None:
            return
        lt_now = self._lt_current
        # RB: borda de subida → 9 com LT ativo (prioridade), senão 3 (sempre toque —
        # o antigo hold SHIFT morreu no remap).
        if state.pressed("rb") and not self._previous.pressed("rb"):
            self._tap_binding(bindings.get("lt_rb", "9") if lt_now else bindings.get("rb", "3"))
        # RT: borda de subida (histerese no limiar) → 0 com LT ativo, senão 4.
        if self._rt_edge_up:
            self._tap_binding(bindings.get("lt_rt", "0") if lt_now else bindings.get("rt", "4"))
        # A com LT: borda de subida → Ctrl+clique (painel aberto) ou toque 5 (fechado).
        # O A com LT segurado NUNCA segura o clique esquerdo comum: o _process_active
        # desliga o left_pressed enquanto LT está ativo (o combo tem prioridade).
        if state.pressed("a") and not self._previous.pressed("a") and lt_now:
            stable = self._previous_panels
            if bool(stable[0]) or bool(stable[1]):
                self._modifier_click("CTRL")
                # O clique injetado consome a parte de cursor/botões deste tick.
                self._skip_pointer_frame = True
            else:
                self._tap_binding(bindings.get("lt_a", "5"))
        # X com LT: borda de subida → 6 (o X com LT nunca segura o clique direito comum).
        if state.pressed("x") and not self._previous.pressed("x") and lt_now:
            self._tap_binding(bindings.get("lt_x", "6"))
        # Y com LT: borda de subida → 7 SEMPRE (o remap contextual de Y=Shift+clique
        # confere o LT antes — o LT+ tem prioridade sobre o remap, igual ao LT+B=8).
        if state.pressed("y") and not self._previous.pressed("y") and lt_now:
            self._tap_binding(bindings.get("lt_y", "7"))
        # B com LT: borda de subida → 8 SEMPRE (o LT+ tem prioridade sobre o remap
        # contextual de B=ESC — o _handle_overworld_remap confere o LT antes).
        if state.pressed("b") and not self._previous.pressed("b") and lt_now:
            self._tap_binding(bindings.get("lt_b", "8"))

    # Remap contextual do overworld (docs/REMAP-BOTOES): com ao menos um painel lateral
    # ABERTO no estado do tick ANTERIOR (estável — ver _previous_panels), B e Y trocam
    # de função:
    #   B  = ESC: fecha todos os painéis do jogo e reseta o rastreador (mesma
    #      semântica do Start). Sem painel no tick anterior: B toca o 2 clássico.
    #   Y  = Shift + clique esquerdo injetado no lugar do cursor (o jogador apontou
    #      antes com o stick). Sem painel no tick anterior: Y toca o 1 clássico.
    #   O Y no LADO DA TELA COM O PAINEL ABERTO faz o jogo abrir o OUTRO painel
    #      automaticamente (comportamento do jogo — ver a OBS do doc): o rastreador
    #      acompanha de forma otimista (zona "left" + esquerda aberta → abre o
    #      direito "I"; zona "right" + direita aberta → abre o esquerdo "P";
    #      aba de fechar → fecha só aquele lado).
    # Por que o estado do tick ANTERIOR: abrir o painel pela roda e apertar B no MESMO
    # tick não abre a ESC (a borda do B pertence ao overworld fechado); e no tick de
    # transição pós-ESC (estado já zerado) o B volta a ser o 2. Sem painel aberto no
    # tick anterior e sem borda de B/Y: nada acontece aqui.
    def _handle_overworld_remap(
        self,
        state: ControllerState,
        rect: Rect,
        bindings: dict[str, Any],
    ) -> None:
        # Roda aberta: a roda é o mundo — B/Y suprimidos (regra clássica).
        if state.pressed("lb") and not self._radial_dismissed:
            return
        # Sequência do pet em curso: o cursor/click é exclusivo dela — o Shift+clique
        # do Y não pode mexer no cursor no meio do clique do pet, nem a ESC do B
        # pode fechar os menus no meio da sequência.
        if self._pet_click_seq is not None:
            return
        lt_now = self._lt_current
        stable = self._previous_panels
        panels_open = bool(stable[0]) or bool(stable[1])
        # B: LT+B (8) já foi consumido pelos combos — não reprocessar aqui (senão
        # sairia ESC E 8 no mesmo tick). Sem LT: com painel aberto no tick anterior,
        # B = ESC + reset; senão B toca o 2.
        if state.pressed("b") and not self._previous.pressed("b") and not lt_now:
            if panels_open:
                # ESC direto no injetor (não _tap_binding): o jogo fechou os menus,
                # o rastreador acompanha. O clique esquerdo retido do click-to-move
                # segue intacto — a parte de cursor roda depois no mesmo tick.
                self.injector.tap("ESC")
                self._reset_active_panels()
            else:
                self._tap_binding(bindings.get("b"))
        # Y: LT+Y (7) já foi consumido pelos combos. Sem LT: com painel aberto no tick
        # anterior, Y = Shift + clique esquerdo; senão Y toca o 1.
        if state.pressed("y") and not self._previous.pressed("y") and not lt_now:
            if panels_open:
                self._modifier_click("SHIFT")
                # O jogo abre o OUTRO painel quando o Y é pressionado no lado da
                # tela com o painel ABERTO (comportamento do doc, OBS): o rastreador
                # acompanha de forma otimista. Clique no lado do ABERTO com o outro
                # fechado → abre o outro; clique na aba de fechar → fecha só aquele.
                x, y = self.injector.cursor_position()
                zone = click_zone(rect, x, y, self._hud_mask)
                left, right = self._active_panels
                if zone == "left" and left and not right:
                    self._active_panels[1] = "I"
                elif zone == "right" and right and not left:
                    self._active_panels[0] = "P"
                elif zone == "close_left" and left:
                    self._active_panels[0] = ""
                elif zone == "close_right" and right:
                    self._active_panels[1] = ""
                if [left, right] != self._active_panels:
                    self.shared.update(active_panels=list(self._active_panels))
                # O clique injetado consome a parte de cursor/botões deste tick: o
                # _process_active pula _move_pointer/_set_mouse agora — senão o
                # cursor saltaria no meio do clique e a borda de subida do left
                # dispararia o click_zone de fechamento com o cursor na região do painel.
                self._skip_pointer_frame = True
            else:
                self._tap_binding(bindings.get("y"))

    # Sequência de clique das ações do pet (confirmada pelo A com a sublinha aberta).
    # Não há tecla de teclado para essas ações: o cursor é levado ATÉ O BOTÃO na
    # caixinha do pet (movimento absoluto — um único evento SendInput, instantâneo,
    # sem interpolação), o botão esquerdo aperta e solta com o tempo de um clique
    # físico e o cursor volta para o centro. Cronograma (t = segundos desde o A):
    #   0.00  movimento absoluto até o botão (mesmo tick da confirmação)
    #   0.12  mouse LEFT DOWN (o jogo já processou o hover em ~1 frame)
    #   0.21  mouse LEFT UP (90 ms segurando — duração real de um clique)
    #   0.24  letra do painel esquerdo pendente (fecha o painel com o cenário limpo)
    #   0.30  retorno do cursor ao centro (âncora do movimento direto)
    # Total ~300 ms — menos que um piscar de olhos. Durante toda a sequência o
    # cursor é EXCLUSIVO daqui: _move_pointer ignora os sticks e o clique retido
    # do click-to-move não é tocado; ao terminar, o controle volta ao jogador.
    # O clique NÃO entra em _held_mouse (a borda de subida do left no tick seguinte
    # dispararia a lógica de fechamento de painéis por click_zone).
    #
    # Com PAINEL ESQUERDO aberto (pending_panel) a ordem INVERTE e o clique só sai
    # DEPOIS que o jogo termina de fechar o painel — com o painel abrindo na tela o
    # botão do pet não recebe o clique de verdade (bug reportado em ago/2026):
    #   0.00    letra do painel esquerdo (fecha o painel no jogo)
    #   0.50    500 ms de espera (PET_PANEL_CLOSE_DELAY — a animação de fechamento)
    #   0.50    movimento absoluto até o botão
    #   0.62    mouse LEFT DOWN
    #   0.71    mouse LEFT UP
    #   0.77    retorno do cursor ao centro (âncora do movimento direto)
    # Sem painel, o cronograma rápido de ~300 ms continua igual.
    PET_PANEL_CLOSE_DELAY = 0.5

    def _handle_pet_click(self, now: float) -> None:
        seq = self._pet_click_seq
        if seq is None:
            return
        target_x, target_y, return_x, return_y, pending_panel, t0 = seq
        t = now - t0
        if t < 0:
            return
        # Com painel aberto: a letra sai na hora (animação de fechamento começa) e
        # o cursor só vai até o botão depois dos 500 ms de espera.
        panel_wait = self.PET_PANEL_CLOSE_DELAY if pending_panel else 0.0
        if not self._pet_click_panel_done:
            # Letra do painel esquerdo pendente: fecha o painel no jogo (a tecla
            # repetida é o toggle) — ANTES do clique quando o painel está aberto.
            if pending_panel:
                self._tap_binding(pending_panel)
            self._pet_click_panel_done = True
            return
        if t < panel_wait:
            # Esperando a animação de fechamento do painel — o botão do pet só é
            # clicável com o painel já fora da tela.
            return
        if t < panel_wait + 0.12:
            # Fase de ida: mantém o cursor no botão (defensivo — o move já aconteceu
            # na fase anterior; reenviar o mesmo ponto absoluto é idempotente).
            self.injector.move(target_x, target_y)
            return
        if t < panel_wait + 0.21:
            # Descida: aperta o botão esquerdo exatamente uma vez na borda.
            if self._pet_click_down:
                return
            if self.injector.mouse_button("left", True):
                self._pet_click_down = True
            return
        if t < panel_wait + 0.24:
            # Solta na hora que chega (e logo em seguida, se o tick atrasou):
            # nunca segura o clique além do tempo de um clique físico.
            if self._pet_click_down:
                self.injector.mouse_button("left", False)
                self._pet_click_down = False
            return
        # Fim (ou tick que puxou fases por atraso): garante solta, garante a letra
        # do painel (nunca perde) e devolve o cursor ao centro — fim da sequência.
        if not self._pet_click_panel_done and pending_panel:
            self._tap_binding(pending_panel)
            self._pet_click_panel_done = True
        if self._pet_click_down:
            self.injector.mouse_button("left", False)
            self._pet_click_down = False
        self._pet_click_seq = None
        self.injector.move(return_x, return_y)

    # Roda de habilidades: LB + analógico direito escolhe o setor; A confirma o atalho e
    # soltar LB só fecha a roda (a confirmação saiu do soltar em ago/2026).
    def _handle_radial(
        self,
        hub: ControllerHub,
        state: ControllerState,
        bindings: dict[str, Any],
        rect: Rect,
        now: float,
    ) -> None:
        active = state.pressed("lb")
        slots = bindings.get("radial_slots", [])
        # Borda de subida do LB: rearma o latch — o próximo aperto reabre a roda do zero.
        if not self._previous.pressed("lb") and active:
            self._radial_dismissed = False
        # Roda de pé: LB segurado E sem latch de confirmação pelo A (apertar A com o LB
        # ainda segurado mantém a roda fechada até soltar o LB).
        open = active and not self._radial_dismissed
        # Sem LB (ou roda confirmada) não há seleção; radial_slot devolve 1..N (N = slots
        # do perfil) com inclinação mínima.
        selection = radial_slot(state.rx, state.ry, len(slots)) if open else None
        # Setor mudou (roda de pé): atualiza a seleção e vibra para dar retorno tátil.
        if open and selection is not None and selection != self._radial_selection:
            self._radial_selection = selection
            hub.rumble(0.04, 0.10, 35)
        # A sublinha acompanha a seleção PERSISTIDA (self._radial_selection), não a
        # seleção instantânea do analógico: assim, com a alavanca de volta ao centro
        # (radial_slot devolve None), o setor segue o último que o analogo apontou — e o
        # d-pad BAIXO abre o submenu sem precisar manter o stick inclinado.
        current = self._radial_selection if open else None
        # Sublinha de pet actions (4 quadradinhos sob o slot 'P'): d-pad PARA BAIXO abre,
        # para CIMA fecha — enquanto a roda está aberta e o setor do pet é o selecionado.
        # Vibra só na transição real (abriu ou fechou).
        if current is not None:
            current_slot = slots[current - 1] if 1 <= current <= len(slots) else ""
            if pet_submenu_open(open, current, current_slot, True):
                # Setor do pet selecionado: o d-pad inteiro é da sublinha (os toques de
                # tecla já são suprimidos em _handle_discrete_bindings com a roda de pé).
                if state.pressed("dpad_down") and not self._previous.pressed("dpad_down"):
                    if not self._pet_submenu:
                        self._pet_submenu = True
                        # Ao abrir, o marcador nasce no quadrado padrão (o 4º).
                        self._pet_submenu_selection = PET_SUBMENU_DEFAULT
                        hub.rumble(0.04, 0.10, 35)
                elif state.pressed("dpad_up") and not self._previous.pressed("dpad_up"):
                    if self._pet_submenu:
                        self._pet_submenu = False
                        hub.rumble(0.04, 0.10, 35)
                # Navegação horizontal: d-pad DIRITO/ESQUERDO move o marcador com wrap
                # (do 4º volta pro 1º e vice-versa). Só com a sublinha aberta — fechada,
                # esquerdo/direito são toques normais de tecla (mas com a roda aberta eles
                # também são suprimidos, igual aos verticais).
                elif self._pet_submenu:
                    if state.pressed("dpad_right") and not self._previous.pressed("dpad_right"):
                        self._pet_submenu_selection = (self._pet_submenu_selection % PET_SUBMENU_COUNT) + 1
                        hub.rumble(0.03, 0.08, 30)
                    elif state.pressed("dpad_left") and not self._previous.pressed("dpad_left"):
                        self._pet_submenu_selection = ((self._pet_submenu_selection - 2) % PET_SUBMENU_COUNT) + 1
                        hub.rumble(0.03, 0.08, 30)
            # Setor desmarcado do pet (análogo apontou outro setor): a sublinha se esconde
            # sozinha (estado interno volta a False — o próximo 'P' começa fechado).
            elif self._pet_submenu:
                self._pet_submenu = False
        else:
            # Roda fechada: a sublinha não existe mais.
            self._pet_submenu = False

        # Confirmação da roda é do botão A — PS cross / Xbox A / genérico A / Nintendo A
        # chegam todos normalizados como "a" (mapeamento SDL), um caminho cobre as
        # famílias. Com a sublinha aberta: A fecha a sublinha E a roda e dispara a
        # ação do quadrado marcado — não existe tecla de teclado para as ações do pet,
        # então o motor leva o mouse até o botão correspondente na caixinha do pet,
        # clica e devolve o cursor ao centro (sequência em _handle_pet_click).
        # Sem sublinha: dispara o atalho do setor (o papel do antigo "soltar LB").
        if (
            open
            and state.pressed("a")
            and not self._previous.pressed("a")
            and self._radial_selection is not None
            and self._radial_selection <= len(slots)
        ):
            if not self._pet_submenu:
                slot = slots[self._radial_selection - 1]
                self._tap_binding(slot)
                # O atalho também alterna o rastreador de painéis mostrado no overlay.
                next_panels = toggle_panel(self._active_panels, slot)
                if next_panels != self._active_panels:
                    self._active_panels = next_panels
                    self.shared.update(active_panels=list(next_panels))
            else:
                # Sublinha do pet aberta: arma a sequência de clique do quadrado
                # marcado (1=agressivo/vermelho, 2=defensivo/azul, 3=passivo/branco,
                # 4=vendedor/amarelo). A letra do painel ESQUERDO, se aberto, é
                # pendurada na sequência e dispara DEPOIS do clique — mesma regra
                # antiga, só que sequenciada para o jogo fechar o painel com o
                # cenário limpo. O cursor de retorno é a âncora do movimento direto
                # (o "centro" que o click-to-move já usa); fallback no âncora do perfil.
                target = pet_click_point(rect, self._pet_submenu_selection)
                if self._last_anchor:
                    return_x, return_y = self._last_anchor
                else:
                    movement = self.config.get()["movement"]
                    return_x = rect.left + rect.width * float(movement["anchor_x"])
                    return_y = rect.top + rect.height * float(movement["anchor_y"])
                pending_panel = self._active_panels[0] or None
                self._pet_click_seq = (
                    int(round(target[0])),
                    int(round(target[1])),
                    int(round(return_x)),
                    int(round(return_y)),
                    pending_panel,
                    now,
                )
                self._pet_click_down = False
                # Sem painel, a fase da letra já está "resolvida" no armar — o caminho
                # rápido não desperdiça o primeiro tick em fase vazia (o move sai no
                # mesmo tick da confirmação, como sempre). Com painel, o primeiro tick
                # dispara a letra e começa a contagem dos 500 ms de espera.
                self._pet_click_panel_done = pending_panel is None
                if pending_panel:
                    self._active_panels[0] = ""
                    self.shared.update(active_panels=list(self._active_panels))
            self._pet_submenu = False
            self._pet_submenu_selection = PET_SUBMENU_DEFAULT
            self._radial_selection = None
            self._radial_dismissed = True
            hub.rumble(0.06, 0.12, 50)

        # LB liberado: fecha a roda (SÓ fecha — a confirmação de slot/marcação é do botão A).
        if self._previous.pressed("lb") and not active:
            self._radial_selection = None
            self._radial_dismissed = False

        # O A pode ter armado o latch no meio deste tick: recomputa para a publicação
        # já fechar a roda agora (senão ela só sumiria no tick seguinte).
        open = active and not self._radial_dismissed

        self.shared.update(
            radial_active=open,
            radial_selection=self._radial_selection if open else None,
            pet_submenu_open=pet_submenu_open(
                open,
                self._radial_selection if open else None,
                slots[self._radial_selection - 1]
                if self._radial_selection is not None and 1 <= self._radial_selection <= len(slots)
                else "",
                self._pet_submenu,
            ),
            # O quadrado com marcador só tem sentido com a sublinha aberta.
            pet_submenu_selection=(
                self._pet_submenu_selection
                if pet_submenu_open(
                    open,
                    self._radial_selection if open else None,
                    slots[self._radial_selection - 1]
                    if self._radial_selection is not None and 1 <= self._radial_selection <= len(slots)
                    else "",
                    self._pet_submenu,
                )
                else None
            ),
        )

    # Posiciona o cursor do Windows a partir dos analógicos (movimento direto ou cursor).
    def _move_pointer(
        self,
        state: ControllerState,
        rect: Rect,
        cfg: dict[str, Any],
        dt: float,
    ) -> bool:
        input_cfg = cfg["input"]
        movement = cfg["movement"]
        deadzone = float(input_cfg["deadzone"])
        curve = float(input_cfg["response_curve"])
        lx, ly, lmag = radial_deadzone(state.lx, state.ly, deadzone, curve)
        rx, ry, rmag = radial_deadzone(state.rx, state.ry, deadzone, curve)
        radial_active = state.pressed("lb")
        auto_move = False
        cursor_active = False
        aim_local: tuple[int, int] | None = None

        # Movimento direto: exige analógico esquerdo inclinado, direito parado, roda fechada
        # e ao menos uma lateral livre — com os dois painéis abertos, o esquerdo vira cursor livre.
        # Com a sequência de clique do pet em curso o cursor é EXCLUSIVO dela: os sticks
        # ficam bloqueados até o retorno ao centro (evita disputar o cursor no meio do
        # hover/clique do pet).
        if (
            self._mode == "direct"
            and lmag > 0
            and rmag == 0
            and not radial_active
            and self._pet_click_seq is None
            and not both_panels_open(self._active_panels)
        ):
            # Âncora do herói em pixels, a partir das frações do perfil.
            # Painel lateral aberto sozinho desloca a âncora para o lado oposto (área visível).
            anchor_shift = panels_x_shift(self._active_panels)
            anchor_x = rect.left + rect.width * clamp(
                float(movement["anchor_x"]) + anchor_shift, 0.05, 0.95
            )
            anchor_y = rect.top + rect.height * float(movement["anchor_y"])
            # Memória da âncora atual: destino do retorno do cursor ao soltar o stick.
            self._last_anchor = (round(anchor_x), round(anchor_y))
            # Raio circular: fração da ALTURA da janela nos dois eixos (área de movimento = círculo
            # ideal, sem ovalizar nas laterais como quando o eixo x usava a largura).
            radius = rect.height * float(movement["movement_radius_percent"])
            # Distância do cursor em fração do raio: começa em click_center_fraction (perto da
            # âncora — o clique do click-to-move cai longe de NPCs/inimigos) e cresce até o raio
            # cheio conforme o stick é empurrado; o botão fica segurado e o herói segue o cursor.
            center_frac = float(movement["click_center_fraction"])
            reach = center_frac + (1.0 - center_frac) * lmag
            target_x = round(anchor_x + (lx / max(lmag, 1e-6)) * radius * reach)
            target_y = round(anchor_y + (ly / max(lmag, 1e-6)) * radius * reach)
            # Move o cursor do sistema para o alvo do movimento direto (evento absoluto via SendInput).
            # Mantém o cursor dentro da área jogável (borda de 2 px).
            target_x = int(clamp(target_x, rect.left + 2, rect.right - 2))
            target_y = int(clamp(target_y, rect.top + 2, rect.bottom - 2))
            self.injector.move(target_x, target_y)
            aim_local = (target_x - rect.left, target_y - rect.top)
            auto_move = True
        else:
            # Modo cursor: analógico direito move o cursor; no modo 'cursor', o esquerdo também.
            cursor_x = cursor_y = cursor_mag = 0.0
            if not radial_active and rmag > 0:
                cursor_x, cursor_y, cursor_mag = rx, ry, rmag
            # No modo cursor/menus, o analógico esquerdo assume o papel de cursor.
            elif self._mode == "cursor" and lmag > 0:
                cursor_x, cursor_y, cursor_mag = lx, ly, lmag
            # Ambos os painéis abertos: o esquerdo vira cursor livre (sem o click-to-move).
            elif lmag > 0 and both_panels_open(self._active_panels):
                cursor_x, cursor_y, cursor_mag = lx, ly, lmag

            # Há movimento de cursor: desloca da posição atual em vez de saltar para um ponto fixo.
            if cursor_mag > 0:
                cursor_active = True
                current_x, current_y = self.injector.cursor_position()
                speed = float(cfg["cursor"]["speed_pixels_per_second"])
                # Delta em pixels; dt limitado a 50 ms para o cursor não 'saltar' após engasgos.
                dx, dy = cursor_delta(cursor_x, cursor_y, speed, min(dt, 0.05))
                # Move o cursor do sistema para a nova posição do modo cursor.
                target_x = int(clamp(round(current_x + dx), rect.left + 2, rect.right - 2))
                target_y = int(clamp(round(current_y + dy), rect.top + 2, rect.bottom - 2))
                self.injector.move(target_x, target_y)
                aim_local = (target_x - rect.left, target_y - rect.top)

        # Borda de descida do movimento direto: o stick voltou pra dentro da deadzone (ou o modo
        # trocou) e NADA mais está movendo o cursor — devolve o cursor para a âncora do herói,
        # para o próximo click-to-move nascer no centro e não cair em cima de um NPC/inimigo.
        if not auto_move and not cursor_active and self._direct_move_active and self._last_anchor:
            self.injector.move(*self._last_anchor)
        self._direct_move_active = auto_move

        # Publica o marcador de mira no overlay (posição relativa à janela).
        if cfg["overlay"].get("show_aim_marker", True) and aim_local:
            self.shared.update(aim_x=aim_local[0], aim_y=aim_local[1])
        else:
            # Sem alvo (ou marcador desligado): limpa o marcador.
            self.shared.update(aim_x=None, aim_y=None)
        return auto_move

    # Tick completo quando tudo está ativo: jogo em foco + controle conectado + habilitado.
    def _process_active(
        self,
        hub: ControllerHub,
        state: ControllerState,
        rect: Rect,
        cfg: dict[str, Any],
        now: float,
        dt: float,
    ) -> None:
        # BORDAS DOS GATILHOS PRIMEIRO: os handlers de combo/remap leem _lt_current e
        # _rt_edge_up calculados aqui contra o estado do tick.
        self._update_trigger_edges(state)
        bindings = cfg["bindings"]
        self._handle_center_buttons(state, rect, bindings, now)
        self._handle_discrete_bindings(state, bindings)
        # A roda antes da sequência: o A arma a sequência neste mesmo tick e o
        # _handle_pet_click abaixo já faz o movimento até o botão na hora.
        self._handle_radial(hub, state, bindings, rect, now)
        self._handle_pet_click(now)
        # Remap do overworld e combos de gatilho (mapa docs/REMAP-BOTOES): B/Y
        # contextuais (ESC / Shift+clique) e RB/RT/LT+ (3/4/5..0). Rodam depois da
        # roda: com LB de pé eles se auto-suprimem lá dentro.
        self._handle_trigger_combos(state, bindings)
        self._handle_overworld_remap(state, rect, bindings)

        # Posiciona o cursor; retorna True quando o movimento direto deve segurar o clique esquerdo.
        # Com a sequência do pet em curso os sticks estão bloqueados: nenhum move, nenhum
        # clique retido e nenhuma borda de subida dispara a lógica de fechamento de painéis.
        pet_seq_active = self._pet_click_seq is not None
        if pet_seq_active:
            # O clique do pet é injetado direto pelo _handle_pet_click (fora de
            # _held_mouse): não mexe no botão esquerdo nem na borda de subida — o
            # estado 'left' fica exatamente como estava antes da sequência, então
            # nem a borda de descida (soltar por engano) nem a borda de subida
            # (click_zone fechando painéis) disparam no meio do hover/clique.
            self._previous_panels = (self._active_panels[0], self._active_panels[1])
            return
        # Clique modificado injetado neste tick (Shift+clique do Y / Ctrl+clique do
        # LT+A com painel aberto): a parte de cursor/botões fica para o tick
        # seguinte — o cursor não salta no meio do clique e a borda de subida do
        # left não dispara o click_zone com o cursor ainda na região do painel.
        if self._skip_pointer_frame:
            self._skip_pointer_frame = False
            self._previous_panels = (self._active_panels[0], self._active_panels[1])
            return
        auto_move = self._move_pointer(state, rect, cfg, dt)
        # Cliques de mouse vêm dos botões face: A = esquerdo, X = direito.
        # Cruz (PS) / A (Xbox) / A (Nintendo) chegam normalizados como "a" e
        # quadrado (PS) / X (Xbox) / X (Nintendo) como "x" (mapeamento do SDL —
        # ver BUTTONS em controller.py): uma regra cobre as três famílias.
        # Enquanto LB está segurado (roda de atalhos) os dois ficam inertes: o A
        # confirma slot/sublinha lá dentro e nenhum toque pode vazar clique de
        # mouse no jogo — inclusive depois da confirmação, quando o usuário
        # ainda segura A/LB no fim da sequência do pet (sem isso o clique
        # esquerdo grudaria e o herói seguiria o cursor).
        # Com LT ativo os dois ficam inertes TAMBÉM: A/X pertencem aos combos
        # LT+A (5 / Ctrl+clique) e LT+X (6) — segurar o LT com o A não pode
        # segurar o clique comum.
        radial_held = state.pressed("lb")
        lt_held = self._lt_current
        left_pressed = auto_move or (state.pressed("a") and not radial_held and not lt_held)
        self._set_mouse("left", left_pressed)
        self._set_mouse("right", state.pressed("x") and not radial_held and not lt_held)
        # Borda de subida do clique esquerdo: sincroniza com o fechamento de menus do jogo
        # (mesma regra do ESC/Alt+F4). Botão do painel = fecha só aquele lado; zona central
        # com os dois abertos = fechou tudo.
        if left_pressed and not self._previous_left_pressed:
            cursor_x, cursor_y = self.injector.cursor_position()
            zone = click_zone(rect, cursor_x, cursor_y, self._hud_mask)
            # Botão fechar do painel ESQUERDO: o jogo fechou só o lado esquerdo.
            if zone == "close_left" and self._active_panels[0]:
                self._active_panels[0] = ""
                self.shared.update(active_panels=list(self._active_panels))
            # Botão fechar do painel DIREITO: espelho do caso acima.
            elif zone == "close_right" and self._active_panels[1]:
                self._active_panels[1] = ""
                self.shared.update(active_panels=list(self._active_panels))
            # Ambos abertos e clique no meio (fora dos painéis E fora da HUD): o jogo
            # fechou os dois. A área verde da HUD (zone "hud") é interativa no jogo —
            # o jogador apertou um botão ali — então NUNCA zera os painéis.
            # Restringido a ambos-abertos para não interferir no click-to-move (um painel só),
            # cuja âncora ainda cai na zona central.
            elif zone == "center" and both_panels_open(self._active_panels):
                self._reset_active_panels()
        self._previous_left_pressed = left_pressed
        # Snapshot do estado de painéis para o tick seguinte: o remap contextual de
        # B/Y decide com o estado ESTÁVEL do tick anterior (ver docstring do handler).
        self._previous_panels = (self._active_panels[0], self._active_panels[1])

    # Loop principal da thread: lê controle → confere foco → age → espera o restante do período.
    def run(self) -> None:
        # Sobe a resolução do relógio do Windows para 1 ms (estabilidade do 120 Hz).
        begin_high_resolution_timer()
        hub: ControllerHub | None = None
        last_tick = time.perf_counter()
        try:
            hub = ControllerHub()
            while not self._stop_event.is_set():
                tick_start = time.perf_counter()
                # Intervalo real desde o último tick (cursor em px/s); 'now' alimenta timeouts.
                dt = tick_start - last_tick
                last_tick = tick_start
                now = time.monotonic()

                # A cada 1 s tenta recarregar o perfil — edição externa vale na hora.
                if now - self._last_reload >= 1.0:
                    # Só avisa o usuário quando houve mudança real.
                    if self.config.reload():
                        self.shared.toast("Perfil recarregado")
                    self._last_reload = now
                # Cópia da configuração para este tick (imune a edições no meio do ciclo).
                cfg = self.config.get()
                # Estado atual do controle (SDL ou RAW, com reconexão automática).
                state = hub.poll(cfg["raw_controller"])
                # Janela do Torchlight (usa cache; varre com EnumWindows quando preciso).
                hwnd = self.locator.find()
                # Área útil da janela em coordenadas de tela; vazia sem janela.
                rect = self.locator.client_rect(hwnd) if hwnd else Rect()
                # Jogo presente e com área utilizável?
                game_found = bool(hwnd and rect.valid)
                # E está em primeiro plano — condição que autoriza enviar entrada.
                game_active = bool(game_found and self.locator.is_foreground(hwnd))
                enabled = self.is_enabled()

                # Controle conectado (novo ou religado): avisa e vibra para confirmar.
                if state.connected and state.name != self._last_controller_name:
                    self._last_controller_name = state.name
                    self.shared.toast(f"Controle conectado: {state.name}", 3.0)
                    hub.rumble()
                    self._previous = state
                # Controle desconectado: avisa — o release_all abaixo solta o que estava retido.
                elif not state.connected and self._last_controller_name:
                    self.shared.toast("Controle desconectado", 2.5)
                    self._last_controller_name = ""

                # Jogo acabou de aparecer: aviso único (sem spam a cada tick).
                if game_found and not self._game_was_found:
                    self.shared.toast("Torchlight detectado", 2.5)
                    # Sessão nova: reseta painéis/seleção da roda (Alt+F4 não limpou o estado).
                    self._reset_radial_session()
                # Guarda o estado de detecção para as bordas (subida/descida).
                self._game_was_found = game_found

                # Publica o estado completo para o overlay Qt (thread-safe).
                self.shared.update(
                    enabled=enabled and bool(cfg["overlay"].get("enabled", True)),
                    game_found=game_found,
                    game_active=game_active,
                    game_rect=rect,
                    controller_connected=state.connected,
                    controller_name=state.name,
                    controller_mapping=state.mapping,
                    mode=self._mode,
                )

                # PORTÃO DE SEGURANÇA: comandos só saem com jogo em foco, habilitado e controle conectado.
                if enabled and game_active and state.connected:
                    self._process_active(hub, state, rect, cfg, now, dt)
                # Qualquer interrupção (pausa, jogo em segundo plano, sem controle): libera tudo.
                # As bordas dos gatilhos continuam acompanhando o hardware NOS TICKS
                # INATIVOS (dentro de _process_active no tick ativo): um gatilho
                # segurado através de uma perda de foco não vaza toque falso ao retomar.
                else:
                    self._update_trigger_edges(state)
                    self._release_all()

                self._previous = state
                # Duração do tick alvo (1/120 s por padrão).
                period = 1.0 / float(cfg["input"]["poll_hz"])
                remaining = period - (time.perf_counter() - tick_start)
                # Dorme o restante do período para manter a taxa configurada.
                if remaining > 0:
                    self._stop_event.wait(remaining)
        # Erro inesperado não derruba em silêncio: loga e mostra toast com o caminho do log.
        except Exception:
            log.exception("Falha fatal no loop de entrada")
            self.shared.toast("Erro no motor; consulte torchbridge.log", 8.0)
        # Garantias finais: solta entradas, desconecta o controle e restaura o relógio do Windows.
        finally:
            self._release_all()
            if hub is not None:
                hub.close()
            end_high_resolution_timer()
