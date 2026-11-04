from __future__ import annotations

import os
import sys
import tempfile
import unittest
import zipfile
from io import BytesIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
import server  # noqa: E402


class HomeAssistantBackendTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.old_config = server.HA_CONFIG_DIR
        self.old_data = server.DATA_DIR
        server.HA_CONFIG_DIR = self.root / "config"
        server.DATA_DIR = self.root / "data"
        server.DATA_DIR.mkdir(parents=True)

    def tearDown(self) -> None:
        server.HA_CONFIG_DIR = self.old_config
        server.DATA_DIR = self.old_data
        self.temp.cleanup()

    def test_component_installs_into_a_writable_config(self) -> None:
        state = server.component_state()
        self.assertFalse(state["installed"])
        installed = server.install_component()
        self.assertTrue(installed["installed"])
        self.assertTrue((server.HA_CONFIG_DIR / "custom_components" / "jarvis" / "manifest.json").is_file())
        self.assertTrue((server.HA_CONFIG_DIR / "custom_components" / "jarvis" / "agent.py").is_file())

    def test_component_bundle_is_a_zip(self) -> None:
        bundle = server.integration_bundle()
        raw = __import__("base64").b64decode(bundle["base64"])
        with zipfile.ZipFile(BytesIO(raw)) as archive:
            names = set(archive.namelist())
        self.assertIn("custom_components/jarvis/manifest.json", names)
        self.assertIn("custom_components/jarvis/agent.py", names)

    def test_saved_tokens_are_private_and_preferred(self) -> None:
        server.write_token("ha", "secret-value")
        path = server.token_path("ha")
        self.assertEqual(stat_mode(path), 0o600)
        token, source = server.token_for("ha")
        self.assertEqual(token, "secret-value")
        self.assertEqual(source, "settings")

    def test_clearing_a_token_removes_the_file(self) -> None:
        server.write_token("voice", "secret-value")
        server.write_token("voice", "")
        self.assertFalse(server.token_path("voice").exists())

    def test_proxy_example_has_websocket_enabled(self) -> None:
        example = server.proxy_example()
        self.assertIn('id = "homeassistant"', example)
        self.assertIn("websocket = true", example)


def stat_mode(path: Path) -> int:
    return os.stat(path).st_mode & 0o777


if __name__ == "__main__":
    unittest.main()
