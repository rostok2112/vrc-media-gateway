// background.js (patched)
// Based on your original background with added resolveTgPublicLink and extra logging

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

const LONG_BUILD_GRACE_MS = 20000;
const TELEGRAM_BUILD_POLL_MS = 2000;
const TELEGRAM_BUILD_MAX_WAIT_MS = 15 * 60 * 1000;
const RETRYABLE_BUILD_STATUSES = new Set([408, 429, 502, 503, 504, 522, 523, 524]);

function isLongBuildEndpoint(endpoint) {
  return typeof endpoint === "string" && endpoint.startsWith("/api/stream-");
}

function isTelegramMediaEndpoint(endpoint) {
  return typeof endpoint === "string" &&
    /^\/api\/stream-tg-(media|video|image)\?/.test(endpoint);
}

function isAbortError(err) {
  return !!err && (err.name === "AbortError" || /abort/i.test(err.message || ""));
}

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

function buildStreamUrl(base, sid) {
  return `${String(base || "").replace(/\/$/, "")}/streams/${sid}/index.m3u8`;
}

function endpointQuery(endpoint) {
  const idx = String(endpoint || "").indexOf("?");
  return idx >= 0 ? endpoint.slice(idx) : "";
}

async function fetchWithGrace(url, timeoutMs) {
  if (!timeoutMs || timeoutMs <= 0) {
    return await fetch(url, { cache: "no-store" });
  }

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);

  try {
    return await fetch(url, {
      cache: "no-store",
      signal: controller.signal
    });
  } finally {
    clearTimeout(timer);
  }
}

async function canUseLocalBuildBase(localBase) {
  if (!localBase) return false;

  try {
    const res = await fetchWithGrace(localBase + "/api/tunnel", 1500);
    return res.ok;
  } catch {
    return false;
  }
}

async function resolveTelegramBuildBases() {
  const { fetchBase: resolvedFetchBase, resultBase, localBase } = await resolveBases();
  let processBase = resolvedFetchBase;

  if (localBase && resolvedFetchBase !== localBase) {
    if (await canUseLocalBuildBase(localBase)) {
      processBase = localBase;
    }
  }

  const finalBase = (resultBase || processBase || "").replace(/\/$/, "");
  if (!processBase) throw new Error("No fetch base available");
  if (!finalBase) throw new Error("No result base available");

  return { processBase, finalBase };
}

async function startTelegramBuild(endpoint) {
  const { processBase, finalBase } = await resolveTelegramBuildBases();

  const query = endpointQuery(endpoint);
  const startUrl = `${processBase}/api/stream-tg-build-start${query}`;
  console.log("[bg] startTelegramBuild:", startUrl);

  const startRes = await fetchWithGrace(startUrl, 15000);
  if (!startRes.ok) throw new Error("HTTP " + startRes.status);

  const status = await startRes.json();
  console.log("[bg] startTelegramBuild ->", status);

  if (status.error) throw new Error(status.error);
  if (status.ready && status.result_sid) {
    return { url: buildStreamUrl(finalBase, status.result_sid), ready: true };
  }

  const jobId = status.job_id;
  if (!jobId) throw new Error("Telegram build job was not created");

  return {
    ready: false,
    jobId,
    state: status.state || "pending",
  };
}

async function pollTelegramBuild(jobId) {
  const { processBase, finalBase } = await resolveTelegramBuildBases();
  const pollUrl = `${processBase}/api/stream-tg-build-status?job_id=${encodeURIComponent(jobId)}`;
  console.log("[bg] pollTelegramBuild:", pollUrl);

  const pollRes = await fetchWithGrace(pollUrl, 10000);
  if (!pollRes.ok) throw new Error("HTTP " + pollRes.status);

  const status = await pollRes.json();
  if (status.error) throw new Error(status.error);
  if (status.ready && status.result_sid) {
    return { url: buildStreamUrl(finalBase, status.result_sid), ready: true };
  }

  return {
    ready: false,
    jobId,
    state: status.state || "pending",
  };
}

