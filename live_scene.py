# Diagnóstico ao vivo da identificação de cena: roda fora do app completo e
# usa as MESMAS peças de produção (WindowLocator, capture_client_grid,
# SceneAnalyzer com os limiares do perfil) para dizer em que tela o jogo está.
# Uso: deixe a janela do Torchlight em primeiro plano e rode:
#   PYTHONPATH=src .venv/Scripts/python.exe live_scene.py [segundos] [--save]
# Os segundos contam apenas enquanto o jogo está em primeiro plano: o script
# espera o quanto for preciso até você focar a janela, e só então amostra.
# Com --save, salva em capturas/ (a) o print 1:1 da área do jogo e (b) a
# grade 48×27 ampliada com as faixas de análise (topo vermelho, base
# amarela) e a máscara (escurecida) desenhadas por cima.
import argparse
from pathlib import Path
import sys
import time

from torchbridge.config import ConfigManager
from torchbridge.scene import (
    SceneAnalyzer,
    SceneKind,
    grid_color_features,
    grid_motion,
    ignore_cells,
    is_blank_grid,
)
from torchbridge.win32 import WindowLocator, capture_client_grid, enable_dpi_awareness

# Onde as capturas são salvas: capturas/ na raiz do repo (junto a este arquivo).
SAVE_DIR = Path(__file__).resolve().parent / "capturas"


def _timestamp(tick: float) -> str:
    return time.strftime("%H%M%S") + f"_{int((tick % 60) * 1000):04d}"


# Salva o print 1:1 da área do jogo (buffer BGRA do DIB → PNG via Qt, sem
# dependência extra: QImage Format_RGB32 é exatamente BGRA em memória little-endian).
def save_full_capture(raw: bytes, width: int, height: int, path: Path) -> None:
    from PySide6.QtGui import QImage

    image = QImage(raw, width, height, width * 4, QImage.Format.Format_RGB32)
    image.save(str(path), "PNG")


# Renderiza a grade de cor ampliada (cada célula = um bloco) com os overlays da
# análise: faixa do topo (vermelho), faixa da base (amarelo) e máscara (preto).
def save_grid_capture(
    color_grid, mask: frozenset[tuple[int, int]], path: Path, scale: int = 16
) -> None:
    from PySide6.QtGui import QColor, QImage, QPainter

    rows = len(color_grid)
    cols = len(color_grid[0])
    image = QImage(cols * scale, rows * scale, QImage.Format.Format_RGB32)
    image.fill(0xFF000000)
    painter = QPainter(image)
    for row_index, row in enumerate(color_grid):
        for col_index, (b, g, r) in enumerate(row):
            painter.fillRect(
                col_index * scale, row_index * scale, scale, scale, QColor(r, g, b)
            )
    # Faixas de cor da análise (mesmas regras do grid_color_features): topo
    # (HUD) em vermelho e faixa do painel do pause em amarelo — nas colunas
    # centrais, as únicas que as features consideram.
    band_x0 = int(cols * 0.20) * scale
    band_x1 = int(cols * 0.80) * scale
    for row_index in range(rows):
        center_y = (row_index + 0.5) / rows
        if center_y < 0.2:
            painter.fillRect(
                band_x0, row_index * scale, band_x1 - band_x0, scale, QColor(255, 60, 60, 70)
            )
        elif 0.39 <= center_y <= 0.60:
            painter.fillRect(
                band_x0, row_index * scale, band_x1 - band_x0, scale, QColor(255, 230, 60, 90)
            )
    # Células ignoradas pela máscara (escurecidas).
    for row_index, col_index in mask:
        painter.fillRect(
            col_index * scale, row_index * scale, scale, scale, QColor(0, 0, 0, 110)
        )
    # Linhas de grade para enxergar as células.
    painter.setPen(QColor(0, 0, 0, 70))
    for col_index in range(1, cols):
        painter.drawLine(col_index * scale, 0, col_index * scale, rows * scale)
    for row_index in range(1, rows):
        painter.drawLine(0, row_index * scale, cols * scale, row_index * scale)
    painter.end()
    image.save(str(path), "PNG")


