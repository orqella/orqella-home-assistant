(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[char]));
  const api = (path) => window.orqella.call("backend.request", { method: "GET", path });

  async function load() {
    try {
      const status = await api("/api/status");
      const ha = status.ha || {};
      const voice = status.voice || {};
      const integration = status.integration || {};
      $("dot").className = `dot ${ha.connected ? "online" : "bad"}`;
      $("location").textContent = ha.location_name || "Home Assistant";
      $("entities").textContent = ha.connected ? String(ha.entities ?? "—") : "—";
      $("version").textContent = ha.version || "—";
      $("voice").textContent = voice.reachable ? "ready" : "off";
      $("state").textContent = ha.connected ? "connected" : "offline";
      $("pipeline").textContent = integration.pipeline
        ? `Assist · ${integration.pipeline.name || "Voice Assistant"}`
        : integration.entry
          ? "Assist entry ready"
          : integration.error
            ? "Assist not connected"
            : "Checking Assist…";
    } catch (error) {
      $("dot").className = "dot bad";
      $("state").textContent = "offline";
      $("pipeline").textContent = "backend unavailable";
    }
  }

  window.orqella.ready(() => {
    load();
    setInterval(load, 20000);
  });
})();
