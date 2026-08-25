"""Deriva a geometria da aba como fração do painel/janela, a partir da própria imagem
de referência (sem depender da escala absoluta):
- janela = bounding box do que difere do fundo uniforme dos cantos
- painel esquerdo = bloco claro à esquerda
- aba = componente vermelho já medido
"""

from pathlib import Path

from PIL import Image

img = Image.open(
    Path(__file__).resolve().parents[1] / "docs" / "proporcao" / "forma botao de fechar.png"
).convert("RGB")
w, h = img.size
px = img.load()
print(f"imagem {w}x{h}")

bg = px[3, 3]
print("cor de fundo dos cantos:", bg)


def far_from_bg(x, y, thresh=40):
    r, g, b = px[x, y][:3]
    return (abs(r - bg[0]) + abs(g - bg[1]) + abs(b - bg[2])) > thresh * 3


# Bounding box da janela (o que difere do fundo), passo 2 pra velocidade
xs = [x for x in range(0, w, 2) if any(far_from_bg(x, y) for y in range(0, h, 2))]
ys = [y for y in range(0, h, 2) if any(far_from_bg(x, y) for x in range(0, w, 2))]
wx0, wx1, wy0, wy1 = min(xs), max(xs), min(ys), max(ys)
print(f"janela: x {wx0}-{wx1} ({wx1 - wx0 + 1}px), y {wy0}-{wy1} ({wy1 - wy0 + 1}px)")

# Painel esquerdo: colunas com pixels claros (branco/cinza claro) à esquerda
def bright(x, y):
    r, g, b = px[x, y][:3]
    return r > 175 and g > 175 and b > 175


col_counts = []
for x in range(wx0, wx0 + (wx1 - wx0) // 2):
    col_counts.append(sum(1 for y in range(wy0, wy1, 2) if bright(x, y)))
panel_x0 = None
for i, c in enumerate(col_counts):
    if c > 40 and panel_x0 is None:
        panel_x0 = wx0 + i
    elif c <= 40 and panel_x0 is not None:
        panel_x1 = wx0 + i - 1
        break
# A aba encobre o fim do painel claro; estende até a base da aba (252 medido antes)
print(f"painel esq. claro: x {panel_x0}-{panel_x1} ({panel_x1 - panel_x0 + 1}px)")

# A aba (medida anterior): x 236-252, y 223-251
tab_x0, tab_x1, tab_y0, tab_y1 = 236, 252, 223, 251
# Bordas internas: a base da aba fica na borda interna do painel
inner_edge = tab_x1  # 252
panel_w_img = inner_edge - panel_x0
print(f"\nPainel (até borda interna {inner_edge}): {panel_w_img}px de imagem")
win_w = wx1 - wx0 + 1
win_h = wy1 - wy0 + 1
print(f"Janela: {win_w}x{win_h}px de imagem")

tab_w = tab_x1 - tab_x0 + 1
tab_h = tab_y1 - tab_y0 + 1
print(f"\nAba: {tab_w}px x {tab_h}px de imagem")
print(f"  largura da aba / largura do painel = {tab_w / panel_w_img:.4f}")
print(f"  altura da aba / altura da janela   = {tab_h / win_h:.4f}")
top_frac = (tab_y0 - wy0) / win_h
bot_frac = (tab_y1 - wy0) / win_h
print(f"  topo da aba   = {top_frac:.4f} da altura da janela")
print(f"  base da aba   = {bot_frac:.4f} da altura da janela")
print(f"  largura do painel / altura da janela = {panel_w_img / win_h:.4f} (esperado ~0.43-0.47)")
