"""Extrai a geometria exata das abas vermelhas de fechar em 'forma botao de fechar.png'.

Estratégia:
1. Mapa ASCII 128x62 da imagem inteira (# = vermelho, + = painel claro, . = resto)
   pra entender o layout: onde ficam os painéis e as abas.
2. Componentes conexos do mask vermelho com área >= 40px: cada um é renderizado
   em ASCII na resolução original, com dimensões e extremos.
3. Pra cada aba: varre scanlines e printa (y, x_min, x_max) — a varredura revela
   o formato (base constante que cresce até a ponta e encolhe).
"""

from pathlib import Path

from PIL import Image

IMAGE = Path(__file__).resolve().parents[1] / "docs" / "proporcao" / "forma botao de fechar.png"


def red(p):
    r, g, b = p[0], p[1], p[2]
    return r > 150 and g < 110 and b < 110 and (r - g) > 60


def bright(p):
    r, g, b = p[0], p[1], p[2]
    return r > 175 and g > 175 and b > 175


def main() -> None:
    img = Image.open(IMAGE).convert("RGB")
    w, h = img.size
    px = img.load()
    print(f"Imagem: {w}x{h}\n")

    # 1) Mapa ASCII do layout
    cols, rows = 128, 62
    print("=== MAPA DO LAYOUT (# vermelho, + claro, . resto) ===")
    for ry in range(rows):
        line = []
        for rx in range(cols):
            x0, y0 = rx * w // cols, ry * h // rows
            x1, y1 = max(x0 + 1, (rx + 1) * w // cols), max(y0 + 1, (ry + 1) * h // rows)
            nred, nlight = 0, 0
            for yy in range(y0, y1, 2):
                for xx in range(x0, x1, 2):
                    p = px[xx, yy]
                    if red(p):
                        nred += 1
                    elif bright(p):
                        nlight += 1
            line.append("#" if nred >= 2 else ("+" if nlight >= 2 else "."))
        print("".join(line))

    # 2) Componentes conexos vermelhos (BFS)
    seen = [[False] * w for _ in range(h)]
    components: list[list[tuple[int, int]]] = []
    for y in range(h):
        for x in range(w):
            if seen[y][x] or not red(px[x, y]):
                continue
            stack = [(x, y)]
            seen[y][x] = True
            comp = []
            while stack:
                cx, cy = stack.pop()
                comp.append((cx, cy))
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    nx, ny = cx + dx, cy + dy
                    if 0 <= nx < w and 0 <= ny < h and not seen[ny][nx] and red(px[nx, ny]):
                        seen[ny][nx] = True
                        stack.append((nx, ny))
            components.append(comp)

    big = [c for c in components if len(c) >= 40]
    print(f"\n=== {len(big)} componentes vermelhos >= 40px ===")
    for idx, comp in enumerate(sorted(big, key=lambda c: min(x for x, _ in c)), start=1):
        xs = [x for x, _ in comp]
        ys = [y for _, y in comp]
        x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
        print(f"\n--- Componente {idx}: {len(comp)}px, x {x0}-{x1} ({x1 - x0 + 1}px), "
              f"y {y0}-{y1} ({y1 - y0 + 1}px) ---")

        # Render ASCII do componente (1 char = 1px)
        for y in range(y0, y1 + 1):
            row = "".join("#" if red(px[x, y]) else "." for x in range(x0, x1 + 1))
            if y < y0 + 12 or y > y1 - 6 or (y - y0) % 4 == 0:
                print(f"  y{y:>3}: {row}")

        # Scanlines completas (y: x_min x_max largura) — revela o formato
        print("  scanlines:")
        for y in range(y0, y1 + 1):
            xs_row = [x for x in range(x0, x1 + 1) if red(px[x, y])]
            if xs_row:
                print(f"    y{y}: {min(xs_row)} {max(xs_row)}  w={max(xs_row) - min(xs_row) + 1}")


if __name__ == "__main__":
    main()
