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


if __name__ == "__main__":
    unittest.main()
