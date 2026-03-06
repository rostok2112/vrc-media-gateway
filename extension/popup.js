document.addEventListener("DOMContentLoaded", () => {
  const $ = id => document.getElementById(id);
  const POPUP_STATE_KEY = "popupState";
  const TELEGRAM_BUILD_POLL_MS = 2000;
  const TELEGRAM_BUILD_MAX_WAIT_MS = 15 * 60 * 1000;

  const input = $("input");
  const output = $("output");
  const getBtn = $("get");
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

  async function savePopupState() {
    await chrome.storage.session.set({
      [POPUP_STATE_KEY]: {
        input: input.value,
        output: output.value,
      },
    });
  }

  async function loadPopupState() {
    const res = await chrome.storage.session.get(POPUP_STATE_KEY);
    const state = res[POPUP_STATE_KEY];
    if (!state) return;

    if (state.input) input.value = state.input;
    if (state.output) output.value = state.output;
  }

  input.addEventListener("input", savePopupState);

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

    spinner.style.display = "block";
    status.textContent = "Auto-detecting public URL...";
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
      spinner.style.display = "none";
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
    spinner.style.display = "block";
    status.textContent = "Refreshing public URL...";

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
      spinner.style.display = "none";
    }
  });

  function sleep(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
  }

  async function waitForTelegramReady(endpoint) {
    const start = await chrome.runtime.sendMessage({
      action: "startTelegramBuild",
      endpoint
    });

    if (!start || start.error) {
      throw new Error(start?.error || "Failed to start Telegram build");
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
        action: "pollTelegramBuild",
        jobId: start.jobId
      });

      if (!statusResult || statusResult.error) {
        throw new Error(statusResult?.error || "Telegram build failed");
      }

      if (statusResult.url) {
        return statusResult.url;
      }
    }

    throw new Error("Timed out waiting for Telegram stream to be ready");
  }

  getBtn.addEventListener("click", async () => {
    const src = input.value.trim();
    if (!src) return;

    spinner.style.display = "block";
    status.textContent = "Waiting for server...";
    clearError();

    try {
      if (/^https?:\/\/t\.me\//.test(src)) {
        status.textContent = "Preparing Telegram stream. Large videos can take a few minutes...";
        const endpoint = "/api/stream-tg-media?url=" + encodeURIComponent(src);
        const readyUrl = await waitForTelegramReady(endpoint);

        output.value = readyUrl;
        await navigator.clipboard.writeText(readyUrl);
        status.textContent = "Ready & copied";
        await savePopupState();
        return;
      }

      let endpoint;
      if (/soundcloud\.com|on\.soundcloud\.com/.test(src)) {
        endpoint = "/api/stream-sc?url=" + encodeURIComponent(src);
      } else if (/open\.spotify\.com\/track|spotify:track:/.test(src)) {
        const bases = await chrome.runtime.sendMessage({ action: "resolveBases" });
        const base = (bases && (bases.resultBase || bases.fetchBase)) || "";
        if (!base) throw new Error("No base URL");

        const link =
          `${base.replace(/\/$/, "")}/api/stream-spotify?url=` +
          encodeURIComponent(src);

        output.value = link;
        await navigator.clipboard.writeText(link);
        status.textContent = "Ready & copied";
        await savePopupState();
        return;
      } else if (/youtube\.com|youtu\.be/.test(src)) {
        endpoint = "/api/stream-yt?url=" + encodeURIComponent(src);
      } else {
        endpoint = "/api/stream-image?url=" + encodeURIComponent(src);
      }

      const r = await chrome.runtime.sendMessage({ action: "waitAndBuild", endpoint });
      if (!r || !r.url) {
        throw new Error(r?.error || "Failed");
      }

      output.value = r.url;
      await navigator.clipboard.writeText(r.url);
      status.textContent = "Ready & copied";
      await savePopupState();
    } catch (e) {
      status.textContent = "Error: " + e.message;
    } finally {
      spinner.style.display = "none";
    }
  });

  loadSettingsAndMaybeAutofill();
});
