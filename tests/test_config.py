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

    # Máscara de cena: o perfil padrão herda a região medida do print de referência.
    def test_default_profile_has_scene_ignore_mask(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "perfil.json"
            manager = ConfigManager(path)
            rects = manager.get()["scenes"]["ignore_rects"]
            self.assertEqual(rects, [[0.0104, 0.1667, 0.9901, 0.8713]])

    # Features de cor: o perfil novo nasce com os limiares calibrados ao vivo.
    def test_default_profile_has_color_thresholds(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "perfil.json"
            manager = ConfigManager(path)
            scenes = manager.get()["scenes"]
            self.assertEqual(scenes["top_blue_menu"], 0.5)
            self.assertEqual(scenes["panel_warm_menu"], 0.4)

    # Limiares de cor são frações 0..1; valores absurdos são limitados na recarga.
    def test_color_thresholds_are_clamped_to_unit_interval(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "perfil.json"
            manager = ConfigManager(path)
            data = json.loads(path.read_text(encoding="utf-8"))
            data["scenes"]["top_blue_menu"] = 7
            data["scenes"]["panel_warm_menu"] = -1
            path.write_text(json.dumps(data), encoding="utf-8")
            self.assertTrue(manager.reload(force=True))
            scenes = manager.get()["scenes"]
            self.assertEqual(scenes["top_blue_menu"], 1.0)
            self.assertEqual(scenes["panel_warm_menu"], 0.0)

    # Retângulos fora da ordem/faixa são normalizados; inválidos são descartados.
    def test_ignore_rects_are_normalized_and_invalid_dropped(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "perfil.json"
            manager = ConfigManager(path)
            data = json.loads(path.read_text(encoding="utf-8"))
            data["scenes"]["ignore_rects"] = [
                [0.9, 0.2, 0.1, 0.8],  # fora de ordem → ordenado
                [-5.0, 0.0, 2.0, 0.5],  # fora da faixa → limitado
                "lixo",  # tipo errado → descartado
                [0.5, 0.5, 0.5, 0.52],  # largura zero → descartado
            ]
            path.write_text(json.dumps(data), encoding="utf-8")
            self.assertTrue(manager.reload(force=True))
            self.assertEqual(
                manager.get()["scenes"]["ignore_rects"],
                [[0.1, 0.2, 0.9, 0.8], [0.0, 0.0, 1.0, 0.5]],
            )


if __name__ == "__main__":
    unittest.main()
