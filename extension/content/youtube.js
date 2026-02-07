(() => {
  const BTN_ATTR = "data-vrchat-btn";
  const POPUP_ID = "vrchat-popup";
  let abortController = null;

  /* ---------- helpers ---------- */

  const svgLink = `
    <svg viewBox="0 0 24 24" width="20" height="20">
      <path d="M3.9 12a5 5 0 0 1 5-5h4v2h-4a3 3 0 1 0 0 6h4v2h-4a5 5 0 0 1-5-5Zm7-1h2v2h-2v-2Zm4-4h-4V5h4a5 5 0 1 1 0 10h-4v-2h4a3 3 0 1 0 0-6Z"/>
    </svg>
  `;

  function qsMenu() {
    return document.querySelector("ytd-menu-renderer #top-level-buttons-computed");
  }

  function closePopup() {
    document.getElementById(POPUP_ID)?.remove();
    abortController?.abort();
    abortController = null;
  }

  function showPopup(inner) {
    closePopup();
    const wrap = document.createElement("div");
    wrap.id = POPUP_ID;
    wrap.innerHTML = inner;
    document.body.appendChild(wrap);
  }

  /* ---------- main ---------- */

  async function run() {
    abortController = new AbortController();

    showPopup(`
      <div class="vr-overlay">
        <div class="vr-box">
          <div class="vr-spinner"></div>
          <div class="vr-text">Building stream link…</div>
          <button class="vr-btn secondary cancel">Cancel</button>
        </div>
      </div>
    `);

    document.querySelector(".cancel")?.addEventListener("click", closePopup);

    try {
      const res = await chrome.runtime.sendMessage({
        action: "waitAndBuild",
        endpoint: "/api/stream-yt?url=" + encodeURIComponent(location.href)
      });

      if (!res?.url) throw new Error();

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

      async function copy() {
        input.select();
        try {
          await navigator.clipboard.writeText(input.value);
        } catch {
          document.execCommand("copy");
        }
        copyBtn.textContent = "Copied ✓";
      }

      copy(); // auto copy
      copyBtn.onclick = copy;
      document.querySelector(".close").onclick = closePopup;

    } catch {
      showPopup(`
        <div class="vr-overlay">
          <div class="vr-box">
            <div class="vr-error">Failed to build stream link</div>
            <button class="vr-btn close">Close</button>
          </div>
        </div>
      `);
      document.querySelector(".close")?.addEventListener("click", closePopup);
    }
  }

  /* ---------- inject ---------- */

  function injectOnce() {
    const menu = qsMenu();
    if (!menu || menu.hasAttribute(BTN_ATTR)) return;
    menu.setAttribute(BTN_ATTR, "1");

    const btn = document.createElement("button");
    btn.className =
      "yt-spec-button-shape-next yt-spec-button-shape-next--tonal yt-spec-button-shape-next--mono yt-spec-button-shape-next--size-m yt-spec-button-shape-next--icon-leading";
    btn.style.marginLeft = "8px";
    btn.innerHTML = `
      <span class="yt-spec-button-shape-next__icon">${svgLink}</span>
      <span class="yt-spec-button-shape-next__button-text-content">VRChat</span>
    `;
    btn.onclick = run;

    menu.insertAdjacentElement("beforeend", btn);
  }

  /* ---------- styles ---------- */

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
      border-top-color: #3ea6ff;
      border-radius: 50%;
      animation: spin 1s linear infinite;
      margin: 0 auto 14px;
    }
    @keyframes spin { to { transform: rotate(360deg); } }
    .vr-text {
      text-align: center;
      font-size: 14px;
      margin-bottom: 12px;
      opacity: .9;
    }
    .vr-input {
      width: 100%;
      padding: 8px;
      border-radius: 8px;
      border: 1px solid #555;
      background: #000;
      color: #fff;
    }
    .vr-row {
      display: flex;
      gap: 8px;
      margin-top: 12px;
    }
    .vr-btn {
      flex: 1;
      padding: 8px;
      border-radius: 8px;
      border: none;
      cursor: pointer;
      background: #3ea6ff;
      color: #000;
      font-weight: 500;
    }
    .vr-btn.secondary {
      background: #555;
      color: #fff;
    }
    .vr-error {
      color: #ff6a6a;
      text-align: center;
      margin-bottom: 12px;
    }
  `;
  document.head.appendChild(style);

  /* ---------- observer ---------- */

  const obs = new MutationObserver(injectOnce);
  obs.observe(document, { childList: true, subtree: true });
  injectOnce();
})();
