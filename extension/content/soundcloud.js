// content/soundcloud.js
(() => {
  const BTN_CLASS = "vrchat-sc-btn";
  const POPUP_ID = "vrchat-popup";

  /* ================= SVG ================= */

  const SVG = `
    <svg viewBox="0 0 24 24"
         width="16"
         height="16"
         fill="currentColor"
         aria-hidden="true"
         style="display:block">
      <path d="M3.9 12a5 5 0 0 1 5-5h4v2h-4a3 3 0 1 0 0 6h4v2h-4a5 5 0 0 1-5-5Zm7-1h2v2h-2v-2Zm4-4h-4V5h4a5 5 0 1 1 0 10h-4v-2h4a3 3 0 1 0 0-6Z"/>
    </svg>
  `;

  /* ================= CONTAINER ================= */

  function findContainer() {
    return document.querySelector(
      ".soundActions, .listenEngagement__actions, .sc-button-toolbar, .listenEngagement"
    );
  }

  /* ================= POPUP ================= */

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
      const res = await chrome.runtime.sendMessage({
        action: "waitAndBuild",
        endpoint: "/api/stream-sc?url=" + encodeURIComponent(location.href)
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

    } catch {
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

  /* ================= BUTTON  ================= */

  function createButton() {
    const btn = document.createElement("button");

    btn.className =
      "sc-button-queue addToNextUp sc-button-secondary sc-button sc-button-medium sc-button-responsive " +
      BTN_CLASS;

    btn.type = "button";
    btn.title = "Share to VRChat";
    btn.setAttribute("aria-label", "Share to VRChat");

    // ❗ ВАЖНО: flex + gap, ОДИН текст
    btn.style.display = "inline-flex";
    btn.style.alignItems = "center";
    btn.style.gap = "6px";

    btn.innerHTML = `
      ${SVG}
      <span class="vrchat-label">VRChat</span>

      <span class="sc-button-alt-labels sc-visuallyhidden">
        <span class="sc-button-label-default">VRChat</span>
        <span class="sc-button-label-hover">VRChat</span>
        <span class="sc-button-label-alt">VRChat</span>
      </span>
    `;

    btn.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      runPopup();
    });

    return btn;
  }

  /* ================= INSERT ================= */

  function insertOrUpdate() {
    const container = findContainer();
    if (!container) return;

    const group = container.querySelector(".sc-button-group") || container;

    let existing = document.querySelector("." + BTN_CLASS);
    if (existing) {
      if (existing.parentNode !== group) {
        existing.remove();
        group.appendChild(existing);
      }
      return;
    }

    group.appendChild(createButton());
  }

  insertOrUpdate();
  new MutationObserver(insertOrUpdate).observe(document.body, {
    childList: true,
    subtree: true
  });

  /* ================= POPUP STYLES ================= */

  const style = document.createElement("style");
  style.textContent = `
    .vr-overlay {
      position: fixed;
      inset: 0;
      background: rgba(0,0,0,.55);
      backdrop-filter: blur(4px);
      z-index: 999999;
      display: flex;
      align-items: center;
      justify-content: center;
    }
    .vr-box {
      background: rgba(30,30,30,.95);
      border-radius: 14px;
      padding: 18px;
      min-width: 320px;
      color: #fff;
      box-shadow: 0 20px 60px rgba(0,0,0,.6);
    }
    .vr-spinner {
      width: 36px;
      height: 36px;
      border: 3px solid #444;
      border-top-color: #f50;
      border-radius: 50%;
      animation: spin 1s linear infinite;
      margin: 0 auto 14px;
    }
    @keyframes spin { to { transform: rotate(360deg); } }
    .vr-text { text-align: center; font-size: 14px; margin-bottom: 12px; }
    .vr-input {
      width: 100%;
      padding: 8px;
      border-radius: 8px;
      border: 1px solid #555;
      background: #000;
      color: #fff;
    }
    .vr-row { display: flex; gap: 8px; margin-top: 12px; }
    .vr-btn {
      flex: 1;
      padding: 8px;
      border-radius: 8px;
      border: none;
      cursor: pointer;
      background: #f50;
      color: #000;
      font-weight: 500;
    }
    .vr-btn.secondary { background: #555; color: #fff; }
    .vr-error { color: #ff6a6a; text-align: center; margin-bottom: 12px; }
  `;
  document.head.appendChild(style);
})();
