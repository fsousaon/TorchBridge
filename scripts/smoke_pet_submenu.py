"""Valida a sublinha de pet actions (4 quadradinhos com ícones) no menu radial offscreen.
Desenha o overlay com a roda aberta no slot P + pet_submenu_open=True, confere os
pixels dos quadradinhos (ícones + marcador dourado) e os pontos de clique do modo
de calibração, e publica snapshots para o usuário.
"""
import os
import sys

os.environ["QT_QPA_PLATFORM"] = "offscreen"
from PySide6.QtWidgets import QApplication

app = QApplication.instance() or QApplication(sys.argv)

from torchbridge.config import ConfigManager
from torchbridge.models import Rect, SharedOverlayState, pet_click_point
from torchbridge.overlay import GameOverlay

shared = SharedOverlayState()
cfg = ConfigManager()
cfg.reload()
# Força a calibração em memória: o smoke não pode presumir o valor do perfil do
# usuário em disco (ele liga/desliga a flag o tempo todo). O overlay lê config.get()
# a cada paint, então basta mutar em memória — o perfil em disco fica intocado.
cfg._data["overlay"]["show_calibration"] = True

overlay = GameOverlay(shared, cfg)
overlay.resize(1920, 1080)

rect = Rect(0, 0, 1920, 1080)

# 1) Roda aberta no slot P (seleção 5) com a sublinha ligada e marcador no 4º.
shared.update(
    enabled=True, game_found=True, game_active=True,
    game_rect=rect, radial_active=True, radial_selection=5,
    pet_submenu_open=True, pet_submenu_selection=4,
    aim_x=None, aim_y=None,
)
overlay._radial_progress = 1.0
img = overlay.grab()
img.save("pet_submenu_open.png")

# 2) Confere os pixels: P no setor 5 (ângulo -90 + 4*360/7 = 115,7°) → nó em
# (960 + 126*cos, 540 + 126*sin) ≈ (905, 654). Fileira de CÍRCULOS (ago/2026):
# diâmetro 44, vão 20, topo y≈704 → centro da fileira y≈726. A fileira NÃO é
# centralizada: o centro do 4º círculo cai em x=905 (eixo do nó), e os centros
# ficam em 713, 777, 841 e 905 (vão de 64px entre centros).
def sample(x, y):
    return img.toImage().pixelColor(x, y)

# Centro do primeiro círculo: (905 - 3*64, 726) = (713, 726) — o ícone da espada.
c1 = sample(713, 726)
# Abaixo do ícone, dentro do 4º círculo (marcado): o FUNDO escuro dele (24,28,34).
# O centro seria a moeda (ícone dourado), que também é clara — por isso o ponto.
c4 = sample(905, 745)
# Fundo do círculo 1 abaixo do ícone (713, 745): cinza claro dos não marcados.
bg1 = sample(713, 745)
# Entre o 1º (termina em 735) e o 2º (começa em 755): ~745, 726 NÃO pode ser círculo.
gap = sample(745, 726)

print("1o centro (ícone):", c1.name(), "| 4o fundo (escuro):", c4.name(),
      "| fundo 1o:", bg1.name(), "| vão:", gap.name())
# O fundo dos círculos NÃO marcados continua claro; o do marcado é escuro.
assert bg1.lightness() > 150, f"fundo do 1o círculo não é claro: {bg1.name()}"
assert gap.lightness() < 100, f"vão parece círculo: {gap.name()}"
# O fundo do 4º (marcado) é bem mais escuro que o cinza dos outros.
assert c4.lightness() < bg1.lightness() - 30, f"4o círculo não mudou (fundo escuro esperado): {c4.name()}"

# Marcador: o 4º círculo tem borda dourada (255,202,82) e o 1º NÃO tem.
# Com o antialiasing, a linha exata do pen varia 1-2px: escaneia a faixa do topo
# do círculo (y 700..710) e procura o pixel mais 'dourado' de cada um.
def border(x, y):
    c = img.toImage().pixelColor(x, y)
    return c.red(), c.green(), c.blue()

def most_gold(x, y0, y1):
    best = None
    for y in range(y0, y1):
        r, g, b = border(x, y)
        # Dourado: vermelho e verde altos, azul baixo.
        score = r + g - 2 * b
        if best is None or score > best[0]:
            best = (score, (r, g, b), y)
    return best  # (score, (r, g, b), y)

g4 = most_gold(905, 700, 712)  # topo do 4º círculo (marcado)
g1 = most_gold(713, 700, 712)  # topo do 1º círculo (não marcado)
print("melhor dourado 4o:", g4, "| melhor do 1o:", g1)
assert g4[1][0] > 200 and g4[1][1] > 150 and g4[1][2] < 130, f"borda marcada não é dourada: {g4}"
assert not (g1[1][0] > 200 and g1[1][1] > 150 and g1[1][2] < 130), f"1o círculo com borda dourada por engano: {g1}"

# 3) Sublinha desligada (d-pad cima): os pixels viram fundo/roda de novo.
shared.update(pet_submenu_open=False, pet_submenu_selection=None)
img2 = overlay.grab()
img2.save("pet_submenu_closed.png")
off = img2.toImage().pixelColor(713, 726)
print("com sublinha fechada no mesmo ponto:", off.name())
assert off.lightness() < 150, f"sublinha 'fechada' ainda clara: {off.name()}"

# 4) Modo de calibração: os 4 pontos de clique (pet_click_point) aparecem como
# bolinhas vermelhas com cruz na caixinha do pet — confere o pixel de cada um.
shared.update(radial_active=False, radial_selection=None)
img3 = overlay.grab()
img3.save("pet_calibration.png")
for index in (1, 2, 3, 4):
    px, py = pet_click_point(rect, index)
    c = img3.toImage().pixelColor(px, py)
    print(f"ponto {index} em ({px}, {py}):", c.name())
    # O traço da cruz passa exatamente pelo centro: vermelho forte.
    assert c.red() > 200 and c.green() < 120 and c.blue() < 120, \
        f"ponto {index} não tem o traço vermelho no centro: {c.name()}"

print("PET SUBMENU OK: 4 círculos com ícones sob o slot P, somem ao desligar; "
      "pontos de clique do calibração presentes")
