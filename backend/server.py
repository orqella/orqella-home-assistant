#!/usr/bin/env python3
"""Home Assistant module sidecar.

The sidecar talks to Home Assistant over its REST and WebSocket APIs, so the
dashboard can be proxied by Orqella and the module can work whether Home
Assistant is local or on another machine. It also bundles the Jarvis custom
component used to expose the Voice Assistant module as an Assist conversation
agent and Whisper STT engine.
"""
from __future__ import annotations

import base64
import io
import json
import os
import shlex
import socket
import ssl
import struct
import subprocess
import threading
import time
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

TOKEN = os.environ.get("ORQELLA_MODULE_TOKEN", "")
try:
    CONFIG = json.loads(os.environ.get("ORQELLA_MODULE_CONFIG") or "{}")
except json.JSONDecodeError:
    CONFIG = {}
MODULE_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = MODULE_ROOT / "data"
INTEGRATION_SOURCE = MODULE_ROOT / "integration" / "jarvis"
HA_URL = str(CONFIG.get("ha_url") or "http://127.0.0.1:8123").rstrip("/")
VOICE_URL = str(CONFIG.get("voice_assistant_url") or "http://127.0.0.1:8790").rstrip("/")
HA_CONFIG_DIR = (
    Path(os.path.expanduser(str(CONFIG.get("ha_config_dir"))))
    if str(CONFIG.get("ha_config_dir") or "").strip()
    else None
)
DASHBOARD_PATH = str(CONFIG.get("dashboard_path") or "/lovelace/0")
PIPELINE_NAME = str(CONFIG.get("pipeline_name") or "Voice Assistant")
AUTO_OPEN = bool(CONFIG.get("auto_open", True))
PROXY_MOUNT = "homeassistant"
TOKEN_TIMEOUT = 8
HTTP_TIMEOUT = 10
WS_TIMEOUT = 15
INTEGRATION_TTL = 20
MAX_JSON = 2 * 1024 * 1024
MAX_BUNDLE = 4 * 1024 * 1024

INTEGRATION_CACHE: dict[str, object] = {"at": 0.0, "value": None}
INTEGRATION_LOCK = threading.Lock()


def now() -> float:
    return time.time()


def token_path(kind: str) -> Path:
    return DATA_DIR / f"{kind}.token"


