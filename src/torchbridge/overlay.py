# Overlay Qt (PySide6): desenha mira, roda de habilidades, badge de modo e toasts.
# É uma janela sem título, sempre no topo, sem foco e transparente a cliques —
# o conteúdo vem do SharedOverlayState, preenchido pela thread do motor.
from __future__ import annotations

import math
import sys
import time
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPixmap, QPolygonF
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QWidget

from .config import ConfigManager
from .models import (
    OverlaySnapshot,
    SharedOverlayState,
    close_tab_vertices,
    hud_asset_path,
    hud_target_rect,
    panel_regions,
    panels_x_shift,
    pet_actions_asset_path,
    pet_actions_target_rect,
    pet_click_point,
)
from .win32 import make_overlay_clickthrough


# Diretório dos ícones do menu radial: assets/ do projeto, ou o bundle PyInstaller.
def _radial_icons_dir() -> Path:
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    else:
        base = Path(__file__).resolve().parent.parent.parent
    return base / "assets" / "images" / "radial-menu-icons"


# Janela de sobreposição alinhada à área do jogo, redesenhada a ~60 FPS.
class GameOverlay(QWidget):
    def __init__(self, shared: SharedOverlayState, config: ConfigManager) -> None:
        super().__init__()
        self.shared = shared
        self.config = config
        # Controle do showEvent: aplica os estilos Win32 só na primeira exibição.
        self._native_styled = False
        self._last_rect = None
        self.setWindowTitle("TorchBridge Overlay")
        # FramelessWindowHint: sem borda/título; StaysOnTop: acima do jogo; Tool: some da barra de tarefas.
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            # Transparente a cliques e sem foco (nunca rouba o input do jogo).
            | Qt.WindowType.WindowTransparentForInput
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        # Fundo transparente, sem receber eventos de mouse e sem ativar ao aparecer.
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        # Cache dos pixmaps do radial: carrega cada PNG uma única vez (desenhar a 60 FPS).
        self._radial_cache: dict[tuple[str, str], QPixmap] = {}
        # Silhueta verde da HUD (modo de calibração): renderizada uma vez do SVG, se existir.
        # None = asset ausente (nada a desenhar).
        self._hud_pixmap = self._load_hud_pixmap()
        # Caixinha de pet actions (modo de calibração): referência visual no canto
        # superior esquerdo, renderizada uma vez do SVG, se existir. None = ausente.
        self._pet_actions_pixmap = self._load_pet_actions_pixmap()
        # Progresso da animação de entrada/saída do menu radial (0 = fechado, 1 = aberto).
        self._radial_progress = 0.0
        self._radial_last_time = time.monotonic()
        # Duração da animação do radial em cada direção (aparecer e sumir).
        self._radial_anim_seconds = 0.2
        # Progresso da animação da sublinha de pet (0 = escondida atrás do nó, 1 = no lugar).
        # As bolinhas deslizam de trás do ícone da opção P para baixo da fileira.
        self._pet_submenu_progress = 0.0
        self._pet_submenu_last_time = time.monotonic()
        self._pet_submenu_anim_seconds = 0.2
        # Último nó P e marcador com a sublinha ABERTA — guardados para a animação de
        # SAÍDA: no tick em que a sublinha fecha, o snapshot já zera pet_submenu_open e
        # a seleção, então sem esses backups a saída não teria onde partir.
        self._last_pet_point: QPointF | None = None
        self._last_pet_selection: int | None = None
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh)
        # Redesenho a cada 16 ms (~60 FPS); o motor publica o estado a 120 Hz.
        self._timer.start(16)

    # Na primeira exibição, aplica o click-through do Win32 usando o handle nativo (winId).
    def showEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().showEvent(event)
        if not self._native_styled:
            make_overlay_clickthrough(int(self.winId()))
            self._native_styled = True

    # Avança o progresso da animação do menu radial em direção ao alvo (1 aberto / 0 fechado).
    # Usa o tempo real entre ticks para a duração ser exata (0.4 s) em qualquer taxa do QTimer.
    def _tick_radial_anim(self, snapshot: OverlaySnapshot) -> None:
        target = 1.0 if snapshot.radial_active else 0.0
        now = time.monotonic()
        dt = now - self._radial_last_time
        self._radial_last_time = now
        # Sem nada a animar: mantém o progresso e para de gastar ciclos.
        if abs(self._radial_progress - target) < 1e-3:
            self._radial_progress = target
            return
        step = dt / self._radial_anim_seconds
        if target > self._radial_progress:
            self._radial_progress = min(target, self._radial_progress + step)
        else:
            self._radial_progress = max(target, self._radial_progress - step)

    # Avança o progresso da sublinha de pet em direção ao alvo (1 aberta / 0 fechada).
    # Mesma mecânica da roda: dt real entre ticks, duração exata em qualquer taxa.
    def _tick_pet_submenu_anim(self, snapshot: OverlaySnapshot) -> None:
        target = 1.0 if snapshot.pet_submenu_open else 0.0
        now = time.monotonic()
        dt = now - self._pet_submenu_last_time
        self._pet_submenu_last_time = now
        if abs(self._pet_submenu_progress - target) < 1e-3:
            self._pet_submenu_progress = target
            return
        step = dt / self._pet_submenu_anim_seconds
        if target > self._pet_submenu_progress:
            self._pet_submenu_progress = min(target, self._pet_submenu_progress + step)
        else:
            self._pet_submenu_progress = max(target, self._pet_submenu_progress - step)

    # Acompanha o retângulo do jogo e mostra/esconde conforme o estado.
    def _refresh(self) -> None:
        snapshot = self.shared.get()
        # As animações avançam pelo tempo real do tick, independente de visibilidade.
        # A da sublinha é CHAMADA DIRETO AQUI (não dentro da da roda): o atalho de
        # "chegou ao alvo" do radial faz return cedo e congelaria a da sublinha.
        self._tick_radial_anim(snapshot)
        self._tick_pet_submenu_anim(snapshot)
        rect = snapshot.game_rect
        # Só desenha com overlay habilitado, jogo presente e janela válida.
        should_show = snapshot.enabled and snapshot.game_found and rect.valid
        if should_show:
            geometry = (rect.left, rect.top, rect.width, rect.height)
            # O jogo moveu/redimensionou: reposiciona a janela do overlay por cima.
            if geometry != self._last_rect:
                self.setGeometry(*geometry)
                self._last_rect = geometry
            # Mostra uma única vez (evita chamadas repetidas).
            if not self.isVisible():
                self.show()
            self.update()
        # Sem razão de aparecer: esconde para não sobrar janela órfã na tela.
        elif self.isVisible():
            self.hide()

    @staticmethod
    # Fonte padrão do overlay, com tamanho e peso.
    def _font(size: int, bold: bool = False) -> QFont:
        font = QFont("Segoe UI", size)
        font.setBold(bold)
        return font

    # Mira: círculo com 'cruz' ao redor do ponto que o motor está apontando.
    def _draw_aim(self, painter: QPainter, snapshot: OverlaySnapshot, scale: float) -> None:
        # Sem alvo ou com a roda aberta: não desenha a mira.
        if snapshot.aim_x is None or snapshot.aim_y is None or snapshot.radial_active:
            return
        center = QPointF(snapshot.aim_x, snapshot.aim_y)
        radius = 11 * scale
        painter.setPen(QPen(QColor(73, 224, 255, 205), max(1.5, 2.0 * scale)))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(center, radius, radius)
        painter.drawLine(QPointF(center.x() - radius - 5, center.y()), QPointF(center.x() - radius + 1, center.y()))
        painter.drawLine(QPointF(center.x() + radius - 1, center.y()), QPointF(center.x() + radius + 5, center.y()))
        painter.drawLine(QPointF(center.x(), center.y() - radius - 5), QPointF(center.x(), center.y() - radius + 1))
        painter.drawLine(QPointF(center.x(), center.y() + radius - 1), QPointF(center.x(), center.y() + radius + 5))

    # Ícone do radial: "Center" (centro) ou letra do slot, com/sem a variante "-ativo".
    # Carrega do disco uma única vez e guarda no cache; None se o PNG não existir
    # ou falhar o load (o desenho cai no fallback vetorial).
    def _radial_icon(self, name: str, active: bool) -> QPixmap | None:
        key = (name, "ativo" if active else "normal")
        cached = self._radial_cache.get(key)
        if cached is not None:
            return cached
        filename = f"{name}-ativo.png" if active else f"{name}.png"
        pixmap = QPixmap(str(_radial_icons_dir() / filename))
        if pixmap.isNull():
            return None
        self._radial_cache[key] = pixmap
        return pixmap

    # Silhueta verde da HUD inferior renderizada do SVG (viewBox nativo do asset,
    # proporção 1920x1080). Usada SÓ no modo de calibração para mostrar a mesma área que
    # o hit-test considera "não fecha painéis". None se o asset não existir ou o Qt Svg
    # falhar.
    def _load_hud_pixmap(self) -> QPixmap | None:
        path = hud_asset_path()
        if path is None or not path.exists():
            return None
        try:
            renderer = QSvgRenderer()
            if not renderer.load(str(path)):
                return None
            size = renderer.defaultSize()
            pixmap = QPixmap(int(size.width()), int(size.height()))
            pixmap.fill(QColor(0, 0, 0, 0))
            painter = QPainter(pixmap)
            renderer.render(painter)
            painter.end()
            return pixmap
        except Exception:  # noqa: BLE001 - sem Qt Svg o modo calibração segue sem a HUD.
            return None

    # Caixinha de pet actions renderizada do SVG (viewBox nativo 156x201). Usada SÓ no
    # modo de calibração, como referência visual no canto superior esquerdo. None se o
    # asset não existir ou o Qt Svg falhar.
    def _load_pet_actions_pixmap(self) -> QPixmap | None:
        path = pet_actions_asset_path()
        if path is None or not path.exists():
            return None
        try:
            renderer = QSvgRenderer()
            if not renderer.load(str(path)):
                return None
            size = renderer.defaultSize()
            pixmap = QPixmap(int(size.width()), int(size.height()))
            pixmap.fill(QColor(0, 0, 0, 0))
            painter = QPainter(pixmap)
            renderer.render(painter)
            painter.end()
            return pixmap
        except Exception:  # noqa: BLE001 - sem Qt Svg o modo calibração segue sem ela.
            return None

    # Roda central: emblema "Center" (assets) + um ícone por slot do perfil.
    # Sem arte disponível, volta ao desenho vetorial antigo (disco "MENUS" + círculos com letra).
    def _draw_radial(self, painter: QPainter, snapshot: OverlaySnapshot, scale: float) -> None:
        # A animação só termina quando o progresso chega ao alvo: a saída some aos poucos,
        # então desenha enquanto houver progresso (LB solto e progresso ainda > 0).
        progress = self._radial_progress
        if progress <= 0.0:
            return
        # Painel lateral aberto sozinho desloca a roda para o lado oposto (12,5% da largura).
        shift = panels_x_shift(snapshot.active_panels)
        center = QPointF(self.width() / 2 + self.width() * shift, self.height() / 2)
        # Easing suave (smoothstep) do progresso: entra e sai mais lento nas pontas,
        # duração total continua sendo a do _tick_radial_anim (0.4 s nos dois sentidos).
        eased = progress * progress * (3.0 - 2.0 * progress)
        painter.save()
        # Aparência da animação: opacidade 0→1 e escala 0.7→1 em torno do centro da roda.
        painter.setOpacity(eased)
        scale_factor = 0.7 + 0.3 * eased
        painter.translate(center.x(), center.y())
        painter.scale(scale_factor, scale_factor)
        painter.translate(-center.x(), -center.y())
        ring_radius = 126 * scale
        node_radius = 27 * scale

        cfg = self.config.get()
        radial_slots = cfg["bindings"].get("radial_slots", [])

        # Centro: emblema dos assets; sem a arte, disco escuro com "MENUS".
        center_icon = self._radial_icon("Center", False)
        if center_icon is not None:
            size = int(124 * scale)
            painter.drawPixmap(int(center.x() - size / 2), int(center.y() - size / 2), size, size, center_icon)
        else:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(4, 9, 15, 174))
            painter.drawEllipse(center, 62 * scale, 62 * scale)
            painter.setFont(self._font(max(8, round(9 * scale)), True))
            painter.setPen(QColor(221, 236, 244, 225))
            painter.drawText(
                QRectF(center.x() - 52 * scale, center.y() - 18 * scale, 104 * scale, 36 * scale),
                Qt.AlignmentFlag.AlignCenter,
                "MENUS",
            )

        # Ícones a partir do topo, no sentido horário — mesmo layout da função radial_slot.
        selected_point: QPointF | None = None
        for index in range(len(radial_slots)):
            angle = math.radians(-90 + index * (360 / len(radial_slots)))
            point = QPointF(
                center.x() + math.cos(angle) * ring_radius,
                center.y() + math.sin(angle) * ring_radius,
            )
            # Slot marcado pelo análogo troca para a arte "-ativo".
            selected = snapshot.radial_selection == index + 1
            if selected:
                selected_point = point
            icon = self._radial_icon(str(radial_slots[index]).upper(), selected)
            if icon is not None:
                width = int(80 * scale)
                height = int(width * icon.height() / icon.width())
                painter.drawPixmap(int(point.x() - width / 2), int(point.y() - height / 2), width, height, icon)
            else:
                # Fallback sem arte para essa letra: círculo vetorial com a letra.
                radius = node_radius * (1.14 if selected else 1.0)
                painter.setPen(
                    QPen(
                        QColor(255, 202, 82, 245) if selected else QColor(111, 210, 235, 205),
                        3.0 * scale if selected else 1.5 * scale,
                    )
                )
                painter.setBrush(QColor(35, 30, 18, 238) if selected else QColor(6, 17, 25, 224))
                painter.drawEllipse(point, radius, radius)
                painter.setFont(self._font(max(11, round(15 * scale)), True))
                painter.setPen(QColor(255, 220, 137) if selected else QColor(231, 247, 251))
                painter.drawText(
                    QRectF(point.x() - radius, point.y() - radius, radius * 2, radius * 2),
                    Qt.AlignmentFlag.AlignCenter,
                    str(radial_slots[index]),
                )
        # Sublinha de pet actions: 4 quadradinhos cinza sob o nó do slot 'P', o 4º alinhado
        # no eixo do nó. Visível quando snapshot.pet_submenu_open (roda aberta + pet
        # selecionado + d-pad baixo). O quadrado marcado fica dourado. Desenhada com a
        # animação de entrada/saída própria: as bolinhas deslizam de TRÁS do ícone da
        # opção P para baixo da fileira (e voltam ao fechar), com fade e cascata.
        pet_progress = self._pet_submenu_progress
        if snapshot.pet_submenu_open and selected_point is not None:
            # Sublinha aberta: atualiza os backups (nó P atual + marcador) — eles
            # sustentam a animação de saída no tick em que o snapshot zerar tudo.
            self._last_pet_point = selected_point
            self._last_pet_selection = snapshot.pet_submenu_selection
        elif not (self._last_pet_point is not None and progress > 0.0):
            # Nem aberta, nem com roda de pé para a saída: zera (e apaga o backup,
            # para uma roda NOVA não herdar a sublinha de uma sessão anterior).
            pet_progress = 0.0
            self._last_pet_point = None
            self._last_pet_selection = None
        # `progress` é o da roda: a sublinha só desenha com a roda de pé. A saída
        # segue no último nó guardado até o progresso zerar.
        if pet_progress > 0.0 and self._last_pet_point is not None:
            self._draw_pet_submenu(
                painter, self._last_pet_point, scale, self._last_pet_selection, pet_progress
            )
        # Devolve transformações de estado (opacidade/translate/scale) ao desenhista.
        painter.restore()

    # Ícones dos quadradinhos da sublinha de pet (ordem 1..4 = agressivo/defensivo/
    # passivo/vendedor). Mesmos PNGs de assets/images/radial-menu-icons (31x31, fundo
    # transparente), carregados uma vez e em cache.
    PET_SUBMENU_ICONS = ("pet-agressive", "pet-defensive", "pet-passive", "pet-seller")

    # Ícone de uma ação do pet: carrega do disco uma única vez (cache do radial);
    # None se o PNG não existir — o quadrado volta ao cinza simples.
    def _pet_icon(self, name: str) -> QPixmap | None:
        return self._radial_icon(name, False)

    # Quatro CÍRCULOS da sublinha de pet actions, embaixo do nó de slot selecionado.
    # Layout (escala da roda, referência 1080p): diâmetro = 44px (dobro do quadrado
    # antigo de 22px — pedido do usuário, ago/2026), vão entre eles = 20px, topo da
    # fileira = 50px abaixo do centro do nó. A fileira NÃO é centralizada: o CÍRCULO
    # 4 fica alinhado no eixo horizontal do nó (direto embaixo do ícone do slot P) —
    # os centros ficam a 64px (diâmetro + vão) uns dos outros. Cada círculo desenha o
    # ícone da ação correspondente (1=espada/agressivo, 2=escudo/defensivo,
    # 3=pássaro/passivo, 4=moeda/vendedor) e o círculo `selection` (1..4) recebe o
    # marcador: borda mais grossa na cor dourada da roda selecionada (255, 202, 82).
    # Animação de entrada/saída (`progress` 0→1): as bolinhas nascem ESCONDIDAS ATRÁS
    # do ícone da opção P (clip na base do ícone) e deslizam para baixo da fileira,
    # com fade e cascata — a 4ª (debaixo do nó) sai primeiro, a 1ª por último. Ao
    # fechar, o mesmo caminho em reverso: voltam para trás do ícone e somem.
    PET_SUBMENU_STAGGER = 0.12  # atraso do fade/slide de cada bolinha relativa à anterior
    # Distância do slide (em 1080p, sem escala): do lugar final até TOTALMENTE atrás do
    # ícone P. O clip começa 44px abaixo do centro do nó (logo sob a base do ícone);
    # para a bolinha (raio 22) começar INVISÍVEL, seu fundo (cy+22) tem de ficar acima
    # do clip → slide >= 726 - (698 - 22) ≈ 50. Usamos 78: folga de 28px, zero vazamento.
    PET_SUBMENU_SLIDE = 78.0

    def _draw_pet_submenu(
        self,
        painter: QPainter,
        point: QPointF,
        scale: float,
        selection: int | None,
        progress: float,
    ) -> None:
        if progress <= 0.0:
            return
        side = 44 * scale
        gap = 20 * scale
        top = point.y() + 50 * scale
        # Centro do 4º círculo no eixo do nó; os demais recuam de 64 em 64 (lado+vão).
        center4_x = point.x()
        center_y = top + side / 2.0
        # O ícone (31x31) entra no círculo com leve respiro: 78% do diâmetro.
        icon_size = side * 0.78
        # Clip: a sublinha só é visível ABAIXO da base do ícone da opção P — é assim que
        # as bolinhas "saiam de trás da opção". O ícone P desenha com ~66px de altura
        # (86x71 em 80px de largura), então 44px abaixo do centro fica folga de sobra
        # por baixo dele e segura a sublinha toda (fileira termina a 72px abaixo).
        clip_top = point.y() + 44 * scale
        clip_bottom = top + side + 8 * scale
        painter.save()
        painter.setClipRect(QRectF(point.x() - 220 * scale, clip_top, 440 * scale, clip_bottom - clip_top))
        base_opacity = painter.opacity()
        stagger = self.PET_SUBMENU_STAGGER
        span = 1.0 - stagger * 3  # janela de cada bolinha (a 1ª tem o maior atraso)
        for i in range(4):
            # Cascata: a 4ª bolinha (i=3, debaixo do nó) começa primeiro; a 1ª (i=0)
            # termina exatamente em progress=1. O slide desloca cada círculo PET_SUBMENU_SLIDE
            # px para cima do lugar final: no 0 ela está atrás do ícone, escondida pelo clip.
            raw = (progress - stagger * (3 - i)) / span
            p_i = max(0.0, min(1.0, raw))
            eased = p_i * p_i * (3.0 - 2.0 * p_i)
            cy = center_y - self.PET_SUBMENU_SLIDE * scale * (1.0 - eased)
            cx = center4_x - (3 - i) * (side + gap)
            # Fade individual por cima da opacidade herdada da roda.
            painter.setOpacity(base_opacity * (0.2 + 0.8 * eased))
            if selection is not None and i + 1 == selection:
                # Marcador do círculo ativo: dourado, igual à cor do nó selecionado da roda.
                painter.setPen(QPen(QColor(255, 202, 82, 255), 3.0 * scale))
                painter.setBrush(QColor(24, 28, 34, 235))
            else:
                painter.setPen(QPen(QColor(232, 234, 238, 235), 2.0 * scale))
                painter.setBrush(QColor(216, 219, 224, 242))
            painter.drawEllipse(QPointF(cx, cy), side / 2.0, side / 2.0)
            # Ícone da ação centralizado no círculo (fallback: sem PNG, só o círculo).
            pixmap = self._pet_icon(self.PET_SUBMENU_ICONS[i])
            if pixmap is not None:
                painter.drawPixmap(
                    QRectF(cx - icon_size / 2.0, cy - icon_size / 2.0, icon_size, icon_size),
                    pixmap,
                    QRectF(pixmap.rect()),
                )
        # Devolve opacidade/clip antes de fechar o painter.
        painter.setOpacity(base_opacity)
        painter.restore()

    # Badge superior direito com o modo atual (DIRETO/CURSOR).
    def _draw_mode_badge(self, painter: QPainter, snapshot: OverlaySnapshot, scale: float) -> None:
        cfg = self.config.get()
        # Pode ser desligado pelo perfil.
        if not cfg["overlay"].get("show_mode_badge", True):
            return
        label = "DIRETO" if snapshot.mode == "direct" else "CURSOR"
        color = QColor(69, 211, 239, 210) if snapshot.mode == "direct" else QColor(255, 191, 69, 220)
        width = 104 * scale
        height = 28 * scale
        box = QRectF(self.width() - width - 16 * scale, 16 * scale, width, height)
        painter.setPen(QPen(color, 1.2 * scale))
        painter.setBrush(QColor(3, 10, 16, 185))
        painter.drawRoundedRect(box, 8 * scale, 8 * scale)
        painter.setFont(self._font(max(8, round(9 * scale)), True))
        painter.setPen(QColor(230, 245, 248, 235))
        painter.drawText(box, Qt.AlignmentFlag.AlignCenter, f"MODO {label}")

    # Indicador discreto no topo-central: painéis abertos pela roda (esquerdo | direito).
    def _draw_active_panels(self, painter: QPainter, snapshot: OverlaySnapshot, scale: float) -> None:
        # Índice 0 = lado esquerdo (C/P), 1 = direito (I/S/Q/J); tolera listas curtas.
        left, right = (list(snapshot.active_panels) + ["", ""])[:2]
        # Ambos os lados vazios: nada a indicar — mantém a tela limpa (só aparece com painel aberto).
        if not left and not right:
            return
        text = f"{left or '·'} | {right or '·'}"
        painter.setFont(self._font(max(7, round(8 * scale)), True))
        metrics = painter.fontMetrics()
        padding = 8 * scale
        height = 16 * scale
        width = metrics.horizontalAdvance(text) + padding * 2
        box = QRectF((self.width() - width) / 2, 4 * scale, width, height)
        painter.setPen(QPen(QColor(87, 218, 244, 130), 1.0 * scale))
        painter.setBrush(QColor(3, 10, 16, 165))
        painter.drawRoundedRect(box, 8 * scale, 8 * scale)
        painter.setPen(QColor(220, 242, 248, 210))
        painter.drawText(box, Qt.AlignmentFlag.AlignCenter, text)

    # Zonas de calibração dos painéis: desenha as MESMAS formas que a click_zone usa
    # para fechar painéis, para calibrar visualmente as frações (overlay.show_calibration).
    def _draw_calibration(self, painter: QPainter, snapshot: OverlaySnapshot, scale: float) -> None:
        cfg = self.config.get()
        # Só visível com a flag ligada e o retângulo do jogo válido.
        if not cfg["overlay"].get("show_calibration", False):
            return
        rect = snapshot.game_rect
        if not rect.valid:
            return
        # Janela do overlay = área do jogo: converte coordenadas absolutas para locais.
        def local(box: tuple[int, int, int, int]) -> QRectF:
            x, y, w, h = box
            return QRectF(x - rect.left, y - rect.top, w, h)

        # Vértices da aba em coordenadas locais do overlay (QPolygonF, pra drawPolygon).
        def local_polygon(side: str) -> QPolygonF:
            return QPolygonF([
                QPointF(x - rect.left, y - rect.top)
                for x, y in close_tab_vertices(rect, side)
            ])

        regions = panel_regions(rect)
        # Painéis: traço cheio ciano, rótulo pequeno.
        painter.setPen(QPen(QColor(111, 210, 235, 170), 1.5 * scale))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(local(regions["panel_left"]))
        painter.drawRect(local(regions["panel_right"]))
        # Zonas de fechar: a MESMA aba (pentágono) que a click_zone hit-testa — laranja.
        painter.setPen(QPen(QColor(255, 159, 67, 235), 2.5 * scale))
        painter.setBrush(QColor(255, 159, 67, 55))
        close_left_poly = local_polygon("left")
        close_right_poly = local_polygon("right")
        painter.drawPolygon(close_left_poly)
        painter.drawPolygon(close_right_poly)
        # Zona central: tracejado (clique nela zera os dois com ambos abertos).
        painter.setPen(
            QPen(QColor(120, 220, 150, 170), 1.2 * scale, Qt.PenStyle.DashLine)
        )
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(local(regions["center"]))
        # HUD inferior: a MESMA silhueta verde que a click_zone hit-testa como "não fecha
        # painéis" — desenhada na posição real (frações da janela) para calibrar o ajuste fino.
        if self._hud_pixmap is not None:
            hl, ht, hw, hh = hud_target_rect(rect)
            # hud_target_rect devolve absolutos; converte para local do overlay.
            target = QRectF(hl - rect.left, ht - rect.top, hw, hh)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            painter.setPen(QPen(QColor(60, 235, 90, 120), 1.0 * scale))
            painter.setBrush(QColor(60, 235, 90, 38))
            painter.drawPixmap(
                int(target.x()), int(target.y()), int(target.width()), int(target.height()),
                self._hud_pixmap,
            )
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        # Pet actions: a caixinha real do jogo (formas geométricas) no canto superior
        # esquerdo — referência visual para ações futuras, ainda SEM hit-test. Roxa pra
        # não confundir com as cores das outras zonas (verde=HUD, ciano= painel, laranja=fechar).
        if self._pet_actions_pixmap is not None:
            pl, pt, pw, ph = pet_actions_target_rect(rect)
            pet_local = QRectF(pl - rect.left, pt - rect.top, pw, ph)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            painter.setPen(QPen(QColor(196, 120, 255, 130), 1.0 * scale))
            painter.setBrush(QColor(196, 120, 255, 34))
            painter.drawPixmap(
                int(pet_local.x()), int(pet_local.y()), int(pet_local.width()), int(pet_local.height()),
                self._pet_actions_pixmap,
            )
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
            # Pontos de clique das 4 ações do pet: bolinha vermelha com cruz no centro
            # EXATO de cada botão (pet_click_point — a mesma fonte do motor). É ali que
            # o cursor vai quando o A confirma; calibrar a caixinha acima move os pontos.
            # Contorno branco por baixo: sem ele, o traço vermelho some no círculo vermelho.
            radius = 7.0 * scale
            pens = (
                QPen(QColor(255, 255, 255, 255), 4.5 * scale),
                QPen(QColor(255, 80, 80, 255), 2.0 * scale),
            )
            for index in range(1, 5):
                px, py = pet_click_point(rect, index)
                # Absolute -> local do overlay.
                lx = px - rect.left
                ly = py - rect.top
                painter.setBrush(Qt.BrushStyle.NoBrush)
                for pen in pens:
                    painter.setPen(pen)
                    painter.drawEllipse(QPointF(lx, ly), radius, radius)
                    painter.drawLine(QPointF(lx - radius - 3 * scale, ly), QPointF(lx + radius + 3 * scale, ly))
                    painter.drawLine(QPointF(lx, ly - radius - 3 * scale), QPointF(lx, ly + radius + 3 * scale))
        # Rótulos: o que cada zona faz (posicionados na bounding box da aba).
        painter.setFont(self._font(max(7, round(9 * scale)), True))
        painter.setPen(QColor(255, 159, 67, 245))
        painter.drawText(close_left_poly.boundingRect().adjusted(0, -26 * scale, 0, -6 * scale), Qt.AlignmentFlag.AlignCenter, "FECHA ESQ")
        painter.drawText(close_right_poly.boundingRect().adjusted(0, -26 * scale, 0, -6 * scale), Qt.AlignmentFlag.AlignCenter, "FECHA DIR")
        painter.setPen(QColor(111, 210, 235, 200))
        painter.drawText(local(regions["panel_left"]).adjusted(0, 6 * scale, 0, 24 * scale), Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop, "PAINEL")
        painter.drawText(local(regions["panel_right"]).adjusted(0, 6 * scale, 0, 24 * scale), Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop, "PAINEL")
        painter.setPen(QColor(120, 220, 150, 200))
        painter.drawText(local(regions["center"]).adjusted(0, 6 * scale, 0, 24 * scale), Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop, "CENTRO (ZERA TUDO)")
        if self._hud_pixmap is not None:
            hl, ht, hw, hh = hud_target_rect(rect)
            hud_local = QRectF(hl - rect.left, ht - rect.top, hw, hh)
            painter.setFont(self._font(max(7, round(9 * scale)), True))
            painter.setPen(QColor(120, 235, 90, 235))
            # Rótulo numa faixa de 20px logo ACIMA do topo da HUD (o hud_local.top() já é
            # o topo da silhueta; desenhá-lo dentro dela faria o verde sumir no verde).
            label_box = QRectF(
                hud_local.left(), hud_local.top() - 24 * scale,
                hud_local.width(), 20 * scale,
            )
            painter.drawText(
                label_box,
                Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom,
                "HUD (NÃO FECHA)",
            )
        if self._pet_actions_pixmap is not None:
            pl, pt, pw, ph = pet_actions_target_rect(rect)
            pet_local = QRectF(pl - rect.left, pt - rect.top, pw, ph)
            painter.setFont(self._font(max(7, round(9 * scale)), True))
            painter.setPen(QColor(216, 156, 255, 235))
            # A caixinha está colada ao canto: o rótulo fica à DIREITA dela, centralizado
            # na vertical (não dá pra colocar acima/esquerda que não há espaço).
            label_box = QRectF(
                pet_local.right() + 6 * scale,
                pet_local.top(),
                120 * scale, pet_local.height(),
            )
            painter.drawText(
                label_box,
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                "PET ACTIONS",
            )

    # Mensagens temporárias (conectado, calibrado, perfil recarregado...).
    def _draw_toast(self, painter: QPainter, snapshot: OverlaySnapshot, scale: float) -> None:
        import time

        # Texto vazio ou expirado: nada a desenhar.
        if not snapshot.toast_text or snapshot.toast_until <= time.monotonic():
            return
        width = min(self.width() - 40 * scale, max(260 * scale, len(snapshot.toast_text) * 8.4 * scale))
        height = 44 * scale
        box = QRectF((self.width() - width) / 2, 22 * scale, width, height)
        painter.setPen(QPen(QColor(87, 218, 244, 210), 1.3 * scale))
        painter.setBrush(QColor(2, 9, 14, 222))
        painter.drawRoundedRect(box, 12 * scale, 12 * scale)
        painter.setFont(self._font(max(9, round(11 * scale)), True))
        painter.setPen(QColor(235, 248, 251))
        painter.drawText(box, Qt.AlignmentFlag.AlignCenter, snapshot.toast_text)

    # Redesenho completo: lê o snapshot e pinta os quatro elementos na escala do perfil.
    def paintEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        del event
        snapshot = self.shared.get()
        scale = float(self.config.get()["overlay"]["scale"])
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self._draw_aim(painter, snapshot, scale)
        self._draw_calibration(painter, snapshot, scale)
        self._draw_radial(painter, snapshot, scale)
        self._draw_mode_badge(painter, snapshot, scale)
        # Desenhado antes do toast para o aviso temporário cobrir o indicador quando sobreposto.
        self._draw_active_panels(painter, snapshot, scale)
        self._draw_toast(painter, snapshot, scale)
        painter.end()

