"""Mede o botão vermelho REAL do jogo em docs/proporcao/calibracao1.png.

Detecção de vermelho puro (r>180, g<100, b<100) — exclui a caixa laranja de
calibração (g≈165) e os painéis ciano. Agrupa os pixels em componentes
conectados (BFS) e imprime o bounding box de cada um. Em seguida calcula,
com o painel = 7/15 da altura da imagem, as frações-alvo para:
  - altura da aba  (topo/base em fração da altura da janela)
  - ponta da aba   (em fração da largura do painel)
"""

from __future__ import annotations

from PIL import Image

PAINEL_FRAC = 7 / 15
CAMINHO = r"docs/proporcao/calibracao1.png"


def componentes(img):
    """BFS sobre pixels vermelhos, devolve lista de (minx, miny, maxx, maxy, n)."""
    px = img.load()
    w, h = img.size
    ver = {}
    for y in range(h):
        for x in range(w):
            r, g, b = px[x, y][:3]
            if r > 180 and g < 100 and b < 100:
                ver[(x, y)] = True
    vistos = set()
    comps = []
    for s in ver:
        if s in vistos:
            continue
        fila = [s]
        vistos.add(s)
        xs, ys = [s[0]], [s[1]]
        while fila:
            cx, cy = fila.pop()
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    v = (cx + dx, cy + dy)
                    if v in ver and v not in vistos:
                        vistos.add(v)
                        fila.append(v)
                        xs.append(v[0])
                        ys.append(v[1])
        comps.append((min(xs), min(ys), max(xs), max(ys), len(xs)))
    return comps


def main():
    img = Image.open(CAMINHO).convert("RGB")
    W, H = img.size
    painel = PAINEL_FRAC * H
    print(f"imagem {W}x{H}, largura do painel = {painel:.1f}px")
    print()
    comps = [c for c in componentes(img) if c[4] >= 30]
    comps.sort(key=lambda c: c[0])
    for i, (x0, y0, x1, y1, n) in enumerate(comps):
        w, h = x1 - x0 + 1, y1 - y0 + 1
        lado = "esquerdo" if (x1 - x0) + (y1 - y0) and x1 < W / 2 else "direito"
        print(f"componente {i}: x {x0}-{x1} ({w}px), y {y0}-{y1} ({h}px), "
              f"{n}px, painel {lado}")
        print(f"   altura  = {h}px = {h / H:.3f} da altura da janela")
        print(f"   ponta   = {w}px = {w / painel:.3f} da largura do painel")
    # Frações-alvo com o maior componente (o botão do jogo).
    x0, y0, x1, y1, n = max(comps, key=lambda c: c[4])
    h_real = y1 - y0 + 1
    w_real = x1 - x0 + 1
    centro_y = (y0 + y1) / 2 / H
    print()
    print(f"MAIOR componente (botão do jogo): altura {h_real}px, ponta {w_real}px")
    print(f"centro em y = {centro_y:.3f} (fração da altura)")
    meia = h_real / 2 / H
    print(f"alvo: topo  = {centro_y - meia:.3f}, base = {centro_y + meia:.3f}")
    print(f"alvo: ponta = {w_real / painel:.3f} (fração do painel)")


if __name__ == "__main__":
    main()
