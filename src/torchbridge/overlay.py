from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QWidget

from .config import ConfigManager
from .models import OverlaySnapshot, SharedOverlayState
from .win32 import make_overlay_clickthrough


class GameOverlay(QWidget):
    def __init__(self, shared: SharedOverlayState, config: ConfigManager) -> None:
        super().__init__()
        self.shared = shared
        self.config = config
        self._native_styled = False
        self._last_rect = None
        self.setWindowTitle("TorchBridge Overlay")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowTransparentForInput
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh)
        self._timer.start(16)

    def showEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().showEvent(event)
        if not self._native_styled:
            make_overlay_clickthrough(int(self.winId()))
            self._native_styled = True

    def _refresh(self) -> None:
        snapshot = self.shared.get()
        rect = snapshot.game_rect
        should_show = snapshot.enabled and snapshot.game_found and rect.valid
        if should_show:
            geometry = (rect.left, rect.top, rect.width, rect.height)
            if geometry != self._last_rect:
                self.setGeometry(*geometry)
                self._last_rect = geometry
            if not self.isVisible():
                self.show()
            self.update()
        elif self.isVisible():
            self.hide()

    @staticmethod
    def _font(size: int, bold: bool = False) -> QFont:
        font = QFont("Segoe UI", size)
        font.setBold(bold)
        return font

    def _draw_aim(self, painter: QPainter, snapshot: OverlaySnapshot, scale: float) -> None:
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

    def _draw_radial(self, painter: QPainter, snapshot: OverlaySnapshot, scale: float) -> None:
        if not snapshot.radial_active:
            return
        center = QPointF(self.width() / 2, self.height() / 2)
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

        for index in range(6):
            angle = math.radians(-90 + index * 60)
            point = QPointF(
                center.x() + math.cos(angle) * ring_radius,
                center.y() + math.sin(angle) * ring_radius,
            )
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
                str(index + 1),
            )

    def _draw_mode_badge(self, painter: QPainter, snapshot: OverlaySnapshot, scale: float) -> None:
        cfg = self.config.get()
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

    def _draw_toast(self, painter: QPainter, snapshot: OverlaySnapshot, scale: float) -> None:
        import time

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

    def paintEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        del event
        snapshot = self.shared.get()
        scale = float(self.config.get()["overlay"]["scale"])
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self._draw_aim(painter, snapshot, scale)
        self._draw_radial(painter, snapshot, scale)
        self._draw_mode_badge(painter, snapshot, scale)
        self._draw_toast(painter, snapshot, scale)
        painter.end()

