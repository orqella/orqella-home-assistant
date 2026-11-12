from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import unittest
import zipfile
from unittest import mock
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
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
        self.old_ha_url = server.HA_URL
        self.old_voice_url = server.VOICE_URL
        server.HA_CONFIG_DIR = self.root / "config"
        server.DATA_DIR = self.root / "data"
        server.DATA_DIR.mkdir(parents=True)
        self.http = None

    def tearDown(self) -> None:
        if self.http:
            self.http.shutdown()
            self.http.server_close()
        server.HA_CONFIG_DIR = self.old_config
        server.DATA_DIR = self.old_data
        server.HA_URL = self.old_ha_url
        server.VOICE_URL = self.old_voice_url
        self.temp.cleanup()

    def start_http(self, handler):
        self.http = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        thread.start()
        return f"http://127.0.0.1:{self.http.server_address[1]}"

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

    def test_ha_status_reads_config_and_entity_count(self) -> None:
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_GET(self):
                if self.path == "/api/config":
                    body = {"version": "2026.10.4", "location_name": "Home"}
                elif self.path == "/api/states":
                    body = [{"entity_id": "light.one"}, {"entity_id": "light.two"}]
                else:
                    self.send_response(404)
                    self.end_headers()
                    return
                data = json.dumps(body).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        server.HA_URL = self.start_http(Handler)
        server.write_token("ha", "test-token")
        result = server.ha_status()
        self.assertTrue(result["connected"])
        self.assertEqual(result["version"], "2026.10.4")
        self.assertEqual(result["location_name"], "Home")
        self.assertEqual(result["entities"], 2)

    def test_ha_status_reports_a_rejected_token(self) -> None:
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_GET(self):
                self.send_response(401)
                self.end_headers()

        server.HA_URL = self.start_http(Handler)
        server.write_token("ha", "bad-token")
        result = server.ha_status()
        self.assertFalse(result["connected"])
        self.assertIn("rejected", result["error"])

    def test_voice_status_checks_health(self) -> None:
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_GET(self):
                data = json.dumps({"ok": True, "name": "jarvis", "version": "0.1"}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        server.VOICE_URL = self.start_http(Handler)
        result = server.voice_status()
        self.assertTrue(result["reachable"])
        self.assertEqual(result["name"], "jarvis")
        self.assertEqual(result["version"], "0.1")

    def test_ensure_integration_creates_entry_and_pipeline(self) -> None:
        class FakeWebSocket:
            created = False
            pipeline = None

            def __init__(self, _url, _token):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def command(self, command, **payload):
                if command == "config_entries/get":
                    if not self.created:
                        return []
                    return [{"domain": "jarvis", "entry_id": "entry-1", "title": "Jarvis", "state": "loaded"}]
                if command == "assist_pipeline/pipeline/list":
                    return {"pipelines": [self.pipeline] if self.pipeline else [], "preferred_pipeline": ""}
                if command == "assist_pipeline/pipeline/create":
                    self.pipeline = {"id": "pipeline-1", "name": payload.get("name"), "conversation_engine": "entry-1"}
                    return {"pipeline": self.pipeline}
                raise AssertionError(f"unexpected command {command}")

        fake = FakeWebSocket
        old = (server.ha_status, server.component_state, server.token_for, server.HAWebSocket, server.request_json, server.integration_status)
        calls = []

        def fake_request(url, _token, method="GET", body=None):
            calls.append((method, url, body))
            if url.endswith("/api/config/config_entries/flow"):
                return 200, {"flow_id": "flow-1", "type": "form"}
            if url.endswith("/api/config/config_entries/flow/flow-1"):
                fake.created = True  # type: ignore[attr-defined]
                return 200, {"type": "create_entry", "result": {"entry_id": "entry-1"}}
            raise AssertionError(url)

        server.ha_status = lambda: {"connected": True, "error": ""}
        server.component_state = lambda: {"installed": True, "current": True, "writable": True, "version": "0.1.0", "source_version": "0.1.0", "path": "", "config_dir": ""}
        server.token_for = lambda kind: ("token", "settings") if kind == "ha" else ("", "")
        server.HAWebSocket = fake  # type: ignore[assignment]
        server.request_json = fake_request  # type: ignore[assignment]
        server.integration_status = lambda _force=False: {"entry": None, "pipeline": None, "error": ""}
        try:
            result = server.ensure_integration()
        finally:
            (server.ha_status, server.component_state, server.token_for, server.HAWebSocket, server.request_json, server.integration_status) = old
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["entry"]["entry_id"], "entry-1")
        self.assertEqual(result["pipeline"]["id"], "pipeline-1")
        self.assertIn("user_input", calls[-1][2])

    def test_proxy_example_has_websocket_enabled(self) -> None:
        example = server.proxy_example()
        self.assertIn('id = "homeassistant"', example)
        self.assertIn("websocket = true", example)
        self.assertIn("frame = true", example)


