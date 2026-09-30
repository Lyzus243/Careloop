/*
 * Careloop dialogs: branded replacements for the browser's alert() and confirm().
 * Loaded by every page. Follows each page's theme (dashboard CSS variables,
 * html.dark / html.eyecare on the auth pages).
 *
 *   await clAlert("Saved!", "success")                        -> resolves when dismissed
 *   if (await clConfirm("Delete this?", "Delete customer")) … -> resolves true / false
 *   clConfirm(msg, title, { confirmText, cancelText, type })
 *
 * type: "info" | "success" | "warning" | "error"
 */
(function () {
  if (window.__clDialog) return;
  window.__clDialog = true;

  var CSS = [
    ".cl-dlg-backdrop{--dlg-bg:var(--bg,#fff);--dlg-text:var(--text,#0a0a0a);--dlg-text2:var(--text2,#6b7280);--dlg-border:var(--border,#e5e7eb);--dlg-blue:var(--blue,#3333CC);",
    "position:fixed;inset:0;z-index:10000;display:flex;align-items:center;justify-content:center;padding:16px;box-sizing:border-box;",
    "background:rgba(0,0,0,.5);opacity:0;visibility:hidden;transition:opacity .2s,visibility .2s;}",
    "body.dark-mode .cl-dlg-backdrop{--dlg-border:#2a2a2a;}",
    "html.dark .cl-dlg-backdrop{--dlg-bg:#1a1f2e;--dlg-text:#f3f4f6;--dlg-text2:#9ca3af;--dlg-border:#2d3446;}",
    "html.eyecare .cl-dlg-backdrop{--dlg-bg:#fef9e6;--dlg-text:#2d2a24;--dlg-text2:#6b5d45;--dlg-border:#e6d5b8;}",
    "html.dark.eyecare .cl-dlg-backdrop{--dlg-bg:#221e0e;--dlg-text:#f5ecd7;--dlg-text2:#b8a888;--dlg-border:#3b3122;}",
    ".cl-dlg-backdrop.open{opacity:1;visibility:visible;}",
    ".cl-dlg{box-sizing:border-box;width:100%;max-width:400px;background:var(--dlg-bg);color:var(--dlg-text);border:1px solid var(--dlg-border);border-radius:24px;padding:28px 24px 20px;",
    "box-shadow:0 12px 40px rgba(0,0,0,.18);text-align:center;font-family:inherit;transform:translateY(8px) scale(.98);transition:transform .2s;}",
    ".cl-dlg-backdrop.open .cl-dlg{transform:none;}",
    ".cl-dlg-icon{width:48px;height:48px;border-radius:50%;display:flex;align-items:center;justify-content:center;margin:0 auto 14px;}",
    ".cl-dlg-icon.info{background:rgba(51,51,204,.12);color:var(--dlg-blue);}",
    ".cl-dlg-icon.success{background:rgba(34,197,94,.15);color:#16a34a;}",
    ".cl-dlg-icon.warning{background:rgba(245,158,11,.16);color:#d97706;}",
    ".cl-dlg-icon.error{background:rgba(239,68,68,.14);color:#dc2626;}",
    ".cl-dlg-title{font-size:1.1rem;font-weight:700;margin:0 0 8px;color:var(--dlg-text);line-height:1.3;}",
    ".cl-dlg-msg{font-size:.9rem;color:var(--dlg-text2);line-height:1.6;margin:0 0 22px;white-space:pre-line;overflow-wrap:anywhere;}",
    ".cl-dlg-btns{display:flex;gap:10px;justify-content:center;}",
    ".cl-dlg-btn{flex:1;max-width:170px;padding:12px 20px;border-radius:50px;font-size:.9rem;font-weight:600;font-family:inherit;cursor:pointer;border:1px solid transparent;transition:filter .15s,background .15s;}",
    ".cl-dlg-btn:focus-visible{outline:2px solid var(--dlg-blue);outline-offset:2px;}",
    ".cl-dlg-btn.primary{background:var(--dlg-blue);color:#fff;}",
    ".cl-dlg-btn.danger{background:#dc2626;color:#fff;}",
    ".cl-dlg-btn.primary:hover,.cl-dlg-btn.danger:hover{filter:brightness(1.08);}",
    ".cl-dlg-btn.secondary{background:transparent;color:var(--dlg-text);border-color:var(--dlg-border);}",
    ".cl-dlg-btn.secondary:hover{background:rgba(127,127,127,.08);}",
    "@media (prefers-reduced-motion:reduce){.cl-dlg-backdrop,.cl-dlg{transition:none;}}"
  ].join("");

  var ICONS = {
    success: '<polyline points="20 6 9 17 4 12"/>',
    error: '<circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/>',
    warning: '<path d="M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/>',
    info: '<circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/>'
  };
  var TITLES = { success: "Success", error: "Something went wrong", warning: "Please check", info: "Notice" };

  var root, box, icon, titleEl, msgEl, btns, current = null, lastFocus = null;

  function build() {
    if (root) return;
    var style = document.createElement("style");
    style.textContent = CSS;
    document.head.appendChild(style);

    root = document.createElement("div");
    root.className = "cl-dlg-backdrop";
    root.innerHTML =
      '<div class="cl-dlg" role="alertdialog" aria-modal="true" aria-labelledby="cl-dlg-title" aria-describedby="cl-dlg-msg">' +
      '<div class="cl-dlg-icon" aria-hidden="true"></div>' +
      '<h2 class="cl-dlg-title" id="cl-dlg-title"></h2>' +
      '<p class="cl-dlg-msg" id="cl-dlg-msg"></p>' +
      '<div class="cl-dlg-btns"></div></div>';
    document.body.appendChild(root);
    box = root.firstChild;
    icon = box.querySelector(".cl-dlg-icon");
    titleEl = box.querySelector(".cl-dlg-title");
    msgEl = box.querySelector(".cl-dlg-msg");
    btns = box.querySelector(".cl-dlg-btns");

    // Clicking outside the box dismisses it (as "No" for a confirm).
    root.addEventListener("mousedown", function (e) { if (e.target === root) close(current && current.cancelValue); });
    document.addEventListener("keydown", function (e) {
      if (!current) return;
      if (e.key === "Escape") { e.preventDefault(); close(current.cancelValue); }
      else if (e.key === "Tab") {
        // keep keyboard focus inside the dialog
        var b = btns.querySelectorAll("button"), first = b[0], last = b[b.length - 1];
        if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
        else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
        else if (!box.contains(document.activeElement)) { e.preventDefault(); first.focus(); }
      }
    }, true);
  }

  function close(value) {
    var c = current;
    if (!c) return;
    current = null;
    root.classList.remove("open");
    if (lastFocus && typeof lastFocus.focus === "function" && document.contains(lastFocus)) {
      try { lastFocus.focus({ preventScroll: true }); } catch (e) { /* ignore */ }
    }
    c.resolve(value);
  }

  function button(text, cls, value) {
    var b = document.createElement("button");
    b.type = "button";
    b.className = "cl-dlg-btn " + cls;
    b.textContent = text;
    b.addEventListener("click", function () { close(value); });
    btns.appendChild(b);
    return b;
  }

  function open(opts) {
    if (!document.body) {
      return new Promise(function (r) { document.addEventListener("DOMContentLoaded", function () { open(opts).then(r); }); });
    }
    build();
    // A new dialog replaces one that is already showing; the old one counts as dismissed.
    if (current) close(current.cancelValue);
    var type = ICONS[opts.type] ? opts.type : "info";
    return new Promise(function (resolve) {
      var isConfirm = !!opts.cancelText;
      current = { resolve: resolve, cancelValue: isConfirm ? false : undefined };
      if (!box.contains(document.activeElement)) lastFocus = document.activeElement;
      icon.className = "cl-dlg-icon " + type;
      icon.innerHTML = '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">' + ICONS[type] + "</svg>";
      titleEl.textContent = opts.title || TITLES[type];
      msgEl.textContent = opts.msg == null ? "" : String(opts.msg);
      btns.innerHTML = "";
      if (isConfirm) button(opts.cancelText, "secondary", false);
      var ok = button(opts.confirmText || "OK", opts.danger || type === "error" ? "danger" : "primary", isConfirm ? true : undefined);
      root.classList.add("open");
      setTimeout(function () { if (current) ok.focus({ preventScroll: true }); }, 30);
    });
  }

  window.clAlert = function (msg, type, title) {
    return open({ msg: msg, type: type || "info", title: title || "" });
  };

  window.clConfirm = function (msg, title, options) {
    var o = options || {};
    return open({
      msg: msg, title: title || "", type: o.type || "warning", danger: !!o.danger,
      confirmText: o.confirmText || "Yes", cancelText: o.cancelText || "No"
    }).then(function (v) { return v === true; });
  };

  // Older signature used by the dashboard: clDialog(msg, type, title, onConfirm, confirmText, cancelText)
  window.clDialog = function (msg, type, title, onConfirm, confirmText, cancelText) {
    return open({ msg: msg, type: type, title: title, confirmText: confirmText || "OK", cancelText: cancelText || null })
      .then(function (v) { if (v !== false && onConfirm) onConfirm(); });
  };
})();
