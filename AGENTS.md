# Home Assistant module

Keep Home Assistant-specific behavior in this module. The daemon only provides
the generic proxy mount, app surface, and module backend/WebSocket bridge.

- `backend/server.py` owns HA REST/WebSocket access, token lookup, the component
  bundle, and Assist pipeline setup.
- Do not add Home Assistant routes to the core daemon.
- Do not return or log token values. Saved tokens belong in `data/*.token` with
  mode `0600`.
- The dashboard is a proxied app surface, not an embedded iframe. It requires a
  configured `[[proxy]]` mount.
- Keep the Voice Assistant integration compatible with the Voice Assistant module's
  `/api/conversation` and `/api/stt` endpoints.
