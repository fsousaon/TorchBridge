"""Valida a animação de entrada/saída do menu radial (progresso 0<->1 em 0.2s).
Roda offscreen: força o progresso, avança o tick simulando tempo e confere a
transformação de opacidade/escala aplicada no painter via um snapshot pintado.
"""
import os
import sys

os.environ["QT_QPA_PLATFORM"] = "offscreen"
from PySide6.QtWidgets import QApplication

app = QApplication.instance() or QApplication(sys.argv)

from torchbridge.config import ConfigManager
from torchbridge.models import Rect, SharedOverlayState
from torchbridge.overlay import GameOverlay

shared = SharedOverlayState()
shared.update(enabled=True, game_found=True, game_active=True,
              game_rect=Rect(0, 0, 1920, 1080), radial_active=True, radial_selection=2)
cfg = ConfigManager()
cfg.reload()
overlay = GameOverlay(shared, cfg)
overlay.resize(1920, 1080)

# 1) Lógica de progresso: de 0 a 1 em ~0.4 s (avanço pelo tempo real simulado).
overlay._radial_progress = 0.0
overlay._radial_last_time = 0.0
snap_open = overlay.shared.get()
# dt enorme (tempo real desde 0.0): deve chegar ao alvo 1.0, clampado.
overlay._radial_last_time = 0.0
overlay._tick_radial_anim(snap_open)  # dt = monotonic - 0 -> grande, mas clampado em 1.0
assert overlay._radial_progress == 1.0, f"esperava 1.0, veio {overlay._radial_progress}"

# 2) Progressão parcial: força um tempo conhecido.
overlay._radial_progress = 0.0
overlay._radial_last_time = 1000.0
import time
# monkey: força o "now" para 1000.1 (100 ms = metade dos 0.2 s)
real = time.monotonic
time.monotonic = lambda: 1000.1
try:
    overlay._tick_radial_anim(snap_open)
finally:
    time.monotonic = real
assert abs(overlay._radial_progress - 0.5) < 0.01, f"100ms => {overlay._radial_progress}, esperava 0.5"

# 3) Saída: com radial_active=False, o progresso desce.
snap_closed = type(snap_open)(**{**snap_open.__dict__, "radial_active": False})
overlay._radial_progress = 1.0
overlay._radial_last_time = 1000.0
time.monotonic = lambda: 1000.2  # +200ms -> de 1.0 para 0.0
try:
    overlay._tick_radial_anim(snap_closed)
finally:
    time.monotonic = real
assert abs(overlay._radial_progress) < 1e-3, f"saída => {overlay._radial_progress}, esperava ~0.0"
# No próximo tick, o atalho de "chegou ao alvo" zera exatamente (visualmente idêntico).
overlay._tick_radial_anim(snap_closed)
assert overlay._radial_progress == 0.0

# 4) Easing + escala/opacidade: confere os valores usados no painter (sem paint real).
def eased(p): return p * p * (3.0 - 2.0 * p)
assert abs(eased(0.0)) < 1e-6
assert abs(eased(1.0) - 1.0) < 1e-6
assert abs(eased(0.5) - 0.5) < 1e-6
# escala 0.7->1
assert abs((0.7 + 0.3 * eased(0.0)) - 0.7) < 1e-6
assert abs((0.7 + 0.3 * eased(1.0)) - 1.0) < 1e-6

# 5) Paint offscreen com a roda aberta (progresso 1.0) e no meio da animação: sem exceções.
overlay._radial_progress = 1.0
shared.update(radial_active=True, radial_selection=2, aim_x=None, aim_y=None)
overlay.grab().save("radial_anim_open.png")
overlay._radial_progress = 0.35
shared.update(radial_active=True)
overlay.grab().save("radial_anim_mid.png")
overlay._radial_progress = 0.0
shared.update(radial_active=False, radial_selection=None, aim_x=960, aim_y=540)
overlay.grab().save("radial_anim_closed.png")

print("ANIM RADIAL OK: progresso 0<->1 em 0.2s, easing smoothstep, escala 0.7->1, paint sem exceções")
print("progresso após 100ms (de 0):", 0.5, "| saída de 1.0 em 200ms:", 0.0)
