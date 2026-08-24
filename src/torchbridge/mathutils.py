# Funções matemáticas puras (sem estado, sem I/O): deadzone, curva, setores da roda e gatilhos.
from __future__ import annotations

import math


# Limita value ao intervalo [low, high].
def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


# Aplica deadzone circular + curva de resposta ao vetor (x, y) de um analógico.
def radial_deadzone(
    x: float,
    y: float,
    deadzone: float = 0.18,
    curve: float = 1.6,
) -> tuple[float, float, float]:
    """Apply a circular deadzone and response curve.

    Returns the adjusted x/y vector and its 0..1 magnitude. Direction is
    preserved, including on diagonals.
    """
    # Magnitude do vetor; a hipotenusa trata diagonais corretamente (deadzone redonda, não quadrada).
    magnitude = min(1.0, math.hypot(x, y))
    # Dentro da deadzone: ignora o analógico (evita 'andar sozinho' por drift/folga).
    if magnitude <= deadzone:
        return 0.0, 0.0, 0.0

    # Re-mapeia a magnitude para 0..1 (fora da deadzone) antes de aplicar a curva.
    scaled = (magnitude - deadzone) / (1.0 - deadzone)
    scaled = clamp(scaled, 0.0, 1.0) ** max(0.1, curve)
    # Escala o vetor original mantendo a direção; a magnitude final vira 'scaled'.
    factor = scaled / magnitude
    return x * factor, y * factor, scaled


# Converte a direção do analógico direito em setor 1..N da roda: setor 1 no topo, sentido horário.
def radial_slot(x: float, y: float, slots: int = 6, threshold: float = 0.42) -> int | None:
    """Return a 1-based radial slot, starting at the top and going clockwise."""
    # Sem inclinação mínima (ex.: LB solto, x=y=0): nenhum setor selecionado.
    if slots < 1 or math.hypot(x, y) < threshold:
        return None
    # Ângulo medido a partir do topo (y negativo = para cima), sempre em 0..2π.
    angle = math.atan2(x, -y) % (2.0 * math.pi)
    # Largura angular de cada setor da roda.
    sector = (2.0 * math.pi) / slots
    return int((angle + sector / 2.0) // sector) % slots + 1


# Normaliza um eixo de gatilho para 0..1 dados o repouso (rest) e o fundo (active) — ex.: -1..1 vira 0..1.
def trigger_value(raw: float, rest: float = 0.0, active: float = 1.0) -> float:
    """Normalize an arbitrary trigger axis to 0..1."""
    span = active - rest
    # Descritor degenerado (rest == active): sem faixa útil, devolve 0.
    if abs(span) < 1e-6:
        return 0.0
    return clamp((raw - rest) / span, 0.0, 1.0)


# Deslocamento do cursor neste frame: direção × velocidade do perfil × tempo decorrido.
def cursor_delta(x: float, y: float, speed: float, dt: float) -> tuple[float, float]:
    return x * speed * dt, y * speed * dt