def write_token(kind: str, value: str) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = token_path(kind)
    if not value:
        path.unlink(missing_ok=True)
        return
    path.write_text(value.strip() + "\n", encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass


def token_for(kind: str) -> tuple[str, str]:
    """Return (token, source) without ever logging or returning the token."""
    path = token_path(kind)
    try:
        saved = path.read_text(encoding="utf-8").strip()
        if saved:
            return saved, "settings"
    except OSError:
        pass
    command = str(CONFIG.get(f"{kind}_token_command") or "").strip()
    if command:
        try:
            args = shlex.split(command)
            proc = subprocess.run(
                args,
                capture_output=True,
                text=True,
                timeout=TOKEN_TIMEOUT,
                errors="replace",
            )
            if proc.returncode == 0:
                for line in proc.stdout.splitlines():
                    value = line.strip()
                    if value:
                        return value[:8192], "command"
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
    env_name = "HA_TOKEN" if kind == "ha" else "VOICE_ASSISTANT_TOKEN"
    env_value = os.environ.get(env_name, "").strip()
    if env_value:
        return env_value, "environment"
    return "", ""


def request_json(url: str, token: str = "", method: str = "GET", body: dict | None = None) -> tuple[int, object]:
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(url, data=data, method=method, headers=headers)
    try:
        with urlopen(request, timeout=HTTP_TIMEOUT) as response:
            raw = response.read()
            try:
                return response.status, json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                return response.status, {}
    except HTTPError as error:
        if error.code == 401:
            raise RuntimeError("Home Assistant rejected the token") from error
        raise RuntimeError(f"Home Assistant returned HTTP {error.code}") from error
    except URLError as error:
        raise RuntimeError(f"could not reach Home Assistant: {error.reason}") from error
    except (OSError, TimeoutError) as error:
        raise RuntimeError(f"could not reach Home Assistant: {error}") from error


def ha_status() -> dict:
    token, source = token_for("ha")
    result = {
        "url": HA_URL,
        "connected": False,
        "version": "",
        "location_name": "",
        "entities": 0,
        "token_source": source,
        "error": "",
    }
    if not token:
        result["error"] = "No Home Assistant token. Save one in Settings or configure a token command."
        return result
    try:
        _, config = request_json(f"{HA_URL}/api/config", token)
        _, states = request_json(f"{HA_URL}/api/states", token)
        result.update({
            "connected": True,
            "version": str(config.get("version") or "") if isinstance(config, dict) else "",
            "location_name": str(config.get("location_name") or "") if isinstance(config, dict) else "",
            "entities": len(states) if isinstance(states, list) else 0,
        })
    except RuntimeError as error:
        result["error"] = str(error)
    return result


def voice_status() -> dict:
    token, source = token_for("voice")
    result = {
        "url": VOICE_URL,
        "reachable": False,
        "name": "",
        "version": "",
        "token_source": source,
        "error": "",
    }
    try:
        _, health = request_json(f"{VOICE_URL}/health", token)
        if isinstance(health, dict) and health.get("ok"):
            result.update({
                "reachable": True,
                "name": str(health.get("name") or ""),
                "version": str(health.get("version") or ""),
            })
        else:
            result["error"] = "Voice Assistant health response was invalid."
    except RuntimeError as error:
        result["error"] = str(error)
    return result


class HAWebSocket:
    """Minimal Home Assistant WebSocket API client using only the stdlib."""

    def __init__(self, base_url: str, token: str):
        parsed = urlparse(base_url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise RuntimeError("Home Assistant URL is invalid")
        self.secure = parsed.scheme == "https"
        self.host = parsed.hostname
        self.port = parsed.port or (443 if self.secure else 80)
        self.base_path = parsed.path.rstrip("/")
        self.token = token
        self.socket: socket.socket | None = None
        self.buffer = b""
        self.next_id = 1
        self.ha_version = ""

    def __enter__(self) -> "HAWebSocket":
        self.connect()
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def connect(self) -> None:
        raw = socket.create_connection((self.host, self.port), timeout=WS_TIMEOUT)
        if self.secure:
            context = ssl.create_default_context()
            raw = context.wrap_socket(raw, server_hostname=self.host)
        self.socket = raw
        key = base64.b64encode(os.urandom(16)).decode()
        path = f"{self.base_path}/api/websocket" if self.base_path else "/api/websocket"
        host_header = self.host
        if (self.secure and self.port != 443) or (not self.secure and self.port != 80):
            host_header = f"{self.host}:{self.port}"
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host_header}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "\r\n"
        )
        self.socket.sendall(request.encode())
        head = self._read_until(b"\r\n\r\n")
        if b" 101 " not in head.split(b"\r\n", 1)[0]:
            raise RuntimeError("Home Assistant WebSocket upgrade failed")
        first = self._recv_json()
        if first.get("type") != "auth_required":
            raise RuntimeError("unexpected Home Assistant WebSocket handshake")
        self.ha_version = str(first.get("ha_version") or "")
        self._send_json({"type": "auth", "access_token": self.token})
        auth = self._recv_json()
        if auth.get("type") != "auth_ok":
            raise RuntimeError("Home Assistant rejected the token")
        self.ha_version = str(auth.get("ha_version") or self.ha_version)

    def command(self, command_type: str, **payload) -> object:
        if not self.socket:
            raise RuntimeError("Home Assistant WebSocket is not connected")
        message_id = self.next_id
        self.next_id += 1
        message = {"id": message_id, "type": command_type, **payload}
        self._send_json(message)
        while True:
            value = self._recv_json()
            if value.get("id") != message_id:
                continue
            if not value.get("success"):
                error = value.get("error") or {}
                raise RuntimeError(str(error.get("message") or "Home Assistant command failed"))
            return value.get("result")

    def close(self) -> None:
        if not self.socket:
            return
        try:
            self._send_frame(0x8, b"")
        except OSError:
            pass
        try:
            self.socket.close()
        finally:
            self.socket = None

    def _read_until(self, marker: bytes) -> bytes:
        assert self.socket
        while marker not in self.buffer:
            chunk = self.socket.recv(4096)
            if not chunk:
                raise RuntimeError("Home Assistant closed the WebSocket")
            self.buffer += chunk
        head, self.buffer = self.buffer.split(marker, 1)
        return head

    def _recv_json(self) -> dict:
        while True:
            opcode, data = self._recv_frame()
            if opcode == 0x9:
                self._send_frame(0xA, data)
                continue
            if opcode == 0x8:
                raise RuntimeError("Home Assistant closed the WebSocket")
            if opcode in (0x1, 0x2):
                try:
                    value = json.loads(data.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as error:
                    raise RuntimeError("invalid Home Assistant WebSocket message") from error
                if isinstance(value, dict):
                    return value

    def _recv_frame(self) -> tuple[int, bytes]:
        assert self.socket
        header = self._read_exact(2)
        opcode = header[0] & 0x0F
        masked = bool(header[1] & 0x80)
        length = header[1] & 0x7F
        if length == 126:
            length = struct.unpack("!H", self._read_exact(2))[0]
        elif length == 127:
            length = struct.unpack("!Q", self._read_exact(8))[0]
        if length > MAX_JSON:
            raise RuntimeError("Home Assistant WebSocket frame is too large")
        mask = self._read_exact(4) if masked else b""
        data = self._read_exact(length)
        if masked:
            data = bytes(byte ^ mask[index % 4] for index, byte in enumerate(data))
        return opcode, data

    def _read_exact(self, count: int) -> bytes:
        assert self.socket
        while len(self.buffer) < count:
            chunk = self.socket.recv(65536)
            if not chunk:
                raise RuntimeError("Home Assistant closed the WebSocket")
            self.buffer += chunk
        data, self.buffer = self.buffer[:count], self.buffer[count:]
        return data

    def _send_json(self, value: dict) -> None:
        self._send_frame(0x1, json.dumps(value).encode("utf-8"))

    def _send_frame(self, opcode: int, data: bytes) -> None:
        if not self.socket:
            raise OSError("socket closed")
        mask = os.urandom(4)
        length = len(data)
        header = bytearray([0x80 | opcode])
        if length < 126:
            header.append(0x80 | length)
        elif length < 65536:
            header.append(0x80 | 126)
            header += struct.pack("!H", length)
        else:
            header.append(0x80 | 127)
            header += struct.pack("!Q", length)
        masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(data))
        self.socket.sendall(bytes(header) + mask + masked)


