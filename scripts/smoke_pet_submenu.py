"""Valida a sublinha de pet actions (4 quadradinhos com ícones) no menu radial offscreen.
Desenha o overlay com a roda aberta no slot P + pet_submenu_open=True, confere os
pixels dos quadradinhos (ícones + marcador dourado) e os pontos de clique do modo
de calibração, e publica snapshots para o usuário.
"""
import os
import sys
import time

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
overlay._pet_submenu_progress = 1.0
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

# 3) Animação de ENTRADA — progress 0: todas as bolinhas estão no CENTRO do nó P
# (905, 654), atrás do ícone — o clip (que começa em 654+44 = 698) as segura e nada
# vaza. Confere apenas abaixo da linha do clip (698..751): acima disso mora o próprio
# ícone P, que é brilhante e legítimo.
overlay._pet_submenu_progress = 0.0
img_in0 = overlay.grab()
img_in0.save("pet_submenu_anim_in0.png")
for x in (713, 777, 841, 905):
    maxbright = max(
        img_in0.toImage().pixelColor(x, y).lightness() for y in range(698, 751)
    )
    print(f"entrada progress=0, coluna x={x} — brilho máx abaixo do clip: {maxbright}")
    assert maxbright < 150, f"bolinha visível com progress 0 na coluna x={x}"

def col_blue(img, x, y0, y1):
    return max(img.toImage().pixelColor(x, y).blue() for y in range(y0, y1))

# 4) Animacao de ENTRADA — progress 0.5 (janelas de 25%: a 4ª viaja em [0, 0.25],
# a 3ª em [0.25, 0.5], a 2ª em [0.5, 0.75], a 1ª em [0.75, 1.0]). Neste frame:
# 4ª (x=905) e 3ª (x=841) JÁ NO LUGAR FINAL (cy=726, opacidade 1); a 2ª (x=777)
# acabou de partir do nó e a 1ª (x=713) nem começou — ambas ainda em (905,654),
# escondidas pelo clip. O fundo das bolinhas não marcadas é cinza claro (216,219,224):
# confere pelo CANAL AZUL — robusto mesmo com a opacidade intermediária do fade.
overlay._pet_submenu_progress = 0.5
img_in50 = overlay.grab()
img_in50.save("pet_submenu_anim_mid.png")
b4 = col_blue(img_in50, 905, 698, 748)   # 4ª no lugar final
b3 = col_blue(img_in50, 841, 698, 748)   # 3ª no lugar final
b2 = col_blue(img_in50, 777, 698, 751)   # 2ª ainda no nó (escondida)
b1 = col_blue(img_in50, 713, 698, 751)   # 1ª ainda no nó (escondida)
print(f"entrada 0.5: 4o no lugar (azul {b4}) | 3o no lugar (azul {b3}) | "
      f"2o escondido (azul {b2}) | 1o escondido (azul {b1})")
assert b4 > 100, f"4o círculo não chegou ao lugar final com progress 0.5 (azul {b4})"
assert b3 > 100, f"3o círculo não chegou ao lugar final com progress 0.5 (azul {b3})"
assert b2 < 50, f"2o círculo visível antes da janela dele (progress 0.5): azul {b2}"
assert b1 < 50, f"1o círculo visível antes da janela dele (progress 0.5): azul {b1}"

# 5) Animacao de ENTRADA — progress 0.875: a 1ª (x=713) está na METADE da diagonal
# nó→final: lerp 50% entre (905,654) e (713,726) → centro em (809,690), raio 22.
# Visível abaixo do clip (698) em torno de x=809, y 698..712; o lugar final dela
# (713,726) ainda está vazio. As 4/3/2 já estão nas posições finais.
overlay._pet_submenu_progress = 0.875
img_in875 = overlay.grab()
img_in875.save("pet_submenu_anim_late.png")
b1_rising = col_blue(img_in875, 809, 698, 712)   # 1ª no meio do caminho
b1_final = col_blue(img_in875, 713, 728, 748)    # rodapé do lugar final (vazio)
print(f"entrada 0.875: 1o em (809,690) (azul {b1_rising}) | lugar final (azul {b1_final})")
assert b1_rising > 100, f"1o círculo não está no meio do caminho (azul {b1_rising})"
assert b1_final < 50, f"1o círculo já no lugar final com progress 0.875 (azul {b1_final})"

# 6) Animacao de SAÍDA — pet_submenu_open=False (como o engine publica) com
# progress 0.5: o desenho continua no nó P guardado (caminho reverso do leque).
# Mesma configuração do frame de entrada 0.5: 4ª e 3ª nos lugares finais; a 1ª
# (coluna x=713) ainda escondida atrás do ícone.
shared.update(pet_submenu_open=False, pet_submenu_selection=None)
overlay._pet_submenu_progress = 0.5
img_out50 = overlay.grab()
img_out50.save("pet_submenu_anim_out50.png")
o4 = col_blue(img_out50, 905, 698, 748)
o1 = col_blue(img_out50, 713, 698, 751)
print(f"saída 0.5: 4o voltando (azul {o4}) | 1o escondido (azul {o1})")
assert o4 > 100, f"saída não desenha o 4o no nó guardado (azul {o4})"
assert o1 < 50, f"saída 0.5 com 1o visível (deveria estar no nó, atrás do ícone): azul {o1}"

# 6) Saída completa: tick com dt grande zera o progresso; pixels voltam ao fundo.
overlay._pet_submenu_last_time = time.monotonic() - 1.0
overlay._tick_pet_submenu_anim(shared.get())
assert abs(overlay._pet_submenu_progress) < 1e-3, f"saída não zerou: {overlay._pet_submenu_progress}"
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
