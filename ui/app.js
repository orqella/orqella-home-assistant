(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  let context = { node: { id: "local", name: "local" }, config: {} };
  let opened = false;
  let busy = false;

  const backend = (path, method = "GET", body) =>
    window.orqella.call("backend.request", {
      method,
      path,
      ...(body !== undefined ? { body } : {}),
    });

  const showError = (title, detail, setup) => {
    $("title").textContent = title;
    $("title").className = "error";
    $("detail").textContent = detail;
    $("actions").hidden = false;
    $("setup").textContent = setup || "";
    $("setup").hidden = !setup;
  };

  const openDashboard = async () => {
    if (busy) return;
    busy = true;
    opened = false;
    $("actions").hidden = true;
    $("setup").hidden = true;
    $("title").className = "";
    $("title").textContent = "Opening dashboard…";
    $("detail").textContent = `Connecting to ${context.node?.name || "this machine"}.`;
    const mount = "homeassistant";
    const node = context.node?.id || "local";
    try {
      const status = await backend("/api/status");
      const ha = status.ha || {};
      if (ha.version) {
        $("detail").textContent = `Home Assistant ${ha.version}${ha.location_name ? ` · ${ha.location_name}` : ""}`;
      }
      let proxy;
      try {
        proxy = await window.orqella.call("proxy.status", { mount, node });
      } catch (error) {
        showError(
          "Home Assistant proxy is not configured",
          `Add a ${mount} proxy mount for ${ha.url || "Home Assistant"}, then reopen this module.`,
          status.proxy_example || "",
        );
        return;
      }
      if (!proxy?.ok) {
        showError(
          "Home Assistant is not reachable",
          proxy?.error || `No response from ${ha.url || "Home Assistant"}.`,
          status.proxy_example || "",
        );
        return;
      }
      const configured = String(context.config?.dashboard_path || "/lovelace/0").trim();
      const path = configured.startsWith("/") ? configured : `/${configured}`;
      const result = await window.orqella.call("app.open", {
        mount,
        node,
        path,
      });
      if (result?.ok === false) {
        throw new Error(result.error || "The app surface did not open.");
      }
      opened = true;
      $("title").textContent = "Home Assistant is open";
      $("detail").textContent = "Use the app surface above. Return here to reopen it.";
    } catch (error) {
      showError(
        "Could not open Home Assistant",
        error.message || "The Home Assistant service is unavailable.",
      );
    } finally {
      busy = false;
    }
  };

  $("retry").addEventListener("click", openDashboard);
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) return;
    if (!opened) return;
    opened = false;
    $("title").textContent = "Home Assistant closed";
    $("title").className = "";
    $("detail").textContent = "Open the dashboard again when you are ready.";
    $("actions").hidden = false;
  });
  window.orqella.ready((ctx) => {
    context = ctx;
    const auto = ctx.config?.auto_open !== false;
    if (auto) openDashboard();
    else {
      $("title").textContent = "Home Assistant";
      $("detail").textContent = "Open the dashboard when you are ready.";
      $("actions").hidden = false;
    }
  });
})();