async function waitForTelegramBuild(endpoint) {
  const start = await startTelegramBuild(endpoint);
  if (start.url) {
    return { url: start.url };
  }

  const jobId = start.jobId;
  const deadline = Date.now() + TELEGRAM_BUILD_MAX_WAIT_MS;
  while (Date.now() < deadline) {
    await sleep(TELEGRAM_BUILD_POLL_MS);
    try {
      const status = await pollTelegramBuild(jobId);
      if (status.url) {
        return { url: status.url };
      }
    } catch (e) {
      if (!RETRYABLE_BUILD_STATUSES.has(Number((e.message || "").replace("HTTP ", "")))) {
        throw e;
      }
    }
  }

  throw new Error("Timed out waiting for Telegram stream to be ready");
}

async function resolveBases() {
  const cfg = await getSettings();
  const localBase = `http://${cfg.localAddress}:${cfg.localPort}`;
  let publicBase = cfg.globalUrl?.replace(/\/$/, "") || "";

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
    return { fetchBase: localBase, resultBase: localBase, localBase, publicBase };
  }
  if (cfg.useLocalApiForProcessing) {
    return { fetchBase: localBase, resultBase: publicBase, localBase, publicBase };
  }
  return { fetchBase: publicBase, resultBase: publicBase, localBase, publicBase };
}

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
      const data = msg.data || {};
      const cur = await getSettings();
      const patch = { ...data };
      if (Object.prototype.hasOwnProperty.call(data, "manual")) {
        patch.manualGlobal = !!data.manual;
      } else {
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
        await saveSettingsLocal({ globalUrl: url, manualGlobal: false });
        console.log("[bg] refreshPublicUrl: saved auto url", url);
      } else {
        console.log("[bg] refreshPublicUrl: detect failed");
      }
      sendResponse({ url });
      return;
    }

    // NEW: resolve public username for internal id via local API /api/resolve-tg-public-link
    if (msg.action === "resolveTgPublicLink") {
      try {
        console.log("[bg] resolveTgPublicLink ->", msg.internal);
        const { fetchBase } = await resolveBases();
        if (!fetchBase) throw new Error("No fetch base available for resolve");

        // Accept internal as passed: could be '#-100224...' or '-100...' or 'https://web.telegram.org/a/#-100...'
        const internalParam = encodeURIComponent(String(msg.internal));
        const fetchUrl = `${fetchBase}/api/resolve-tg-public-link?internal=${internalParam}`;
        console.log("[bg] resolveTgPublicLink fetching:", fetchUrl);

        const res = await fetch(fetchUrl, { cache: "no-store" });
        if (res.status === 204) {
          // explicit no public username
          console.log("[bg] resolveTgPublicLink -> 204 no public username");
          sendResponse({ found: false });
          return;
        }
        if (!res.ok) {
          const txt = await res.text();
          console.log("[bg] resolveTgPublicLink HTTP error", res.status, txt);
          sendResponse({ error: `HTTP ${res.status}: ${txt}` });
          return;
        }
        const j = await res.json();
        console.log("[bg] resolveTgPublicLink -> got json:", j);
        sendResponse({ url: j.url || j.tme || null });
      } catch (e) {
        console.log("[bg] resolveTgPublicLink error", e && e.message);
        sendResponse({ error: e && e.message });
      }
      return;
    }

    if (msg.action === "waitAndBuild") {
      try {
        console.log("[bg] waitAndBuild endpoint=", msg.endpoint);

        if (isTelegramMediaEndpoint(msg.endpoint)) {
          const telegramResult = await waitForTelegramBuild(msg.endpoint);
          console.log("[bg] waitAndBuild telegram success ->", telegramResult.url);
          sendResponse(telegramResult);
          return;
        }

        const { fetchBase: resolvedFetchBase, resultBase, localBase } = await resolveBases();
        const longBuild = isLongBuildEndpoint(msg.endpoint);
        let fetchBase = resolvedFetchBase;

        if (longBuild && localBase && resolvedFetchBase !== localBase) {
          if (await canUseLocalBuildBase(localBase)) {
            fetchBase = localBase;
          }
        }
        console.log("[bg] waitAndBuild resolved:", { fetchBase, resultBase, localBase, longBuild });

        if (!fetchBase) throw new Error("No fetch base available");
        if (!resultBase) throw new Error("No result base available");

        const fetchUrl = fetchBase + msg.endpoint;
        const resultUrl = resultBase + msg.endpoint;

        console.log("[bg] waitAndBuild: fetching", fetchUrl);
        const res = await fetchWithGrace(fetchUrl, longBuild ? LONG_BUILD_GRACE_MS : 0);
        if (!res.ok) {
          if (longBuild && RETRYABLE_BUILD_STATUSES.has(res.status)) {
            console.log("[bg] waitAndBuild: upstream timed out, returning pending url", res.status);
            sendResponse({ url: resultUrl, pending: true });
            return;
          }
          throw new Error("HTTP " + res.status);
        }

        console.log("[bg] waitAndBuild: success ->", resultUrl);
        sendResponse({ url: resultUrl });
      } catch (e) {
        if (isLongBuildEndpoint(msg.endpoint) && isAbortError(e)) {
          const { resultBase } = await resolveBases();
          const resultUrl = resultBase + msg.endpoint;
          console.log("[bg] waitAndBuild: build still running after grace period, returning pending url");
          sendResponse({ url: resultUrl, pending: true });
          return;
        }
        console.log("[bg] waitAndBuild error:", e && e.message);
        sendResponse({ error: e.message });
      }
      return;
    }

    if (msg.action === "startTelegramBuild") {
      try {
        const result = await startTelegramBuild(msg.endpoint);
        sendResponse(result);
      } catch (e) {
        console.log("[bg] startTelegramBuild error:", e && e.message);
        sendResponse({ error: e && e.message });
      }
      return;
    }

    if (msg.action === "pollTelegramBuild") {
      try {
        const result = await pollTelegramBuild(msg.jobId);
        sendResponse(result);
      } catch (e) {
        console.log("[bg] pollTelegramBuild error:", e && e.message);
        sendResponse({ error: e && e.message });
      }
      return;
    }

    if (msg.action === "resolveBases") {
      try {
        const bases = await resolveBases();
        sendResponse(bases);
      } catch (e) {
        console.log("[bg] resolveBases error:", e && e.message);
        sendResponse({ error: e && e.message });
      }
      return;
    }

    if (msg.action === "clearSpotifyCache") {
      try {
        const url = msg.url;
        if (!url) throw new Error("missing url");

        const { fetchBase } = await resolveBases();
        if (!fetchBase) throw new Error("No fetch base available");

        const endpoint = "/api/stream-spotify-clear?url=" + encodeURIComponent(url);
        const fetchUrl = fetchBase + endpoint;

        console.log("[bg] clearSpotifyCache fetching:", fetchUrl);
        const res = await fetch(fetchUrl, { method: "POST", cache: "no-store" });
        if (!res.ok) {
          const txt = await res.text();
          throw new Error(`HTTP ${res.status}: ${txt}`);
        }

        sendResponse({ ok: true });
      } catch (e) {
        console.log("[bg] clearSpotifyCache error:", e && e.message);
        sendResponse({ ok: false, error: e && e.message });
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

const VR_IMAGE_MENU_ID = "vrchat-resolve-image";

chrome.runtime.onInstalled.addListener(() => {
  try {
    chrome.contextMenus.removeAll(() => {
      chrome.contextMenus.create({
        id: VR_IMAGE_MENU_ID,
        title: "VRChat",
        contexts: ["image"]
      });
      console.log("[bg] image context menu created");
    });
  } catch (e) {
    console.warn("[bg] failed to create image menu", e);
  }
});

chrome.contextMenus.onClicked.addListener((info, tab) => {
  if (info.menuItemId !== VR_IMAGE_MENU_ID) return;

  const imageUrl = info.srcUrl;

  console.log("[bg] image menu clicked", imageUrl);

  if (!imageUrl) {
    chrome.tabs.sendMessage(tab.id, {
      action: "vr_image_error",
      error: "No image url"
    });
    return;
  }

  chrome.tabs.sendMessage(tab.id, {
    action: "vr_resolve_image",
    url: imageUrl
  });
});
