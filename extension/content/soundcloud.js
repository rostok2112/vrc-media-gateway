// content/soundcloud.js
function insertSCButton() {
    // locate play controls or header
    const header = document.querySelector(".soundTitle__secondary") || document.querySelector(".listenEngagement");
    if (!header || document.getElementById("vr-sc-btn")) return;
  
    const btn = document.createElement("button");
    btn.id = "vr-sc-btn";
    btn.innerText = "Share to VRChat";
    btn.style.marginLeft = "8px";
    btn.style.padding = "6px 10px";
    btn.style.borderRadius = "6px";
    btn.style.cursor = "pointer";
  
    btn.onclick = async () => {
      const url = location.href;
      const endpoint = "/api/stream-sc?url=" + encodeURIComponent(url);
      const resp = await chrome.runtime.sendMessage({ action: "callStream", endpoint, copyToClipboard: true });
      if (resp && resp.url) {
        showToast("VRChat link copied!");
      } else {
        showToast("Ошибка получения ссылки");
      }
    };
  
    header.appendChild(btn);
  }
  
  function showToast(msg) {
    let t = document.getElementById("vr-toast");
    if (!t) {
      t = document.createElement("div");
      t.id = "vr-toast";
      t.style.position = "fixed";
      t.style.right = "20px";
      t.style.bottom = "20px";
      t.style.zIndex = 999999;
      t.style.background = "rgba(0,0,0,0.8)";
      t.style.color = "white";
      t.style.padding = "8px 12px";
      t.style.borderRadius = "6px";
      document.body.appendChild(t);
    }
    t.innerText = msg;
    setTimeout(() => t.remove(), 2500);
  }
  
  insertSCButton();
  new MutationObserver(() => insertSCButton()).observe(document.body, { childList: true, subtree: true });
  