"""Valida a sublinha de pet actions (4 quadradinhos) no menu radial offscreen.
Desenha o overlay com a roda aberta no slot P + pet_submenu_open=True, confere os
pixels dos quadradinhos na posição esperada e publica snapshots para o usuário.
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
cfg = ConfigManager()
cfg.reload()
overlay = GameOverlay(shared, cfg)
overlay.resize(1920, 1080)

# 1) Roda aberta no slot P (seleção 5) com a sublinha ligada.
shared.update(
    enabled=True, game_found=True, game_active=True,
    game_rect=Rect(0, 0, 1920, 1080), radial_active=True, radial_selection=5,
    pet_submenu_open=True, aim_x=None, aim_y=None,
)
overlay._radial_progress = 1.0
img = overlay.grab()
img.save("pet_submenu_open.png")

# 2) Confere os pixels: P no setor 5 (ângulo -90 + 4*360/7 = 115,7°) → nó em
# (960 + 126*cos, 540 + 126*sin) ≈ (905, 654). Fileira: topo y≈704, lado 22, vão 10,
# largura total 118 centralizada no x do nó (~846..964).
def sample(x, y):
    return img.toImage().pixelColor(x, y)

# Centro do primeiro quadrado: (846.35 + 11, 703.55 + 11) ≈ (857, 715).
c1 = sample(857, 715)
# Centro do quarto quadrado: (964 - 11, 715) ≈ (953, 715).
c4 = sample(953, 715)
# Entre o 1º e o 2º (no vão 868..878, ~873, 715) NÃO pode ser quadrado.
gap = sample(873, 715)
# Bem acima da fileira (857, 690) não é quadrado.
above = sample(857, 690)

print("1o quadrado:", c1.name(), "| 4o quadrado:", c4.name(),
      "| vão:", gap.name(), "| acima:", above.name())
# O cinza do quadrado (216,219,224,242) é bem mais claro que o fundo/roda.
assert c1.lightness() > 150, f"1o quadrado não é claro o bastante: {c1.name()}"
assert c4.lightness() > 150, f"4o quadrado não é claro o bastante: {c4.name()}"
assert gap.lightness() < c1.lightness() - 30, f"vão parece quadrado: {gap.name()}"

# 3) Sublinha desligada (d-pad cima): os pixels viram fundo/roda de novo.
shared.update(pet_submenu_open=False)
img2 = overlay.grab()
img2.save("pet_submenu_closed.png")
off = img2.toImage().pixelColor(857, 715)
print("com sublinha fechada no mesmo ponto:", off.name())
assert off.lightness() < 150, f"sublinha 'fechada' ainda clara: {off.name()}"

print("PET SUBMENU OK: 4 quadradinhos desenhados sob o slot P, somem ao desligar")
