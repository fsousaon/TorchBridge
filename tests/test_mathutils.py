# Testes puros das funções matemáticas (deadzone, roda e gatilhos) — sem hardware.
import math
import unittest

from torchbridge.mathutils import radial_deadzone, radial_slot, trigger_value


class MathUtilsTests(unittest.TestCase):
    # Dentro da deadzone o analógico vira zero (não 'anda sozinho').
    def test_deadzone_suppresses_drift(self):
        self.assertEqual(radial_deadzone(0.08, -0.05, deadzone=0.18), (0.0, 0.0, 0.0))

    # Fora da deadzone, diagonais preservam a direção (x ≈ y).
    def test_deadzone_preserves_direction(self):
        x, y, magnitude = radial_deadzone(0.7, 0.7, deadzone=0.1, curve=1.0)
        self.assertGreater(magnitude, 0.9)
        self.assertTrue(math.isclose(x, y))

    # Setores: 1 no topo, 3 à direita, 5 embaixo, 7 à esquerda; inclinação fraca = None.
    def test_radial_slots_start_at_top_clockwise(self):
        self.assertEqual(radial_slot(0.0, -1.0), 1)
        self.assertEqual(radial_slot(1.0, 0.0), 3)
        self.assertEqual(radial_slot(0.0, 1.0), 5)
        self.assertEqual(radial_slot(-1.0, 0.0), 7)
        self.assertIsNone(radial_slot(0.1, 0.1))

    # Gatilho -1..1 vira 0..1 linear (0.5 no meio).
    def test_trigger_normalization_supports_minus_one_rest(self):
        self.assertEqual(trigger_value(-1.0, -1.0, 1.0), 0.0)
        self.assertEqual(trigger_value(0.0, -1.0, 1.0), 0.5)
        self.assertEqual(trigger_value(1.0, -1.0, 1.0), 1.0)


if __name__ == "__main__":
    unittest.main()
