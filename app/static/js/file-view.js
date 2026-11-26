/* In-app file viewer (templates/file_view.html): sheet tabs, image zoom and the
   PDF renderer. PDFs are drawn page by page with pdf.js onto canvases because
   iPhone Safari and Android Chrome show nothing useful for a PDF in an iframe.
   pdf.js comes from cdnjs at an exact version; offline (LAN without internet) the
   page falls back to an iframe of the same file plus the Download button.
   shortcut: no SRI hash and not vendored (no offline copy to hash); vendor pdf.js into
   app/static when it is next upgraded. */
(function () {
  "use strict";
  var PDFJS = "https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/";

  /* ---- spreadsheet tabs (WAI-ARIA tabs pattern) ---- */
  var tabs = Array.prototype.slice.call(document.querySelectorAll(".fv-tab"));
  function pick(tab, focus) {
    tabs.forEach(function (t) {
      var on = t === tab;
      t.setAttribute("aria-selected", on ? "true" : "false");
      t.tabIndex = on ? 0 : -1;
      var panel = document.getElementById(t.getAttribute("aria-controls"));
      if (panel) panel.hidden = !on;
    });
    if (focus) tab.focus();
  }
  tabs.forEach(function (t, i) {
    t.addEventListener("click", function () { pick(t); });
    t.addEventListener("keydown", function (e) {
      var rtl = document.documentElement.dir === "rtl";
      var step = { ArrowRight: rtl ? -1 : 1, ArrowLeft: rtl ? 1 : -1 }[e.key];
      if (e.key === "Home") step = -i;
      if (e.key === "End") step = tabs.length - 1 - i;
      if (step === undefined) return;
      e.preventDefault();
      pick(tabs[(i + step + tabs.length) % tabs.length], true);
    });
  });

  /* ---- image: tap toggles fit-to-screen / actual size ---- */
  var imgBtn = document.querySelector(".fv-img-btn");
  if (imgBtn) imgBtn.addEventListener("click", function () {
    imgBtn.parentNode.classList.toggle("zoomed");
  });

  /* ---- PDF ---- */
  var box = document.getElementById("fvPdf");
  if (!box) return;
  var src = box.getAttribute("data-src");
  var status = box.querySelector(".fv-status");
  var pagesEl = box.querySelector(".fv-pages");
  var done = false;

  function fallback() {
    if (done) return;
    done = true;
    status.hidden = true;
    pagesEl.innerHTML = "";
    var fb = box.querySelector(".fv-fallback");
    var frame = fb.querySelector("iframe");
    frame.src = frame.getAttribute("data-src");
    fb.hidden = false;
  }

  function loadScript(url) {
    return new Promise(function (resolve, reject) {
      var s = document.createElement("script");
      s.src = url;
      s.crossOrigin = "anonymous";
      s.referrerPolicy = "no-referrer";
      s.onload = resolve;
      s.onerror = reject;
      document.head.appendChild(s);
      setTimeout(reject, 30000);   // a blocked CDN can hang instead of failing; phones on slow links need time
    });
  }

  function start(lib) {
    lib.GlobalWorkerOptions.workerSrc = PDFJS + "pdf.worker.min.js";
    return lib.getDocument({
      url: src, withCredentials: true,
      // SECURITY: keep isEvalSupported false. pdf.js < 4.2.67 runs a crafted font as
      // script on our origin otherwise (CVE-2024-4367); uploads come from any user.
      isEvalSupported: false,
      // one plain fetch: every chunk request would re-run the route (DB read / PDF build)
      disableRange: true, disableStream: true, disableAutoFetch: true
    }).promise.then(function (doc) {
      return doc.getPage(1).then(function (first) {
        var vp1 = first.getViewport({ scale: 1 });
        if (done) return;
        done = true;
        status.hidden = true;
        var count = document.querySelector(".fv-count");
        if (count) { count.querySelector("b").textContent = doc.numPages; count.hidden = false; }
        // shortcut: lower pixel density on long files to stay inside iOS canvas memory; page-unloading would allow more.
        var pageWord = (document.getElementById("fvPageLbl") || {}).textContent || "Page";
        var dpr = Math.min(window.devicePixelRatio || 1, doc.numPages > 30 ? 1.25 : 2);
        var slots = [];
        for (var n = 1; n <= doc.numPages; n++) {
          var slot = document.createElement("div");
          slot.className = "fv-page";
          slot.style.aspectRatio = vp1.width + " / " + vp1.height;
          slot.setAttribute("data-page", n);
          var no = document.createElement("span");
          no.className = "fv-page-no";
          no.textContent = n + " / " + doc.numPages;
          slot.appendChild(no);
          pagesEl.appendChild(slot);
          slots.push(slot);
        }
        function draw(slot) {
          if (slot.getAttribute("data-drawn")) return;
          slot.setAttribute("data-drawn", "1");
          doc.getPage(+slot.getAttribute("data-page")).then(function (page) {
            var base = page.getViewport({ scale: 1 });
            var vp = page.getViewport({ scale: (slot.clientWidth / base.width) * dpr });
            var canvas = document.createElement("canvas");
            canvas.width = Math.floor(vp.width);
            canvas.height = Math.floor(vp.height);
            canvas.setAttribute("role", "img");
            canvas.setAttribute("aria-label", pageWord + " " + slot.getAttribute("data-page") + " / " + doc.numPages);
            slot.style.aspectRatio = base.width + " / " + base.height;
            slot.insertBefore(canvas, slot.firstChild);
            return page.render({ canvasContext: canvas.getContext("2d"), viewport: vp }).promise;
          }).catch(function () {
            // e.g. iOS out of canvas memory: drop the half-made canvas and let the
            // observer try again when the page scrolls back into view (3 tries).
            var c = slot.querySelector("canvas");
            if (c) c.remove();
            slot.removeAttribute("data-drawn");
            var tries = +(slot.getAttribute("data-tries") || 0) + 1;
            slot.setAttribute("data-tries", tries);
            if (io && tries < 3) io.observe(slot);
          });
        }
        var io = null;
        if ("IntersectionObserver" in window) {
          io = new IntersectionObserver(function (entries) {
            entries.forEach(function (e) { if (e.isIntersecting) { io.unobserve(e.target); draw(e.target); } });
          }, { rootMargin: "800px 0px" });
          slots.forEach(function (s) { io.observe(s); });
        } else {
          slots.forEach(draw);
        }
      });
    });
  }

  loadScript(PDFJS + "pdf.min.js").then(function () {
    var lib = window.pdfjsLib || window["pdfjs-dist/build/pdf"];
    if (!lib) throw new Error("pdf.js missing");
    return start(lib);
  }).catch(fallback);
})();
