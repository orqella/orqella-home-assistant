(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const api = (path, method = "GET", body) =>
    window.orqella.call("backend.request", {
      method,
      path,
      ...(body !== undefined ? { body } : {}),
    });
  let status = null;
  let proxyState;

  function toast(message, bad = false) {
    $("toast").textContent = message;
    $("toast").style.borderColor = bad ? "var(--red)" : "";
    $("toast").hidden = false;
    clearTimeout(toast.timer);
    toast.timer = setTimeout(() => { $("toast").hidden = true; }, 3200);
  }

  let dialogResolve = null;

  function closeDialog(value = null) {
    if (!dialogResolve) return;
    const resolve = dialogResolve;
    dialogResolve = null;
    $("dialog").hidden = true;
    $("dialogBackdrop").hidden = true;
    $("dialogInput").hidden = true;
    $("dialogInput").value = "";
    $("dialogCancel").hidden = false;
    resolve(value);
  }

  function askConfirm(title, text, confirmLabel = "Confirm") {
    return new Promise((resolve) => {
      dialogResolve = resolve;
      $("dialogTitle").textContent = title;
      $("dialogText").textContent = text;
      $("dialogInput").hidden = true;
      $("dialogCancel").hidden = false;
      $("dialogOk").textContent = confirmLabel;
      $("dialogBackdrop").hidden = false;
      $("dialog").hidden = false;
      $("dialogCancel").focus();
    });
  }

  function showText(title, text) {
    return new Promise((resolve) => {
      dialogResolve = resolve;
      $("dialogTitle").textContent = title;
      $("dialogText").textContent = "Select and copy the text below.";
      $("dialogInput").value = text;
      $("dialogInput").hidden = false;
      $("dialogCancel").hidden = true;
      $("dialogOk").textContent = "Close";
      $("dialogBackdrop").hidden = false;
      $("dialog").hidden = false;
      $("dialogInput").focus();
      $("dialogInput").select();
    });
  }

  function message(text, bad = false) {
    $("message").textContent = text || "";
    $("message").className = `message${bad ? " bad" : ""}`;
    $("message").hidden = !text;
  }

  function showLog(value) {
    if (!value) {
      $("log").hidden = true;
      return;
    }
    $("log").textContent = typeof value === "string" ? value : JSON.stringify(value, null, 2);
    $("log").hidden = false;
  }

  function render(next) {
    status = next;
    const ha = next.ha || {};
    const voice = next.voice || {};
    const integration = next.integration || {};
    const component = integration.component || {};
    const entry = integration.entry;
    const pipeline = integration.pipeline;
    $("haState").textContent = ha.connected ? "Connected" : "Offline";
    $("haDetail").textContent = ha.connected
      ? `${ha.version || "unknown version"} · ${ha.entities ?? 0} entities${ha.location_name ? ` · ${ha.location_name}` : ""}`
      : (ha.error || ha.url || "unavailable");
    $("voiceState").textContent = voice.reachable ? "Ready" : "Offline";
    $("voiceDetail").textContent = voice.reachable
      ? `${voice.name || "Voice Assistant"} ${voice.version || ""} · ${voice.url}`
      : (voice.error || voice.url || "unavailable");
    $("integrationState").textContent = pipeline ? "Integrated" : entry ? "Entry ready" : component.installed ? "Needs setup" : "Not installed";
    $("integrationDetail").textContent = pipeline
      ? `Assist pipeline · ${pipeline.name || "Voice Assistant"}`
      : entry
        ? `Entry ${entry.title || entry.entry_id} · ${entry.state || ""}`
        : (integration.error || "No Jarvis entry yet");
    $("summary").textContent = ha.connected
      ? `${ha.location_name || "Home Assistant"} · ${ha.entities ?? 0} entities · ${voice.reachable ? "Voice Assistant ready" : "Voice Assistant offline"}`
      : (ha.error || "Home Assistant is not reachable.");
    $("componentVersion").textContent = component.version
      ? `Jarvis ${component.version}${component.current ? "" : " · update available"}`
      : (component.installed ? "Jarvis installed" : "Jarvis not installed");
    $("tokenSource").textContent = `HA token: ${ha.token_source || "none"} · Voice token: ${voice.token_source || "none"}`;
    $("haToken").placeholder = ha.token_source === "settings" ? "A token is saved" : "Paste a Home Assistant long-lived token";
    $("voiceToken").placeholder = voice.token_source === "settings" ? "A token is saved" : "Usually blank";
    $("proxySnippet").textContent = next.proxy_example || "";
    $("proxyConfig").textContent = `mount ${next.config?.proxy_mount || "homeassistant"}`;
    if (!$("proxyUrl").value) $("proxyUrl").value = ha.url || "";
    renderProxy();
    $("restart").hidden = !(component.installed && !pipeline && !entry);
    if (integration.error) message(integration.error, true);
    else if (pipeline) message(`Connected through the ${pipeline.name || "Voice Assistant"} Assist pipeline.`);
    else if (entry) message("The integration entry exists; open Home Assistant to create or select an Assist pipeline.");
    else message("");
  }

  function proxyMount() {
    return status?.config?.proxy_mount || "homeassistant";
  }

  function renderProxy() {
    const node = $("proxyStatus");
    node.className = "muted";
    if (proxyState === undefined) {
      node.textContent = "Checking proxy…";
      return;
    }
    if (!proxyState) {
      node.textContent = "Not configured";
      node.className = "muted";
      return;
    }
    if (proxyState.ok) {
      node.textContent = `Ready${proxyState.url ? ` · ${proxyState.url}` : ""}`;
      node.className = "ok";
      return;
    }
    node.textContent = proxyState.url
      ? `Configured, but ${proxyState.base_url || "the upstream"} is not reachable`
      : "Configured, but the proxy host is missing";
    node.className = "warn";
  }

  async function loadProxy(showError = false) {
    try {
      proxyState = await window.orqella.call("proxy.status", {
        mount: proxyMount(),
        node: "local",
      });
      if (!$("proxyUrl").value) {
        $("proxyUrl").value = proxyState.base_url || status?.ha?.url || "";
      }
      renderProxy();
    } catch (error) {
      proxyState = null;
      renderProxy();
      if (showError) toast(String(error.message || error), true);
    }
  }

  async function saveProxy() {
    const base = $("proxyUrl").value.trim() || status?.ha?.url || "";
    if (!base) {
      toast("Enter the Home Assistant URL first", true);
      $("proxyUrl").focus();
      return;
    }
    $("saveProxy").disabled = true;
    $("proxyStatus").textContent = "Saving proxy…";
    try {
      await window.orqella.call("proxy.configure", {
        mount: proxyMount(),
        base_url: base,
        title: "Home Assistant",
        websocket: true,
        frame: true,
      });
      toast("Dashboard proxy saved");
      await loadProxy();
    } catch (error) {
      const text = String(error?.message || error);
      proxyState = null;
      renderProxy();
      if (/permission/i.test(text)) {
        $("proxyStatus").textContent = "Permission required";
        $("proxyStatus").className = "bad";
        toast("Approve 'node.proxy.manage:homeassistant' in Settings → Modules, then save again", true);
      } else if (/unknown module API method/i.test(text)) {
        $("proxyStatus").textContent = "Update Orqella to manage this here";
        $("proxyStatus").className = "warn";
        toast("This Orqella version needs the manual mount below", true);
      } else {
        $("proxyStatus").textContent = "Could not save the proxy";
        $("proxyStatus").className = "bad";
        toast(text, true);
      }
    } finally {
      $("saveProxy").disabled = false;
    }
  }

  async function load() {
    try {
      render(await api("/api/status"));
    } catch (error) {
      message(String(error.message || error), true);
    }
  }

  async function saveTokens() {
    const ha = $("haToken").value.trim();
    const voice = $("voiceToken").value.trim();
    if (!ha && !voice) {
      toast("Enter a token to save", true);
      return;
    }
    $("saveTokens").disabled = true;
    try {
      if (ha) await api("/api/token", "POST", { kind: "ha", token: ha });
      if (voice) await api("/api/token", "POST", { kind: "voice", token: voice });
      $("haToken").value = "";
      $("voiceToken").value = "";
      toast("Tokens saved");
      await load();
    } catch (error) {
      toast(String(error.message || error), true);
    } finally {
      $("saveTokens").disabled = false;
    }
  }

  async function clearTokens() {
    const ok = await askConfirm(
      "Remove saved tokens",
      "Remove the saved Home Assistant and Voice Assistant tokens?",
      "Remove",
    );
    if (!ok) return;
    try {
      await api("/api/token", "POST", { kind: "ha", token: "" });
      await api("/api/token", "POST", { kind: "voice", token: "" });
      toast("Saved tokens removed");
      await load();
    } catch (error) {
      toast(String(error.message || error), true);
    }
  }

  async function integrate() {
    $("integrate").disabled = true;
    message("Installing the component and creating the Assist pipeline…");
    showLog("");
    try {
      const result = await api("/api/integration/ensure", "POST", {});
      if (result.ok) {
        message(`Integrated. Assist pipeline: ${result.pipeline?.name || "Voice Assistant"}.`);
        showLog({ entry: result.entry, pipeline: result.pipeline });
        $("restart").hidden = true;
      } else if (result.restart_required) {
        message(result.message || "Restart Home Assistant, then click Integrate again.");
        $("restart").hidden = false;
      } else if (result.needs_manual_install) {
        message(result.error || "Install the custom component on the Home Assistant host, then try again.", true);
      } else {
        message(result.error || "Integration failed.", true);
        showLog(result.integration || result);
      }
      await load();
    } catch (error) {
      message(String(error.message || error), true);
    } finally {
      $("integrate").disabled = false;
    }
  }

  async function restart() {
    const ok = await askConfirm(
      "Restart Home Assistant",
      "Restart Home Assistant now? The dashboard and automations will be briefly unavailable.",
      "Restart",
    );
    if (!ok) return;
    $("restart").disabled = true;
    try {
      await api("/api/integration/restart", "POST", {});
      message("Home Assistant is restarting. Wait a minute, then click Integrate again.");
      toast("Home Assistant restart requested");
    } catch (error) {
      message(String(error.message || error), true);
    } finally {
      $("restart").disabled = false;
    }
  }

  async function download() {
    $("download").disabled = true;
    // Open the popup synchronously so the sandbox's allow-popups permission is
    // used before the async request loses the user gesture.
    const popup = window.open("about:blank", "_blank");
    try {
      const bundle = await api("/api/integration/bundle");
      const raw = atob(bundle.base64 || "");
      const bytes = new Uint8Array(raw.length);
      for (let index = 0; index < raw.length; index += 1) bytes[index] = raw.charCodeAt(index);
      const url = URL.createObjectURL(new Blob([bytes], { type: bundle.content_type || "application/zip" }));
      if (popup) {
        popup.location.href = url;
        toast("Download opened in a new tab");
      } else {
        const anchor = document.createElement("a");
        anchor.href = url;
        anchor.download = bundle.filename || "home-assistant-jarvis-component.zip";
        anchor.click();
        toast("Component bundle download started");
      }
      setTimeout(() => URL.revokeObjectURL(url), 60000);
    } catch (error) {
      try { popup?.close(); } catch (_) {}
      toast(String(error.message || error), true);
    } finally {
      $("download").disabled = false;
    }
  }

  async function copyProxy() {
    const text = $("proxySnippet").textContent || "";
    try {
      await navigator.clipboard.writeText(text);
      toast("Proxy mount copied");
    } catch (_) {
      await showText("Proxy mount", text);
    }
  }

  function bind() {
    $("refresh").onclick = load;
    $("saveTokens").onclick = saveTokens;
    $("clearTokens").onclick = clearTokens;
    $("integrate").onclick = integrate;
    $("restart").onclick = restart;
    $("download").onclick = download;
    $("copyProxy").onclick = copyProxy;
    $("saveProxy").onclick = saveProxy;
    $("checkProxy").onclick = () => loadProxy(true);
    $("dialogCancel").onclick = () => closeDialog(false);
    $("dialogOk").onclick = () => closeDialog($("dialogInput").hidden ? true : null);
    $("dialogBackdrop").onclick = () => closeDialog(false);
    document.addEventListener("keydown", (event) => {
      if (!$("dialog").hidden && event.key === "Escape") {
        event.preventDefault();
        closeDialog(false);
      }
    });
  }

  let heightTimer = null;
  function reportHeight() {
    clearTimeout(heightTimer);
    heightTimer = setTimeout(() => {
      const height = Math.ceil(Math.max(document.documentElement.scrollHeight, document.body.scrollHeight));
      parent.postMessage({ type: "orqella:frame-height", height }, "*");
    }, 30);
  }

  if (window.ResizeObserver) {
    const observer = new ResizeObserver(reportHeight);
    observer.observe(document.body);
    observer.observe(document.documentElement);
  }
  window.addEventListener("load", reportHeight);
  window.addEventListener("resize", reportHeight);
  requestAnimationFrame(reportHeight);

  window.orqella.ready(() => {
    bind();
    load();
    loadProxy();
  });
})();
