# Home Assistant

Opens your Home Assistant dashboard as an Orqella app surface and integrates the
Voice Assistant module as a Home Assistant Assist conversation agent and Whisper
STT engine.

## Dashboard

Home Assistant runs on its own origin through the Orqella proxy, exactly like the
OpenCode module. Add a mount to `config.toml`:

```toml
[[proxy]]
id = "homeassistant"
title = "Home Assistant"
base_url = "http://127.0.0.1:8123"
host = "homeassistant.orqella.localhost"
websocket = true
frame = true
```

For Home Assistant on another machine, change `base_url` to that host and make
sure this Orqella daemon can reach it. `websocket = true` is required by the HA
frontend.

The module page checks the mount and calls `app.open` with the configured
`dashboard_path` (default `/lovelace/0`).

## REST and WebSocket

The sidecar uses:

- `GET /api/config` and `GET /api/states` for status and entity counts.
- `ws://<ha>/api/websocket` for config entries, Assist pipelines, and restart.
- `GET <voice-assistant>/health` to detect the Voice Assistant API.

It works with a local container or a remote Home Assistant. Tokens can come
from:

1. a token saved in Settings (module `data/ha.token`, mode `0600`);
2. the configured token command, if any (blank by default; Linux users can point it at `secret-tool`, macOS users at `security find-generic-password`, and Windows users can leave it blank and save a token in Settings);
3. `HA_TOKEN` in the daemon environment.

For remote Home Assistant, save a long-lived token in Settings.

Optional dependency: **Voice Assistant**. Installing it adds the Assist
conversation agent and Whisper STT pipeline; the Home Assistant dashboard and
proxy work without it.

## Voice Assistant integration

The module bundles the Jarvis custom component under `integration/jarvis`.

The **Integrate with Home Assistant** button:

1. installs the component into `<ha_config_dir>/custom_components/jarvis` when
   a local config directory is configured and writable; otherwise use the
   downloadable bundle;
2. creates a `jarvis` config entry pointing at the Voice Assistant API;
3. creates an Assist pipeline using the `stt.jarvis_whisper` entity if one does
   not exist.

If Home Assistant is remote or its config directory is not writable, use
**Download custom component**, copy `custom_components/jarvis` into the HA
config directory, restart HA, then click **Integrate** again.

The integration uses the same API paths as the Voice Assistant engine:
`POST /api/conversation` and `POST /api/stt`. Start the Voice Assistant API
(usually `jarvis serve` on `127.0.0.1:8790`) before integrating.

## Install

```bash
orqella module install ~/Projects/orqella/modules/home-assistant
```

Then configure the proxy mount, restart the daemon, and open **Home Assistant**
from the sidebar. Settings → Home Assistant has the connection, tokens, proxy
snippet, and integration button.

## Security

- The sidecar is unsandboxed because it may install a component into the HA
  config directory and run a local token command.
- Saved tokens are written with mode `0600`; token values are never returned by
  the status API or logged.
- The HA custom component talks to Voice Assistant only; it does not proxy
  Home Assistant itself.