def component_state() -> dict:
    if HA_CONFIG_DIR is None:
        return {
            "installed": False,
            "writable": False,
            "path": "",
            "config_dir": "",
            "version": "",
            "source_version": "",
            "current": False,
            "error": "Set a local HA config directory or download the component bundle.",
        }
    destination = HA_CONFIG_DIR / "custom_components" / "jarvis"
    manifest = destination / "manifest.json"
    source_manifest = INTEGRATION_SOURCE / "manifest.json"
    installed = manifest.is_file()
    version = ""
    source_version = ""
    try:
        source_version = json.loads(source_manifest.read_text(encoding="utf-8")).get("version", "")
    except (OSError, json.JSONDecodeError):
        pass
    if installed:
        try:
            version = json.loads(manifest.read_text(encoding="utf-8")).get("version", "")
        except (OSError, json.JSONDecodeError):
            pass
    writable = False
    try:
        parent = destination.parent
        parent.mkdir(parents=True, exist_ok=True)
        writable = os.access(destination if destination.exists() else parent, os.W_OK)
    except OSError:
        writable = False
    return {
        "installed": installed,
        "writable": writable,
        "path": str(destination),
        "config_dir": str(HA_CONFIG_DIR),
        "version": version,
        "source_version": source_version,
        "current": bool(installed and source_version and version == source_version),
    }


def install_component() -> dict:
    if HA_CONFIG_DIR is None:
        raise RuntimeError(
            "Set the Home Assistant config directory or download the component bundle."
        )
    state = component_state()
    if state["installed"] and state["current"]:
        return state
    if not state["writable"]:
        raise RuntimeError(
            f"Home Assistant config is not writable: {state['config_dir']}. "
            "Download the component bundle and copy it to custom_components/jarvis on the HA host."
        )
    destination = HA_CONFIG_DIR / "custom_components" / "jarvis"
    destination.parent.mkdir(parents=True, exist_ok=True)
    for source in INTEGRATION_SOURCE.rglob("*"):
        if source.is_dir() or "__pycache__" in source.parts or source.suffix == ".pyc":
            continue
        relative = source.relative_to(INTEGRATION_SOURCE)
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
    return component_state()


def integration_bundle() -> dict:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for source in INTEGRATION_SOURCE.rglob("*"):
            if source.is_dir() or "__pycache__" in source.parts or source.suffix == ".pyc":
                continue
            archive.write(source, Path("custom_components/jarvis") / source.relative_to(INTEGRATION_SOURCE))
        archive.writestr(
            "custom_components/jarvis/INSTALL.txt",
            "Copy the custom_components/jarvis directory into your Home Assistant "
            "config directory, restart Home Assistant, then run the Home Assistant "
            "module integration button again.\n",
        )
    data = buffer.getvalue()
    if len(data) > MAX_BUNDLE:
        raise RuntimeError("integration bundle is unexpectedly large")
    return {
        "filename": "home-assistant-jarvis-component.zip",
        "content_type": "application/zip",
        "base64": base64.b64encode(data).decode("ascii"),
    }


