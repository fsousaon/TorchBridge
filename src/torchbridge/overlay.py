# Overlay Qt (PySide6): desenha mira, roda de habilidades, badge de modo e toasts.
# É uma janela sem título, sempre no topo, sem foco e transparente a cliques —
# o conteúdo vem do SharedOverlayState, preenchido pela thread do motor.
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QWidget

from .config import ConfigManager
from .models import OverlaySnapshot, SharedOverlayState, panels_x_shift
from .win32 import make_overlay_clickthrough


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

    # Acompanha o retângulo do jogo e mostra/esconde conforme o estado.
    def _refresh(self) -> None:
        snapshot = self.shared.get()
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

    # Roda central: disco 'MENUS' + 6 nós (um por slot do perfil).
    def _draw_radial(self, painter: QPainter, snapshot: OverlaySnapshot, scale: float) -> None:
        # Só enquanto LB estiver pressionado (radial_active).
        if not snapshot.radial_active:
            return
        # Painel lateral aberto sozinho desloca a roda para o lado oposto (12,5% da largura).
        shift = panels_x_shift(snapshot.active_panels)
        center = QPointF(self.width() / 2 + self.width() * shift, self.height() / 2)
        ring_radius = 126 * scale
        node_radius = 27 * scale

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

        cfg = self.config.get()
        radial_slots = cfg["bindings"].get("radial_slots", [])

        # Nós a partir do topo, no sentido horário — mesmo layout da função radial_slot.
        for index in range(min(6, len(radial_slots))):
            angle = math.radians(-90 + index * (360/len(radial_slots)))
            point = QPointF(
                center.x() + math.cos(angle) * ring_radius,
                center.y() + math.sin(angle) * ring_radius,
            )
            # Nó selecionado cresce e muda de cor.
            selected = snapshot.radial_selection == index + 1
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
        self._draw_radial(painter, snapshot, scale)
        self._draw_mode_badge(painter, snapshot, scale)
        # Desenhado antes do toast para o aviso temporário cobrir o indicador quando sobreposto.
        self._draw_active_panels(painter, snapshot, scale)
        self._draw_toast(painter, snapshot, scale)
        painter.end()

