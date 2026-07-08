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
      "What fabric for a durable summer polo?",
      "Estimate cost of a cotton T-shirt",
      "Difference between twill and satin weave?",
      "How do I raise a purchase request here?"
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
      history.push({ role: "user", content: text }); save();
      var typing = addTyping();
      setBusy(true);
      jpost("/garamento/chat", { messages: history })
        .then(function (r) { return r.json(); })
        .then(function (d) {
          typing.remove();
          var reply = d.reply || "…";
          addMsg("assistant", reply);
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

    function open() {
      panel.classList.add("open");
      panel.setAttribute("aria-hidden", "false");
      launch.classList.add("open");
      firstOpen();
      setTimeout(function () { input.focus(); }, 120);
    }
    function close() {
      panel.classList.remove("open");
      panel.setAttribute("aria-hidden", "true");
      launch.classList.remove("open");
      launch.focus();
    }

    launch.addEventListener("click", open);
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
  window.Garamento.renderMarketResult = function (d) {
    if (!d || !d.ok) {
      return '<div class="mr-summary">' + esc((d && d.message) || "No result.") + "</div>";
    }
    var fmt = function (n) {
      return (n == null) ? "—" : Number(n).toLocaleString(undefined, { maximumFractionDigits: 2 });
    };
    var range = (d.price_low != null && d.price_high != null)
      ? '<div class="mr-range">Range<br><b>' + fmt(d.price_low) + " – " + fmt(d.price_high) + "</b> " + esc(d.currency) + "</div>"
      : "";
    var conf = '<span class="mr-conf ' + esc(d.confidence) + '"><span class="d"></span>' +
      esc((d.confidence || "medium").charAt(0).toUpperCase() + (d.confidence || "medium").slice(1)) + " confidence</span>";
    var unit = d.unit ? '<span class="mr-unit">/ ' + esc(d.unit) + "</span>" : "";
    var html = '<div class="mr-result">' +
      '<div class="mr-price"><span class="mr-big">' + fmt(d.price_est) + '</span>' +
      '<span class="mr-cur">' + esc(d.currency) + "</span>" + unit + range + "</div>" +
      '<div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">' + conf + "</div>" +
      '<div class="mr-summary">' + mdRender(d.summary) + "</div>";
    if (d.sources && d.sources.length) {
      html += '<div class="mr-sources"><div class="mr-shead">Sources</div>';
      d.sources.forEach(function (s) {
        var name = s.url
          ? '<a href="' + esc(s.url) + '" target="_blank" rel="noopener noreferrer">' + esc(s.name) + "</a>"
          : esc(s.name);
        html += '<div class="mr-src"><span class="mr-sname">' + name + "</span>" +
          (s.price ? '<span class="mr-sprice">' + esc(s.price) + "</span>" : "") + "</div>";
      });
      html += "</div>";
    }
    html += "</div>";
    return html;
  };
  window.Garamento.mdRender = mdRender;

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", initChat);
  else initChat();
})();
