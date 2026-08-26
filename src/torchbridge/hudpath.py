# Parser mínimo de path SVG: converte o atributo `d` em polilinhas (listas de
# pontos fechados), para hit-test com a mesma point_in_polygon de models.py.
# Suporta os comandos usados em docs/proporcao/hud-click-no-reset-variable.svg
# (M m H h V v L l C c Z z); curvas Bézélicas são amostradas em 12 pontos
# (1/141 de altura ~= 0,15 px em 1080p, imperceptível no hit-test).
import math
import re
from pathlib import Path

_HUD_SVG = Path(__file__).resolve().parent.parent.parent / "docs" / "proporcao" / "hud-click-no-reset-variable.svg"
_SAMPLES = 12
_TOKEN_RE = re.compile(r"[MmHhVvLlCcZz]|-?\d*\.?\d+(?:[eE][-+]?\d+)?")


def _cubic(p0, p1, p2, p3, t: float) -> tuple[float, float]:
    u = 1.0 - t
    x = u**3 * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t**3 * p3[0]
    y = u**3 * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t**3 * p3[1]
    return (x, y)


def parse_path_d(d: str) -> list[list[tuple[float, float]]]:
    # Retorna um subpath (polilinha) por cada M/m do path; Z fecha o subpath.
    tokens = _TOKEN_RE.findall(d)
    subpaths: list[list[tuple[float, float]]] = []
    current: list[tuple[float, float]] = []
    cur = (0.0, 0.0)          # último ponto do subpath
    start = (0.0, 0.0)        # primeiro ponto do subpath (destino do Z)
    command = ""
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok[0] in "MmHhVvLlCcZz":
            command = tok
            i += 1
            continue
        # Parâmetros numéricos do comando atual (H/V/L/M = 1 par, C = 3 pares).
        if command in ("M", "m"):
            # M sempre abre um NOVO subpath (regra do SVG), mesmo depois de Z.
            rel = command == "m"
            x = float(tok)
            i += 1
            y = float(tokens[i]); i += 1
            if rel and current:
                x, y = cur[0] + x, cur[1] + y
            cur = (x, y)
            start = cur
            current = [cur]
            subpaths.append(current)
        elif command in ("L", "l"):
            rel = command == "l"
            x = float(tok); i += 1
            y = float(tokens[i]); i += 1
            if rel:
                x, y = cur[0] + x, cur[1] + y
            cur = (x, y)
            current.append(cur)
        elif command in ("H", "h"):
            rel = command == "h"
            x = float(tok); i += 1
            cur = (cur[0] + x if rel else x, cur[1])
            current.append(cur)
        elif command in ("V", "v"):
            rel = command == "v"
            y = float(tok); i += 1
            cur = (cur[0], cur[1] + y if rel else y)
            current.append(cur)
        elif command in ("C", "c"):
            rel = command == "c"
            nums = [float(tokens[i + j]) for j in range(6)]
            i += 6
            p1 = (nums[0], nums[1]); p2 = (nums[2], nums[3]); p3 = (nums[4], nums[5])
            if rel:
                p1 = (cur[0] + p1[0], cur[1] + p1[1])
                p2 = (cur[0] + p2[0], cur[1] + p2[1])
                p3 = (cur[0] + p3[0], cur[1] + p3[1])
            for s in range(1, _SAMPLES + 1):
                current.append(_cubic(cur, p1, p2, p3, s / _SAMPLES))
            cur = p3
        elif command == "Z" or command == "z":
            cur = start
            current.append(cur)
            # O próximo número abre um novo subpath implícito pelo M seguinte.
            current = []
        else:  # pragma: no cover - token inesperado falha explícito
            raise ValueError(f"Comando SVG não suportado: {command!r}")
    return [sp for sp in subpaths if len(sp) >= 3]


def hud_path_data() -> list[list[tuple[float, float]]]:
    # Lê o SVG uma vez e devolve os subpaths em coordenadas do viewBox (0..1171, 0..141).
    svg = _HUD_SVG.read_text(encoding="utf-8")
    m = re.search(r'viewBox="0 0 (\d+) (\d+)"', svg)
    if not m:
        raise ValueError("viewBox do SVG do HUD não encontrado")
    path = re.search(r'path d="([^"]+)"', svg)
    if not path:
        raise ValueError("path do SVG do HUD não encontrado")
    return parse_path_d(path.group(1))


def hud_viewbox() -> tuple[int, int]:
    svg = _HUD_SVG.read_text(encoding="utf-8")
    m = re.search(r'viewBox="0 0 (\d+) (\d+)"', svg)
    return (int(m.group(1)), int(m.group(2)))