def read_integration() -> dict:
    token, source = token_for("ha")
    result = {
        "component": component_state(),
        "entry": None,
        "pipeline": None,
        "preferred_pipeline": "",
        "ha_version": "",
        "token_source": source,
        "error": "",
    }
    if not token:
        result["error"] = "No Home Assistant token. Save one in Settings or configure a token command."
        return result
    try:
        with HAWebSocket(HA_URL, token) as ws:
            entries = ws.command("config_entries/get", domain="jarvis")
            if isinstance(entries, list):
                for entry in entries:
                    if entry.get("domain") == "jarvis":
                        result["entry"] = {
                            "entry_id": entry.get("entry_id"),
                            "title": entry.get("title"),
                            "state": entry.get("state"),
                        }
                        break
            pipelines = ws.command("assist_pipeline/pipeline/list")
            if isinstance(pipelines, dict):
                result["preferred_pipeline"] = pipelines.get("preferred_pipeline") or ""
                for pipeline in pipelines.get("pipelines") or []:
                    if result["entry"] and pipeline.get("conversation_engine") == result["entry"].get("entry_id"):
                        result["pipeline"] = pipeline
                        break
                    if not result["entry"] and pipeline.get("stt_engine") == "stt.jarvis_whisper":
                        result["pipeline"] = pipeline
            result["ha_version"] = ws.ha_version
    except RuntimeError as error:
        result["error"] = str(error)
    return result


def integration_status(force: bool = False) -> dict:
    with INTEGRATION_LOCK:
        cached = INTEGRATION_CACHE["value"]
        if not force and cached and now() - float(INTEGRATION_CACHE["at"]) < INTEGRATION_TTL:
            return cached  # type: ignore[return-value]
    value = read_integration()
    with INTEGRATION_LOCK:
        INTEGRATION_CACHE["at"] = now()
        INTEGRATION_CACHE["value"] = value
    return value


def ensure_integration() -> dict:
    ha = ha_status()
    if not ha["connected"]:
        return {"ok": False, "error": ha["error"], "ha": ha, "integration": integration_status(True)}
    component = component_state()
    restart_required = False
    if not component["installed"] or not component["current"]:
        if not component["writable"]:
            integration = integration_status(True)
            return {
                "ok": False,
                "needs_manual_install": True,
                "error": "Home Assistant config is not writable. Download the component bundle and install it on the HA host.",
                "component": component,
                "integration": integration,
            }
        component = install_component()
        restart_required = True
        # A newly copied custom component is not loaded until Home Assistant restarts.
        integration = integration_status(True)
        return {
            "ok": False,
            "restart_required": True,
            "component": component,
            "integration": integration,
            "message": "The Jarvis component was installed. Restart Home Assistant, then click Integrate again.",
        }

    token, _source = token_for("ha")
    voice_token, _voice_source = token_for("voice")
    try:
        with HAWebSocket(HA_URL, token) as ws:
            entries = ws.command("config_entries/get", domain="jarvis")
            entry = next(
                (item for item in entries if item.get("domain") == "jarvis"), None
            ) if isinstance(entries, list) else None
            if not entry:
                _status, flow = request_json(
                    f"{HA_URL}/api/config/config_entries/flow",
                    token,
                    "POST",
                    {"handler": "jarvis", "show_advanced_options": False},
                )
                if not isinstance(flow, dict) or not flow.get("flow_id"):
                    raise RuntimeError("Home Assistant did not start the Jarvis config flow")
                _status, submitted = request_json(
                    f"{HA_URL}/api/config/config_entries/flow/{flow['flow_id']}",
                    token,
                    "POST",
                    {"user_input": {"url": VOICE_URL, "token": voice_token}},
                )
                if not isinstance(submitted, dict):
                    raise RuntimeError("Home Assistant returned an invalid config flow response")
                if submitted.get("type") == "abort" and submitted.get("reason") == "already_configured":
                    entries = ws.command("config_entries/get", domain="jarvis")
                elif submitted.get("type") != "create_entry":
                    reason = submitted.get("reason") or submitted.get("errors") or ""
                    raise RuntimeError(f"Home Assistant did not create the Jarvis integration entry: {reason}")
                else:
                    entries = ws.command("config_entries/get", domain="jarvis")
                entry = next(
                    (item for item in entries if item.get("domain") == "jarvis"), None
                ) if isinstance(entries, list) else None
            if not entry:
                raise RuntimeError("Home Assistant did not return the Jarvis config entry")
            entry_id = entry.get("entry_id")
            pipeline_result = ws.command("assist_pipeline/pipeline/list")
            pipeline = None
            if isinstance(pipeline_result, dict):
                for item in pipeline_result.get("pipelines") or []:
                    if item.get("conversation_engine") == entry_id:
                        pipeline = item
                        break
            if not pipeline:
                created = ws.command(
                    "assist_pipeline/pipeline/create",
                    name=PIPELINE_NAME,
                    language="en",
                    conversation_engine=entry_id,
                    conversation_language="en",
                    stt_engine="stt.jarvis_whisper",
                    stt_language="en",
                    prefer_local_intents=False,
                )
                pipeline = created.get("pipeline") if isinstance(created, dict) else created
                if not isinstance(pipeline, dict):
                    raise RuntimeError("Home Assistant did not create an Assist pipeline")
    except RuntimeError as error:
        integration = integration_status(True)
        return {
            "ok": False,
            "error": str(error),
            "component": component_state(),
            "integration": integration,
        }
    integration = integration_status(True)
    return {
        "ok": True,
        "component": component_state(),
        "entry": {
            "entry_id": entry_id,
            "title": entry.get("title"),
            "state": entry.get("state"),
        },
        "pipeline": pipeline,
        "integration": integration,
        "voice_assistant_url": VOICE_URL,
    }


