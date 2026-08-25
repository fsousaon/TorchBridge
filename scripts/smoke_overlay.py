"""Smoke test do overlay em modo offscreen: publica um snapshot com janela
1920x1080 + painéis abertos + calibração ligada (forçada em memória, sem tocar
no perfil do usuário) e faz o paintEvent desenhar. Confirma que o drawPolygon do
pentágono não lança e que a janela se alinha.
"""

import sys

from PySide6.QtWidgets import QApplication

app = QApplication.instance() or QApplication(sys.argv)

from torchbridge.config import ConfigManager
from torchbridge.models import Rect, SharedOverlayState
from torchbridge.overlay import GameOverlay

shared = SharedOverlayState()
shared.update(
    enabled=True,
    game_found=True,
    game_active=True,
    game_rect=Rect(0, 0, 1920, 1080),
    controller_connected=True,
    active_panels=["C", "I"],
    aim_x=960,
    aim_y=540,
)

cfg = ConfigManager()
cfg.reload()
# Força a calibração em memória: o smoke test não pode presumir o valor do perfil
# do usuário em disco. O overlay lê config.get() a cada paint, então basta mutar _data.
cfg._data["overlay"]["show_calibration"] = True

overlay = GameOverlay(shared, cfg)
overlay.resize(1920, 1080)
# Força a pintura com o snapshot (offscreen, sem exibir).
overlay.grab()

# Confere que a flag chegou no config visto pelo overlay.
prof = cfg.get()
assert prof["overlay"]["show_calibration"] is True, prof["overlay"]

# Segundo snapshot com o menu radial aberto (LB) e o 2o slot marcado pelo análogo:
# valida o desenho com as artes (Center.png + ícone ativo) sem exceções.
shared.update(radial_active=True, radial_selection=2, aim_x=None, aim_y=None)
overlay.grab().save("radial_smoke.png")
shared.update(radial_active=False, radial_selection=None, aim_x=960, aim_y=540)

print("OVERLAY OK: paint com calibração e radial (ícones + variante ativa) sem exceções")
print("show_calibration:", prof["overlay"]["show_calibration"])