def stat_mode(path: Path) -> int:
    return os.stat(path).st_mode & 0o777


class HomeAssistantEdgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.old_config_dir = server.HA_CONFIG_DIR
        self.old_data = server.DATA_DIR
        self.old_config = dict(server.CONFIG)
        server.HA_CONFIG_DIR = None
        server.DATA_DIR = self.root / "data"
        server.DATA_DIR.mkdir(parents=True)
        server.INTEGRATION_CACHE["at"] = 0.0
        server.INTEGRATION_CACHE["value"] = None

    def tearDown(self) -> None:
        server.HA_CONFIG_DIR = self.old_config_dir
        server.DATA_DIR = self.old_data
        server.CONFIG.clear()
        server.CONFIG.update(self.old_config)
        self.temp.cleanup()

    def test_component_state_requires_a_config_directory(self) -> None:
        state = server.component_state()
        self.assertFalse(state["installed"])
        self.assertFalse(state["writable"])
        self.assertIn("config directory", state["error"])
        with self.assertRaises(RuntimeError):
            server.install_component()

    def test_token_precedence_and_commands(self) -> None:
        server.CONFIG["ha_token_command"] = ""
        os.environ.pop("HA_TOKEN", None)
        self.assertEqual(server.token_for("ha"), ("", ""))

        with mock.patch.dict(os.environ, {"HA_TOKEN": "env-token"}):
            self.assertEqual(server.token_for("ha"), ("env-token", "environment"))

        server.CONFIG["ha_token_command"] = "printf 'command-token\\n'"
        self.assertEqual(server.token_for("ha"), ("command-token", "command"))

        server.write_token("ha", "saved-token")
        self.assertEqual(server.token_for("ha"), ("saved-token", "settings"))

        server.CONFIG["ha_token_command"] = "sh -c 'exit 1'"
        server.write_token("ha", "")
        with mock.patch.dict(os.environ, {"HA_TOKEN": "env-token"}):
            self.assertEqual(server.token_for("ha"), ("env-token", "environment"))

    def test_integration_cache_can_be_bypassed(self) -> None:
        calls = []

        def fake_read():
            calls.append(True)
            return {"entry": None, "pipeline": None, "error": ""}

        with mock.patch.object(server, "read_integration", side_effect=fake_read):
            first = server.integration_status()
            second = server.integration_status()
            forced = server.integration_status(True)
        self.assertEqual(first, second)
        self.assertEqual(forced, first)
        self.assertEqual(len(calls), 2)

    def test_ensure_integration_reports_manual_install_when_unwritable(self) -> None:
        with (
            mock.patch.object(server, "ha_status", return_value={"connected": True, "error": ""}),
            mock.patch.object(
                server,
                "component_state",
                return_value={
                    "installed": False,
                    "current": False,
                    "writable": False,
                    "error": "",
                },
            ),
            mock.patch.object(
                server, "integration_status", return_value={"entry": None, "pipeline": None, "error": ""}
            ),
        ):
            result = server.ensure_integration()
        self.assertFalse(result["ok"])
        self.assertTrue(result["needs_manual_install"])
        self.assertIn("not writable", result["error"])

    def test_restart_requires_a_token_and_uses_the_websocket(self) -> None:
        server.CONFIG["ha_token_command"] = ""
        server.CONFIG["ha_token_command"] = "printf 'token\\n'"
        events = []

        class FakeWebSocket:
            ha_version = "2026.11.0"

            def __init__(self, url, token):
                events.append(("connect", url, token))

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def command(self, command, **payload):
                events.append(("command", command, payload))

        old = server.HAWebSocket
        server.HAWebSocket = FakeWebSocket
        try:
            result = server.restart_ha()
        finally:
            server.HAWebSocket = old
        self.assertTrue(result["ok"])
        self.assertEqual(result["ha_version"], "2026.11.0")
        self.assertIn(("command", "homeassistant/restart", {}), events)

    def test_status_payload_includes_config_and_proxy_example(self) -> None:
        with (
            mock.patch.object(server, "ha_status", return_value={"connected": False}),
            mock.patch.object(server, "voice_status", return_value={"reachable": False}),
            mock.patch.object(server, "integration_status", return_value={"entry": None}),
        ):
            payload = server.status_payload()
        self.assertFalse(payload["ha"]["connected"])
        self.assertIn("proxy_mount", payload["config"])
        self.assertIn('id = "homeassistant"', payload["proxy_example"])
        self.assertIn("websocket = true", payload["proxy_example"])


if __name__ == "__main__":
    unittest.main()
