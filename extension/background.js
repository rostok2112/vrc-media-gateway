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
cleanupStaleLocalMediaUploads().catch(() => {});

const LONG_BUILD_GRACE_MS = 20000;
const TELEGRAM_BUILD_POLL_MS = 2000;
const TELEGRAM_BUILD_MAX_WAIT_MS = 15 * 60 * 1000;
const RETRYABLE_BUILD_STATUSES = new Set([408, 429, 502, 503, 504, 522, 523, 524]);
const LOCAL_MEDIA_API_BASE = "http://127.0.0.1:5000";
const QUICK_LINK_JOB_KEY = "activeQuickLinkJob";
const LOCAL_MEDIA_UPLOAD_DB_NAME = "vrchat-local-media";
const LOCAL_MEDIA_UPLOAD_STORE = "uploads";
const LOCAL_MEDIA_UPLOAD_MAX_AGE_MS = 24 * 60 * 60 * 1000;

function isLongBuildEndpoint(endpoint) {
  return typeof endpoint === "string" && endpoint.startsWith("/api/stream-");
}

function isProbablyLocalPath(value) {
  const src = String(value || "").trim();
  return (
    /^[a-zA-Z]:[\\/]/.test(src) ||
    /^\\\\/.test(src) ||
    /^file:\/\//i.test(src)
  );
}

function isTelegramMediaEndpoint(endpoint) {
  return typeof endpoint === "string" &&
    /^\/api\/stream-tg-(media|video|image)\?/.test(endpoint);
}

function getManagedBuildConfig(endpoint) {
  if (typeof endpoint !== "string") return null;
  if (/^\/api\/stream-tg-(media|video|image)\?/.test(endpoint)) {
    return {
      startPath: "/api/stream-tg-build-start",
      statusPath: "/api/stream-tg-build-status"
    };
  }
  if (endpoint.startsWith("/api/stream-yt?")) {
    return {
      startPath: "/api/stream-yt-build-start",
      statusPath: "/api/stream-yt-build-status"
    };
  }
  if (endpoint.startsWith("/api/stream-sc?")) {
    return {
      startPath: "/api/stream-sc-build-start",
      statusPath: "/api/stream-sc-build-status"
    };
  }
  if (endpoint.startsWith("/api/stream-image?")) {
    return {
      startPath: "/api/stream-image-build-start",
      statusPath: "/api/stream-image-build-status"
    };
  }
  return null;
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

async function getSessionValue(key) {
  const result = await chrome.storage.session.get(key);
  return result[key];
}

async function setSessionValue(key, value) {
  if (value === undefined || value === null) {
    await chrome.storage.session.remove(key);
    return null;
  }
  await chrome.storage.session.set({ [key]: value });
  return value;
}

async function getQuickLinkJobState() {
  return await getSessionValue(QUICK_LINK_JOB_KEY);
}

async function setQuickLinkJobState(job) {
  return await setSessionValue(QUICK_LINK_JOB_KEY, job);
}

function openLocalMediaUploadDb() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(LOCAL_MEDIA_UPLOAD_DB_NAME, 1);
    req.onupgradeneeded = () => {
      const db = req.result;
      if (!db.objectStoreNames.contains(LOCAL_MEDIA_UPLOAD_STORE)) {
        db.createObjectStore(LOCAL_MEDIA_UPLOAD_STORE);
      }
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error || new Error("Failed to open local media DB"));
  });
}

async function getLocalMediaUpload(uploadId) {
  const db = await openLocalMediaUploadDb();
  return await new Promise((resolve, reject) => {
    const tx = db.transaction(LOCAL_MEDIA_UPLOAD_STORE, "readonly");
    const store = tx.objectStore(LOCAL_MEDIA_UPLOAD_STORE);
    const req = store.get(uploadId);
    req.onsuccess = () => resolve(req.result || null);
    req.onerror = () => reject(req.error || new Error("Failed to read local upload"));
    tx.oncomplete = () => db.close();
    tx.onabort = () => db.close();
    tx.onerror = () => db.close();
  });
}

