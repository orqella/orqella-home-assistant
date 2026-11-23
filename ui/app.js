(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  let context = { node: { id: "local", name: "local" }, config: {} };
  let opened = false;
  let busy = false;
  let lastStatus = null;

  const backend = (path, method = "GET", body) =>
    window.orqella.call("backend.request", {
      method,
      path,
      ...(body !== undefined ? { body } : {}),
    });

  const showError = (title, detail, setup, canConfigure = false) => {
    $("title").textContent = title;
    $("title").className = "error";
    $("detail").textContent = detail;
    $("actions").hidden = false;
    $("configure").hidden = !canConfigure;
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
      lastStatus = status;
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
          `Save the dashboard proxy for ${ha.url || "Home Assistant"} once; Orqella will not ask again.`,
          status.proxy_example || "",
          true,
        );
        return;
      }
      if (!proxy?.ok) {
        showError(
          "Home Assistant is not reachable",
          proxy?.error || `No response from ${ha.url || "Home Assistant"}.`,
          status.proxy_example || "",
          true,
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

  async function configureProxy() {
    if (busy) return;
    const base = lastStatus?.ha?.url || context.config?.ha_url || "";
    if (!base) {
      showError(
        "Home Assistant URL is missing",
        "Set the Home Assistant URL in Settings → Modules → Home Assistant, then try again.",
        lastStatus?.proxy_example || "",
        false,
      );
      return;
    }
    busy = true;
    $("configure").disabled = true;
    $("detail").textContent = "Saving the dashboard proxy…";
    try {
      await window.orqella.call("proxy.configure", {
        mount: "homeassistant",
        base_url: base,
        title: "Home Assistant",
        websocket: true,
        frame: true,
      });
      busy = false;
      await openDashboard();
    } catch (error) {
      busy = false;
      const text = String(error?.message || error);
      const hint = /permission/i.test(text)
        ? "Approve 'node.proxy.manage:homeassistant' in Settings → Modules, then try again."
        : /unknown module API method/i.test(text)
          ? "This Orqella version does not support in-app proxy setup; use the manual mount below."
          : "Open Settings → Modules → Home Assistant → Settings to save it there.";
      showError(
        "Could not configure the dashboard proxy",
        `${text} ${hint}`,
        lastStatus?.proxy_example || "",
        true,
      );
    } finally {
      $("configure").disabled = false;
    }
  }

  $("configure").addEventListener("click", configureProxy);
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
