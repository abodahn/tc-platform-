/* ============================================================
   Garamento — client widget
   Chat assistant + shared market-research renderer.
   ============================================================ */
(function () {
  "use strict";

  var CSRF = (document.querySelector('meta[name="csrf-token"]') || {}).content || "";
  var LS_KEY = "tc.garamento.history.v1";

  function jpost(url, body) {
    return fetch(url, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": CSRF },
      body: JSON.stringify(body || {})
    });
  }

  /* ---- safe mini-markdown (escape first, then a tiny allowlist) ---- */
  function esc(s) {
    return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;")
      .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }
  function mdInline(s) {
    return s
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
      .replace(/`([^`]+)`/g, "<code>$1</code>")
      .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g,
        '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>')
      .replace(/(^|[\s(])(https?:\/\/[^\s<)]+)/g,
        '$1<a href="$2" target="_blank" rel="noopener noreferrer">$2</a>');
  }
  function mdRender(text) {
    var lines = esc(text || "").split(/\n/);
    var html = "", list = null;
    lines.forEach(function (ln) {
      var li = ln.match(/^\s*[-*•]\s+(.*)$/);
      var oli = ln.match(/^\s*\d+[.)]\s+(.*)$/);
      if (li || oli) {
        var tag = oli ? "ol" : "ul";
        if (list !== tag) { if (list) html += "</" + list + ">"; html += "<" + tag + ">"; list = tag; }
        html += "<li>" + mdInline((li || oli)[1]) + "</li>";
      } else {
        if (list) { html += "</" + list + ">"; list = null; }
        if (ln.trim()) html += "<p>" + mdInline(ln) + "</p>";
      }
    });
    if (list) html += "</" + list + ">";
    return html || "<p></p>";
  }

  var MASCOT = '<svg aria-hidden="true"><use href="#gm-mascot"></use></svg>';

  /* =========================================================
     Sfx — sewing/textile sounds, synthesized (no audio files)
     ========================================================= */
  var Sfx = (function () {
    var SKEY = "tc.garamento.sound";
    var on = localStorage.getItem(SKEY) !== "0";   // default ON
    var actx = null;
    function ctx() {
      try {
        actx = actx || new (window.AudioContext || window.webkitAudioContext)();
        if (actx.state === "suspended") actx.resume();
      } catch (e) { actx = null; }
      return actx;
    }
    function tone(freq, start, dur, type, gain) {
      var a = ctx(); if (!a) return;
      var o = a.createOscillator(), g = a.createGain(), t0 = a.currentTime + start;
      o.type = type || "sine"; o.frequency.setValueAtTime(freq, t0);
      g.gain.setValueAtTime(0.0001, t0);
      g.gain.exponentialRampToValueAtTime(gain || 0.12, t0 + 0.008);
      g.gain.exponentialRampToValueAtTime(0.0001, t0 + dur);
      o.connect(g); g.connect(a.destination);
      o.start(t0); o.stop(t0 + dur + 0.02);
    }
    function swoosh(start, dur, f0, f1, gain) {   // fabric/thread pull
      var a = ctx(); if (!a) return;
      var n = Math.floor(a.sampleRate * dur), buf = a.createBuffer(1, n, a.sampleRate), d = buf.getChannelData(0);
      for (var i = 0; i < n; i++) d[i] = (Math.random() * 2 - 1) * (1 - i / n);
      var src = a.createBufferSource(); src.buffer = buf;
      var bp = a.createBiquadFilter(); bp.type = "bandpass"; bp.Q.value = 0.8;
      var t0 = a.currentTime + start;
      bp.frequency.setValueAtTime(f0, t0);
      bp.frequency.exponentialRampToValueAtTime(f1, t0 + dur);
      var g = a.createGain(); g.gain.value = gain || 0.08;
      src.connect(bp); bp.connect(g); g.connect(a.destination);
      src.start(t0); src.stop(t0 + dur);
    }
    function stitch(t) {   // one sewing-machine stitch = a short mechanical "cha-chunk"
      tone(1500, t, 0.03, "square", 0.045);
      tone(680, t + 0.012, 0.045, "triangle", 0.06);
    }
    var api = {
      arm: ctx,
      isOn: function () { return on; },
      toggle: function () { on = !on; localStorage.setItem(SKEY, on ? "1" : "0"); if (on) { ctx(); api.open(); } return on; },
      open: function () { if (!on) return; swoosh(0, 0.28, 300, 1600, 0.07); tone(523.25, 0.03, 0.16, "sine", 0.05); },
      send: function () { if (!on) return; stitch(0); stitch(0.07); stitch(0.14); },          // a quick run of stitches
      receive: function () { if (!on) return; stitch(0); tone(659.25, 0.05, 0.18, "sine", 0.06); tone(987.77, 0.13, 0.22, "sine", 0.05); },
      click: function () { if (!on) return; stitch(0); }
    };
    return api;
  })();

  /* =========================================================
     Drag — make an element movable, position persisted
     ========================================================= */
  function clamp(v, a, b) { return Math.max(a, Math.min(b, v)); }
  function makeDraggable(el, handle, opts) {
    opts = opts || {};
    var sx, sy, ox, oy, moved = false, dragging = false;
    handle.addEventListener("pointerdown", function (e) {
      if (e.button != null && e.button !== 0) return;
      if (opts.ignore && e.target.closest && e.target.closest(opts.ignore)) return;
      dragging = true; moved = false;
      var r = el.getBoundingClientRect();
      ox = r.left; oy = r.top; sx = e.clientX; sy = e.clientY;
      el.classList.add("moved"); el.style.left = ox + "px"; el.style.top = oy + "px";
      el.classList.add(opts.dragClass || "dragging");
      try { handle.setPointerCapture(e.pointerId); } catch (_) {}
      e.preventDefault();
    });
    handle.addEventListener("pointermove", function (e) {
      if (!dragging) return;
      var dx = e.clientX - sx, dy = e.clientY - sy;
      if (Math.abs(dx) > 4 || Math.abs(dy) > 4) moved = true;
      el.style.left = clamp(ox + dx, 4, window.innerWidth - el.offsetWidth - 4) + "px";
      el.style.top = clamp(oy + dy, 4, window.innerHeight - el.offsetHeight - 4) + "px";
    });
    function end(e) {
      if (!dragging) return;
      dragging = false;
      el.classList.remove(opts.dragClass || "dragging");
      try { handle.releasePointerCapture(e.pointerId); } catch (_) {}
      if (moved) { save(); if (opts.onDrop) opts.onDrop(); }
      else if (opts.onClick) opts.onClick(e);
    }
    handle.addEventListener("pointerup", end);
    handle.addEventListener("pointercancel", end);
    function save() {
      if (!opts.storeKey) return;
      try { localStorage.setItem(opts.storeKey, JSON.stringify({ left: parseInt(el.style.left, 10), top: parseInt(el.style.top, 10) })); } catch (_) {}
    }
    return {
      moved: function () { return moved; },
      restore: function () {
        if (!opts.storeKey) return false;
        try {
          var p = JSON.parse(localStorage.getItem(opts.storeKey));
          if (p && typeof p.left === "number") {
            el.classList.add("moved");
            el.style.left = clamp(p.left, 4, window.innerWidth - el.offsetWidth - 4) + "px";
            el.style.top = clamp(p.top, 4, window.innerHeight - el.offsetHeight - 4) + "px";
            return true;
          }
        } catch (_) {}
        return false;
      },
      clampIntoView: function () {
        if (!el.classList.contains("moved")) return;
        el.style.left = clamp(parseInt(el.style.left, 10) || 0, 4, window.innerWidth - el.offsetWidth - 4) + "px";
        el.style.top = clamp(parseInt(el.style.top, 10) || 0, 4, window.innerHeight - el.offsetHeight - 4) + "px";
      }
    };
  }

  /* =========================================================
     Chat widget
     ========================================================= */
  function initChat() {
    var launch = document.getElementById("gmLaunch");
    var panel = document.getElementById("gmPanel");
    if (!launch || !panel) return;

    var body = document.getElementById("gmBody");
    var input = document.getElementById("gmInput");
    var send = document.getElementById("gmSend");
    var closeBtn = document.getElementById("gmClose");
    var dot = document.getElementById("gmDot");
    var sndBtn = document.getElementById("gmSound");
    var header = panel.querySelector(".gm-head");

    var history = load();
    var enabled = true;
    var greeted = false;
    var busy = false;

    function load() { try { return JSON.parse(localStorage.getItem(LS_KEY)) || []; } catch (e) { return []; } }
    function save() { try { localStorage.setItem(LS_KEY, JSON.stringify(history.slice(-24))); } catch (e) {} }

    function scrollDown() { body.scrollTop = body.scrollHeight; }

    function addMsg(role, text) {
      var wrap = document.createElement("div");
      wrap.className = "gm-msg " + (role === "user" ? "me" : "bot");
      var bubble = '<div class="gm-bubble">' + mdRender(text) + "</div>";
      wrap.innerHTML = (role === "user" ? "" : '<div class="gm-mava">' + MASCOT + "</div>") + bubble;
      body.appendChild(wrap);
      scrollDown();
      return wrap;
    }

    function addTyping() {
      var wrap = document.createElement("div");
      wrap.className = "gm-msg bot";
      wrap.innerHTML = '<div class="gm-mava">' + MASCOT + '</div>' +
        '<div class="gm-bubble"><span class="gm-typing"><i></i><i></i><i></i></span></div>';
      body.appendChild(wrap); scrollDown();
      return wrap;
    }

    var CHIPS = [
      "How do I open a maintenance ticket?",
      "How do I raise a purchase request?",
      "What fabric suits a durable summer polo?",
      "Estimate the cost of a cotton T-shirt"
    ];
    function addChips() {
      var box = document.createElement("div");
      box.className = "gm-chips";
      CHIPS.forEach(function (c) {
        var b = document.createElement("button");
        b.type = "button"; b.className = "gm-chip"; b.textContent = c;
        b.addEventListener("click", function () { box.remove(); ask(c); });
        box.appendChild(b);
      });
      body.appendChild(box); scrollDown();
    }

    function renderHistory() {
      body.innerHTML = "";
      history.forEach(function (m) { addMsg(m.role, m.content); });
    }

    function firstOpen() {
      if (greeted) return;
      greeted = true;
      fetch("/garamento/hello", { credentials: "same-origin" })
        .then(function (r) { return r.json(); })
        .then(function (d) {
          enabled = !!d.enabled;
          dot.classList.toggle("off", !enabled);
          dot.parentElement && dot.parentElement.setAttribute("title", enabled ? "Online" : "Offline — API key not set");
          if (history.length) { renderHistory(); }
          else { addMsg("assistant", d.greeting || "Ciao! I'm Garamento."); addChips(); }
        })
        .catch(function () {
          if (history.length) renderHistory();
          else { addMsg("assistant", "Ciao! I'm Garamento 🧵 — your textile & fashion right hand."); addChips(); }
        });
    }

    function setBusy(v) { busy = v; send.disabled = v; input.disabled = v; }

    function ask(text) {
      text = (text || input.value || "").trim();
      if (!text || busy) return;
      input.value = ""; autoGrow();
      addMsg("user", text);
      Sfx.send();
      history.push({ role: "user", content: text }); save();
      var typing = addTyping();
      setBusy(true);
      jpost("/garamento/chat", { messages: history })
        .then(function (r) { return r.json(); })
        .then(function (d) {
          typing.remove();
          var reply = d.reply || "…";
          addMsg("assistant", reply);
          Sfx.receive();
          if (d.ok) { history.push({ role: "assistant", content: reply }); save(); }
        })
        .catch(function () {
          typing.remove();
          addMsg("assistant", "My line to the atelier dropped. Try again in a moment?");
        })
        .finally(function () { setBusy(false); input.focus(); });
    }

    function autoGrow() {
      input.style.height = "auto";
      input.style.height = Math.min(input.scrollHeight, 120) + "px";
    }

    // --- sound toggle ---
    function paintSound() { if (sndBtn) { sndBtn.textContent = Sfx.isOn() ? "🔊" : "🔇"; sndBtn.classList.toggle("off", !Sfx.isOn()); } }
    paintSound();
    if (sndBtn) sndBtn.addEventListener("click", function (e) {
      e.stopPropagation();
      var on = Sfx.toggle(); paintSound();
      toast(on ? "Garamento sound on 🧵" : "Garamento sound off", on ? "success" : "");
    });

    // --- movable: launcher (click to open) + panel (drag by header) ---
    var panelDrag = makeDraggable(panel, header, { ignore: ".gm-hbtn", storeKey: "tc.garamento.panelPos", onDrop: function () { panelUserMoved = true; } });
    var panelUserMoved = panelDrag.restore();
    var launchDrag = makeDraggable(launch, launch, { storeKey: "tc.garamento.launchPos", onClick: open });
    launchDrag.restore();
    launch.addEventListener("keydown", function (e) { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); open(); } });
    window.addEventListener("resize", function () { launchDrag.clampIntoView(); panelDrag.clampIntoView(); });

    function anchorPanel() {
      // If the user hasn't placed the panel and the launcher was moved, open the
      // panel next to the launcher; otherwise use the default (bottom-right) CSS.
      if (panelUserMoved) return;
      if (!launch.classList.contains("moved") || window.innerWidth <= 520) {
        panel.classList.remove("moved");
        panel.style.left = panel.style.top = panel.style.width = panel.style.height = "";
        return;
      }
      var lr = launch.getBoundingClientRect();
      var pw = Math.min(408, window.innerWidth - 32), ph = Math.min(620, window.innerHeight - 90);
      var left = clamp(lr.right - pw, 8, window.innerWidth - pw - 8);
      var top = lr.top - ph - 12;
      if (top < 8) top = clamp(lr.bottom + 12, 8, window.innerHeight - ph - 8);
      panel.classList.add("moved");
      panel.style.left = left + "px"; panel.style.top = top + "px";
      panel.style.width = pw + "px"; panel.style.height = ph + "px";
    }

    function open() {
      Sfx.arm(); anchorPanel();
      panel.classList.add("open");
      panel.setAttribute("aria-hidden", "false");
      launch.classList.add("open");
      Sfx.open();
      firstOpen();
      setTimeout(function () { input.focus(); }, 120);
    }
    function close() {
      panel.classList.remove("open");
      panel.setAttribute("aria-hidden", "true");
      launch.classList.remove("open");
      launch.focus();
    }

    closeBtn.addEventListener("click", close);
    send.addEventListener("click", function () { ask(); });
    input.addEventListener("input", autoGrow);
    input.addEventListener("keydown", function (e) {
      if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); ask(); }
    });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && panel.classList.contains("open")) close();
    });

    // Public hook so other pages can pop Garamento open with a prefilled question.
    window.Garamento = window.Garamento || {};
    window.Garamento.open = open;
    window.Garamento.ask = function (q) { open(); setTimeout(function () { ask(q); }, 200); };
  }

  /* =========================================================
     Shared market-research renderer (used by procurement page)
     ========================================================= */
  window.Garamento = window.Garamento || {};

  function fmtNum(n) {
    return (n == null) ? "—" : Number(n).toLocaleString(undefined, { maximumFractionDigits: 2 });
  }
  function confPill(c) {
    c = (c || "medium").toLowerCase();
    return '<span class="mr-conf ' + esc(c) + '"><span class="d"></span>' +
      esc(c.charAt(0).toUpperCase() + c.slice(1)) + " confidence</span>";
  }
  function priceBar(d) {
    if (d.price_low == null || d.price_high == null || d.price_high <= d.price_low) return "";
    var span = d.price_high - d.price_low;
    var est = d.price_est != null ? d.price_est : (d.price_low + d.price_high) / 2;
    var pct = Math.max(0, Math.min(100, ((est - d.price_low) / span) * 100));
    return '<div class="mr-bar"><div class="mr-bar-track"><span class="mr-bar-est" style="inset-inline-start:' + pct.toFixed(1) + '%"></span></div>' +
      '<div class="mr-bar-lbl"><span>' + fmtNum(d.price_low) + '</span><span>' + fmtNum(d.price_high) + '</span></div></div>';
  }
  function sourcesBlock(d) {
    if (!d.sources || !d.sources.length) return "";
    var h = '<div class="mr-sources"><div class="mr-shead">Sources</div>';
    d.sources.forEach(function (s) {
      var name = s.url
        ? '<a href="' + esc(s.url) + '" target="_blank" rel="noopener noreferrer">' + esc(s.name) + "</a>"
        : esc(s.name);
      h += '<div class="mr-src"><span class="mr-sname">' + name + "</span>" +
        (s.price ? '<span class="mr-sprice">' + esc(s.price) + "</span>" : "") + "</div>";
    });
    return h + "</div>";
  }

  window.Garamento.renderMarketResult = function (d) {
    if (!d || !d.ok) return '<div class="mr-summary">' + esc((d && d.message) || "No result.") + "</div>";
    var unit = d.unit ? '<span class="mr-unit">/ ' + esc(d.unit) + "</span>" : "";
    var asof = d.as_of ? '<span class="mr-asof">as of ' + esc(d.as_of) + "</span>" : "";
    var html = '<div class="mr-result">' +
      '<div class="mr-price"><span class="mr-big">' + fmtNum(d.price_est) + '</span>' +
      '<span class="mr-cur">' + esc(d.currency) + "</span>" + unit +
      '<div class="mr-meta">' + confPill(d.confidence) + asof + "</div></div>" +
      priceBar(d);
    if (d.best_vendor && d.best_vendor.name) {
      html += '<div class="mr-vendor"><span class="mr-vlabel">Best value</span>' +
        '<span class="mr-vname">' + esc(d.best_vendor.name) + "</span>" +
        (d.best_vendor.reason ? '<span class="mr-vreason">' + esc(d.best_vendor.reason) + "</span>" : "") + "</div>";
    }
    html += '<div class="mr-summary">' + mdRender(d.summary) + "</div>";
    if (d.note) html += '<div class="mr-note">⚠︎ ' + esc(d.note) + "</div>";
    html += sourcesBlock(d) + "</div>";
    return html;
  };

  // Bulk: a compact list of per-item results (page wires "Apply all").
  window.Garamento.renderMarketBulk = function (data, items) {
    if (!data || !data.ok) return '<div class="mr-summary">' + esc((data && data.message) || "No result.") + "</div>";
    var cur = esc(data.currency || "");
    var results = data.results || [];
    var ok = 0, h = '<div class="mr-bulk">';
    results.forEach(function (r) {
      var name = esc((items && items[r.index] && items[r.index].item) || r.item || ("Item " + (r.index + 1)));
      if (r.ok) {
        ok++;
        h += '<div class="mr-brow" data-index="' + r.index + '" data-price="' + r.price_est + '">' +
          '<span class="mr-bname">' + name + '</span>' +
          '<span class="mr-bconf ' + esc(r.confidence) + '" title="' + esc(r.confidence) + ' confidence"></span>' +
          '<span class="mr-bprice">' + fmtNum(r.price_est) + ' ' + cur +
          (r.unit ? '<i>/' + esc(r.unit) + '</i>' : '') + '</span></div>';
      } else {
        h += '<div class="mr-brow miss"><span class="mr-bname">' + name + '</span>' +
          '<span class="mr-bprice">no price found</span></div>';
      }
    });
    h += "</div>";
    var head = '<div class="mr-summary" style="margin-bottom:6px">Priced <b>' + ok + '</b> of <b>' +
      results.length + '</b> items in ' + cur + '. Review, then apply.</div>';
    return head + h;
  };

  window.Garamento.mdRender = mdRender;

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", initChat);
  else initChat();
})();
