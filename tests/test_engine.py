# Testes do BridgeEngine: reset do estado da roda na borda de detecção do jogo.
from pathlib import Path
import tempfile
import unittest

from torchbridge.config import ConfigManager
from torchbridge.engine import BridgeEngine
from torchbridge.models import SharedOverlayState


class RadialSessionResetTests(unittest.TestCase):
    # Monta um engine sem subir a thread: ConfigManager em perfil temporário + estado compartilhado.
    def _make_engine(self, directory: str) -> tuple[BridgeEngine, SharedOverlayState]:
        config = ConfigManager(Path(directory) / "perfil.json")
        shared = SharedOverlayState()
        return BridgeEngine(config, shared), shared

    # Painel fantasma (sobrevivente de um Alt+F4) é limpo ao redetectar o jogo.
    def test_reset_clears_phantom_panels_and_selection(self):
        with tempfile.TemporaryDirectory() as directory:
            engine, shared = self._make_engine(directory)
            # Simula o estado de antes do encerramento forçado: painel direito aberto + setor 2.
            engine._active_panels = ["", "I"]
            engine._radial_selection = 2
            engine._reset_radial_session()
            self.assertEqual(engine._active_panels, ["", ""])
            self.assertIsNone(engine._radial_selection)
            # O overlay também precisa ver o estado limpo.
            self.assertEqual(shared.get().active_panels, ["", ""])
            self.assertIsNone(shared.get().radial_selection)

    # Sem painel aberto o reset é inócuo (seguro de rodar a cada detecção, inclusive na inicialização).
    def test_reset_is_noop_when_already_clean(self):
        with tempfile.TemporaryDirectory() as directory:
            engine, _ = self._make_engine(directory)
            engine._reset_radial_session()
            self.assertEqual(engine._active_panels, ["", ""])
            self.assertIsNone(engine._radial_selection)
