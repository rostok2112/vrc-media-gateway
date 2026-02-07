// content/inject_copy.js
// This file can stay empty or contain helper functions accessible via executeScript
// but keep a function in global scope so background can call it.
window._vrchat_copy_helper = async function(text){
    try {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        await navigator.clipboard.writeText(text);
        return true;
      }
    } catch (e) {}
    // fallback
    const ta = document.createElement("textarea");
    ta.value = text;
    document.body.appendChild(ta);
    ta.select();
    document.execCommand("copy");
    ta.remove();
    return true;
  };
  