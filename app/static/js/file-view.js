/* In-app file viewer (templates/file_view.html): sheet tabs, image zoom and the
   PDF renderer. PDFs are drawn page by page with pdf.js onto canvases because
   iPhone Safari and Android Chrome show nothing useful for a PDF in an iframe.
   pdf.js comes from cdnjs at an exact version; offline (LAN without internet) the
   page falls back to an iframe of the same file plus the Download button. */
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
      setTimeout(reject, 12000);   // a blocked CDN can hang instead of failing
    });
  }

  function start(lib) {
    lib.GlobalWorkerOptions.workerSrc = PDFJS + "pdf.worker.min.js";
    return lib.getDocument({ url: src, withCredentials: true }).promise.then(function (doc) {
      return doc.getPage(1).then(function (first) {
        var vp1 = first.getViewport({ scale: 1 });
        if (done) return;
        done = true;
        status.hidden = true;
        var count = document.querySelector(".fv-count");
        if (count) { count.querySelector("b").textContent = doc.numPages; count.hidden = false; }
        // shortcut: lower pixel density on long files to stay inside iOS canvas memory; page-unloading would allow more.
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
            canvas.setAttribute("aria-label", "PDF " + slot.getAttribute("data-page") + " / " + doc.numPages);
            slot.style.aspectRatio = base.width + " / " + base.height;
            slot.insertBefore(canvas, slot.firstChild);
            return page.render({ canvasContext: canvas.getContext("2d"), viewport: vp }).promise;
          }).catch(function () { slot.removeAttribute("data-drawn"); });
        }
        if ("IntersectionObserver" in window) {
          var io = new IntersectionObserver(function (entries) {
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