def restart_ha() -> dict:
    token, _source = token_for("ha")
    if not token:
        raise RuntimeError("No Home Assistant token")
    with HAWebSocket(HA_URL, token) as ws:
        ws.command("homeassistant/restart")
        return {"ok": True, "ha_version": ws.ha_version}


def proxy_example() -> str:
    return (
        "[[proxy]]\n"
        'id = "homeassistant"\n'
        'title = "Home Assistant"\n'
        f'base_url = "{HA_URL}"\n'
        'host = "homeassistant.orqella.localhost"\n'
        "websocket = true\n"
        "frame = true\n"
    )


def status_payload() -> dict:
    return {
        "ha": ha_status(),
        "voice": voice_status(),
        "integration": integration_status(),
        "config": {
            "proxy_mount": PROXY_MOUNT,
            "dashboard_path": DASHBOARD_PATH,
            "auto_open": AUTO_OPEN,
            "pipeline_name": PIPELINE_NAME,
            "ha_config_dir": str(HA_CONFIG_DIR or ""),
        },
        "proxy_example": proxy_example(),
    }


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def send_json(self, status: int, body: dict) -> None:
        data = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def authorized(self) -> bool:
        if not TOKEN or self.headers.get("X-Orqella-Module-Token") == TOKEN:
            return True
        self.send_json(401, {"error": "unauthorized"})
        return False

    def read_json(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0:
            return {}
        if length > MAX_JSON:
            raise ValueError("request is too large")
        value = json.loads(self.rfile.read(length) or b"{}")
        if not isinstance(value, dict):
            raise ValueError("JSON object required")
        return value

    def do_GET(self) -> None:
        if not self.authorized():
            return
        path = self.path.split("?", 1)[0]
        try:
            if path == "/health":
                return self.send_json(200, {"ok": True})
            if path == "/api/status":
                return self.send_json(200, status_payload())
            if path == "/api/integration":
                return self.send_json(200, integration_status(True))
            if path == "/api/integration/bundle":
                return self.send_json(200, integration_bundle())
            return self.send_json(404, {"error": "not found"})
        except RuntimeError as error:
            return self.send_json(502, {"error": str(error)})
        except Exception as error:  # noqa: BLE001 - API boundary
            return self.send_json(500, {"error": str(error)})

    def do_POST(self) -> None:
        if not self.authorized():
            return
        path = self.path.split("?", 1)[0]
        try:
            payload = self.read_json()
            if path == "/api/token":
                kind = str(payload.get("kind") or "").strip()
                if kind not in ("ha", "voice"):
                    raise ValueError("token kind must be ha or voice")
                token = str(payload.get("token") or "").strip()
                write_token(kind, token)
                with INTEGRATION_LOCK:
                    INTEGRATION_CACHE["at"] = 0.0
                    INTEGRATION_CACHE["value"] = None
                return self.send_json(200, {"ok": True, "kind": kind})
            if path == "/api/integration/ensure":
                return self.send_json(200, ensure_integration())
            if path == "/api/integration/restart":
                return self.send_json(200, restart_ha())
            return self.send_json(404, {"error": "not found"})
        except ValueError as error:
            return self.send_json(400, {"error": str(error)})
        except RuntimeError as error:
            return self.send_json(502, {"error": str(error)})
        except Exception as error:  # noqa: BLE001 - API boundary
            return self.send_json(500, {"error": str(error)})


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    port = int(os.environ["ORQELLA_MODULE_PORT"])
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
