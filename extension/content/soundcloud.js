(() => {
  const BTN_CLASS = "vrchat-sc-btn";
  const POPUP_ID = "vrchat-popup";
  const COPYLINK_SELECTOR = 'button.sc-button-copylink[aria-label="Copy Link"], button.sc-button-copylink';

  const SC_URL_RE = /(https?:\/\/(?:www\.)?soundcloud\.com\/[^\s"'<>]+)/i;

  function debug(...args) {
    try { console.debug("VRCHAT-SC:", ...args); } catch(e) {}
  }

  function findContainer() {
    return document.querySelector(
      ".soundActions, .listenEngagement__actions, .sc-button-toolbar, .listenEngagement"
    );
  }

  /* ---- Detect if page is likely a single track page ---- */
  function isLikelyTrackPage() {
    try {
      const parts = location.pathname.split('/').filter(Boolean);
      // typical track url: /{user}/{track-slug}  => exactly 2 segments
      if (parts.length !== 2) return false;

      // blacklist common non-track second segments
      const forbidden = new Set(['sets','likes','tracks','albums','reposts','groups','collections','search','discover','charts','upload','messages','notifications']);
      if (forbidden.has(parts[1].toLowerCase())) return false;

      // If og:type explicitly says it's music/song — strong indicator
      const og = document.querySelector('meta[property="og:type"], meta[name="og:type"]');
      if (og) {
        const val = (og.getAttribute('content') || '').toLowerCase();
        if (/song|music|track/.test(val)) return true;
      }

      // Check canonical link — if it's to a specific item with two segments it's ok
      const canonical = document.querySelector('link[rel="canonical"]');
      if (canonical) {
        const href = canonical.getAttribute('href') || '';
        if (SC_URL_RE.test(href)) {
          const p = (new URL(href)).pathname.split('/').filter(Boolean);
          if (p.length === 2 && !forbidden.has(p[1].toLowerCase())) return true;
        }
      }

      // If there is a single .sound__header on page -> likely the single track view
      const headers = document.querySelectorAll('.sound__header');
      if (headers && headers.length === 1) return true;

      // Fallback conservative check: presence of a large waveform element + single player area
      const wf = document.querySelectorAll('.waveform, .playControls, .listenEngagement__actions');
      if (wf && wf.length > 0) {
        // but to avoid list items, ensure there is not an obvious list container dominating the page
        const list = document.querySelector('.soundList, .profileTruncatedList, .searchResults');
        if (!list) return true;
      }

      return false;
    } catch (e) {
      debug("isLikelyTrackPage err", e && e.message);
      return false;
    }
  }

  function extractFirstScUrl(text) {
    if (!text || typeof text !== "string") return null;
    const m = text.match(SC_URL_RE);
    if (m && m[1]) {
      // remove trailing punctuation
      return m[1].replace(/[.,)\]]+$/, "");
    }
    return null;
  }

  function pickSoundCloudUrl(raw) {
    if (!raw) return null;
    if (typeof raw !== "string") {
      try { raw = String(raw); } catch { return null; }
    }
    raw = raw.trim();
    const direct = extractFirstScUrl(raw);
    if (direct) return direct;
    try {
      const dec = decodeURIComponent(raw);
      const fromDec = extractFirstScUrl(dec);
      if (fromDec) return fromDec;
    } catch {}
    return null;
  }

  function readUrlFromAttrs(el) {
    if (!el) return null;
    const attrNames = [
      "data-clipboard-text","data-clipboard","data-clipboard-target","data-link",
      "data-url","data-share","data-permalink","data-permalink-url","href","value","title","aria-label"
    ];
    for (const name of attrNames) {
      try {
        const val = el.getAttribute && el.getAttribute(name);
        const url = pickSoundCloudUrl(val);
        if (url) return url;
      } catch {}
    }
    if (el.dataset) {
      for (const key in el.dataset) {
        try {
          const url = pickSoundCloudUrl(el.dataset[key]);
          if (url) return url;
        } catch {}
      }
    }
    try {
      const url = pickSoundCloudUrl(el.innerText || el.textContent || "");
      if (url) return url;
    } catch {}
    return null;
  }

  function readUrlFromInputs(root) {
    if (!root) return null;
    const inputs = root.querySelectorAll('input[type="text"], input[type="url"], textarea, input');
    for (const input of inputs) {
      const val = input.value || (input.getAttribute && input.getAttribute("value")) || input.placeholder || input.getAttribute("aria-label");
      const url = pickSoundCloudUrl(val);
      if (url) return url;
    }
    return null;
  }

  async function tryClipboardRead(retries = 3, delayMs = 250) {
    if (!navigator.clipboard || typeof navigator.clipboard.readText !== "function") {
      debug("clipboard API not available");
      return null;
    }
    for (let i=0;i<retries;i++) {
      try {
        const txt = await navigator.clipboard.readText();
        const url = pickSoundCloudUrl(txt);
        debug("clipboard read attempt", i, "->", txt ? (txt.length > 120 ? txt.slice(0,120)+"..." : txt) : "<empty>", "extracted:", url);
        if (url) return url;
      } catch (err) {
        debug("clipboard.readText() failed on attempt", i, err && err.message);
      }
      await new Promise(r => setTimeout(r, delayMs));
    }
    return null;
  }

  function getSelectedText() {
    try {
      const sel = document.getSelection();
      if (sel && sel.toString()) return sel.toString();
      const active = document.activeElement;
      if (active && (active.tagName === "INPUT" || active.tagName === "TEXTAREA")) {
        return active.value || "";
      }
    } catch {}
    return "";
  }

  async function captureCopyLink(btn) {
    let captured = null;
    const cleanup = [];

    const onCopy = (e) => {
      try {
        const txt = e.clipboardData && e.clipboardData.getData && e.clipboardData.getData("text/plain");
        const url = pickSoundCloudUrl(txt);
        if (url) {
          captured = url;
          debug("copy event provided url:", url);
        }
      } catch (err) { debug("onCopy err", err && err.message); }
    };
    document.addEventListener("copy", onCopy, true);
    cleanup.push(() => document.removeEventListener("copy", onCopy, true));

    try {
      btn.click();
    } catch (err) { debug("btn.click() failed", err && err.message); }

    const fromClipboard = await tryClipboardRead(4, 200);
    if (fromClipboard) captured = fromClipboard;

    if (!captured) {
      const selText = getSelectedText();
      const url = pickSoundCloudUrl(selText);
      if (url) {
        captured = url;
        debug("captured from selection:", url);
      }
    }

    if (!captured) {
      const allText = document.body && document.body.innerText;
      const url = pickSoundCloudUrl(allText);
      if (url) {
        captured = url;
        debug("captured from document text:", url);
      }
    }

    for (const fn of cleanup) { try { fn(); } catch(e) {} }
    return captured;
  }

  function findKeyRecursively(obj, key) {
    if (!obj || typeof obj !== "object") return null;
    if (key in obj) return obj[key];
    for (const k of Object.keys(obj)) {
      try {
        const res = findKeyRecursively(obj[k], key);
        if (res !== null && res !== undefined) return res;
      } catch {}
    }
    return null;
  }

  function hydrationShareUrl() {
    try {
      const hyd = window.__sc_hydration;
      if (!Array.isArray(hyd)) return null;
      for (const item of hyd) {
        const data = item && item.data;
        if (!data || typeof data !== "object") continue;
        const permalink = findKeyRecursively(data, "permalink_url")
          || findKeyRecursively(data, "permalink")
          || findKeyRecursively(data, "url");
        const secret = findKeyRecursively(data, "secret_token") || findKeyRecursively(data, "secret");
        if (permalink && secret) {
          try {
            const base = String(permalink);
            const sep = base.includes("?") ? "&" : "?";
            const qForm = `${base}${sep}secret_token=${encodeURIComponent(secret)}`;
            debug("hydration -> built qForm", qForm);
            return qForm;
          } catch {}
        }
        const permalinkText = JSON.stringify(data);
        const maybe = extractFirstScUrl(permalinkText);
        if (maybe) return maybe;
      }
    } catch (err) { debug("hydrationShareUrl err", err && err.message); }
    return null;
  }

  async function tryOpenShareModalAndRead() {
    const shareBtn = document.querySelector('button[aria-label="Share"], button[title="Share"], button.sc-button-share, button[data-action="share"]');
    if (!shareBtn) return null;
    try { shareBtn.click(); } catch(e) { debug("shareBtn.click failed", e && e.message); }
    await new Promise(r => setTimeout(r, 350));
    const modalSelectors = ['.shareModal', '.sc-sharingModal', '.sharingModal', '.sc-shareModal', '[role="dialog"]'];
    for (const sel of modalSelectors) {
      const modal = document.querySelector(sel);
      if (!modal) continue;
      const url = readUrlFromInputs(modal) || readUrlFromAttrs(modal);
      if (url) {
        const closeBtn = modal.querySelector('button[aria-label="Close"], button[title="Close"], .close, .sc-button-close');
        try { if (closeBtn) closeBtn.click(); } catch(e) {}
        debug("modal -> url", url);
        return url;
      }
    }
    const fallback = readUrlFromInputs(document) || readUrlFromAttrs(document.body);
    if (fallback) {
      debug("modal fallback ->", fallback);
      return fallback;
    }
    return null;
  }

  async function resolveShareUrl() {
    debug("resolveShareUrl start", location.href);
    if (/[?&]secret_token=/.test(location.href) || /\/s-[A-Za-z0-9_-]+/.test(location.href)) {
      debug("location already contains secret token ->", location.href);
      return location.href;
    }

    const hyd = hydrationShareUrl();
    if (hyd) {
      debug("strategy=hydration url=", hyd);
      return hyd;
    }

    const btn =
      document.querySelector(COPYLINK_SELECTOR) ||
      document.querySelector('button[aria-label="Copy Link"]') ||
      document.querySelector('button[title="Copy Link"]');

    if (btn) {
      debug("found Copy Link button -> trying attrs/modal/clipboard");
      const attrUrl = readUrlFromAttrs(btn);
      if (attrUrl) {
        debug("strategy=attrs url=", attrUrl);
        return attrUrl;
      }

      const scope = btn.closest(".soundActions, .listenEngagement__actions, .sc-button-toolbar, .listenEngagement, .sc-button-group") || document;
      const inputUrl = readUrlFromInputs(scope);
      if (inputUrl) {
        debug("strategy=inputs-near-btn url=", inputUrl);
        return inputUrl;
      }

      try {
        const captured = await captureCopyLink(btn);
        if (captured) {
          debug("strategy=captureCopyLink url=", captured);
          return captured;
        }
      } catch (err) { debug("captureCopyLink err", err && err.message); }

      try {
        const cb = await tryClipboardRead(3, 200);
        if (cb) {
          debug("strategy=clipboard-read-after-btn url=", cb);
          return cb;
        }
      } catch (err) { debug("clipboard read err", err && err.message); }

      try {
        const modalUrl = await tryOpenShareModalAndRead();
        if (modalUrl) {
          debug("strategy=share-modal url=", modalUrl);
          return modalUrl;
        }
      } catch (err) { debug("share modal err", err && err.message); }
    } else {
      debug("Copy Link button NOT found; try opening share modal");
      const modalUrl = await tryOpenShareModalAndRead();
      if (modalUrl) {
        debug("strategy=share-modal-no-copybtn url=", modalUrl);
        return modalUrl;
      }
    }

    const allText = document.body && document.body.innerText;
    const global = pickSoundCloudUrl(allText);
    if (global) {
      debug("strategy=document-text url=", global);
      return global;
    }

    debug("strategy=fallback location href", location.href);
    return location.href;
  }

  /* Popup UI */
  function closePopup() {
    const el = document.getElementById(POPUP_ID);
    if (el) el.remove();
  }

  function showPopup(html) {
    closePopup();
    const wrap = document.createElement("div");
    wrap.id = POPUP_ID;
    wrap.innerHTML = html;
    document.body.appendChild(wrap);
  }

  async function runPopup() {
    showPopup(`
      <div class="vr-overlay">
        <div class="vr-box">
          <div class="vr-spinner"></div>
          <div class="vr-text">Building stream link…</div>
          <button class="vr-btn secondary cancel">Cancel</button>
        </div>
      </div>
    `);

    const cancelBtn = document.querySelector(".cancel");
    if (cancelBtn) cancelBtn.addEventListener("click", closePopup);

    try {
      const shareUrl = await resolveShareUrl();
      debug("resolved shareUrl ->", shareUrl);
      const res = await chrome.runtime.sendMessage({
        action: "waitAndBuild",
        endpoint: "/api/stream-sc?url=" + encodeURIComponent(shareUrl)
      });

      if (!res || !res.url) throw new Error();

      showPopup(`
        <div class="vr-overlay">
          <div class="vr-box">
            <input class="vr-input" readonly value="${res.url}">
            <div class="vr-row">
              <button class="vr-btn copy">Copy</button>
              <button class="vr-btn secondary close">Close</button>
            </div>
          </div>
        </div>
      `);

      const input = document.querySelector(".vr-input");
      const copyBtn = document.querySelector(".copy");
      const closeBtn = document.querySelector(".close");

      const copy = async () => {
        input.select();
        try {
          await navigator.clipboard.writeText(input.value);
        } catch {
          document.execCommand("copy");
        }
        copyBtn.textContent = "Copied ✓";
      };

      copy();
      copyBtn.addEventListener("click", copy);
      closeBtn.addEventListener("click", closePopup);

    } catch (err) {
      debug("runPopup failed:", err && err.message);
      showPopup(`
        <div class="vr-overlay">
          <div class="vr-box">
            <div class="vr-error">Failed to build stream link</div>
            <button class="vr-btn close">Close</button>
          </div>
        </div>
      `);
      const closeBtn = document.querySelector(".close");
      if (closeBtn) closeBtn.addEventListener("click", closePopup);
    }
  }

  /* Button creation & insertion */
  function createButton() {
    const btn = document.createElement("button");
    btn.className = "sc-button-queue addToNextUp sc-button-secondary sc-button sc-button-medium sc-button-responsive " + BTN_CLASS;
    btn.type = "button";
    btn.title = "Share to VRChat";
    btn.setAttribute("aria-label", "Share to VRChat");
    btn.style.display = "inline-flex";
    btn.style.alignItems = "center";
    btn.style.gap = "6px";
    btn.innerHTML = `
      <svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor" aria-hidden="true" style="display:block">
        <path d="M3.9 12a5 5 0 0 1 5-5h4v2h-4a3 3 0 1 0 0 6h4v2h-4a5 5 0 0 1-5-5Zm7-1h2v2h-2v-2Zm4-4h-4V5h4a5 5 0 1 1 0 10h-4v-2h4a3 3 0 1 0 0-6Z"/>
      </svg>
      <span class="vrchat-label">VRChat</span>
    `;
    btn.addEventListener("click", (e) => {
      e.preventDefault(); e.stopPropagation();
      runPopup();
    });
    return btn;
  }

  function insertOrUpdate() {
    // only show on pages that look like a single track
    if (!isLikelyTrackPage()) {
      // if button exists from previous navigation, remove it to avoid showing on profile/list pages
      const prev = document.querySelector("." + BTN_CLASS);
      if (prev) prev.remove();
      return;
    }

    const container = findContainer();
    if (!container) return;
    const group = container.querySelector(".sc-button-group") || container;
    let existing = document.querySelector("." + BTN_CLASS);
    if (existing) {
      if (existing.parentNode !== group) { existing.remove(); group.appendChild(existing); }
      return;
    }
    group.appendChild(createButton());
  }

  // initial run + observe SPA nav / DOM changes
  insertOrUpdate();
  new MutationObserver(insertOrUpdate).observe(document.body, { childList: true, subtree: true });

  // styles
  const style = document.createElement("style");
  style.textContent = `
    .vr-overlay { position: fixed; inset: 0; background: rgba(0,0,0,.55); backdrop-filter: blur(4px); z-index: 999999; display: flex; align-items: center; justify-content: center; }
    .vr-box { background: rgba(30,30,30,.95); border-radius: 14px; padding: 18px; min-width: 320px; color: #fff; box-shadow: 0 20px 60px rgba(0,0,0,.6); }
    .vr-spinner { width: 36px; height: 36px; border: 3px solid #444; border-top-color: #f50; border-radius: 50%; animation: spin 1s linear infinite; margin: 0 auto 14px; }
    @keyframes spin { to { transform: rotate(360deg); } }
    .vr-text { text-align: center; font-size: 14px; margin-bottom: 12px; }
    .vr-input { width: 100%; padding: 8px; border-radius: 8px; border: 1px solid #555; background: #000; color: #fff; }
    .vr-row { display: flex; gap: 8px; margin-top: 12px; }
    .vr-btn { flex: 1; padding: 8px; border-radius: 8px; border: none; cursor: pointer; background: #f50; color: #000; font-weight: 500; }
    .vr-btn.secondary { background: #555; color: #fff; }
    .vr-error { color: #ff6a6a; text-align: center; margin-bottom: 12px; }
  `;
  document.head.appendChild(style);
})();