def main() -> None:
    parser = argparse.ArgumentParser(description="Identificação de cena ao vivo")
    parser.add_argument("segundos", nargs="?", type=float, default=12.0)
    parser.add_argument("--save", action="store_true", help="salva capturas em capturas/")
    args = parser.parse_args()

    enable_dpi_awareness()
    config = ConfigManager()
    cfg = config.get()
    scenes = cfg["scenes"]
    target = cfg["target"]

    locator = WindowLocator(target["process_names"], target["window_titles"])
    analyzer = SceneAnalyzer(
        motion_gameplay=float(scenes["motion_gameplay"]),
        motion_menu=float(scenes["motion_menu"]),
        confirm_samples=int(scenes["confirm_samples"]),
        top_blue_menu=float(scenes["top_blue_menu"]),
        panel_warm_menu=float(scenes["panel_warm_menu"]),
    )
    mask = ignore_cells(
        int(scenes["grid_cols"]), int(scenes["grid_rows"]), scenes["ignore_rects"]
    )
    if args.save:
        SAVE_DIR.mkdir(parents=True, exist_ok=True)

    duration = args.segundos
    interval = float(scenes.get("sample_interval_s", 0.25))
    print(
        f"perfil: {config.path} | grade {scenes['grid_cols']}x{scenes['grid_rows']} | "
        f"mot_gp={analyzer.motion_gameplay} mot_menu={analyzer.motion_menu} "
        f"confirm={analyzer.confirm_samples} top_blue={analyzer.top_blue_menu} "
        f"panel_warm={analyzer.panel_warm_menu} mask={len(mask)} células"
    )
    print(
        f"amostrando por {duration:.0f}s COM FOCO no jogo — Ctrl+C para parar"
        + (" | salvando em " + str(SAVE_DIR) if args.save else "")
        + "\n"
    )

    last_grid = None
    prev_saved: str | None = None  # cena da última captura salva (None = ainda não)
    start = time.perf_counter()
    focused_budget = duration  # só desce enquanto o jogo está em primeiro plano
    last_progress_print = 0.0
    while focused_budget > 0:
        tick = time.perf_counter()
        hwnd = locator.find()
        focused = False
        if not hwnd:
            print(f"[{tick - start:6.2f}s] janela do Torchlight NÃO encontrada")
        else:
            rect = locator.client_rect(hwnd)
            fg = locator.is_foreground(hwnd)
            print(
                f"[{tick - start:6.2f}s] janela ok (cliente {rect.width}x{rect.height} em "
                f"{rect.left},{rect.top}) foco={fg}",
                end="",
            )
            if not fg:
                print(" (sem foco: esperando... sem captura)")
            else:
                focused = True
                grids = capture_client_grid(
                    hwnd,
                    int(scenes["grid_cols"]),
                    int(scenes["grid_rows"]),
                    full=args.save,
                )
                if grids is None:
                    print(" | captura FALHOU (None)")
                else:
                    if args.save:
                        grid, color_grid, raw, width, height = grids
                    else:
                        grid, color_grid = grids
                        raw = width = height = None
                    if is_blank_grid(grid):
                        print(" | captura em BRANCO (preto/flat)")
                    elif last_grid is None:
                        last_grid = grid
                        print(" | primeira amostra (referência)")
                    else:
                        motion = grid_motion(grid, last_grid, mask)
                        last_grid = grid
                        top_blue, panel_warm = grid_color_features(color_grid)
                        kind = analyzer.feed(motion, top_blue, panel_warm)
                        allowed = (
                            "roda LIBERADA"
                            if kind in (SceneKind.GAMEPLAY, SceneKind.UNKNOWN)
                            else "roda BLOQUEADA"
                        )
                        print(
                            f" | mot={motion:5.2f} top_blue={top_blue:.2f} "
                            f"panel_warm={panel_warm:.2f} → {kind.upper():8s} ({allowed})"
                        )
                        # Salva na primeira amostra útil e a cada troca de cena.
                        if args.save and kind != prev_saved:
                            prev_saved = kind
                            stamp = _timestamp(tick - start)
                            full_path = SAVE_DIR / f"captura_{stamp}.png"
                            grid_path = SAVE_DIR / f"grade_{stamp}.png"
                            save_full_capture(raw, width, height, full_path)
                            save_grid_capture(color_grid, mask, grid_path)
                            print(f"        salvos: {full_path.name} + {grid_path.name}")
        now = time.perf_counter()
        if focused:
            focused_budget -= now - tick  # só os segundos com foco contam
        elif now - last_progress_print >= 2.0:
            last_progress_print = now
            print(
                f"        ...esperando foco do jogo (restam "
                f"{focused_budget:.0f}s de captura)"
            )
        remaining = interval - (now - tick)
        if remaining > 0:
            time.sleep(remaining)

    print(f"\nfim ({duration:.0f}s com foco). cena final: {analyzer.kind}")


if __name__ == "__main__":
    main()
