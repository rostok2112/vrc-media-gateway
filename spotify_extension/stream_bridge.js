// stream_bridge.js (Spicetify) — PORT of popup.js logic (manualGlobal preserved, Refresh, auto-detect via WS RPC, fallback fetch)
(async function () {
  while (!window.Spicetify || !Spicetify.Player) await new Promise(r=>setTimeout(r,250));
  console.log("[stream_bridge] starting (popup logic port)");
  if (window.__vrchat_stream_bridge_loaded) {
    console.log("[stream_bridge] already loaded, skipping");
    return;
  }
  window.__vrchat_stream_bridge_loaded = true;

  // Storage keys (mirror background/popup)
  const STORAGE_KEY = "vrchat_stream_bridge:settings_v1";

  function defaultSettings() {
    return {
      usePublicUrl: true,
      localAddress: "127.0.0.1",
      localPort: "8080",
      globalUrl: "",
      manualGlobal: false
    };
  }

  function loadSettings() {
    try {
      const raw = Spicetify.LocalStorage.get(STORAGE_KEY);
      if (!raw) return defaultSettings();
      const parsed = JSON.parse(raw);
      return Object.assign(defaultSettings(), parsed);
    } catch (e) {
      return defaultSettings();
    }
  }
  function saveSettings(partial) {
    try {
      const cur = loadSettings();
      const merged = Object.assign({}, cur, partial);
      Spicetify.LocalStorage.set(STORAGE_KEY, JSON.stringify(merged));
      console.log("[stream_bridge] settings saved", merged);
      return merged;
    } catch (e) {
      console.error("[stream_bridge] saveSettings", e);
      return null;
    }
  }

  // WS RPC helper (use existing WS connection)
  let ws = null;
  let wsUrl = null;
  let reconnectTimer = null;
  let pending = new Map();
  
  function buildWsUrl() {
    const cfg = loadSettings();
    return `ws://${cfg.localAddress || "127.0.0.1"}:${cfg.localPort || "8080"}/api/ws/spotify`;
  }
  async function handleRpc(msg) {
    let result = null;
  
    try {
      if (msg.method === "load") {
        await Spicetify.Player.playUri(msg.params.uri);
        await new Promise(r => setTimeout(r, 350));
        try { Spicetify.Player.pause(); } catch(e){}
        result = { ok: true };
      }
  
      if (msg.method === "metadata") {
        let duration = 0;
        try {
          const data = Spicetify.Player.data;
          if (data?.item?.duration?.milliseconds)
            duration = data.item.duration.milliseconds;
        } catch(e){}
        result = { duration_ms: duration };
      }
  
      if (msg.method === "seek_play") {
        const pos = msg.params.position_ms || 0;
        try { Spicetify.Player.seek(pos); } catch(e){}
        try { Spicetify.Player.play(); } catch(e){}
        result = { ok: true };
      }
  
    } catch (e) {
      console.error("handleRpc error", e);
      result = null;
    }
  
    try {
      ws.send(JSON.stringify({
        type: "rpc_response",
        id: msg.id,
        result: result
      }));
    } catch(e) {
      console.error("failed to send rpc_response", e);
    }
  }
  
  function connectWS(force = false) {
    const url = buildWsUrl();
    if (!force && ws && (ws.readyState === 0 || ws.readyState === 1) && wsUrl === url) {
      return;
    }

    if (reconnectTimer) {
      clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }

    if (ws) {
      try { ws.close(); } catch {}
      ws = null;
    }

    wsUrl = url;
    console.log("[stream_bridge] WS connecting to", url);

    try {
      ws = new WebSocket(url);
    } catch (e) {
      console.error("[stream_bridge] WS create error", e);
      reconnectTimer = setTimeout(() => connectWS(true), 2000);
      return;
    }
  
    ws.onopen = async () => {
      console.log("[stream_bridge] WS open");
    
      const cfg = loadSettings();
    
      if (!cfg.usePublicUrl) return;
      if (cfg.manualGlobal) {
        console.log("[stream_bridge] manual URL set, skipping auto-detect");
        return;
      }
    
      const found = await detectTunnelTransient();
      if (found) {
        transientPublicUrl = found;
        saveSettings({ globalUrl: found, manualGlobal: false });
        console.log("[stream_bridge] tunnel auto-updated via WS:", found);
      }
    };
  
    ws.onclose = () => {
      console.log("[stream_bridge] WS closed");
      if (reconnectTimer) {
        clearTimeout(reconnectTimer);
      }
      reconnectTimer = setTimeout(() => connectWS(true), 2000);
    };
  
    ws.onerror = e => console.warn("[stream_bridge] WS error", e);
  
    ws.onmessage = async (ev) => {
      try {
        const msg = JSON.parse(ev.data);
    
        // 🔥 ОТВЕЧАЕМ НА RPC ОТ БЕКЕНДА
        if (msg.type === "rpc_request") {
          await handleRpc(msg);
          return;
        }
    
        // ответы на наши rpc
        if (msg.type === "rpc_response" && msg.id) {
          const cb = pending.get(msg.id);
          if (cb) {
            cb(msg.result);
            pending.delete(msg.id);
          }
        }
    
      } catch (e) {
        console.error("WS parse error", e);
      }
    };
  }
  
  function wsRpc(method, params = {}, timeout = 900) {
    return new Promise(resolve => {
      if (!ws || ws.readyState !== 1) return resolve(null);
  
      const id = Math.random().toString(36).slice(2);
      pending.set(id, r => resolve(r));
  
      try {
        ws.send(JSON.stringify({
          type: "rpc_request",
          id,
          method,
          params
        }));
      } catch {
        pending.delete(id);
        return resolve(null);
      }
  
      setTimeout(() => {
        if (pending.has(id)) {
          pending.delete(id);
          resolve(null);
        }
      }, timeout);
    });
  }
  
  connectWS();



  // Track URL resolution (same as before)
  function findOpenSpotifyUrl() {
    try {
      const data = Spicetify.Player.data;
      if (!data || !data.item || !data.item.uri) return null;
  
      // Перетворюємо internal URI в URL
      const spUri = Spicetify.URI.fromString(data.item.uri);
      if (!spUri) return null;
  
      const url = spUri.toURL();
      return url;
    } catch (e) {
      console.error("[stream_bridge] findOpenSpotifyUrl error", e);
      return null;
    }
  }
  function ensureWsReady(timeout = 1500) {
    return new Promise(resolve => {
      if (ws && ws.readyState === 1) return resolve(true);

      try {
        if (!ws || ws.readyState === 3 || ws.readyState === 2) {
          connectWS(true);
        }
      } catch {}

      if (!ws) return resolve(false);

      let done = false;
      const onOpen = () => {
        if (done) return;
        done = true;
        cleanup();
        resolve(true);
      };
      const onClose = () => {
        if (done) return;
        done = true;
        cleanup();
        resolve(false);
      };
      const timer = setTimeout(() => {
        if (done) return;
        done = true;
        cleanup();
        resolve(false);
      }, timeout);

      function cleanup() {
        clearTimeout(timer);
        try { ws.removeEventListener("open", onOpen); } catch {}
        try { ws.removeEventListener("close", onClose); } catch {}
      }

      ws.addEventListener("open", onOpen);
      ws.addEventListener("close", onClose);
    });
  }



  // choose base exactly like popup background.resolveBases logic:
  // - if usePublicUrl && manualGlobal -> manual global
  // - else if usePublicUrl && transient (detected now or earlier) -> transient public
  // - else -> localBase
  async function detectTunnelTransient() {
    if (!ws || ws.readyState !== 1) {
      console.log("[stream_bridge] WS not ready for tunnel");
      return null;
    }
  
    try {
      const r = await wsRpc("tunnel");
      if (r && r.url) {
        return String(r.url).replace(/\/$/, "");
      }
    } catch (e) {
      console.log("[stream_bridge] tunnel WS error", e);
    }
  
    return null;
  }

  let transientPublicUrl = null;
  async function ensureTransientIfNeeded(force = false) {
    const cfg = loadSettings();
    if (!cfg.usePublicUrl) return;
    if (cfg.manualGlobal) return;
    if (!force && transientPublicUrl) return;

    await ensureWsReady(1500);
    const found = await detectTunnelTransient();
    if (found) {
      transientPublicUrl = found;
      saveSettings({ globalUrl: found, manualGlobal: false });
    }
  }

    function chooseBase() {
    const cfg = loadSettings();
    const localBase = `http://${cfg.localAddress || "127.0.0.1"}:${cfg.localPort || "8080"}`;

    if (cfg.usePublicUrl) {
      if (cfg.globalUrl && cfg.manualGlobal)
        return cfg.globalUrl.replace(/\/$/, "");
      if (transientPublicUrl)
        return transientPublicUrl;
      if (cfg.globalUrl)
        return cfg.globalUrl.replace(/\/$/, "");
    }

    return localBase;
  }
 
  // UI injection: insert buttons next to Play control (robust)
  // replace existing injectButtons() with this version
  function injectButtons() {
    if (document.getElementById("vrchat-wrapper")) return;
  
    const extra = document.querySelector(".main-nowPlayingBar-extraControls");
    if (!extra) return;
  
    const wrapper = document.createElement("div");
    wrapper.id = "vrchat-wrapper";
    wrapper.style.flex = "0 0 auto";
    wrapper.style.display = "flex";
    wrapper.style.alignItems = "center";
    wrapper.style.gap = "10px";
    wrapper.style.marginRight = "12px";
  
    function createPill(text, green = false) {
      const btn = document.createElement("button");
  
      btn.textContent = text;
      btn.style.height = "32px";
      btn.style.padding = "0 16px";
      btn.style.borderRadius = "999px";
      btn.style.fontSize = "13px";
      btn.style.fontWeight = "600";
      btn.style.display = "flex";
      btn.style.alignItems = "center";
      btn.style.justifyContent = "center";
      btn.style.whiteSpace = "nowrap";
      btn.style.boxSizing = "border-box";
      btn.style.flex = "0 0 auto";
      btn.style.cursor = "pointer";
  
      if (green) {
        btn.style.background = "#1ed760";
        btn.style.color = "#000";
        btn.style.border = "none";
      } else {
        btn.style.background = "transparent";
        btn.style.color = "var(--spice-text)";
        btn.style.border = "1px solid rgba(255,255,255,0.2)";
      }
  
      return btn;
    }
  
        const vrBtn = createPill("VRChat", true);
    const clearBtn = createPill("Clear cache", false);
    const settingsBtn = createPill("Settings", false);

    async function withTrackUrl(btn, fn) {
      const openUrl = findOpenSpotifyUrl();
      if (!openUrl) {
        flash(btn, "No track");
        return null;
      }
      return await fn(openUrl);
    }

    // VRChat button
    vrBtn.onclick = async () => {
      await withTrackUrl(vrBtn, async (openUrl) => {
        const cfg = loadSettings();

        if (cfg.usePublicUrl && !cfg.manualGlobal) {
          await ensureTransientIfNeeded(true);
        }

        const base = chooseBase();

        const link =
          `${base.replace(/\/$/, "")}/api/stream-spotify?url=` +
          encodeURIComponent(openUrl);

        const ok = await copyToClipboard(link);

        flash(vrBtn, ok ? "Copied ✓" : "Copy failed");

        console.log("[stream_bridge] copied", link);
      });
    };

    // Clear cache button
    clearBtn.onclick = async () => {
      await withTrackUrl(clearBtn, async (openUrl) => {
        const cfg = loadSettings();
        if (cfg.usePublicUrl && !cfg.manualGlobal) {
          await ensureTransientIfNeeded(true);
        }

        const res = await wsRpc("clear_cache", { url: openUrl }, 4000);
        if (res && res.ok) {
          flash(clearBtn, "Cleared ✓");
        } else {
          flash(clearBtn, "Failed");
        }
      });
    };

    // Settings button
    settingsBtn.onclick = () => openSettingsModal();

  
    wrapper.appendChild(vrBtn);
    wrapper.appendChild(clearBtn);
    wrapper.appendChild(settingsBtn);
  
    extra.insertBefore(wrapper, extra.firstChild);
  }

// невеликі допоміжні функції — якщо в твоєму файлі вже є, нічого не міняй
function flashTemp(el, txt, ms = 900) {
  const prev = el.innerHTML;
  el.textContent = txt;
  setTimeout(() => { el.innerHTML = prev; }, ms);
}

  function flash(el, txt, ms = 900) {
    const prev = el.textContent;
    el.textContent = txt;
    setTimeout(() => el.textContent = prev, ms);
  }

  async function copyToClipboard(text) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      try {
        const ta = document.createElement("textarea");
        ta.value = text;
        ta.style.position = "fixed";
        ta.style.left = "-9999px";
        document.body.appendChild(ta);
        ta.select();
        document.execCommand("copy");
        document.body.removeChild(ta);
        return true;
      } catch {
        return false;
      }
    }
  }
  // Settings modal (mirror popup.html fields/behavior exactly)
  function openSettingsModal() {
    const cfg = loadSettings();
  
    const root = document.createElement("div");
    root.style.minWidth = "420px";
    root.style.padding = "8px";
  
    function sectionTitle(text) {
      const el = document.createElement("div");
      el.textContent = text;
      el.style.fontSize = "13px";
      el.style.fontWeight = "bold";
      el.style.margin = "6px 0 4px";
      return el;
    }
  
    function row(children) {
      const r = document.createElement("div");
      r.style.display = "flex";
      r.style.gap = "8px";
      r.style.marginBottom = "8px";
      children.forEach(c => r.appendChild(c));
      return r;
    }
  
    function inputField(value) {
      const i = document.createElement("input");
      i.type = "text";
      i.value = value;
      i.style.width = "100%";
      i.style.height = "32px";
      i.style.padding = "4px 6px";
      return i;
    }
  
    // --- Use public ---
    const usePublicWrap = document.createElement("div");
    usePublicWrap.style.display = "flex";
    usePublicWrap.style.alignItems = "center";
    usePublicWrap.style.gap = "8px";
    usePublicWrap.style.marginBottom = "8px";
  
    const usePublic = document.createElement("input");
    usePublic.type = "checkbox";
    usePublic.checked = !!cfg.usePublicUrl;
  
    const usePublicLabel = document.createElement("div");
    usePublicLabel.textContent = "Use public URL (tunnel)";
  
    usePublicWrap.appendChild(usePublic);
    usePublicWrap.appendChild(usePublicLabel);
    root.appendChild(usePublicWrap);
  
    // --- Local ---
    root.appendChild(sectionTitle("Local"));
  
    const localAddress = inputField(cfg.localAddress || "127.0.0.1");
    const localPort = inputField(cfg.localPort || "8080");
  
    root.appendChild(row([localAddress, localPort]));
  
    // --- Public ---
    root.appendChild(sectionTitle("Public"));
  
    const initialUrl =
      transientPublicUrl ||
      cfg.globalUrl ||
      "";

    const globalUrl = inputField(initialUrl);
    root.appendChild(globalUrl);
  
    const detected = document.createElement("div");
    detected.style.fontSize = "12px";
    detected.style.marginBottom = "6px";
    detected.textContent = "Detected: " +
      (transientPublicUrl || (cfg.globalUrl && cfg.manualGlobal ? "(manual)" : "(none)"));
  
    root.appendChild(detected);
  
    const refreshBtn = document.createElement("button");
    refreshBtn.textContent = "Refresh public URL";
    refreshBtn.style.width = "100%";
    refreshBtn.style.height = "32px";
    refreshBtn.style.cursor = "pointer";
  
    root.appendChild(refreshBtn);

    const restoreAudioBtn = document.createElement("button");
    restoreAudioBtn.textContent = "Restore audio output";
    restoreAudioBtn.style.width = "100%";
    restoreAudioBtn.style.height = "32px";
    restoreAudioBtn.style.cursor = "pointer";
    restoreAudioBtn.style.marginTop = "6px";

    root.appendChild(restoreAudioBtn);
  
    // --- Handlers ---
  
    usePublic.onchange = async () => {
      saveSettings({ usePublicUrl: usePublic.checked });
      if (usePublic.checked) {
        await runAutoDetectIfNeeded();
      }
    };
  
    localAddress.onblur = () => {
      saveSettings({ localAddress: localAddress.value.trim() || "127.0.0.1" });
      connectWS(true);
    };
  
    localPort.onblur = () => {
      saveSettings({ localPort: localPort.value.trim() || "8080" });
      connectWS(true);
    };
  
    globalUrl.onchange = () => {
      const v = globalUrl.value.trim();
      if (v) {
        saveSettings({ globalUrl: v, manualGlobal: true });
        detected.textContent = "Detected: (manual)";
      } else {
        saveSettings({ globalUrl: "", manualGlobal: false });
        detected.textContent = "Detected: (none)";
      }
    };
  
    refreshBtn.onclick = async () => {
      detected.textContent = "Detecting...";
      const found = await detectTunnelTransient();
      transientPublicUrl = found;
  
      if (found) {
        saveSettings({ globalUrl: found, manualGlobal: false });
        globalUrl.value = found;
        detected.textContent = "Detected: " + found;
        Spicetify.showNotification("Public URL refreshed");
      } else {
        detected.textContent = "Detected: (none)";
        Spicetify.showNotification("Public URL not found");
      }
    };

    restoreAudioBtn.onclick = async () => {
      await ensureWsReady(1500);
      const res = await wsRpc("restore_audio", {}, 4000);
      if (res && res.ok) {
        Spicetify.showNotification("Audio output restored");
      } else {
        Spicetify.showNotification("Audio restore failed");
      }
    };
  
    Spicetify.PopupModal.display({
      title: "VRChat settings",
      content: root,
      isLarge: true
    });
  }

  async function runAutoDetectIfNeeded() {
    const cfg = loadSettings();
    if (!cfg.usePublicUrl) return;
  
    if (ws && ws.readyState === 1) {
      const r = await wsRpc("tunnel");
      if (r && r.url) {
        const cleaned = String(r.url).replace(/\/$/, "");
        transientPublicUrl = cleaned;
        saveSettings({ globalUrl: cleaned, manualGlobal: false });
        console.log("[stream_bridge] tunnel via WS (auto):", cleaned);
      }
    }
  }

  // initial: try auto-detect in background (non-blocking)
  runAutoDetectIfNeeded().catch(()=>{});

  // inject periodically
  setInterval(injectButtons, 1000);
  injectButtons();
  console.log("[stream_bridge] injected");
})();
