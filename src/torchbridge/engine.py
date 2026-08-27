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
        # Toggle interno da sublinha de pet actions (4 quadradinhos sob o slot 'P'): d-pad
        # para BAIXO abre, para CIMA fecha. Desmarca o setor 'P' ou fecha a roda: a sublinha
        # some automaticamente (estado interno volta a False).
        self._pet_submenu: bool = False
        # Quadrado (1..4) com o marcador na sublinha; ao abrir, nasce em PET_SUBMENU_DEFAULT
        # (o 4º, embaixo do ícone P). D-pad ESQUERDO/DIRITO navega com wrap enquanto aberta.
        self._pet_submenu_selection: int = PET_SUBMENU_DEFAULT
        # Painéis laterais abertos pela roda: índice 0 = esquerdo (C/P), 1 = direito (I/S/Q/J); "" = fechado.
        self._active_panels: list[str] = ["", ""]
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
        # Interrompe o "movimento direto ativo" para o retorno ao centro não disparar no tick de volta.
        self._direct_move_active = False
        # A roda fechou (interrupção): a sublinha de pet actions não pode sobreviver aberta.
        self._pet_submenu = False
        self._pet_submenu_selection = PET_SUBMENU_DEFAULT
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

    # Botões de ação (face, direcional, R3): toque único na borda de subida.
    # Com a roda de atalhos ABERTA (LB segurado) o d-pad inteiro fica sobrecarregado:
    # ele controla a sublinha de pet actions (up/down) e NÃO dispara mais os toques de
    # tecla configurados (o usuário está na roda, não no jogo).
    def _handle_discrete_bindings(
        self,
        state: ControllerState,
        bindings: dict[str, Any],
    ) -> None:
        radial_open = state.pressed("lb")
        for button in (
            "a",
            "b",
            "x",
            "y",
            "dpad_up",
            "dpad_right",
            "dpad_down",
            "dpad_left",
            "r3",
        ):
            # Roda aberta: o d-pad é da sublinha de pet, não do jogo — pula o toque.
            if radial_open and button.startswith("dpad_"):
                continue
            # Borda de subida: um toque por pressionamento, sem auto-repeat.
            if state.pressed(button) and not self._previous.pressed(button):
                self._tap_binding(bindings.get(button))

    # Roda de habilidades: LB + analógico direito escolhe o setor; soltar LB dispara o atalho.
    def _handle_radial(
        self,
        hub: ControllerHub,
        state: ControllerState,
        bindings: dict[str, Any],
    ) -> None:
        active = state.pressed("lb")
        slots = bindings.get("radial_slots", [])
        # Sem LB não há seleção; radial_slot devolve 1..N (N = slots do perfil) com inclinação mínima.
        selection = radial_slot(state.rx, state.ry, len(slots)) if active else None
        # Setor mudou: atualiza a seleção e vibra para dar retorno tátil.
        if active and selection is not None and selection != self._radial_selection:
            self._radial_selection = selection
            hub.rumble(0.04, 0.10, 35)
        # A sublinha acompanha a seleção PERSISTIDA (self._radial_selection), não a
        # seleção instantânea do analógico: assim, com a alavanca de volta ao centro
        # (radial_slot devolve None), o setor segue o último que o analogo apontou — e o
        # d-pad BAIXO abre o submenu sem precisar manter o stick inclinado.
        current = self._radial_selection if active else None
        # Sublinha de pet actions (4 quadradinhos sob o slot 'P'): d-pad PARA BAIXO abre,
        # para CIMA fecha — enquanto a roda está aberta e o setor do pet é o selecionado.
        # Vibra só na transição real (abriu ou fechou).
        if current is not None:
            current_slot = slots[current - 1] if 1 <= current <= len(slots) else ""
            if pet_submenu_open(active, current, current_slot, True):
                # Setor do pet selecionado: o d-pad inteiro é da sublinha (os toques de
                # tecla já são suprimidos em _handle_discrete_bindings pelo LB segurado).
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

        # LB liberado: confirma a escolha — dispara o slot selecionado, se mapeado.
        if self._previous.pressed("lb") and not active:
            # Só dispara se o setor tiver slot definido no perfil (1..N).
            if self._radial_selection is not None and self._radial_selection <= len(slots):
                slot = slots[self._radial_selection - 1]
                self._tap_binding(slot)
                # O atalho também alterna o rastreador de painéis mostrado no overlay.
                next_panels = toggle_panel(self._active_panels, slot)
                if next_panels != self._active_panels:
                    self._active_panels = next_panels
                    self.shared.update(active_panels=list(next_panels))
            self._radial_selection = None

        self.shared.update(
            radial_active=active,
            radial_selection=self._radial_selection if active else None,
            pet_submenu_open=pet_submenu_open(
                active,
                self._radial_selection if active else None,
                slots[self._radial_selection - 1]
                if self._radial_selection is not None and 1 <= self._radial_selection <= len(slots)
                else "",
                self._pet_submenu,
            ),
            # O quadrado com marcador só tem sentido com a sublinha aberta.
            pet_submenu_selection=(
                self._pet_submenu_selection
                if pet_submenu_open(
                    active,
                    self._radial_selection if active else None,
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
        if (
            self._mode == "direct"
            and lmag > 0
            and rmag == 0
            and not radial_active
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
        bindings = cfg["bindings"]
        self._handle_center_buttons(state, rect, bindings, now)
        self._handle_discrete_bindings(state, bindings)
        self._handle_radial(hub, state, bindings)

        center_combo = state.pressed("back") and state.pressed("start")
        # RB segura Shift (atacar sem avançar) e L3 segura Alt (ver itens); nunca durante o combo de calibração.
        self._set_key(bindings.get("rb_hold", "SHIFT"), state.pressed("rb") and not center_combo)
        self._set_key(bindings.get("l3_hold", "ALT"), state.pressed("l3") and not center_combo)

        # Posiciona o cursor; retorna True quando o movimento direto deve segurar o clique esquerdo.
        auto_move = self._move_pointer(state, rect, cfg, dt)
        # Limiar do perfil: a partir de quanto o gatilho vira clique.
        threshold = float(cfg["input"]["trigger_threshold"])
        # Clique esquerdo: retido no movimento direto (click-to-move) ou com RT fundo.
        left_pressed = auto_move or state.rt >= threshold
        self._set_mouse("left", left_pressed)
        # Clique direito (habilidade secundária) com LT fundo.
        self._set_mouse("right", state.lt >= threshold)
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
                else:
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
