/* ============================================================
   TC Platform — lane navback
   1. Top-bar Back button: step back through this tab's in-app history, or
      go UP to the parent page the server put in its href.
   2. Installed app (standalone PWA, mostly iPhones): a file link must not
      replace the app with a bare file that has no way back. Fetch the file and
      hand it to the share sheet (Save to Files / open in Excel / Quick Look),
      the app stays underneath. A normal browser tab is left exactly as today.
   Small and defensive on purpose: every storage/API call can fail.
   ============================================================ */
(function () {
  "use strict";

  var KEY = "tc_navstack", MAX = 30;
  var t = function (k, f) { var v = window.tcT ? window.tcT(k) : k; return v === k ? f : v; };

  /* Same-origin path + query of a URL, or null for anything off-site. */
  function localPath(u) {
    try {
      var x = new URL(u, location.href);
      return x.origin === location.origin ? x.pathname + x.search : null;
    } catch (e) { return null; }
  }
  function load() {
    try { var s = JSON.parse(sessionStorage.getItem(KEY) || "[]"); return Array.isArray(s) ? s : []; }
    catch (e) { return []; }
  }
  function save(s) { try { sessionStorage.setItem(KEY, JSON.stringify(s.slice(-MAX))); } catch (e) {} }

  /* ---- 1. this tab's trail of app pages ----
     pageshow also fires when the phone's own back gesture restores a page
     from the back/forward cache. A page already on the trail cuts it back to
     itself (reload, native back), so the trail only ever mirrors real history. */
  window.addEventListener("pageshow", function (e) {
    // Restored from the back/forward cache with the page-load bar still running.
    if (e.persisted && window.TCBar) window.TCBar.done();
    var s = load(), cur = location.pathname + location.search, i = s.lastIndexOf(cur);
    if (i >= 0) s = s.slice(0, i + 1); else s.push(cur);
    save(s);
  });

  document.addEventListener("click", function (ev) {
    var a = ev.target.closest && ev.target.closest("a[data-navback]");
    if (!a || ev.defaultPrevented || ev.button !== 0 || ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.altKey) return;
    ev.preventDefault();
    var s = load(), prev = s[s.length - 2];
    // history.back() only when the browser's previous entry is provably the
    // previous app page: the referrer must be that same page. Anything else
    // (opened from a notification, an external site in between) goes UP.
    if (prev && localPath(document.referrer) === prev) { history.back(); return; }
    var parent = localPath(a.getAttribute("data-parent") || a.getAttribute("href"));
    if (!parent) return;
    s.pop(); save(s);           // this page is replaced, so UP never ping-pongs with Back
    location.replace(parent);
  });

  /* ---- 2. files in the installed app ---- */
  var standalone = window.navigator.standalone === true ||
    (window.matchMedia && window.matchMedia("(display-mode: standalone)").matches);
  if (!standalone) return;

  function toast(msg, type) { if (window.tcToast) window.tcToast(msg, type || ""); }

  function fileName(res, url, a) {
    var cd = res.headers.get("Content-Disposition") || "", m;
    if ((m = /filename\*\s*=\s*(?:UTF-8|utf-8)''([^;]+)/.exec(cd))) { try { return decodeURIComponent(m[1].trim()); } catch (e) {} }
    if ((m = /filename\s*=\s*"?([^";]+)"?/.exec(cd))) return m[1].trim();
    if (a && a.getAttribute("download")) return a.getAttribute("download");
    return decodeURIComponent(url.pathname.split("/").pop() || "file") || "file";
  }

  function canShareFiles() {
    try { return !!(navigator.canShare && navigator.share && navigator.canShare({ files: [new File(["x"], "x.txt", { type: "text/plain" })] })); }
    catch (e) { return false; }
  }

  /* iOS drops the user gesture during a slow download, and then refuses the
     share sheet. A second, explicit tap gives it a fresh gesture. */
  function readyToast(file, href) {   // file null: this device cannot share it, open it instead
    var wrap = document.querySelector(".toasts");
    if (!wrap) { wrap = document.createElement("div"); wrap.className = "toasts"; document.body.appendChild(wrap); }
    var el = document.createElement("div"), b = document.createElement("button"), span = document.createElement("span");
    el.className = "toast nb-ready"; el.setAttribute("role", "status");
    span.textContent = t("navback.file_ready", "Your file is ready.");
    b.type = "button"; b.className = "btn btn-primary btn-sm"; b.textContent = t("navback.open_file", "Open file");
    b.addEventListener("click", function () {
      el.remove();
      if (!file) { window.open(href, "_blank", "noopener"); return; }
      navigator.share({ files: [file] }).catch(function (e) {
        if (!e || e.name !== "AbortError") window.open(href, "_blank", "noopener");
      });
    });
    el.appendChild(span); el.appendChild(b); wrap.appendChild(el);
    setTimeout(function () { el.remove(); }, 15000);
  }

  function getFile(url, a) {
    if (a) a.setAttribute("aria-busy", "true");
    toast(t("navback.preparing", "Preparing the file…"));
    return fetch(url.href, { credentials: "same-origin" }).then(function (res) {
      // An HTML answer is not a file: a sign-in page, or a page that refused
      // with a message. Show it in place like any other page.
      var ct = (res.headers.get("Content-Type") || "").toLowerCase();
      // Load the original URL again (not res.url) so a refusal re-flashes its message.
      if (ct.indexOf("text/html") === 0) { location.href = url.href; return null; }
      if (!res.ok) throw new Error("HTTP " + res.status);
      return res.blob().then(function (blob) {
        var file = new File([blob], fileName(res, url, a), { type: blob.type || ct.split(";")[0] || "application/octet-stream" });
        var ok = false;
        try { ok = navigator.canShare({ files: [file] }); } catch (e) {}
        if (!ok) { readyToast(null, url.href); return; }   // e.g. a type iOS will not share
        return navigator.share({ files: [file] }).catch(function (e) {
          if (e && e.name === "AbortError") return;     // the user closed the sheet
          if (e && e.name === "NotAllowedError") { readyToast(file, url.href); return; }
          throw e;
        });
      });
    }).catch(function () {
      toast(t("navback.failed", "The file could not be opened. Check your connection and try again."), "error");
    }).then(function () { if (a) a.removeAttribute("aria-busy"); });
  }

  function handle(ev, url, a) {
    if (!url || url.origin !== location.origin) return;
    ev.preventDefault();
    if (window.TCBar) window.TCBar.done();   // app.js started the page-load bar; no page loads
    // No file sharing on this device: open it over the app instead (iOS shows
    // it in an in-app browser with a Done button), still never in place.
    if (!canShareFiles()) { window.open(url.href, "_blank", "noopener"); return; }
    getFile(url, a);
  }

  var dirty = false;   // unsaved typing: never navigate away from it (see below)
  document.addEventListener("input", function (e) { if (e.target.closest && e.target.closest("form")) dirty = true; }, true);

  document.addEventListener("click", function (ev) {
    if (ev.defaultPrevented || ev.button !== 0 || ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.altKey) return;
    var a = ev.target.closest && ev.target.closest("a[href]");
    if (!a || a.hasAttribute("data-file-view")) return;   // the in-app viewer owns those
    var url;
    try { url = new URL(a.href, location.href); } catch (e) { return; }
    if (a.hasAttribute("data-file-download") || a.hasAttribute("download")) { handle(ev, url, a); return; }
    // An app page opened with target=_blank would leave the installed app for a
    // browser sheet with a separate sign-in; open it in place so Back works —
    // unless the user has typed into a form here, which that would throw away.
    // shortcut: "is it a page" is guessed from the path (no extension, not an
    // attachment/file route); mark any new file route with data-file-download.
    if (a.target === "_blank" && url.origin === location.origin && !dirty &&
        !/\.[a-z0-9]{2,5}$|\/(attachment|file|vendor-doc)(\/|$)/i.test(url.pathname)) {
      ev.preventDefault();
      location.href = url.href;
    }
  });

  // GET forms whose submit button asks for a file (e.g. warehouse daily CSV).
  document.addEventListener("submit", function (ev) {
    var btn = ev.submitter, form = ev.target;
    if (ev.defaultPrevented || !btn || !btn.hasAttribute("data-file-download") || (form.method || "get").toLowerCase() !== "get") return;
    var url;
    try {
      url = new URL(form.getAttribute("action") || location.pathname, location.href);
      var q = new URLSearchParams(new FormData(form));
      if (btn.name) q.set(btn.name, btn.value);
      url.search = q.toString();
    } catch (e) { return; }
    handle(ev, url, btn);
  });
})();
