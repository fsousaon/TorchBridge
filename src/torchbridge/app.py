from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path
import signal
import sys


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Controle para Torchlight PC")
    parser.add_argument("--perfil", type=Path, help="Caminho de um perfil JSON alternativo")
    return parser.parse_args()


def _configure_logging(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=directory / "torchbridge.log",
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        encoding="utf-8",
    )


def main() -> int:
    if os.name != "nt":
        print("TorchBridge requer Windows 10 ou Windows 11.", file=sys.stderr)
        return 2

    args = _arguments()
    os.environ.setdefault("QT_SCALE_FACTOR", "1")
    os.environ.setdefault("SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS", "1")

    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap
    from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

    from .config import ConfigManager, user_config_dir
    from .engine import BridgeEngine
    from .models import SharedOverlayState
    from .overlay import GameOverlay
    from .win32 import SingleInstance, enable_dpi_awareness, show_information

    enable_dpi_awareness()
    single_instance = SingleInstance()
    if single_instance.already_running:
        show_information("TorchBridge", "O TorchBridge já está em execução perto do relógio do Windows.")
        single_instance.close()
        return 0
    config = ConfigManager(args.perfil)
    _configure_logging(user_config_dir())

    app = QApplication(sys.argv[:1])
    app.setApplicationName("TorchBridge")
    app.setQuitOnLastWindowClosed(False)

    pixmap = QPixmap(64, 64)
    pixmap.fill(QColor(0, 0, 0, 0))
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor(7, 24, 34))
    painter.setPen(QColor(75, 222, 247))
    painter.drawEllipse(4, 4, 56, 56)
    painter.setFont(QFont("Segoe UI", 18, QFont.Weight.Bold))
    painter.setPen(QColor(238, 250, 252))
    painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, "TB")
    painter.end()
    icon = QIcon(pixmap)
    app.setWindowIcon(icon)

    shared = SharedOverlayState()
    engine = BridgeEngine(config, shared)
    overlay = GameOverlay(shared, config)
    tray = QSystemTrayIcon(icon, app)
    tray.setToolTip("TorchBridge — aguardando o Torchlight")
    menu = QMenu()
    enabled_action = menu.addAction("Ativado")
    enabled_action.setCheckable(True)
    enabled_action.setChecked(True)
    enabled_action.triggered.connect(engine.set_enabled)

    open_profile_action = menu.addAction("Abrir perfil de controles")

    def open_profile() -> None:
        os.startfile(config.path)  # type: ignore[attr-defined]

    open_profile_action.triggered.connect(open_profile)
    reload_action = menu.addAction("Recarregar perfil")
    reload_action.triggered.connect(lambda: config.reload(force=True))
    menu.addSeparator()
    exit_action = menu.addAction("Sair")
    exit_action.triggered.connect(app.quit)
    tray.setContextMenu(menu)
    tray.show()
    tray.showMessage(
        "TorchBridge iniciado",
        "Conecte o controle e abra o Torchlight.",
        QSystemTrayIcon.MessageIcon.Information,
        2600,
    )

    tooltip_timer = QTimer()

    def update_tooltip() -> None:
        state = shared.get()
        if state.controller_connected and state.game_found:
            suffix = "ativo" if state.game_active else "jogo em segundo plano"
            tray.setToolTip(f"TorchBridge — {state.controller_name} — {suffix}")
        elif state.controller_connected:
            tray.setToolTip(f"TorchBridge — {state.controller_name} — aguardando Torchlight")
        else:
            tray.setToolTip("TorchBridge — aguardando controle")

    tooltip_timer.timeout.connect(update_tooltip)
    tooltip_timer.start(500)

    cleaned = False

    def cleanup() -> None:
        nonlocal cleaned
        if cleaned:
            return
        cleaned = True
        engine.stop()
        engine.join(timeout=3.0)
        tray.hide()
        single_instance.close()

    app.aboutToQuit.connect(cleanup)
    signal.signal(signal.SIGINT, lambda *_: app.quit())
    keep_signals_alive = QTimer()
    keep_signals_alive.timeout.connect(lambda: None)
    keep_signals_alive.start(250)

    engine.start()
    result = app.exec()
    cleanup()
    return int(result)


if __name__ == "__main__":
    raise SystemExit(main())
