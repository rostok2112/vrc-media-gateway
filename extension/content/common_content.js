(() => {
  console.log("[VRChat COMMON] loaded (YT popup style)");

  const POPUP_ID = "vrchat-popup";
  let abortController = null;

  /* ---------- popup helpers (1:1 youtube style) ---------- */

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

  function popupLoading() {
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
  }

  function popupResult(url) {
    showPopup(`
      <div class="vr-overlay">
        <div class="vr-box">
          <input class="vr-input" readonly value="${url}">
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
  }

  function popupError(text) {
    showPopup(`
      <div class="vr-overlay">
        <div class="vr-box">
          <div class="vr-error">${text || "Failed to build stream link"}</div>
          <button class="vr-btn close">Close</button>
        </div>
      </div>
    `);

    document.querySelector(".close")?.addEventListener("click", closePopup);
  }

  function resolveEndpoint(endpoint) {
    popupLoading();

    chrome.runtime.sendMessage(
      { action: "waitAndBuild", endpoint },
      (resp) => {
        if (!resp || !resp.url) {
          popupError(resp?.error);
          return;
        }
        popupResult(resp.url);
      }
    );
  }

  /* ---------- styles (copied from youtube.js) ---------- */

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
      font-family: system-ui, -apple-system, Segoe UI, Roboto, Arial;
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
  function injectStyle() {
    const target = document.head || document.documentElement;
    if (target) {
      target.appendChild(style);
    } else {
      setTimeout(injectStyle, 50);
    }
  }
  
  injectStyle();

  /* ---------- messages from background ---------- */

  chrome.runtime.onMessage.addListener((msg) => {
    if (msg.action === "vr_resolve_image") {
      const imageUrl = msg.url;
      const endpoint = "/api/stream-image?url=" + encodeURIComponent(imageUrl);
      resolveEndpoint(endpoint);
    }

    if (msg.action === "vr_resolve_video") {
      const videoUrl = msg.url;
      let endpoint = "/api/stream-video?url=" + encodeURIComponent(videoUrl);
      if (msg.referer) {
        endpoint += "&referer=" + encodeURIComponent(msg.referer);
      }
      resolveEndpoint(endpoint);
    }

    if (msg.action === "vr_image_error") {
      popupError(msg.error);
    }

    if (msg.action === "vr_video_error") {
      popupError(msg.error);
    }
  });
})();
