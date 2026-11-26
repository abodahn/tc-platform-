/* Online price comparison on a purchase request (proc_price_search).
   The search text comes from the request line itself; the reader may edit it
   and search again. Every node is built with textContent / setAttribute:
   nothing from the model or from a fetched store page is parsed as HTML, and
   a link is only ever an http(s) URL. */
(function () {
  "use strict";
  var dlg = document.getElementById("psDlg");
  if (!dlg) return;
  var t = function (k, f) { var v = window.tcT ? window.tcT(k) : k; return !v || v === k ? f : v; };
  var fmt = function (s, o) {
    return s.replace(/%\((\w+)\)s/g, function (_, k) { return o[k] == null ? "" : String(o[k]); });
  };
  var form = dlg.querySelector("[data-ps-form]"), input = dlg.querySelector("#psQ"),
      go = dlg.querySelector("[data-ps-go]"), out = dlg.querySelector("[data-ps-out]"),
      ctx = dlg.querySelector("[data-ps-ctx]");
  var line = null, opener = null, ctrlLine = null, ctrlAll = null, allView = null, allRes = {};
  var BATCH = 2;   // lines per price-search-all call: short requests, steady progress

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }
  function nf(n) {
    var x = Number(n);
    return isFinite(x) ? x.toLocaleString("en-US", { maximumFractionDigits: 2 }) : "—";
  }
  function money(n, cur) { return nf(n) + " " + (cur || ""); }
  function safeHref(u) { u = String(u || ""); return /^https?:\/\//i.test(u) ? u : null; }
  function hostOf(u) { try { return new URL(u).hostname.replace(/^www\./, ""); } catch (e) { return ""; } }
  function link(a, href) {
    a.setAttribute("href", href);
    a.setAttribute("target", "_blank");
    a.setAttribute("rel", "noopener noreferrer nofollow");
  }
  function button(cls, text, fn) {
    var b = el("button", cls, text);
    b.type = "button";
    b.addEventListener("click", fn);
    return b;
  }

  function post(url, body, signal) {
    return fetch(url, {
      method: "POST", credentials: "same-origin", signal: signal,
      headers: { "Content-Type": "application/json", "X-CSRF-Token": dlg.dataset.csrf },
      body: JSON.stringify(body)
    }).then(function (r) {
      return r.json().catch(function () { return { ok: false, error: r.status === 403 ? "forbidden" : "http" }; });
    });
  }

  var ERR = {
    offline: ["ps.err.offline", "Online price search is not switched on yet. Ask the administrator to set it up."],
    timeout: ["ps.err.timeout", "The stores were slow to answer. Try again."],
    bad_query: ["ps.err.short", "Type at least two characters to search."],
    network: ["ps.err.network", "Network error. Check the connection and try again."],
    forbidden: ["ps.err.forbidden", "You do not have access to online price search."]
  };
  function errText(r) {
    var e = ERR[(r && r.offline) ? "offline" : (r && r.error)] ||
            ["ps.err.generic", "The search did not finish. Try again."];
    return t(e[0], e[1]);
  }
  function showError(target, r, retry) {
    target.removeAttribute("aria-busy");
    target.textContent = "";
    var box = el("div", "ps-state ps-err");
    box.setAttribute("role", "alert");
    box.appendChild(el("p", null, errText(r)));
    if (retry && !(r && r.offline) && !(r && r.error === "bad_query"))
      box.appendChild(button("btn btn-sm", t("ps.retry", "Try again"), retry));
    target.appendChild(box);
  }

  function loading(target) {
    target.textContent = "";
    target.setAttribute("aria-busy", "true");
    target.appendChild(el("p", "ps-wait",
      t("ps.wait", "Checking stores and confirming prices on each site… (can take up to a minute)")));
    for (var i = 0; i < 4; i++) {
      var s = el("div", "ps-skel");
      s.appendChild(el("span")); s.appendChild(el("span"));
      target.appendChild(s);
    }
  }

  function chip(o) {
    var k = o.status === "confirmed" ? ["ps.st.confirmed", "Price confirmed on site", "ok"]
          : o.status === "price_changed" ? ["ps.st.changed", "Price on site now: %(p)s (changed)", "warn"]
          : ["ps.st.unconfirmed", "Not confirmed — check on site", "mute"];
    return el("span", "ps-chip ps-chip-" + k[2], fmt(t(k[0], k[1]), { p: money(o.price, o.currency) }));
  }

  function offerRow(o) {
    var li = el("li", "ps-item"), h = safeHref(o.url);
    var row = el(h ? "a" : "div", "ps-offer");
    if (h) link(row, h);
    var main = el("div", "ps-main");
    var top = el("div", "ps-store");
    top.appendChild(el("span", null, o.store || hostOf(h)));
    if (o.lowest) top.appendChild(el("span", "ps-badge", t("ps.lowest", "Lowest")));
    main.appendChild(top);
    if (o.title) main.appendChild(el("div", "ps-title", o.title));
    if (o.pack) main.appendChild(el("div", "ps-pack", o.pack));
    var chips = el("div", "ps-chips");
    chips.appendChild(chip(o));
    if (o.status !== "confirmed" && o.status !== "price_changed" && !o.verified)
      chips.appendChild(el("span", "ps-chip ps-chip-mute", t("ps.unverified", "Not in the search sources")));
    if (o.in_stock === false) chips.appendChild(el("span", "ps-chip ps-chip-mute", t("ps.oos", "Listed as out of stock")));
    main.appendChild(chips);
    var side = el("div", "ps-side");
    side.appendChild(el("div", "ps-price", money(o.price, o.currency)));
    if (o.status === "price_changed" && o.claimed_price != null)
      side.appendChild(el("div", "ps-was", fmt(t("ps.ai_said", "Search said %(p)s"), { p: nf(o.claimed_price) })));
    if (h) side.appendChild(el("span", "ps-open", t("ps.open", "Open store ↗")));
    row.appendChild(main);
    row.appendChild(side);
    li.appendChild(row);
    return li;
  }

  function stat(key, fb, val) {
    var d = el("div", "ps-stat");
    d.appendChild(el("span", "ps-stat-k", t(key, fb)));
    d.appendChild(el("b", null, val));
    return d;
  }

  /* One line's result. `rerun(fresh)` repeats the search. */
  function renderResult(target, r, qty, rerun) {
    target.removeAttribute("aria-busy");
    target.textContent = "";
    if (!r || r.ok === false) { showError(target, r || {}, function () { rerun(false); }); return; }
    var offers = r.offers || [], st = r.stats || {}, cur = r.currency || "EGP";
    if (!offers.length) {
      var box = el("div", "ps-state");
      box.appendChild(el("p", null, t("ps.empty", "No store offers were found for this text. Try a shorter or more general name.")));
      box.appendChild(button("btn btn-sm", t("ps.retry", "Try again"), function () { rerun(true); }));
      target.appendChild(box);
    } else {
      var strip = el("div", "ps-stats");
      strip.appendChild(stat("ps.low", "Lowest", st.low == null ? "—" : money(st.low, cur)));
      strip.appendChild(stat("ps.median", "Median", st.median == null ? "—" : money(st.median, cur)));
      strip.appendChild(stat("ps.high", "Highest", st.high == null ? "—" : money(st.high, cur)));
      strip.appendChild(stat("ps.n_confirmed", "Confirmed", String(st.confirmed || 0)));
      target.appendChild(strip);
      if (st.estimate)
        target.appendChild(el("p", "ps-note ps-note-warn", t("ps.estimate",
          "No price could be confirmed on a store page. These figures are what the search reported — check on the site before buying.")));
      var low = offers.filter(function (o) { return o.lowest; })[0];
      if (low && qty > 1)
        target.appendChild(el("p", "ps-note", fmt(t("ps.line_total", "Line total at the lowest price: %(q)s × %(p)s = %(t)s"),
          { q: nf(qty), p: money(low.price, low.currency), t: money(low.price * qty, low.currency) })));
      var ul = el("ul", "ps-list");
      offers.forEach(function (o) { ul.appendChild(offerRow(o)); });
      target.appendChild(ul);
    }
    if (r.cached) {
      var meta = el("p", "ps-meta");
      meta.appendChild(el("span", null, fmt(t("ps.cached", "Saved result from %(m)s min ago."),
        { m: Math.max(1, Math.round((r.age_s || 0) / 60)) }) + " "));
      meta.appendChild(button("ps-linkbtn", t("ps.refresh", "Search again now"), function () { rerun(true); }));
      target.appendChild(meta);
    }
    var srcs = (r.sources || []).filter(function (s) { return safeHref(s.url); });
    if (srcs.length) {
      var det = el("details", "ps-src");
      det.appendChild(el("summary", null, fmt(t("ps.sources", "Sources (%(n)s)"), { n: srcs.length })));
      var sl = el("ul");
      srcs.forEach(function (s) {
        var li = el("li"), a = el("a", null, s.title || hostOf(s.url));
        link(a, s.url);
        li.appendChild(a);
        sl.appendChild(li);
      });
      det.appendChild(sl);
      target.appendChild(det);
    }
  }

  function lineOf(b) {
    return { id: b.getAttribute("data-ps-line"), q: b.getAttribute("data-ps-q") || "",
             item: b.getAttribute("data-ps-item") || "", unit: b.getAttribute("data-ps-unit") || "",
             qty: parseFloat(b.getAttribute("data-ps-qty")) || 0 };
  }
  function setLine(l) {
    line = l;
    form.hidden = !l;
    input.value = l ? l.q : "";
    ctx.textContent = l ? fmt(t("ps.ctx", "%(item)s · quantity %(qty)s %(unit)s"),
      { item: l.item, qty: nf(l.qty), unit: l.unit }) : "";
  }
  function show() {
    if (dlg.open) return;
    if (typeof dlg.showModal === "function") dlg.showModal(); else dlg.setAttribute("open", "");
  }

  function search(fresh) {
    if (!line) return;
    var q = input.value.replace(/\s+/g, " ").trim();
    if (q.length < 2) { showError(out, { error: "bad_query" }); input.focus(); return; }
    if (ctrlLine) ctrlLine.abort();       // a new search supersedes the old one
    var my = ctrlLine = new AbortController(), l = line;
    loading(out);
    go.disabled = true;
    // Unchanged text is sent empty: the server rebuilds it from the request line.
    post(dlg.dataset.urlLine, { line_id: Number(l.id), query: q === l.q ? "" : q, fresh: !!fresh }, my.signal)
      .then(function (r) { if (my === ctrlLine) { allRes[l.id] = r; renderLine(r); } })
      .catch(function (e) {
        if (e.name !== "AbortError" && my === ctrlLine) showError(out, { error: "network" }, function () { search(fresh); });
      })
      .then(function () { if (my === ctrlLine) { go.disabled = false; ctrlLine = null; } });
  }
  function renderLine(r) {
    renderResult(out, r, line ? line.qty : 0, search);
    if (allView) out.insertBefore(button("ps-linkbtn ps-back", t("ps.back", "← All items"), backToAll), out.firstChild);
  }
  function backToAll() {
    if (ctrlLine) ctrlLine.abort();
    go.disabled = false;
    setLine(null);
    out.textContent = "";
    out.appendChild(allView);
  }

  /* -------- compare all lines -------- */
  function summaryOf(r) {
    if (!r || r.ok === false) return { text: errText(r || {}), href: null };
    var low = (r.offers || []).filter(function (o) { return o.lowest; })[0];
    if (low) return { text: fmt(t("ps.all_low", "Lowest confirmed: %(p)s at %(s)s"),
      { p: money(low.price, low.currency), s: low.store || hostOf(low.url) }), href: safeHref(low.url) };
    if ((r.offers || []).length) return { text: fmt(t("ps.all_none_conf", "%(n)s offers, none confirmed on site"), { n: r.offers.length }), href: null };
    return { text: t("ps.all_empty", "No store offers found"), href: null };
  }
  function runAll(lines, fresh) {
    if (ctrlAll) ctrlAll.abort();
    var my = ctrlAll = new AbortController(), done = 0;
    allView = el("div", "ps-allview");
    var prog = el("p", "ps-wait", t("ps.wait", "Checking stores and confirming prices on each site… (can take up to a minute)"));
    var ul = el("ul", "ps-all");
    var rows = {};
    lines.forEach(function (l) {
      var li = el("li", "ps-all-row");
      var b = button("ps-all-open", null, function () {
        setLine(l);
        if (allRes[l.id]) renderLine(allRes[l.id]); else search(false);
      });
      b.appendChild(el("span", "ps-all-item", l.item));
      var sum = el("span", "ps-all-sum", t("ps.all_wait", "Searching…"));
      b.appendChild(sum);
      b.disabled = true;
      li.appendChild(b);
      ul.appendChild(li);
      rows[l.id] = { li: li, btn: b, sum: sum };
    });
    allView.appendChild(prog);
    allView.appendChild(ul);
    out.textContent = "";
    out.appendChild(allView);
    function fill(id, r) {
      var row = rows[id];
      if (!row) return;
      allRes[id] = r;
      var s = summaryOf(r);
      row.sum.textContent = s.text;
      row.btn.disabled = false;
      if (s.href) {
        var a = el("a", "ps-all-link", t("ps.open", "Open store ↗"));
        link(a, s.href);
        row.li.appendChild(a);
      }
    }
    var ids = lines.map(function (l) { return l.id; });
    var step = function (i) {
      if (my !== ctrlAll) return;
      if (i >= ids.length) {
        prog.textContent = fmt(t("ps.all_done", "Done: %(n)s items checked."), { n: ids.length });
        ctrlAll = null;
        return;
      }
      var batch = ids.slice(i, i + BATCH);
      post(dlg.dataset.urlAll, { line_ids: batch.map(Number), fresh: !!fresh }, my.signal)
        .then(function (r) {
          if (my !== ctrlAll) return;
          if (!r || r.ok === false) batch.forEach(function (id) { fill(id, r || {}); });
          else (r.lines || []).forEach(function (x) { fill(String(x.line_id), x.result); });
          done += batch.length;
          prog.textContent = fmt(t("ps.all_prog", "Checked %(d)s of %(n)s items… (each can take up to a minute)"),
            { d: done, n: ids.length });
          step(i + BATCH);
        })
        .catch(function (e) {
          if (e.name === "AbortError" || my !== ctrlAll) return;
          batch.forEach(function (id) { fill(id, { error: "network" }); });
          step(i + BATCH);
        });
    };
    step(0);
  }

  form.addEventListener("submit", function (e) { e.preventDefault(); search(false); });
  dlg.querySelector("[data-ps-close]").addEventListener("click", function () { dlg.close(); });
  // A tap on the backdrop (outside the sheet) closes it, like Esc does natively.
  dlg.addEventListener("click", function (e) {
    if (e.target !== dlg) return;
    var r = dlg.getBoundingClientRect();
    if (e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom) dlg.close();
  });
  dlg.addEventListener("close", function () {
    if (ctrlLine) ctrlLine.abort();
    if (ctrlAll) ctrlAll.abort();
    ctrlLine = ctrlAll = null;
    go.disabled = false;
    if (opener && opener.focus) opener.focus();
  });

  document.addEventListener("click", function (e) {
    var b = e.target.closest && e.target.closest("[data-ps-line], [data-ps-all]");
    if (!b || dlg.contains(b)) return;
    opener = b;
    if (b.hasAttribute("data-ps-all")) {
      setLine(null);
      allRes = {};
      show();
      // the server checks at most 25 lines of a request
      runAll(Array.prototype.map.call(document.querySelectorAll("[data-ps-line]"), lineOf).slice(0, 25), false);
    } else {
      allView = null;
      setLine(lineOf(b));
      show();
      search(false);
    }
  });
})();
