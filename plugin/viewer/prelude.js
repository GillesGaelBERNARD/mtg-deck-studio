(function () {
  if (window.crypto && typeof window.crypto.randomUUID !== "function" && typeof window.crypto.getRandomValues === "function") {
    window.crypto.randomUUID = function () {
      var b = window.crypto.getRandomValues(new Uint8Array(16));
      b[6] = (b[6] & 15) | 64;
      b[8] = (b[8] & 63) | 128;
      var h = Array.prototype.map.call(b, function (x) { return (x + 256).toString(16).slice(1); }).join("");
      return h.slice(0, 8) + "-" + h.slice(8, 12) + "-" + h.slice(12, 16) + "-" + h.slice(16, 20) + "-" + h.slice(20);
    };
  }
})();

(function () {
  var STYLE = ".wb-dialog-backdrop{position:fixed;inset:0;z-index:100000;display:grid;place-items:center;background:rgba(4,8,14,.55)}" +
    ".wb-dialog{min-width:320px;max-width:min(560px,90vw);max-height:80vh;overflow:auto;padding:18px;border-radius:10px;border:1px solid rgba(143,182,255,.35);background:#111a27;color:#eef6ff;box-shadow:0 18px 50px rgba(0,0,0,.5);font:14px/1.45 system-ui,sans-serif}" +
    ".wb-dialog p{margin:0 0 12px;white-space:pre-wrap}.wb-dialog input{width:100%;box-sizing:border-box;margin:0 0 12px;padding:8px;border-radius:6px;border:1px solid rgba(143,182,255,.4);background:#0b1220;color:inherit}" +
    ".wb-dialog{position:relative}.wb-dialog-actions{display:flex;justify-content:flex-end;gap:8px}.wb-dialog button{padding:6px 14px}" +
    ".wb-dialog-close{position:absolute;top:6px;right:6px;width:24px;height:24px;padding:0;line-height:1;border:0;border-radius:6px;background:transparent;color:#9fb6d6;font-size:16px;cursor:pointer}" +
    ".wb-dialog-close:hover{background:rgba(143,182,255,.16);color:#eef6ff}" +
    ".wb-notice{position:fixed;right:16px;bottom:16px;z-index:100000;max-width:420px;padding:12px 14px;border-radius:8px;background:#111a27;color:#eef6ff;border:1px solid rgba(102,217,194,.4);white-space:pre-wrap;font:13px/1.4 system-ui,sans-serif;box-shadow:0 10px 30px rgba(0,0,0,.45)}";

  function ensureStyle() {
    if (document.getElementById("wb-dialog-style")) return;
    var style = document.createElement("style");
    style.id = "wb-dialog-style";
    style.textContent = STYLE;
    document.head.appendChild(style);
  }

  function open(message, options) {
    ensureStyle();
    return new Promise(function (resolve) {
      var backdrop = document.createElement("div");
      backdrop.className = "wb-dialog-backdrop";
      var box = document.createElement("div");
      box.className = "wb-dialog";
      box.setAttribute("role", "dialog");
      box.setAttribute("aria-modal", "true");
      var text = document.createElement("p");
      text.textContent = message;
      box.appendChild(text);
      var input = null;
      if (options.input) {
        input = document.createElement("input");
        input.value = options.value || "";
        box.appendChild(input);
      }
      var actions = document.createElement("div");
      actions.className = "wb-dialog-actions";
      var done = function (value) { backdrop.remove(); resolve(value); };
      var close = document.createElement("button");
      close.type = "button";
      close.className = "wb-dialog-close";
      close.textContent = "×";
      close.setAttribute("aria-label", "Cancel");
      close.onclick = function () { done(options.input ? null : false); };
      box.appendChild(close);
      if (options.cancel) {
        var cancel = document.createElement("button");
        cancel.type = "button";
        cancel.textContent = options.cancel;
        cancel.onclick = function () { done(options.input ? null : false); };
        actions.appendChild(cancel);
      }
      var ok = document.createElement("button");
      ok.type = "button";
      ok.textContent = options.ok || "OK";
      ok.onclick = function () { done(options.input ? input.value : true); };
      actions.appendChild(ok);
      box.appendChild(actions);
      box.addEventListener("keydown", function (event) {
        if (event.key === "Escape" && options.cancel) done(options.input ? null : false);
        if (event.key === "Enter" && (options.input || event.target === ok)) { event.preventDefault(); ok.onclick(); }
      });
      backdrop.appendChild(box);
      document.body.appendChild(backdrop);
      (input || ok).focus();
      if (input) input.select();
    });
  }

  // A dialog with an arbitrary set of buttons; resolves with the chosen value.
  function choose(message, options) {
    ensureStyle();
    return new Promise(function (resolve) {
      var backdrop = document.createElement("div");
      backdrop.className = "wb-dialog-backdrop";
      var box = document.createElement("div");
      box.className = "wb-dialog";
      box.setAttribute("role", "dialog");
      box.setAttribute("aria-modal", "true");
      var text = document.createElement("p");
      text.textContent = message;
      box.appendChild(text);
      var close = document.createElement("button");
      close.type = "button";
      close.className = "wb-dialog-close";
      close.textContent = "×";
      close.setAttribute("aria-label", "Cancel");
      close.onclick = function () { backdrop.remove(); resolve(""); };
      box.appendChild(close);
      var actions = document.createElement("div");
      actions.className = "wb-dialog-actions";
      (options || []).forEach(function (option) {
        var button = document.createElement("button");
        button.type = "button";
        button.textContent = option.label;
        button.onclick = function () { backdrop.remove(); resolve(option.value); };
        actions.appendChild(button);
      });
      box.appendChild(actions);
      box.addEventListener("keydown", function (event) {
        if (event.key === "Escape") { backdrop.remove(); resolve(""); }
      });
      backdrop.appendChild(box);
      document.body.appendChild(backdrop);
      actions.lastChild.focus();
    });
  }

  var noticeTimer = null;
  window.wbDialog = {
    choice: choose,
    prompt: function (message, value) { return open(message, { input: true, value: value, ok: "OK", cancel: "Cancel" }); },
    confirm: function (message, labels) { labels = labels || {}; return open(message, { ok: labels.ok || "OK", cancel: labels.cancel || "Cancel" }); },
    alert: function (message) { return open(message, { ok: "Close" }); },
    notice: function (message) {
      ensureStyle();
      var old = document.querySelector(".wb-notice");
      if (old) old.remove();
      var el = document.createElement("div");
      el.className = "wb-notice";
      el.textContent = message;
      el.onclick = function () { el.remove(); };
      document.body.appendChild(el);
      clearTimeout(noticeTimer);
      noticeTimer = setTimeout(function () { el.remove(); }, 9000);
      return true;
    }
  };

  var pendingSaves = {};
  window.addEventListener("message", function (event) {
    var m = event.data || {};
    if (event.source !== parent || m.type !== "wb.file.saved" || !pendingSaves[m.request_id]) return;
    pendingSaves[m.request_id](m);
    delete pendingSaves[m.request_id];
  });
  window.wbHost = {
    saveFile: function (name, text, target) {
      var id = "save-" + Date.now() + "-" + Math.random().toString(36).slice(2);
      return new Promise(function (resolve, reject) {
        pendingSaves[id] = function (m) { if (m.ok) resolve(m); else reject(new Error(m.error || "The file was not saved.")); };
        parent.postMessage({ type: "wb.file.save", request_id: id, name: name, text: text, target: target || "exports" }, "*");
      });
    }
  };
})();