async function deleteLocalMediaUpload(uploadId) {
  if (!uploadId) return;
  const db = await openLocalMediaUploadDb();
  await new Promise((resolve, reject) => {
    const tx = db.transaction(LOCAL_MEDIA_UPLOAD_STORE, "readwrite");
    const store = tx.objectStore(LOCAL_MEDIA_UPLOAD_STORE);
    const req = store.delete(uploadId);
    req.onsuccess = () => resolve();
    req.onerror = () => reject(req.error || new Error("Failed to delete local upload"));
    tx.oncomplete = () => db.close();
    tx.onabort = () => db.close();
    tx.onerror = () => db.close();
  });
}

async function cleanupStaleLocalMediaUploads(maxAgeMs = LOCAL_MEDIA_UPLOAD_MAX_AGE_MS) {
  const cutoff = Date.now() - maxAgeMs;
  const db = await openLocalMediaUploadDb();
  await new Promise((resolve, reject) => {
    const tx = db.transaction(LOCAL_MEDIA_UPLOAD_STORE, "readwrite");
    const store = tx.objectStore(LOCAL_MEDIA_UPLOAD_STORE);
    const req = store.openCursor();

    req.onsuccess = () => {
      const cursor = req.result;
      if (!cursor) {
        resolve();
        return;
      }

      const value = cursor.value || {};
      if (!value.createdAt || value.createdAt < cutoff) {
        cursor.delete();
      }
      cursor.continue();
    };
    req.onerror = () => reject(req.error || new Error("Failed to clean local uploads"));
    tx.oncomplete = () => db.close();
    tx.onabort = () => db.close();
    tx.onerror = () => db.close();
  });
}

