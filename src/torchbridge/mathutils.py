from __future__ import annotations

import math


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


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
    magnitude = min(1.0, math.hypot(x, y))
    if magnitude <= deadzone:
        return 0.0, 0.0, 0.0

    scaled = (magnitude - deadzone) / (1.0 - deadzone)
    scaled = clamp(scaled, 0.0, 1.0) ** max(0.1, curve)
    factor = scaled / magnitude
    return x * factor, y * factor, scaled


def radial_slot(x: float, y: float, slots: int = 8, threshold: float = 0.42) -> int | None:
    """Return a 1-based radial slot, starting at the top and going clockwise."""
    if slots < 1 or math.hypot(x, y) < threshold:
        return None
    angle = math.atan2(x, -y) % (2.0 * math.pi)
    sector = (2.0 * math.pi) / slots
    return int((angle + sector / 2.0) // sector) % slots + 1


def trigger_value(raw: float, rest: float = 0.0, active: float = 1.0) -> float:
    """Normalize an arbitrary trigger axis to 0..1."""
    span = active - rest
    if abs(span) < 1e-6:
        return 0.0
    return clamp((raw - rest) / span, 0.0, 1.0)


def cursor_delta(x: float, y: float, speed: float, dt: float) -> tuple[float, float]:
    return x * speed * dt, y * speed * dt

