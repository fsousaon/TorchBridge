# Testes do ConfigManager: criação do perfil, clamps de valores e tolerância a perfil inválido.
import json
from pathlib import Path
import tempfile
import unittest

from torchbridge.config import ConfigManager


class ConfigTests(unittest.TestCase):
    # Cria o perfil padrão; valor fora da faixa (deadzone 9) é limitado na recarga (0.60).
    def test_config_is_created_and_values_are_clamped(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "perfil.json"
            manager = ConfigManager(path)
            data = json.loads(path.read_text(encoding="utf-8"))
            data["input"]["deadzone"] = 9
            path.write_text(json.dumps(data), encoding="utf-8")
            self.assertTrue(manager.reload(force=True))
            self.assertEqual(manager.get()["input"]["deadzone"], 0.60)

    # Seção com tipo errado não derruba o programa: a seção volta ao padrão (poll_hz 120).
    def test_invalid_section_type_keeps_last_valid_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "perfil.json"
            manager = ConfigManager(path)
            path.write_text('{"input": "quebrado"}', encoding="utf-8")
            self.assertTrue(manager.reload(force=True))
            self.assertEqual(manager.get()["input"]["poll_hz"], 120)

    # Perfil antigo com radius_x_percent/radius_y_percent migra para o raio circular:
    # a média dos dois valores vira movement_radius_percent e as chaves legado somem.
    def test_legacy_radii_migrate_to_circular_radius(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "perfil.json"
            path.write_text(
                '{"movement": {"radius_x_percent": 0.16, "radius_y_percent": 0.13}}',
                encoding="utf-8",
            )
            manager = ConfigManager(path)
            movement = manager.get()["movement"]
            self.assertAlmostEqual(movement["movement_radius_percent"], 0.145)
            self.assertNotIn("radius_x_percent", movement)
            self.assertNotIn("radius_y_percent", movement)

    # Perfil novo (movement_radius_percent explícito) não é sobrescrito pela migração legado.
    def test_new_profile_keeps_its_radius(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "perfil.json"
            path.write_text('{"movement": {"movement_radius_percent": 0.30}}', encoding="utf-8")
            manager = ConfigManager(path)
            self.assertEqual(manager.get()["movement"]["movement_radius_percent"], 0.30)


if __name__ == "__main__":
    unittest.main()
