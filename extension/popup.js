document.addEventListener("DOMContentLoaded", () => {
  const $ = id => document.getElementById(id);

  const POPUP_STATE_KEY = "popupState";

  // ================= ELEMENTS =================
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

  // ================= SETTINGS TOGGLE =================
  toggleSettingsBtn.addEventListener("click", () => {
    settingsBox.style.display =
      settingsBox.style.display === "block" ? "none" : "block";
  });

  // ================= POPUP STATE =================
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

  // ================= UI VISIBILITY =================
  function updateVisibility() {
    useLocalApiToggle.style.display = usePublic.checked ? "flex" : "none";
  }

  // ================= ERRORS =================
  function showError(msg) {
    globalError.textContent = msg;
    globalError.style.display = "block";
  }

  function clearError() {
    globalError.textContent = "";
    globalError.style.display = "none";
  }

  // ================= LOAD SETTINGS + AUTO-FILL LOGIC =================
  // getSettings returns object including manualGlobal boolean
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

    // decide whether to attempt auto-detect:
    // rules: if using public && (no saved url OR saved url is NOT manual) => attempt auto
    const shouldAuto = usePublic.checked && (!cfg.globalUrl || !cfg.manualGlobal);
    if (shouldAuto) {
      // show spinner/status while detecting
      spinner.style.display = "block";
      status.textContent = "Auto-detecting public URL…";
      clearError();

      try {
        const r = await chrome.runtime.sendMessage({ action: "detectTunnel" });
        if (r && r.url) {
          globalUrl.value = r.url;
          // save as auto (manualGlobal: false) so it can be overwritten next time
          await chrome.runtime.sendMessage({
            action: "saveSettings",
            data: { globalUrl: r.url, manualGlobal: false }
          });
          status.textContent = "Public URL detected";
        } else {
          showError("Auto-detect failed");
          status.textContent = "";
        }
      } catch (e) {
        showError("Auto-detect failed");
        status.textContent = "";
      } finally {
        spinner.style.display = "none";
      }
    }
  }

  // ================= SAVE SETTINGS =================
  async function saveSettings(patch) {
    await chrome.runtime.sendMessage({
      action: "saveSettings",
      data: patch
    });
  }

  usePublic.addEventListener("change", async () => {
    updateVisibility();
    // when toggled to usePublic, we may want to attempt autofill immediately if no manual value
    await saveSettings({ usePublicUrl: usePublic.checked });
    if (usePublic.checked) {
      // re-run the autofill logic (non-blocking)
      loadSettingsAndMaybeAutofill().catch(() => {});
    }
  });

  useLocalApi.addEventListener("change", () => saveSettings({ useLocalApiForProcessing: useLocalApi.checked }));
  localAddress.addEventListener("change", () => saveSettings({ localAddress: localAddress.value.trim() }));
  localPort.addEventListener("change", () => saveSettings({ localPort: localPort.value.trim() }));

  // when user edits globalUrl manually -> set manualGlobal = true (if non-empty)
  globalUrl.addEventListener("change", async () => {
    const v = globalUrl.value.trim();
    if (v) {
      await saveSettings({ globalUrl: v, manualGlobal: true });
      clearError();
    } else {
      // if user cleared the field, treat as non-manual so auto can fill later
      await saveSettings({ globalUrl: "", manualGlobal: false });
    }
  });

  // ================= REFRESH PUBLIC =================
  refreshPublic.addEventListener("click", async () => {
    clearError();
    spinner.style.display = "block";
    status.textContent = "Refreshing public URL…";

    try {
      // background's refreshPublicUrl will detect and save (and mark manualGlobal:false)
      const r = await chrome.runtime.sendMessage({ action: "refreshPublicUrl" });
      if (r && r.url) {
        globalUrl.value = r.url;
        // save is already done by background; but ensure local UI reflects manual=false
        await saveSettings({ globalUrl: r.url, manualGlobal: false });
        status.textContent = "Refreshed";
      } else {
        showError("Failed to detect public URL (local API unreachable)");
        status.textContent = "";
      }
    } catch (e) {
      showError("Failed to detect public URL");
      status.textContent = "";
    } finally {
      spinner.style.display = "none";
    }
  });

  // ================= GET (main action) =================
  getBtn.addEventListener("click", async () => {
    const src = input.value.trim();
    if (!src) return;

    spinner.style.display = "block";
    status.textContent = "Waiting for server…";
    clearError();

    try {
      // --- TELEGRAM: try video first, then image ---
      if (/^https?:\/\/t\.me\//.test(src)) {
        status.textContent = "Trying Telegram video...";
        let endpoint = "/api/stream-tg-video?url=" + encodeURIComponent(src);
        let r = await chrome.runtime.sendMessage({ action: "waitAndBuild", endpoint });

        if (!r || !r.url) {
          status.textContent = "Video failed, trying Telegram image...";
          endpoint = "/api/stream-tg-image?url=" + encodeURIComponent(src);
          r = await chrome.runtime.sendMessage({ action: "waitAndBuild", endpoint });
        }

        if (!r || !r.url) {
          throw new Error(r?.error || "Failed to process Telegram post (video+image)");
        }

        output.value = r.url;
        await navigator.clipboard.writeText(r.url);
        status.textContent = "Ready & copied ✔";
        await savePopupState();
        return;
      }

      // --- NON-TELEGRAM ---
      let endpoint;
      if (/soundcloud\.com|on\.soundcloud\.com/.test(src)) {
        endpoint = "/api/stream-sc?url=" + encodeURIComponent(src);
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
      status.textContent = "Ready & copied ✔";
      await savePopupState();
    } catch (e) {
      status.textContent = "Error: " + e.message;
    } finally {
      spinner.style.display = "none";
    }
  });

  // ================= INIT =================
  loadSettingsAndMaybeAutofill();

});
