(() => {
  const MENU_CLASS = "MessageContextMenu_items";
  const ITEM_CLASS = "vrchat-tg-menu-item";
  const POPUP_ID = "vrchat-popup";
  const TELEGRAM_BUILD_POLL_MS = 2000;
  const TELEGRAM_BUILD_MAX_WAIT_MS = 15 * 60 * 1000;

  let lastContextMessage = null;

  document.addEventListener(
    "contextmenu",
    e => {
      const msg = e.target.closest(".Message");
      if (msg && msg.dataset?.messageId) {
        lastContextMessage = msg;
        console.log("[VRChat TG] context message captured:", {
          id: msg.id,
          messageId: msg.dataset.messageId,
        });
      }
    },
    true
  );

  function closePopup() {
    document.getElementById(POPUP_ID)?.remove();
  }

  function showPopup(html) {
    closePopup();
    const el = document.createElement("div");
    el.id = POPUP_ID;
    el.innerHTML = html;
    document.body.appendChild(el);
  }

  function popupLoading() {
    showPopup(`
      <div class="vr-overlay">
        <div class="vr-box">
          <div class="vr-spinner"></div>
          <div class="vr-text">Preparing Telegram stream. Large videos can take a few minutes.</div>
          <button class="vr-btn secondary close">Cancel</button>
        </div>
      </div>
    `);
    document.querySelector(".close")?.addEventListener("click", closePopup);
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
      copyBtn.textContent = "Copied";
    }

    copy();
    copyBtn.addEventListener("click", copy);
    document.querySelector(".close")?.addEventListener("click", closePopup);
  }

  function popupError(message = "Failed to resolve message") {
    showPopup(`
      <div class="vr-overlay">
        <div class="vr-box">
          <div class="vr-error">${message}</div>
          <button class="vr-btn close">Close</button>
        </div>
      </div>
    `);
    document.querySelector(".close")?.addEventListener("click", closePopup);
  }

  function resolveMessageUrl() {
    if (!lastContextMessage) return null;

    const messageId =
      lastContextMessage.dataset.messageId ||
      lastContextMessage.id?.replace("message-", "");

    if (!messageId) return null;

    const match = location.hash.match(/-100(\d+)/);
    if (!match) return null;

    const channelId = match[1];

    return {
      messageId,
      channelId,
      postUrl: `https://t.me/c/${channelId}/${messageId}`,
    };
  }

  function buildInternalFromPeer(peer) {
    if (!peer) return null;
    return peer.startsWith("-100") ? peer : "-100" + peer;
  }

  function resolvePublicUsername(internal) {
    return new Promise(resolve => {
      if (!internal) return resolve(null);
      chrome.runtime.sendMessage(
        { action: "resolveTgPublicLink", internal },
        res => resolve(res?.url || null)
      );
    });
  }

  function sleep(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
  }

  function sendMessage(message) {
    return new Promise(resolve => {
      chrome.runtime.sendMessage(message, result => resolve(result || null));
    });
  }

  async function waitForTelegramReady(postUrl) {
    const endpoint = "/api/stream-tg-media?url=" + encodeURIComponent(postUrl);
    const start = await sendMessage({
      action: "startTelegramBuild",
      endpoint,
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

      const status = await sendMessage({
        action: "pollTelegramBuild",
        jobId: start.jobId,
      });

      if (!status || status.error) {
        throw new Error(status?.error || "Telegram build failed");
      }

      if (status.url) {
        return status.url;
      }
    }

    throw new Error("Timed out waiting for Telegram stream to be ready");
  }

  async function handleMenuClick() {
    popupLoading();

    try {
      const data = resolveMessageUrl();
      if (!data) throw 1;

      let { postUrl, channelId, messageId } = data;

      const internal = buildInternalFromPeer(channelId);
      const publicBase = await resolvePublicUsername(internal);

      if (publicBase) {
        postUrl = publicBase.replace(/\/$/, "") + "/" + messageId;
      }

      const readyUrl = await waitForTelegramReady(postUrl);
      popupResult(readyUrl);
    } catch (e) {
      popupError(e?.message || "Failed to resolve message");
    }
  }

  function createMenuItem() {
    const item = document.createElement("div");
    item.className = "MenuItem compact " + ITEM_CLASS;
    item.setAttribute("role", "menuitem");
    item.tabIndex = 0;
    item.innerHTML = `
      <i class="icon icon-link" aria-hidden="true"></i>
      <span>VRChat</span>
    `;
    item.addEventListener(
      "mousedown",
      e => {
        e.preventDefault();
        e.stopImmediatePropagation();
        handleMenuClick();
      },
      true
    );
    return item;
  }

  function insertIfNeeded(menu) {
    if (menu.querySelector("." + ITEM_CLASS)) return;
    const copy = [...menu.children].find(el =>
      (el.textContent || "").includes("Копіювати")
    );
    const btn = createMenuItem();
    copy ? copy.after(btn) : menu.appendChild(btn);
  }

  new MutationObserver(() => {
    document.querySelectorAll("." + MENU_CLASS).forEach(insertIfNeeded);
  }).observe(document.body, { childList: true, subtree: true });

  const style = document.createElement("style");
  style.textContent = `
  .vr-overlay{position:fixed;inset:0;background:rgba(0,0,0,.55);backdrop-filter:blur(4px);z-index:999999;display:flex;align-items:center;justify-content:center}
  .vr-box{background:#1e1e1e;border-radius:14px;padding:18px;min-width:320px;color:#fff}
  .vr-spinner{width:36px;height:36px;border:3px solid #444;border-top-color:#3ea6ff;border-radius:50%;animation:spin 1s linear infinite;margin:0 auto 12px}
  @keyframes spin{to{transform:rotate(360deg)}}
  .vr-text{text-align:center;margin-bottom:10px}
  .vr-input{width:100%;padding:8px;border-radius:8px;background:#000;color:#fff;border:1px solid #555}
  .vr-row{display:flex;gap:8px;margin-top:10px}
  .vr-btn{flex:1;padding:8px;border-radius:8px;border:none;cursor:pointer;background:#3ea6ff;color:#000}
  .vr-btn.secondary{background:#555;color:#fff}
  .vr-error{color:#ff6a6a;text-align:center;margin-bottom:10px}
  `;
  document.head.appendChild(style);
})();
