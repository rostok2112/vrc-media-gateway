// ===============================
// VRChat Quick Links - background (debug-friendly)
// ===============================

async function getSettings() {
  return await chrome.storage.sync.get({
    usePublicUrl: true,
    useLocalApiForProcessing: true,
    localAddress: "127.0.0.1",
    localPort: "8080",
    globalUrl: "",
    tunnelApiPath: "/api/tunnel",
    manualGlobal: false
  });
}

async function saveSettingsLocal(patch) {
  const cur = await getSettings();
  await chrome.storage.sync.set({ ...cur, ...patch });
}

// ---------- tunnel detect (ALWAYS via LOCAL) ----------
async function detectTunnel() {
  try {
    const cfg = await getSettings();
    const base = `http://${cfg.localAddress}:${cfg.localPort}`;
    console.log("[bg] detectTunnel: fetching", base + cfg.tunnelApiPath);
    const res = await fetch(base + cfg.tunnelApiPath);
    const j = await res.json();
    console.log("[bg] detectTunnel: got", j && j.url);
    return j.url || "";
  } catch (err) {
    console.log("[bg] detectTunnel: failed", err && err.message);
    return "";
  }
}

// ---------- auto detect on startup (ONLY IF EMPTY and not manual) ----------
async function autoDetectOnStartup() {
  const cfg = await getSettings();
  console.log("[bg] autoDetectOnStartup", cfg);
  if (!cfg.usePublicUrl) return;
  if (cfg.globalUrl && cfg.manualGlobal) return;

  const url = await detectTunnel();
  if (url) {
    console.log("[bg] autoDetectOnStartup: saving auto-detected url", url);
    await saveSettingsLocal({ globalUrl: url, manualGlobal: false });
  }
}

chrome.runtime.onStartup.addListener(autoDetectOnStartup);
chrome.runtime.onInstalled.addListener(autoDetectOnStartup);

// ---------- resolve bases (will attempt on-demand detect if allowed) ----------
async function resolveBases() {
  const cfg = await getSettings();
  const localBase = `http://${cfg.localAddress}:${cfg.localPort}`;
  let publicBase = cfg.globalUrl?.replace(/\/$/, "") || "";

  // If public is requested and not manual, attempt on-demand detect
  if (cfg.usePublicUrl && !cfg.manualGlobal) {
    console.log("[bg] resolveBases: usePublicUrl && not manual -> trying detect");
    try {
      const detected = await detectTunnel();
      if (detected) {
        const cleaned = detected.replace(/\/$/, "");
        if (!publicBase || publicBase !== cleaned) {
          console.log("[bg] resolveBases: detected public url changed -> saving", cleaned);
          await saveSettingsLocal({ globalUrl: cleaned, manualGlobal: false });
          publicBase = cleaned;
        }
      } else {
        console.log("[bg] resolveBases: detectTunnel returned empty");
      }
    } catch (err) {
      console.log("[bg] resolveBases: detect failed", err && err.message);
    }
  }

  if (!cfg.usePublicUrl) {
    return { fetchBase: localBase, resultBase: localBase };
  }
  if (cfg.useLocalApiForProcessing) {
    return { fetchBase: localBase, resultBase: publicBase };
  }
  return { fetchBase: publicBase, resultBase: publicBase };
}

// ---------- messages ----------
chrome.runtime.onMessage.addListener((msg, _, sendResponse) => {
  (async () => {
    console.log("[bg] onMessage:", msg && msg.action);

    if (msg.action === "getSettings") {
      const s = await getSettings();
      console.log("[bg] getSettings ->", s);
      sendResponse(s);
      return;
    }

    if (msg.action === "saveSettings") {
      // Expect data possibly containing 'manual' boolean.
      const data = msg.data || {};
      const cur = await getSettings();
      const patch = { ...data };

      // If caller specified explicit manual flag, respect it.
      if (Object.prototype.hasOwnProperty.call(data, "manual")) {
        patch.manualGlobal = !!data.manual;
      } else {
        // If caller didn't specify manual, DO NOT change manualGlobal automatically.
        // (This avoids accidental marking of auto-detected values as manual.)
        delete patch.manualGlobal;
      }

      console.log("[bg] saveSettings patch ->", patch);
      await saveSettingsLocal(patch);
      sendResponse({ ok: true });
      return;
    }

    if (msg.action === "detectTunnel") {
      const url = await detectTunnel();
      sendResponse({ url });
      return;
    }

    if (msg.action === "refreshPublicUrl") {
      console.log("[bg] refreshPublicUrl called");
      const url = await detectTunnel();
      if (url) {
        // Save as AUTO explicitly (manualGlobal:false)
        await saveSettingsLocal({ globalUrl: url, manualGlobal: false });
        console.log("[bg] refreshPublicUrl: saved auto url", url);
      } else {
        console.log("[bg] refreshPublicUrl: detect failed");
      }
      sendResponse({ url });
      return;
    }

    if (msg.action === "streamTelegram") {
      try {
        const cfg = await getSettings();
        const fetchUrl = `http://${cfg.localAddress}:${cfg.localPort}/api/stream-tg`;
        console.log("[bg] streamTelegram -> POST", fetchUrl, msg.url);
        const res = await fetch(fetchUrl, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ url: msg.url })
        });
        if (!res.ok) throw new Error("HTTP " + res.status);
        const resultBase = cfg.usePublicUrl ? cfg.globalUrl.replace(/\/$/, "") : `http://${cfg.localAddress}:${cfg.localPort}`;
        sendResponse({ url: resultBase + "/api/stream-tg" });
      } catch (e) {
        console.log("[bg] streamTelegram error", e && e.message);
        sendResponse({ error: e.message });
      }
      return;
    }

    if (msg.action === "waitAndBuild") {
      try {
        console.log("[bg] waitAndBuild endpoint=", msg.endpoint);
        const { fetchBase, resultBase } = await resolveBases();
        console.log("[bg] waitAndBuild resolved:", { fetchBase, resultBase });

        if (!fetchBase) throw new Error("No fetch base available");
        if (!resultBase) throw new Error("No result base available");

        const fetchUrl = fetchBase + msg.endpoint;
        const resultUrl = resultBase + msg.endpoint;

        console.log("[bg] waitAndBuild: fetching", fetchUrl);
        const res = await fetch(fetchUrl, { cache: "no-store" });
        if (!res.ok) throw new Error("HTTP " + res.status);

        console.log("[bg] waitAndBuild: success ->", resultUrl);
        sendResponse({ url: resultUrl });
      } catch (e) {
        console.log("[bg] waitAndBuild error:", e && e.message);
        sendResponse({ error: e.message });
      }
      return;
    }

    if (msg.action === "debugDump") {
      const s = await getSettings();
      console.log("[bg] debugDump ->", s);
      sendResponse({ settings: s });
      return;
    }

    sendResponse({ error: "unknown action" });
  })();

  return true;
});
