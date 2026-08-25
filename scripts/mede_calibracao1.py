"""Mede calibracao1.png: caixas laranja (calibração nossa) vs abas vermelhas (botão real do jogo).

Detecta:
- caixas laranja (traço + preenchido, cor ~ 255,159,67)
- abas vermelhas do jogo (vermelho forte)
Imprime posições relativas à janela do jogo (a menor região de 'tela' que contém tudo).
"""

from pathlib import Path

from PIL import Image

IMAGE = Path(__file__).resolve().parents[1] / "docs" / "proporcao" / "calibracao1.png"


def is_orange(p):
    r, g, b = p[0], p[1], p[2]
    return r > 200 and 110 < g < 190 and b < 120 and (r - b) > 120


def is_red(p):
    r, g, b = p[0], p[1], p[2]
    return r > 150 and g < 100 and b < 100 and (r - g) > 70


def components(mask, min_area=30):
    h = len(mask)
    w = len(mask[0])
    seen = [[False] * w for _ in range(h)]
    out = []
    for y in range(h):
        for x in range(w):
            if seen[y][x] or not mask[y][x]:
                continue
            stack = [(x, y)]
            seen[y][x] = True
            comp = []
            while stack:
                cx, cy = stack.pop()
                comp.append((cx, cy))
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    nx, ny = cx + dx, cy + dy
                    if 0 <= nx < w and 0 <= ny < h and not seen[ny][nx] and mask[ny][nx]:
                        seen[ny][nx] = True
                        stack.append((nx, ny))
            if len(comp) >= min_area:
                out.append(comp)
    return out


def bbox(comp):
    xs = [x for x, _ in comp]
    ys = [y for _, y in comp]
    return min(xs), min(ys), max(xs), max(ys)


def main() -> None:
    img = Image.open(IMAGE).convert("RGB")
    w, h = img.size
    px = img.load()
    print(f"Imagem: {w}x{h}\n")

    # Mapa de máscaras (passo 1 pra velocidade)
    orange = [[False] * w for _ in range(h)]
    red = [[False] * w for _ in range(h)]
    for y in range(h):
        for x in range(w):
            p = px[x, y]
            if is_orange(p):
                orange[y][x] = True
            elif is_red(p):
                red[y][x] = True

    or_comps = components(orange, min_area=20)
    re_comps = components(red, min_area=20)

    print(f"Caixas laranja: {len(or_comps)}")
    for i, c in enumerate(sorted(or_comps, key=lambda c: bbox(c)[0]), 1):
        x0, y0, x1, y1 = bbox(c)
        print(f"  L{i}: x {x0}-{x1} ({x1 - x0 + 1}px), y {y0}-{y1} ({y1 - y0 + 1}px), {len(c)}px")

    print(f"\nComponentes vermelhos: {len(re_comps)}")
    for i, c in enumerate(sorted(re_comps, key=lambda c: bbox(c)[0]), 1):
        x0, y0, x1, y1 = bbox(c)
        print(f"  V{i}: x {x0}-{x1} ({x1 - x0 + 1}px), y {y0}-{y1} ({y1 - y0 + 1}px), {len(c)}px")
        # Render compacto pra ver formato
        if x1 - x0 <= 60 and y1 - y0 <= 60:
            for y in range(y0, y1 + 1):
                row = "".join("#" if red[y][x] else "." for x in range(x0, x1 + 1))
                print(f"    y{y}: {row}")


if __name__ == "__main__":
    main()
