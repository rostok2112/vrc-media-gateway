document.addEventListener("DOMContentLoaded", () => {
  const $ = id => document.getElementById(id);
  const POPUP_STATE_KEY = "popupState";
  const QUICK_LINK_POLL_MS = 2000;
  const LOCAL_FILE_INPUT_PREFIX = "[Local file] ";
  const LOCAL_FILE_DROP_DEFAULT = "Drop a local image, video, or audio file here";
  const LOCAL_MEDIA_UPLOAD_DB_NAME = "vrchat-local-media";
  const LOCAL_MEDIA_UPLOAD_STORE = "uploads";
  const LOCAL_MEDIA_UPLOAD_MAX_AGE_MS = 24 * 60 * 60 * 1000;
  const LOCAL_MEDIA_BLOB_STAGE_MAX_BYTES = 512 * 1024 * 1024;

  let selectedLocalUploadId = "";
  let selectedLocalFileName = "";
  let selectedLocalFileContentType = "";
  let selectedLocalFileMarker = "";
  let isPollingQuickLink = false;

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
  const clearAllCacheBtn = $("clearAllCache");

  const globalError = $("globalError");

  toggleSettingsBtn.addEventListener("click", () => {
    settingsBox.style.display =
      settingsBox.style.display === "block" ? "none" : "block";
  });

  function sleep(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
  }

  function formatBytes(size) {
    const value = Number(size);
    if (!Number.isFinite(value) || value < 0) return "";

    const units = ["B", "KB", "MB", "GB", "TB"];
    let unitIndex = 0;
    let scaled = value;
    while (scaled >= 1024 && unitIndex < units.length - 1) {
      scaled /= 1024;
      unitIndex += 1;
    }
    const digits = scaled >= 100 || unitIndex === 0 ? 0 : scaled >= 10 ? 1 : 2;
    return `${scaled.toFixed(digits)} ${units[unitIndex]}`;
  }

  function selectedLocalFileLabel(name, sizeText = "") {
    return `${LOCAL_FILE_INPUT_PREFIX}${name}${sizeText ? ` (${sizeText})` : ""}`;
  }

  function isSupportedLocalFileMeta(name, type) {
    const mime = String(type || "").toLowerCase();
    if (
      mime.startsWith("image/") ||
      mime.startsWith("video/") ||
      mime.startsWith("audio/")
    ) {
      return true;
    }

    return /\.(jpe?g|png|webp|bmp|gif|tiff?|mp4|m4v|mov|mkv|avi|webm|ts|mts|m2ts|flv|mp3|m4a|aac|flac|ogg|oga|opus|wav|wma)$/i.test(
      name || ""
    );
  }

  function isSupportedLocalFile(file) {
    if (!file) return false;
    return isSupportedLocalFileMeta(file.name, file.type);
  }

  async function ensureReadableFileHandle(handle) {
    if (!handle || handle.kind !== "file") {
      throw new Error("Only local files are supported");
    }

    if (typeof handle.queryPermission === "function") {
      let permission = await handle.queryPermission({ mode: "read" });
      if (permission !== "granted" && typeof handle.requestPermission === "function") {
        permission = await handle.requestPermission({ mode: "read" });
      }
      if (permission !== "granted") {
        throw new Error("Read access to the selected local file was not granted");
      }
    }

    return handle;
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

  function showError(msg) {
    globalError.textContent = msg;
    globalError.style.display = "block";
  }

  function clearError() {
    globalError.textContent = "";
    globalError.style.display = "none";
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

  async function putLocalMediaUpload(uploadId, value) {
    const db = await openLocalMediaUploadDb();
    await new Promise((resolve, reject) => {
      const tx = db.transaction(LOCAL_MEDIA_UPLOAD_STORE, "readwrite");
      const store = tx.objectStore(LOCAL_MEDIA_UPLOAD_STORE);
      const req = store.put(value, uploadId);
      req.onsuccess = () => resolve();
      req.onerror = () => reject(req.error || new Error("Failed to store local upload"));
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

  async function savePopupState() {
    await chrome.storage.session.set({
      [POPUP_STATE_KEY]: {
        input: selectedLocalUploadId ? "" : input.value,
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

  async function saveSettings(patch) {
    await chrome.runtime.sendMessage({
      action: "saveSettings",
      data: patch
    });
  }

  async function clearSelectedLocalFile(resetInput = false, removeStoredUpload = true) {
    const uploadId = selectedLocalUploadId;

    selectedLocalUploadId = "";
    selectedLocalFileName = "";
    selectedLocalFileContentType = "";
    selectedLocalFileMarker = "";
    localFilePicker.value = "";
    updateDropZone();

    if (resetInput && input.value.startsWith(LOCAL_FILE_INPUT_PREFIX)) {
      input.value = "";
    }

    if (removeStoredUpload && uploadId) {
      try {
        await deleteLocalMediaUpload(uploadId);
      } catch (e) {
        console.log("[popup] deleteLocalMediaUpload error:", e && e.message);
      }
    }
  }

  async function stageSelectedLocalUpload(uploadId, value, fileName, contentType, fileSize, statusLabel) {
    if (selectedLocalUploadId) {
      await clearSelectedLocalFile(false, true);
    }

    await putLocalMediaUpload(uploadId, value);

    const sizeText = formatBytes(fileSize);

    selectedLocalUploadId = uploadId;
    selectedLocalFileName = fileName;
    selectedLocalFileContentType = contentType || "";
    selectedLocalFileMarker = selectedLocalFileLabel(fileName, sizeText);

    input.value = selectedLocalFileMarker;
    output.value = "";
    status.textContent = statusLabel || `${fileName} selected`;
    clearError();
    updateDropZone(`Selected: ${fileName}${sizeText ? ` (${sizeText})` : ""}`);
    await savePopupState();
  }

  async function setSelectedLocalFileHandle(handle) {
    const readableHandle = await ensureReadableFileHandle(handle);
    const file = await readableHandle.getFile();
    if (!isSupportedLocalFile(file)) {
      throw new Error("Only local image, video, and audio files are supported");
    }

    const uploadId = crypto.randomUUID();
    const value = {
      handle: readableHandle,
      filename: file.name,
      contentType: file.type || "",
      size: file.size,
      createdAt: Date.now(),
      sourceType: "handle"
    };

    try {
      await stageSelectedLocalUpload(
        uploadId,
        value,
        file.name,
        file.type || "",
        file.size,
        `${file.name} selected from filesystem`
      );
      return;
    } catch (e) {
      if (file.size > LOCAL_MEDIA_BLOB_STAGE_MAX_BYTES) {
        throw new Error("Large local files require File System Access support or a pasted local path");
      }
      console.log("[popup] handle staging fallback:", e && e.message);
    }

    await setSelectedLocalFile(file);
  }

  async function setSelectedLocalFile(file) {
    if (!isSupportedLocalFile(file)) {
      throw new Error("Only local image, video, and audio files are supported");
    }
    if (file.size > LOCAL_MEDIA_BLOB_STAGE_MAX_BYTES) {
      throw new Error("Large local files must be selected through File System Access or pasted as a local path");
    }

    const uploadId = crypto.randomUUID();
    await stageSelectedLocalUpload(
      uploadId,
      {
        blob: file,
        filename: file.name,
        contentType: file.type || "",
        size: file.size,
        createdAt: Date.now(),
        sourceType: "blob"
      },
      file.name,
      file.type || "",
      file.size,
      `${file.name} selected`
    );
  }

  function applyJobLabel(job) {
    if (!job || !job.sourceLabel) return;

    if (job.sourceKind === "local-upload") {
      input.value = selectedLocalFileLabel(job.sourceLabel);
      return;
    }

    if (!input.value || input.value.startsWith(LOCAL_FILE_INPUT_PREFIX)) {
      input.value = job.sourceLabel;
    }
  }

  function pendingStatusText(job) {
    switch (job.sourceKind) {
      case "telegram":
        return "Preparing Telegram stream. Large videos can take a few minutes...";
      case "local-upload":
        return job.status === "starting"
          ? "Uploading local file and preparing HLS stream..."
          : "Preparing local media stream...";
      case "local-path":
        return job.status === "starting"
          ? "Preparing local media from filesystem path..."
          : "Preparing local media stream...";
      case "youtube":
      case "soundcloud":
      case "audio":
      case "video":
      case "image":
        return "Waiting for server...";
      default:
        return "Waiting for server...";
    }
  }

  async function copyResult(url) {
    try {
      await navigator.clipboard.writeText(url);
      return true;
    } catch {
      try {
        output.focus();
        output.select();
        document.execCommand("copy");
        return true;
      } catch {
        return false;
      }
    }
  }

  async function applyQuickLinkState(job, autoCopy = true) {
    if (!job || job.status === "idle") {
      return false;
    }

    applyJobLabel(job);

    if (job.status === "starting" || job.status === "pending") {
      showSpinner(pendingStatusText(job));
      return false;
    }

    hideSpinner();

    if (job.status === "ready" && job.url) {
      output.value = job.url;
      if (autoCopy && !job.autoCopied) {
        const copied = await copyResult(job.url);
        if (copied) {
          try {
            await chrome.runtime.sendMessage({
              action: "markQuickLinkJobCopied",
              url: job.url
            });
          } catch (e) {
            console.log("[popup] markQuickLinkJobCopied error:", e && e.message);
          }
        }
        status.textContent = copied ? "Ready & copied" : "Ready";
      } else {
        status.textContent = "Ready";
      }
      await savePopupState();
      return true;
    }

    if (job.status === "error") {
      status.textContent = "Error: " + (job.error || "Build failed");
      return true;
    }

    return false;
  }

  async function pollQuickLinkUntilSettled() {
    if (isPollingQuickLink) return;
    isPollingQuickLink = true;

    try {
      while (true) {
        const job = await chrome.runtime.sendMessage({ action: "getQuickLinkJobStatus" });
        if (!job || job.status === "idle") {
          hideSpinner();
          return;
        }

        const done = await applyQuickLinkState(job, true);
        if (done) {
          return;
        }

        await sleep(QUICK_LINK_POLL_MS);
      }
    } finally {
      isPollingQuickLink = false;
    }
  }

  async function resumeQuickLinkJobIfNeeded() {
    const job = await chrome.runtime.sendMessage({ action: "getQuickLinkJobStatus" });
    if (!job || job.status === "idle") {
      return;
    }

    const done = await applyQuickLinkState(job, job.status === "ready" && !job.autoCopied);
    if (!done) {
      await pollQuickLinkUntilSettled();
    }
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

  clearAllCacheBtn.addEventListener("click", async () => {
    if (!confirm("Clear all generated streams and temporary cached media files?")) {
      return;
    }

    clearError();
    showSpinner("Clearing all cache...");

    try {
      const result = await chrome.runtime.sendMessage({ action: "clearAllCache" });
      if (!result || !result.ok) {
        throw new Error(result?.error || "Failed to clear cache");
      }

      await clearSelectedLocalFile(true, false);
      output.value = "";
      await chrome.storage.session.remove(POPUP_STATE_KEY);
      await savePopupState();
      status.textContent = "All cache cleared";
    } catch (e) {
      status.textContent = "Error: " + e.message;
    } finally {
      hideSpinner();
    }
  });

  pickFileBtn.addEventListener("click", async () => {
    if (typeof window.showOpenFilePicker === "function") {
      try {
        const handles = await window.showOpenFilePicker({
          multiple: false,
          excludeAcceptAllOption: false,
          types: [{
            description: "Local media",
            accept: {
              "image/*": [".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tif", ".tiff"],
              "video/*": [".mp4", ".m4v", ".mov", ".mkv", ".avi", ".webm", ".ts", ".mts", ".m2ts", ".flv"],
              "audio/*": [".mp3", ".m4a", ".aac", ".flac", ".ogg", ".oga", ".opus", ".wav", ".wma"]
            }
          }]
        });
        const handle = handles && handles[0];
        if (!handle) return;
        await setSelectedLocalFileHandle(handle);
        return;
      } catch (e) {
        if (e && e.name === "AbortError") {
          return;
        }
        status.textContent = "Error: " + e.message;
        return;
      }
    }

    localFilePicker.click();
  });

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

    try {
      const item = Array.from(event.dataTransfer?.items || []).find(entry => entry && entry.kind === "file");
      if (item && typeof item.getAsFileSystemHandle === "function") {
        const handle = await item.getAsFileSystemHandle();
        if (handle && handle.kind === "file") {
          await setSelectedLocalFileHandle(handle);
          return;
        }
      }

      const file = event.dataTransfer?.files?.[0];
      if (!file) return;
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
    if (selectedLocalUploadId && input.value !== selectedLocalFileMarker) {
      await clearSelectedLocalFile(false, true);
    }
    output.value = "";
    await savePopupState();
  });

  getBtn.addEventListener("click", async () => {
    const src = input.value.trim();
    if (!src && !selectedLocalUploadId) return;

    clearError();
    showSpinner("Waiting for server...");

    try {
      let result;

      if (selectedLocalUploadId) {
        result = await chrome.runtime.sendMessage({
          action: "startQuickLinkJob",
          uploadId: selectedLocalUploadId,
          fileName: selectedLocalFileName,
          contentType: selectedLocalFileContentType,
          sourceLabel: selectedLocalFileName
        });
        await clearSelectedLocalFile(false, false);
      } else {
        result = await chrome.runtime.sendMessage({
          action: "startQuickLinkJob",
          source: src,
          sourceLabel: src
        });
      }

      if (!result) {
        throw new Error("Failed to start build");
      }

      const done = await applyQuickLinkState(result, true);
      if (!done) {
        await pollQuickLinkUntilSettled();
      }
    } catch (e) {
      hideSpinner();
      status.textContent = "Error: " + e.message;
    }
  });

  updateDropZone();

  (async () => {
    await cleanupStaleLocalMediaUploads();
    await loadSettingsAndMaybeAutofill();
    await resumeQuickLinkJobIfNeeded();
  })().catch((e) => {
    hideSpinner();
    status.textContent = "Error: " + (e && e.message ? e.message : String(e));
  });
});
