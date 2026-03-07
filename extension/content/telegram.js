(() => {
  const MENU_CLASS = "MessageContextMenu_items";
  const ITEM_CLASS = "vrchat-tg-menu-item";
  const ITEM_WITH_TEXT_CLASS = "vrchat-tg-menu-item-with-text";
  const POPUP_ID = "vrchat-popup";
  const TELEGRAM_BUILD_POLL_MS = 2000;
  const TELEGRAM_BUILD_MAX_WAIT_MS = 15 * 60 * 1000;

  let lastContextMessage = null;
  const postInfoCache = new Map();

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

  async function fetchTelegramPostInfoForUrl(postUrl) {
    const normalized = String(postUrl || "").trim();
    if (!normalized) {
      return null;
    }
    if (postInfoCache.has(normalized)) {
      return postInfoCache.get(normalized);
    }
    const result = await sendMessage({
      action: "fetchTelegramPostInfo",
      url: normalized,
    });
    if (!result || result.error) {
      throw new Error(result?.error || "Failed to inspect Telegram post");
    }
    postInfoCache.set(normalized, result);
    return result;
  }

  function messageHasTextContent(message) {
    if (!message) return false;

    const selectors = [
      ".text-content",
      ".message-content",
      ".translatable-message",
      ".MessageText",
      ".message",
      ".text",
      ".message-text",
      ".quote-text"
    ];

    for (const selector of selectors) {
      for (const node of message.querySelectorAll(selector)) {
        const text = (node.innerText || node.textContent || "").trim();
        if (text) {
          return true;
        }
      }
    }

    return false;
  }

  function messageHasStreamableMedia(message) {
    if (!message) return false;

    const mediaSelectors = [
      ".media-inner",
      ".MessageMedia",
      ".album-item",
      ".Document",
      ".document-container",
      ".EmbeddedMessage",
      ".full-media",
      ".video-player",
      ".video-content",
      ".audio",
      ".AudioPlayer",
      "video",
      "audio"
    ];

    return mediaSelectors.some(selector => Boolean(message.querySelector(selector)));
  }

  async function resolveContextPostInfo() {
    const data = resolveMessageUrl();
    if (!data) {
      return null;
    }

    let { postUrl, channelId, messageId } = data;
    const internal = buildInternalFromPeer(channelId);
    const publicBase = await resolvePublicUsername(internal);
    if (publicBase) {
      postUrl = publicBase.replace(/\/$/, "") + "/" + messageId;
    }

    return await fetchTelegramPostInfoForUrl(postUrl);
  }

  async function waitForTelegramReady(postUrl, withText = false) {
    const params = new URLSearchParams({ url: postUrl });
    if (withText) {
      params.set("with_text", "1");
    }
    const endpoint = "/api/stream-tg-media?" + params.toString();
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

  async function handleMenuClick(withText = false) {
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

      const readyUrl = await waitForTelegramReady(postUrl, withText);
      popupResult(readyUrl);
    } catch (e) {
      popupError(e?.message || "Failed to resolve message");
    }
  }

  function createMenuItem(label, className, withText = false) {
    const item = document.createElement("div");
    item.className = "MenuItem compact " + className;
    item.setAttribute("role", "menuitem");
    item.tabIndex = 0;
    item.innerHTML = `
      <i class="icon icon-link" aria-hidden="true"></i>
      <span>${label}</span>
    `;
    item.addEventListener(
      "mousedown",
      e => {
        e.preventDefault();
        e.stopImmediatePropagation();
        handleMenuClick(withText);
      },
      true
    );
    return item;
  }

  function findCopyLinkAnchor(menu) {
    const items = [...menu.children];
    return (
      items.find(el => {
        const text = String(el.textContent || "").toLowerCase().trim();
        if (!text) {
          return false;
        }
        const looksLikeCopy = /copy|копіювати|копировать/.test(text);
        const looksLikeLink = /link|посилан|ссыл/.test(text);
        return looksLikeCopy && looksLikeLink;
      }) ||
      null
    );
  }

  function insertIfNeeded(menu) {
    if (menu.querySelector("." + ITEM_CLASS) || menu.querySelector("." + ITEM_WITH_TEXT_CLASS)) return;
    const hasMedia = messageHasStreamableMedia(lastContextMessage);
    const hasText = messageHasTextContent(lastContextMessage);
    if (!hasMedia && !hasText) return;

    const copy = [...menu.children].find(el =>
      (el.textContent || "").includes("Копіювати")
    );

    const items = [];
    if (hasMedia) {
      items.push(createMenuItem("VRChat", ITEM_CLASS, false));
    }
    if (hasText) {
      items.push(createMenuItem("VRChat with post text", ITEM_WITH_TEXT_CLASS, true));
    }

    if (!items.length) {
      return;
    }

    if (copy) {
      let anchor = copy;
      for (const item of items) {
        anchor.after(item);
        anchor = item;
      }
      return;
    }
    for (const item of items) {
      menu.appendChild(item);
    }
  }

  async function insertIfNeededResolved(menu) {
    if (
      menu.querySelector("." + ITEM_CLASS) ||
      menu.querySelector("." + ITEM_WITH_TEXT_CLASS) ||
      menu.dataset.vrchatInsertPending === "1"
    ) {
      return;
    }

    if (!resolveMessageUrl()) {
      return;
    }

    menu.dataset.vrchatInsertPending = "1";

    let hasMedia = messageHasStreamableMedia(lastContextMessage);
    let hasText = messageHasTextContent(lastContextMessage);

    try {
      const info = await resolveContextPostInfo();
      if (info) {
        hasMedia = Boolean(info.media_kind);
        hasText = Boolean(info.has_text || info.post_text);
      }
    } catch (e) {
      console.log("[VRChat TG] post-info inspect failed:", e && e.message);
    }

    if (!hasMedia && !hasText) {
      delete menu.dataset.vrchatInsertPending;
      return;
    }

    if (menu.querySelector("." + ITEM_CLASS) || menu.querySelector("." + ITEM_WITH_TEXT_CLASS)) {
      delete menu.dataset.vrchatInsertPending;
      return;
    }

    const items = [];
    if (hasMedia) {
      items.push(createMenuItem("VRChat", ITEM_CLASS, false));
    }
    if (hasText) {
      items.push(createMenuItem("VRChat with post text", ITEM_WITH_TEXT_CLASS, true));
    }

    if (!items.length) {
      delete menu.dataset.vrchatInsertPending;
      return;
    }

    const anchor = findCopyLinkAnchor(menu);
    if (anchor) {
      let currentAnchor = anchor;
      for (const item of items) {
        currentAnchor.after(item);
        currentAnchor = item;
      }
      delete menu.dataset.vrchatInsertPending;
      return;
    }

    for (const item of items) {
      menu.appendChild(item);
    }
    delete menu.dataset.vrchatInsertPending;
  }

  new MutationObserver(() => {
    document.querySelectorAll("." + MENU_CLASS).forEach(menu => {
      void insertIfNeededResolved(menu);
    });
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
