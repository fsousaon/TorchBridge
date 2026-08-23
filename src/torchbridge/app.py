# Ponto de entrada da aplicação GUI: cria a bandeja do Windows, o overlay Qt,
# o motor de entrada (thread) e liga o menu/sinais de encerramento.
from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path
import signal
import sys


# Linha de comando: permite um perfil alternativo (útil em testes).
def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Controle para Torchlight PC")
    parser.add_argument("--perfil", type=Path, help="Caminho de um perfil JSON alternativo")
    return parser.parse_args()


# Log técnico em %APPDATA%\TorchBridge\torchbridge.log.
def _configure_logging(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=directory / "torchbridge.log",
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        encoding="utf-8",
    )


# Orquestra tudo: valida SO, monta Qt, inicia o motor e roda o event loop até Sair.
def main() -> int:
    # O TorchBridge depende do SendInput; fora do Windows não faz sentido rodar.
    if os.name != "nt":
        print("TorchBridge requer Windows 10 ou Windows 11.", file=sys.stderr)
        return 2

    args = _arguments()
    # Escala Qt fixa: o overlay usa pixels da janela do jogo (não escala de interface).
    os.environ.setdefault("QT_SCALE_FACTOR", "1")
    # SDL lê o controle mesmo com o Torchlight em primeiro plano.
    os.environ.setdefault("SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS", "1")

    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap
    from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

    from .config import ConfigManager, user_config_dir
    from .engine import BridgeEngine
    from .models import SharedOverlayState
    from .overlay import GameOverlay
    from .scene import SceneDetector
    from .win32 import SingleInstance, enable_dpi_awareness, show_information

    # Antes de criar janelas: DPI awareness para as coordenadas casarem com o jogo.
    enable_dpi_awareness()
    # Mutex: uma única instância ativa por sessão.
    single_instance = SingleInstance()
    # Outra instância viva: avisa e encerra esta (evita abrir duas vezes).
    if single_instance.already_running:
        show_information("TorchBridge", "O TorchBridge já está em execução perto do relógio do Windows.")
        single_instance.close()
        return 0
    # Carrega/valida o perfil; o log fica na pasta padrão do usuário.
    config = ConfigManager(args.perfil)
    _configure_logging(user_config_dir())

    # Aplicação Qt; sys.argv[:1] evita que o Qt interprete argumentos nossos.
    app = QApplication(sys.argv[:1])
    app.setApplicationName("TorchBridge")
    # Não fecha ao esconder o overlay — vive na bandeja até 'Sair'.
    app.setQuitOnLastWindowClosed(False)

    # Ícone 'TB' desenhado em código (sem asset externo) para a bandeja.
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

    # Estado compartilhado motor ↔ overlay (ponte thread-safe).
    shared = SharedOverlayState()
    # Motor de entrada (thread de 120 Hz).
    engine = BridgeEngine(config, shared)
    # Detector de cena (thread de amostragem): identifica telas de menu e
    # bloqueia a roda radial; usa o mesmo localizador de janela do motor.
    scene_detector = SceneDetector(config, shared, engine.locator)
    # Overlay Qt; a exibição é controlada pelo próprio _refresh conforme o jogo.
    overlay = GameOverlay(shared, config)
    # Ícone da bandeja: pausa, perfil, recarga e saída.
    tray = QSystemTrayIcon(icon, app)
    tray.setToolTip("TorchBridge — aguardando o Torchlight")
    menu = QMenu()
    # Checkbox 'Ativado': liga/desliga o envio de comandos.
    enabled_action = menu.addAction("Ativado")
    enabled_action.setCheckable(True)
    enabled_action.setChecked(True)
    enabled_action.triggered.connect(engine.set_enabled)

    open_profile_action = menu.addAction("Abrir perfil de controles")

    # Abre o perfil.json no editor padrão do Windows.
    def open_profile() -> None:
        os.startfile(config.path)  # type: ignore[attr-defined]

    open_profile_action.triggered.connect(open_profile)
    reload_action = menu.addAction("Recarregar perfil")
    reload_action.triggered.connect(lambda: config.reload(force=True))
    menu.addSeparator()
    exit_action = menu.addAction("Sair")
    # 'Sair' encerra o event loop → passa pelo cleanup.
    exit_action.triggered.connect(app.quit)
    tray.setContextMenu(menu)
    tray.show()
    # Aviso inicial da bandeja ao iniciar.
    tray.showMessage(
        "TorchBridge iniciado",
        "Conecte o controle e abra o Torchlight.",
        QSystemTrayIcon.MessageIcon.Information,
        2600,
    )

    tooltip_timer = QTimer()

    # Dica do ícone atualizada a cada 500 ms com o estado.
    def update_tooltip() -> None:
        state = shared.get()
        # Caso completo: controle + jogo (mostra se está ativo ou em segundo plano).
        if state.controller_connected and state.game_found:
            suffix = "ativo" if state.game_active else "jogo em segundo plano"
            tray.setToolTip(f"TorchBridge — {state.controller_name} — {suffix}")
        elif state.controller_connected:
            tray.setToolTip(f"TorchBridge — {state.controller_name} — aguardando Torchlight")
        else:
            tray.setToolTip("TorchBridge — aguardando controle")

    tooltip_timer.timeout.connect(update_tooltip)
    tooltip_timer.start(500)

    # cleanup idempotente: roda uma única vez (app.quit e aboutToQuit podem chamar).
    cleaned = False

    # Desligamento ordenado: para o motor, espera a thread, esconde a bandeja, solta o mutex.
    def cleanup() -> None:
        nonlocal cleaned
        if cleaned:
            return
        cleaned = True
        engine.stop()
        scene_detector.stop()
        # Espera o loop encerrar (limitado a 3 s para não travar a saída).
        engine.join(timeout=3.0)
        scene_detector.join(timeout=3.0)
        tray.hide()
        single_instance.close()

    # Garante a limpeza em qualquer caminho de saída.
    app.aboutToQuit.connect(cleanup)
    # Ctrl+C no terminal encerra a UI de forma limpa.
    signal.signal(signal.SIGINT, lambda *_: app.quit())
    # No Windows o Python só roda handlers de sinal quando o loop acorda; este timer de 250 ms garante isso.
    keep_signals_alive = QTimer()
    keep_signals_alive.timeout.connect(lambda: None)
    keep_signals_alive.start(250)

    # As threads do motor e do detector começam aqui; app.exec() roda até 'Sair'.
    engine.start()
    scene_detector.start()
    # Event loop Qt (bandeja + overlay) até o usuário sair.
    result = app.exec()
    cleanup()
    return int(result)


if __name__ == "__main__":
    raise SystemExit(main())
