(() => {
  const WRAP_ID = "vrchat-spotify-web";
  const POPUP_STATE_KEY = "popupState";

  function extractTrackId(url) {
    if (!url) return null;
    if (url.startsWith("spotify:track:")) {
      const parts = url.split(":");
      return parts[2] || null;
    }

    try {
      const u = new URL(url);
      const parts = u.pathname.split("/").filter(Boolean);
      const idx = parts.indexOf("track");
      if (idx !== -1 && parts[idx + 1]) return parts[idx + 1];
    } catch {}
    return null;
  }

  function randomHex(len) {
    const bytesLen = Math.ceil(len / 2);
    try {
      const bytes = new Uint8Array(bytesLen);
      crypto.getRandomValues(bytes);
      return Array.from(bytes, b => b.toString(16).padStart(2, "0"))
        .join("")
        .slice(0, len);
    } catch {
      return Math.random().toString(16).slice(2).padEnd(len, "0").slice(0, len);
    }
  }

  function formatShareUrl(url) {
    const trackId = extractTrackId(url);
    if (!trackId) return null;

    let si = null;
    try {
      const u = new URL(url);
      si = u.searchParams.get("si");
    } catch {}
    if (!si) si = randomHex(16);

    const out = new URL(`https://open.spotify.com/track/${trackId}`);
    out.searchParams.set("si", si);
    return out.toString();
  }

  function findTrackUrl() {
    const selectors = [
      'a[data-testid="nowplaying-track-link"]',
      'a[data-testid="context-item-info-title"]',
      'a[href*="/track/"][data-testid]',
      'a[href*="/track/"]'
    ];

    for (const sel of selectors) {
      const el = document.querySelector(sel);
      if (el && el.href) return el.href;
    }

    if (location.href.includes("/track/")) return location.href;
    return null;
  }

  async function setQuickLink(input, output = "") {
    try {
      await chrome.storage.session.set({
        [POPUP_STATE_KEY]: { input, output }
      });
    } catch {}
  }

  function flash(btn, text, ms = 900) {
    const prev = btn.textContent;
    btn.textContent = text;
    setTimeout(() => (btn.textContent = prev), ms);
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
      btn.style.color = "var(--spice-text, #fff)";
      btn.style.border = "1px solid rgba(255,255,255,0.2)";
    }

    return btn;
  }

  function findControlsContainer() {
    const selectors = [
      ".main-nowPlayingBar-extraControls",
      ".main-nowPlayingBar-right",
      ".player-controls__buttons",
      "footer .player-controls__buttons"
    ];

    for (const sel of selectors) {
      const el = document.querySelector(sel);
      if (el) return el;
    }

    const queueBtn = document.querySelector('button[data-testid="control-button-queue"]');
    if (queueBtn) {
      let el = queueBtn.parentElement;
      for (let i = 0; i < 6 && el; i += 1, el = el.parentElement) {
        if (el.querySelector('[data-testid="volume-bar"]')) return el;
      }
      return queueBtn.parentElement;
    }

    const volumeBar = document.querySelector('[data-testid="volume-bar"]');
    if (volumeBar) {
      let el = volumeBar.parentElement;
      for (let i = 0; i < 6 && el; i += 1, el = el.parentElement) {
        if (el.querySelector('button[data-testid="control-button-queue"]')) return el;
      }
      return volumeBar.parentElement;
    }

    return null;
  }

  async function handleVrChat(btn) {
    const rawUrl = findTrackUrl();
    const shareUrl = formatShareUrl(rawUrl);
    if (!shareUrl) {
      flash(btn, "No track");
      return;
    }

    await setQuickLink(shareUrl, "");

    const bases = await chrome.runtime.sendMessage({ action: "resolveBases" });
    const base = (bases && (bases.resultBase || bases.fetchBase)) || "";
    if (!base) {
      flash(btn, "Failed");
      return;
    }

    const link =
      `${base.replace(/\/$/, "")}/api/stream-spotify?url=` +
      encodeURIComponent(shareUrl);

    await setQuickLink(shareUrl, link);
    const ok = await copyToClipboard(link);
    flash(btn, ok ? "Copied" : "Copy failed");
  }

  async function handleClear(btn) {
    const rawUrl = findTrackUrl();
    const shareUrl = formatShareUrl(rawUrl);
    if (!shareUrl) {
      flash(btn, "No track");
      return;
    }

    await setQuickLink(shareUrl, "");

    const res = await chrome.runtime.sendMessage({
      action: "clearSpotifyCache",
      url: shareUrl
    });

    if (res && res.ok) {
      flash(btn, "Cleared");
    } else {
      flash(btn, "Failed");
    }
  }

  function injectButtons() {
    if (document.getElementById(WRAP_ID)) return;

    const container = findControlsContainer();
    if (!container) return;

    const wrapper = document.createElement("div");
    wrapper.id = WRAP_ID;
    wrapper.style.display = "flex";
    wrapper.style.alignItems = "center";
    wrapper.style.gap = "10px";
    wrapper.style.marginRight = "12px";

    const vrBtn = createPill("VRChat", true);
    const clearBtn = createPill("Clear cache", false);

    vrBtn.onclick = () => handleVrChat(vrBtn);
    clearBtn.onclick = () => handleClear(clearBtn);

    wrapper.appendChild(vrBtn);
    wrapper.appendChild(clearBtn);

    const firstEl = container.firstElementChild;
    if (firstEl) {
      container.insertBefore(wrapper, firstEl);
    } else {
      container.appendChild(wrapper);
    }
  }

  const obs = new MutationObserver(injectButtons);
  obs.observe(document, { childList: true, subtree: true });
  setInterval(injectButtons, 1000);
  injectButtons();
})();
