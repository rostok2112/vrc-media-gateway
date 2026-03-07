document.addEventListener("DOMContentLoaded", () => {
  const $ = id => document.getElementById(id);
  const POPUP_STATE_KEY = "popupState";
  const TELEGRAM_BUILD_POLL_MS = 2000;
  const TELEGRAM_BUILD_MAX_WAIT_MS = 15 * 60 * 1000;
  const LOCAL_FILE_INPUT_PREFIX = "[Local file] ";
  const LOCAL_FILE_DROP_DEFAULT = "Drop a local image, video, or audio file here";

  let selectedLocalFile = null;
  let selectedLocalFileMarker = "";

  const input = $("input");
  const output = $("output");
  const getBtn = $("get");
  const pickFileBtn = $("pickFile");
  const localFilePicker = $("localFilePicker");
  const dropZone = $("dropZone");
  const toggleSettingsBtn = $("toggleSettings");
  const settingsBox = $("settings");
  const spinner = $("spinner");
  const status = $("status");

  const usePublic = $("usePublic");
  const useLocalApi = $("useLocalApi");
  const useLocalApiToggle = $("useLocalApiToggle");

  const localAddress = $("localAddress");
  const localPort = $("localPort");
  const globalUrl = $("globalUrl");
  const refreshPublic = $("refreshPublic");

  const globalError = $("globalError");

  toggleSettingsBtn.addEventListener("click", () => {
    settingsBox.style.display =
      settingsBox.style.display === "block" ? "none" : "block";
  });

  function sleep(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
  }

  function selectedLocalFileLabel(name) {
    return `${LOCAL_FILE_INPUT_PREFIX}${name}`;
  }

  function isSupportedLocalFile(file) {
    if (!file) return false;
    const mime = String(file.type || "").toLowerCase();
    if (
      mime.startsWith("image/") ||
      mime.startsWith("video/") ||
      mime.startsWith("audio/")
    ) {
      return true;
    }

    return /\.(jpe?g|png|webp|bmp|gif|tiff?|mp4|m4v|mov|mkv|avi|webm|ts|mts|m2ts|flv|mp3|m4a|aac|flac|ogg|oga|opus|wav|wma)$/i.test(
      file.name || ""
    );
  }

  function isProbablyLocalPath(value) {
    const src = String(value || "").trim();
    return (
      /^[a-zA-Z]:[\\/]/.test(src) ||
      /^\\\\/.test(src) ||
      /^file:\/\//i.test(src)
    );
  }

  function buildStreamUrl(base, sid) {
    return `${String(base || "").replace(/\/$/, "")}/streams/${sid}/index.m3u8`;
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

  function updateDropZone(text) {
    dropZone.textContent = text || LOCAL_FILE_DROP_DEFAULT;
  }

  function showSpinner(text) {
    spinner.style.display = "block";
    status.textContent = text || "";
  }

  function hideSpinner() {
    spinner.style.display = "none";
  }

  async function savePopupState() {
    await chrome.storage.session.set({
      [POPUP_STATE_KEY]: {
        input: selectedLocalFile ? "" : input.value,
        output: output.value,
      },
    });
  }

  async function loadPopupState() {
    const res = await chrome.storage.session.get(POPUP_STATE_KEY);
    const state = res[POPUP_STATE_KEY];
    if (!state) return;

    if (state.input && !state.input.startsWith(LOCAL_FILE_INPUT_PREFIX)) {
      input.value = state.input;
    }
    if (state.output) output.value = state.output;
  }

  function updateVisibility() {
    useLocalApiToggle.style.display = usePublic.checked ? "flex" : "none";
  }

  function showError(msg) {
    globalError.textContent = msg;
    globalError.style.display = "block";
  }

  function clearError() {
    globalError.textContent = "";
    globalError.style.display = "none";
  }

  async function clearSelectedLocalFile(resetInput = false) {
    selectedLocalFile = null;
    selectedLocalFileMarker = "";
    localFilePicker.value = "";
    updateDropZone();

    if (resetInput && input.value.startsWith(LOCAL_FILE_INPUT_PREFIX)) {
      input.value = "";
    }
  }

  async function setSelectedLocalFile(file) {
    if (!isSupportedLocalFile(file)) {
      throw new Error("Only local image, video, and audio files are supported");
    }

    selectedLocalFile = file;
    selectedLocalFileMarker = selectedLocalFileLabel(file.name);
    input.value = selectedLocalFileMarker;
    output.value = "";
    status.textContent = `${file.name} selected`;
    clearError();
    updateDropZone(`Selected: ${file.name}`);
    await savePopupState();
  }

  async function resolveLocalMediaBases() {
    const result = await chrome.runtime.sendMessage({ action: "resolveLocalMediaBases" });
    if (!result || result.error) {
      throw new Error(result?.error || "Failed to resolve local media bases");
    }
    return result;
  }

  async function startLocalPathBuild(rawPath) {
    const bases = await resolveLocalMediaBases();
    const startRes = await fetch(`${bases.processBase}/local-api/stream-local-path-build-start`, {
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

    return {
      bases,
      start: await startRes.json()
    };
  }

  async function startLocalUploadBuild(file) {
    const bases = await resolveLocalMediaBases();
    const params = new URLSearchParams({
      filename: file.name || "upload.bin"
    });
    if (file.type) {
      params.set("content_type", file.type);
    }

    const startRes = await fetch(
      `${bases.processBase}/local-api/stream-local-upload-build-start?${params.toString()}`,
      {
        method: "POST",
        cache: "no-store",
        headers: {
          "Content-Type": file.type || "application/octet-stream"
        },
        body: file
      }
    );

    if (!startRes.ok) {
      throw new Error(await parseErrorResponse(startRes));
    }

    return {
      bases,
      start: await startRes.json()
    };
  }

  async function waitForLocalReady(startResult) {
    const { bases, start } = startResult;

    if (start.error) {
      throw new Error(start.error);
    }

    if (start.ready && start.result_sid) {
      return buildStreamUrl(bases.finalBase, start.result_sid);
    }

    if (!start.job_id) {
      throw new Error("Local media build job was not created");
    }

    const deadline = Date.now() + TELEGRAM_BUILD_MAX_WAIT_MS;
    while (Date.now() < deadline) {
      await sleep(TELEGRAM_BUILD_POLL_MS);

      const pollRes = await fetch(
        `${bases.processBase}/local-api/stream-local-build-status?job_id=${encodeURIComponent(start.job_id)}`,
        { cache: "no-store" }
      );

      if (!pollRes.ok) {
        throw new Error(await parseErrorResponse(pollRes));
      }

      const pollStatus = await pollRes.json();
      if (pollStatus.error) {
        throw new Error(pollStatus.error);
      }
      if (pollStatus.ready && pollStatus.result_sid) {
        return buildStreamUrl(bases.finalBase, pollStatus.result_sid);
      }
    }

    throw new Error("Timed out waiting for the stream to be ready");
  }

  async function loadSettingsAndMaybeAutofill() {
    const cfg = await chrome.runtime.sendMessage({ action: "getSettings" });

    usePublic.checked = !!cfg.usePublicUrl;
    useLocalApi.checked = !!cfg.useLocalApiForProcessing;

    localAddress.value = cfg.localAddress || "127.0.0.1";
    localPort.value = cfg.localPort || "8080";
    globalUrl.value = cfg.globalUrl || "";

    updateVisibility();
    clearError();
    await loadPopupState();

    const shouldAuto = usePublic.checked && (!cfg.globalUrl || !cfg.manualGlobal);
    if (!shouldAuto) return;

    showSpinner("Auto-detecting public URL...");
    clearError();

    try {
      const r = await chrome.runtime.sendMessage({ action: "detectTunnel" });
      if (r && r.url) {
        globalUrl.value = r.url;
        await chrome.runtime.sendMessage({
          action: "saveSettings",
          data: { globalUrl: r.url, manualGlobal: false }
        });
        status.textContent = "Public URL detected";
      } else {
        showError("Auto-detect failed");
        status.textContent = "";
      }
    } catch {
      showError("Auto-detect failed");
      status.textContent = "";
    } finally {
      hideSpinner();
    }
  }

  async function saveSettings(patch) {
    await chrome.runtime.sendMessage({
      action: "saveSettings",
      data: patch
    });
  }

  usePublic.addEventListener("change", async () => {
    updateVisibility();
    await saveSettings({ usePublicUrl: usePublic.checked });
    if (usePublic.checked) {
      loadSettingsAndMaybeAutofill().catch(() => {});
    }
  });

  useLocalApi.addEventListener("change", () => saveSettings({ useLocalApiForProcessing: useLocalApi.checked }));
  localAddress.addEventListener("change", () => saveSettings({ localAddress: localAddress.value.trim() }));
  localPort.addEventListener("change", () => saveSettings({ localPort: localPort.value.trim() }));

  globalUrl.addEventListener("change", async () => {
    const v = globalUrl.value.trim();
    if (v) {
      await saveSettings({ globalUrl: v, manualGlobal: true });
      clearError();
    } else {
      await saveSettings({ globalUrl: "", manualGlobal: false });
    }
  });

  refreshPublic.addEventListener("click", async () => {
    clearError();
    showSpinner("Refreshing public URL...");

    try {
      const r = await chrome.runtime.sendMessage({ action: "refreshPublicUrl" });
      if (r && r.url) {
        globalUrl.value = r.url;
        await saveSettings({ globalUrl: r.url, manualGlobal: false });
        status.textContent = "Refreshed";
      } else {
        showError("Failed to detect public URL (local API unreachable)");
        status.textContent = "";
      }
    } catch {
      showError("Failed to detect public URL");
      status.textContent = "";
    } finally {
      hideSpinner();
    }
  });

  pickFileBtn.addEventListener("click", () => localFilePicker.click());

  localFilePicker.addEventListener("change", async () => {
    try {
      const file = localFilePicker.files && localFilePicker.files[0];
      if (!file) return;
      await setSelectedLocalFile(file);
    } catch (e) {
      status.textContent = "Error: " + e.message;
    } finally {
      localFilePicker.value = "";
    }
  });

  ["dragenter", "dragover"].forEach(eventName => {
    dropZone.addEventListener(eventName, (event) => {
      event.preventDefault();
      event.stopPropagation();
      dropZone.classList.add("active");
    });
  });

  ["dragleave", "dragend"].forEach(eventName => {
    dropZone.addEventListener(eventName, (event) => {
      event.preventDefault();
      event.stopPropagation();
      dropZone.classList.remove("active");
    });
  });

  dropZone.addEventListener("drop", async (event) => {
    event.preventDefault();
    event.stopPropagation();
    dropZone.classList.remove("active");

    const file = event.dataTransfer?.files?.[0];
    if (!file) return;

    try {
      await setSelectedLocalFile(file);
    } catch (e) {
      status.textContent = "Error: " + e.message;
    }
  });

  document.body.addEventListener("dragover", event => {
    event.preventDefault();
  });

  document.body.addEventListener("drop", event => {
    if (event.target === dropZone) return;
    event.preventDefault();
  });

  input.addEventListener("input", async () => {
    if (selectedLocalFile && input.value !== selectedLocalFileMarker) {
      await clearSelectedLocalFile(false);
    }
    output.value = "";
    await savePopupState();
  });

  async function waitForManagedReady(endpoint) {
    const start = await chrome.runtime.sendMessage({
      action: "startManagedBuild",
      endpoint
    });

    if (!start || start.error) {
      throw new Error(start?.error || "Failed to start build");
    }

    if (start.url) {
      return start.url;
    }

    if (!start.jobId) {
      throw new Error("Telegram build job was not created");
    }

    const deadline = Date.now() + TELEGRAM_BUILD_MAX_WAIT_MS;
    while (Date.now() < deadline) {
      await sleep(TELEGRAM_BUILD_POLL_MS);

      const statusResult = await chrome.runtime.sendMessage({
        action: "pollManagedBuild",
        endpoint,
        jobId: start.jobId
      });

      if (!statusResult || statusResult.error) {
        throw new Error(statusResult?.error || "Build failed");
      }

      if (statusResult.url) {
        return statusResult.url;
      }
    }

    throw new Error("Timed out waiting for the stream to be ready");
  }

  getBtn.addEventListener("click", async () => {
    const src = input.value.trim();
    if (!src && !selectedLocalFile) return;

    showSpinner(selectedLocalFile ? "Uploading local file and preparing HLS stream..." : "Waiting for server...");
    clearError();

    try {
      let readyUrl;

      if (selectedLocalFile) {
        const localBuild = await startLocalUploadBuild(selectedLocalFile);
        readyUrl = await waitForLocalReady(localBuild);
      } else if (isProbablyLocalPath(src)) {
        status.textContent = "Preparing local media from filesystem path...";
        const localBuild = await startLocalPathBuild(src);
        readyUrl = await waitForLocalReady(localBuild);
      } else if (/^https?:\/\/t\.me\//.test(src)) {
        status.textContent = "Preparing Telegram stream. Large videos can take a few minutes...";
        const endpoint = "/api/stream-tg-media?url=" + encodeURIComponent(src);
        readyUrl = await waitForManagedReady(endpoint);
      } else if (/soundcloud\.com|on\.soundcloud\.com/.test(src)) {
        const endpoint = "/api/stream-sc?url=" + encodeURIComponent(src);
        readyUrl = await waitForManagedReady(endpoint);
      } else if (/open\.spotify\.com\/track|spotify:track:/.test(src)) {
        const bases = await chrome.runtime.sendMessage({ action: "resolveBases" });
        const base = (bases && (bases.resultBase || bases.fetchBase)) || "";
        if (!base) throw new Error("No base URL");

        readyUrl =
          `${base.replace(/\/$/, "")}/api/stream-spotify?url=` +
          encodeURIComponent(src);
      } else if (/youtube\.com|youtu\.be/.test(src)) {
        const endpoint = "/api/stream-yt?url=" + encodeURIComponent(src);
        readyUrl = await waitForManagedReady(endpoint);
      } else {
        const endpoint = "/api/stream-image?url=" + encodeURIComponent(src);
        readyUrl = await waitForManagedReady(endpoint);
      }

      output.value = readyUrl;
      await navigator.clipboard.writeText(readyUrl);
      status.textContent = "Ready & copied";
      await clearSelectedLocalFile(false);
      await savePopupState();
    } catch (e) {
      status.textContent = "Error: " + e.message;
    } finally {
      hideSpinner();
    }
  });

  updateDropZone();
  loadSettingsAndMaybeAutofill();
});