async function parseErrorResponse(res) {
  const text = await res.text();
  if (!text) {
    return `HTTP ${res.status}`;
  }

  try {
    const json = JSON.parse(text);
    return json.detail || json.error || `HTTP ${res.status}`;
  } catch {
    return text;
  }
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

async function resolveManagedBuildBases() {
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

async function resolveLocalMediaBases() {
  const { fetchBase, resultBase, localBase } = await resolveBases();
  const finalBase = (resultBase || localBase || fetchBase || "").replace(/\/$/, "");
  if (!finalBase) throw new Error("No result base available");

  try {
    const probe = await fetchWithGrace(`${LOCAL_MEDIA_API_BASE}/api/tunnel`, 1500);
    if (!probe.ok) {
      throw new Error(`HTTP ${probe.status}`);
    }
  } catch (e) {
    throw new Error(`Local media API is not reachable on ${LOCAL_MEDIA_API_BASE}`);
  }

  return { processBase: LOCAL_MEDIA_API_BASE, finalBase };
}

function quickLinkStateBase(patch) {
  return {
    status: "pending",
    sourceKind: "unknown",
    sourceLabel: "",
    error: "",
    url: "",
    endpoint: "",
    jobKind: "",
    jobId: "",
    ...patch,
    updatedAt: Date.now()
  };
}

async function startLocalPathBuild(rawPath) {
  const { processBase, finalBase } = await resolveLocalMediaBases();
  const startRes = await fetch(`${processBase}/local-api/stream-local-path-build-start`, {
    method: "POST",
    cache: "no-store",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify({ path: rawPath })
  });

  if (!startRes.ok) {
    throw new Error(await parseErrorResponse(startRes));
  }

  const start = await startRes.json();
  if (start.error) {
    throw new Error(start.error);
  }

  if (start.ready && start.result_sid) {
    return { ready: true, url: buildStreamUrl(finalBase, start.result_sid) };
  }

  return {
    ready: false,
    jobId: start.job_id,
    state: start.state || "pending"
  };
}

async function startLocalUploadBuild(uploadId, fileName, contentType) {
  const upload = await getLocalMediaUpload(uploadId);
  if (!upload || !upload.blob) {
    throw new Error("Selected local file is no longer available");
  }

  try {
    const { processBase, finalBase } = await resolveLocalMediaBases();
    const params = new URLSearchParams({
      filename: fileName || upload.filename || "upload.bin"
    });
    const effectiveContentType = contentType || upload.contentType || "";
    if (effectiveContentType) {
      params.set("content_type", effectiveContentType);
    }

    const startRes = await fetch(
      `${processBase}/local-api/stream-local-upload-build-start?${params.toString()}`,
      {
        method: "POST",
        cache: "no-store",
        headers: {
          "Content-Type": effectiveContentType || "application/octet-stream"
        },
        body: upload.blob
      }
    );

    if (!startRes.ok) {
      throw new Error(await parseErrorResponse(startRes));
    }

    const start = await startRes.json();
    if (start.error) {
      throw new Error(start.error);
    }

    if (start.ready && start.result_sid) {
      return { ready: true, url: buildStreamUrl(finalBase, start.result_sid) };
    }

    return {
      ready: false,
      jobId: start.job_id,
      state: start.state || "pending"
    };
  } finally {
    try {
      await deleteLocalMediaUpload(uploadId);
    } catch (e) {
      console.log("[bg] deleteLocalMediaUpload error:", e && e.message);
    }
  }
}

async function pollLocalBuild(jobId) {
  const { processBase, finalBase } = await resolveLocalMediaBases();
  const pollRes = await fetch(
    `${processBase}/local-api/stream-local-build-status?job_id=${encodeURIComponent(jobId)}`,
    { cache: "no-store" }
  );

  if (!pollRes.ok) {
    throw new Error(await parseErrorResponse(pollRes));
  }

  const status = await pollRes.json();
  if (status.error) {
    throw new Error(status.error);
  }

  if (status.ready && status.result_sid) {
    return { ready: true, url: buildStreamUrl(finalBase, status.result_sid) };
  }

  return {
    ready: false,
    state: status.state || "pending"
  };
}

async function buildSpotifyQuickLink(src) {
  const bases = await resolveBases();
  const base = (bases && (bases.resultBase || bases.fetchBase)) || "";
  if (!base) {
    throw new Error("No base URL");
  }

  return `${base.replace(/\/$/, "")}/api/stream-spotify?url=${encodeURIComponent(src)}`;
}

function buildManagedEndpointForSource(src) {
  if (/^https?:\/\/t\.me\//.test(src)) {
    return {
      endpoint: "/api/stream-tg-media?url=" + encodeURIComponent(src),
      sourceKind: "telegram",
      sourceLabel: src
    };
  }
  if (/soundcloud\.com|on\.soundcloud\.com/.test(src)) {
    return {
      endpoint: "/api/stream-sc?url=" + encodeURIComponent(src),
      sourceKind: "soundcloud",
      sourceLabel: src
    };
  }
  if (/youtube\.com|youtu\.be/.test(src)) {
    return {
      endpoint: "/api/stream-yt?url=" + encodeURIComponent(src),
      sourceKind: "youtube",
      sourceLabel: src
    };
  }
  return {
    endpoint: "/api/stream-image?url=" + encodeURIComponent(src),
    sourceKind: "image",
    sourceLabel: src
  };
}

async function startQuickLinkJob(msg) {
  const src = String(msg.source || "").trim();
  const sourceLabel = String(msg.sourceLabel || src || msg.fileName || "").trim();

  if (msg.uploadId) {
    await setQuickLinkJobState(quickLinkStateBase({
      status: "starting",
      sourceKind: "local-upload",
      sourceLabel: sourceLabel || msg.fileName || "Local file"
    }));

    const start = await startLocalUploadBuild(msg.uploadId, msg.fileName, msg.contentType);
    const state = start.ready
      ? quickLinkStateBase({
          status: "ready",
          sourceKind: "local-upload",
          sourceLabel: sourceLabel || msg.fileName || "Local file",
          url: start.url
        })
      : quickLinkStateBase({
          status: "pending",
          sourceKind: "local-upload",
          sourceLabel: sourceLabel || msg.fileName || "Local file",
          jobKind: "local",
          jobId: start.jobId
        });
    await setQuickLinkJobState(state);
    return state;
  }

  if (!src) {
    throw new Error("Missing source");
  }

  if (isProbablyLocalPath(src)) {
    await setQuickLinkJobState(quickLinkStateBase({
      status: "starting",
      sourceKind: "local-path",
      sourceLabel: sourceLabel || src
    }));

    const start = await startLocalPathBuild(src);
    const state = start.ready
      ? quickLinkStateBase({
          status: "ready",
          sourceKind: "local-path",
          sourceLabel: sourceLabel || src,
          url: start.url
        })
      : quickLinkStateBase({
          status: "pending",
          sourceKind: "local-path",
          sourceLabel: sourceLabel || src,
          jobKind: "local",
          jobId: start.jobId
        });
    await setQuickLinkJobState(state);
    return state;
  }

  if (/open\.spotify\.com\/track|spotify:track:/.test(src)) {
    const link = await buildSpotifyQuickLink(src);
    const state = quickLinkStateBase({
      status: "ready",
      sourceKind: "spotify",
      sourceLabel: sourceLabel || src,
      url: link
    });
    await setQuickLinkJobState(state);
    return state;
  }

  const managed = buildManagedEndpointForSource(src);
  await setQuickLinkJobState(quickLinkStateBase({
    status: "starting",
    sourceKind: managed.sourceKind,
    sourceLabel: managed.sourceLabel,
    endpoint: managed.endpoint
  }));

  const start = await startManagedBuild(managed.endpoint);
  const state = start.url
    ? quickLinkStateBase({
        status: "ready",
        sourceKind: managed.sourceKind,
        sourceLabel: managed.sourceLabel,
        endpoint: managed.endpoint,
        url: start.url
      })
    : quickLinkStateBase({
        status: "pending",
        sourceKind: managed.sourceKind,
        sourceLabel: managed.sourceLabel,
        endpoint: managed.endpoint,
        jobKind: "managed",
        jobId: start.jobId
      });
  await setQuickLinkJobState(state);
  return state;
}

async function getQuickLinkJobStatus() {
  const job = await getQuickLinkJobState();
  if (!job) {
    return { status: "idle" };
  }

  if (job.status !== "pending") {
    return job;
  }

  try {
    if (job.jobKind === "managed" && job.endpoint && job.jobId) {
      const result = await pollManagedBuild(job.endpoint, job.jobId);
      if (result.url) {
        const ready = quickLinkStateBase({
          ...job,
          status: "ready",
          url: result.url,
          error: ""
        });
        await setQuickLinkJobState(ready);
        return ready;
      }
    } else if (job.jobKind === "local" && job.jobId) {
      const result = await pollLocalBuild(job.jobId);
      if (result.url) {
        const ready = quickLinkStateBase({
          ...job,
          status: "ready",
          url: result.url,
          error: ""
        });
        await setQuickLinkJobState(ready);
        return ready;
      }
    }
  } catch (e) {
    const failed = quickLinkStateBase({
      ...job,
      status: "error",
      error: e && e.message ? e.message : String(e),
      url: ""
    });
    await setQuickLinkJobState(failed);
    return failed;
  }

  const pending = quickLinkStateBase({
    ...job,
    status: "pending"
  });
  await setQuickLinkJobState(pending);
  return pending;
}

async function startBuildWithConfig(endpoint, startPath) {
  const { processBase, finalBase } = await resolveManagedBuildBases();

  const query = endpointQuery(endpoint);
  const startUrl = `${processBase}${startPath}${query}`;
  console.log("[bg] startBuildWithConfig:", startUrl);

  const startRes = await fetchWithGrace(startUrl, 15000);
  if (!startRes.ok) throw new Error("HTTP " + startRes.status);

  const status = await startRes.json();
  console.log("[bg] startBuildWithConfig ->", status);

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

async function pollBuildWithConfig(statusPath, jobId) {
  const { processBase, finalBase } = await resolveManagedBuildBases();
  const pollUrl = `${processBase}${statusPath}?job_id=${encodeURIComponent(jobId)}`;
  console.log("[bg] pollBuildWithConfig:", pollUrl);

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

async function startManagedBuild(endpoint) {
  const config = getManagedBuildConfig(endpoint);
  if (!config) throw new Error("No managed build config for endpoint");
  return await startBuildWithConfig(endpoint, config.startPath);
}

async function pollManagedBuild(endpoint, jobId) {
  const config = getManagedBuildConfig(endpoint);
  if (!config) throw new Error("No managed build config for endpoint");
  return await pollBuildWithConfig(config.statusPath, jobId);
}

async function waitForManagedBuild(endpoint) {
  const config = getManagedBuildConfig(endpoint);
  if (!config) throw new Error("No managed build config for endpoint");

  const start = await startBuildWithConfig(endpoint, config.startPath);
  if (start.url) {
    return { url: start.url };
  }

  const jobId = start.jobId;
  const deadline = Date.now() + TELEGRAM_BUILD_MAX_WAIT_MS;
  while (Date.now() < deadline) {
    await sleep(TELEGRAM_BUILD_POLL_MS);
    try {
      const status = await pollBuildWithConfig(config.statusPath, jobId);
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

async function startTelegramBuild(endpoint) {
  return await startBuildWithConfig(endpoint, "/api/stream-tg-build-start");
}

async function pollTelegramBuild(jobId) {
  return await pollBuildWithConfig("/api/stream-tg-build-status", jobId);
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

    if (msg.action === "startQuickLinkJob") {
      try {
        const result = await startQuickLinkJob(msg);
        sendResponse(result);
      } catch (e) {
        const failed = quickLinkStateBase({
          status: "error",
          sourceKind: msg.uploadId ? "local-upload" : "unknown",
          sourceLabel: msg.sourceLabel || msg.source || msg.fileName || "",
          error: e && e.message ? e.message : String(e)
        });
        await setQuickLinkJobState(failed);
        console.log("[bg] startQuickLinkJob error:", e && e.message);
        sendResponse(failed);
      }
      return;
    }

    if (msg.action === "getQuickLinkJobStatus") {
      try {
        const result = await getQuickLinkJobStatus();
        sendResponse(result);
      } catch (e) {
        console.log("[bg] getQuickLinkJobStatus error:", e && e.message);
        sendResponse({ status: "error", error: e && e.message ? e.message : String(e) });
      }
      return;
    }

    if (msg.action === "clearQuickLinkJob") {
      await setQuickLinkJobState(null);
      sendResponse({ ok: true });
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

        const managedBuild = getManagedBuildConfig(msg.endpoint);
        if (managedBuild) {
          const managedResult = await waitForManagedBuild(msg.endpoint);
          console.log("[bg] waitAndBuild managed success ->", managedResult.url);
          sendResponse(managedResult);
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

    if (msg.action === "startManagedBuild") {
      try {
        const result = await startManagedBuild(msg.endpoint);
        sendResponse(result);
      } catch (e) {
        console.log("[bg] startManagedBuild error:", e && e.message);
        sendResponse({ error: e && e.message });
      }
      return;
    }

    if (msg.action === "pollManagedBuild") {
      try {
        const result = await pollManagedBuild(msg.endpoint, msg.jobId);
        sendResponse(result);
      } catch (e) {
        console.log("[bg] pollManagedBuild error:", e && e.message);
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

    if (msg.action === "resolveLocalMediaBases") {
      try {
        const bases = await resolveLocalMediaBases();
        sendResponse(bases);
      } catch (e) {
        console.log("[bg] resolveLocalMediaBases error:", e && e.message);
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
