# Por fileira: mede os painéis cinza e os retângulos vermelhos, reporta frações.
import sys
from pathlib import Path

from PIL import Image


def is_gray(r, g, b) -> bool:
    return r >= 160 and g >= 160 and b >= 160 and max(r, g, b) - min(r, g, b) < 35


def is_red(r, g, b) -> bool:
    return r >= 140 and g < 110 and b < 110


def spans(counts, threshold) -> list[tuple[int, int]]:
    out, inside, start = [], False, 0
    limit = max(2, threshold)
    for i, c in enumerate(counts):
        if c >= limit and not inside:
            inside, start = True, i
        elif c < limit and inside:
            inside = False
            if i - start >= 3:
                out.append((start, i - 1))
    if inside and len(counts) - start >= 3:
        out.append((start, len(counts) - 1))
    return out


def main() -> None:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent.parent / "docs/proporcao/proporçao.png"
    img = Image.open(path).convert("RGB")
    w, h = img.size
    px = img.load()

    gray = [[False] * w for _ in range(h)]
    red = [[False] * w for _ in range(h)]
    for y in range(h):
        for x in range(w):
            r, g, b = px[x, y][:3]
            if is_gray(r, g, b):
                gray[y][x] = True
            elif is_red(r, g, b):
                red[y][x] = True

    gray_rows = [0] * h
    for y in range(h):
        gray_rows[y] = sum(gray[y])
    rows = spans(gray_rows, 20)

    for y0, y1 in rows:
        # Colunas cinza dentro desta fileira.
        cols = [0] * w
        for y in range(y0, y1 + 1):
            for x in range(w):
                if gray[y][x]:
                    cols[x] += 1
        col_spans = [s for s in spans(cols, (y1 - y0) // 3) if s[1] - s[0] >= 30]
        # Retângulos vermelhos da fileira (BFS).
        seen = [[False] * w for _ in range(y1 - y0 + 1)]
        boxes = []
        for y in range(y0, y1 + 1):
            for x in range(w):
                if red[y][x] and not seen[y - y0][x]:
                    stack, xs, ys = [(x, y)], [x], [y]
                    seen[y - y0][x] = True
                    while stack:
                        cx, cy = stack.pop()
                        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                            nx, ny = cx + dx, cy + dy
                            if y0 <= ny <= y1 and 0 <= nx < w and red[ny][nx] and not seen[ny - y0][nx]:
                                seen[ny - y0][nx] = True
                                xs.append(nx)
                                ys.append(ny)
                                stack.append((nx, ny))
                    bw, bh = max(xs) - min(xs) + 1, max(ys) - min(ys) + 1
                    if bw >= 4 and bh >= 6:  # retângulo de marcação (não ruído de texto)
                        boxes.append((min(xs), min(ys), max(xs), max(ys)))
        print(f"\nFileira y {y0}-{y1} (altura {y1 - y0 + 1}px)")
        for (cx0, cx1) in col_spans:
            pw = cx1 - cx0 + 1
            print(f"  painel cinza x {cx0}-{cx1} (largura {pw}px)")
            for (bx0, by0, bx1, by1) in boxes:
                if cx0 - 10 <= bx0 <= cx1 + 10:
                    # Fração horizontal: posição da borda esquerda do vermelho dentro do painel (0..1+).
                    fx0 = (bx0 - cx0) / pw
                    fx1 = (bx1 - cx0) / pw
                    # Fração vertical: do topo do painel.
                    fy0 = (by0 - y0) / (y1 - y0 + 1)
                    fy1 = (by1 - y0) / (y1 - y0 + 1)
                    side = "ESQUERDO" if cx0 < w / 2 else "DIREITO"
                    print(
                        f"    VERMELHO {side}: x {bx0}-{bx1} (fração do painel: {fx0:.3f}–{fx1:.3f}), "
                        f"y {by0}-{by1} (fração da altura: {fy0:.3f}–{fy1:.3f})"
                    )


if __name__ == "__main__":
    main()
