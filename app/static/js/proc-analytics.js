/* Procurement Analytics workspace (approvals/analytics*.html).
   Draws every chart card from the JSON specs the server put in #an-data,
   gives each a Chart / Table toggle, sends a click on a bar, slice or table row
   to the request list filtered to it, runs the report builder live, and turns
   the filter bar into a bottom sheet on phones. No network libraries: Chart.js
   is the vendored copy (works offline). */
(function () {
  "use strict";
  var el = document.getElementById("an-data");
  if (!el || !window.Chart) return;
  var DATA = JSON.parse(el.textContent || "{}");
  var L = DATA.L || {};
  var lang = DATA.lang || document.documentElement.lang || "en";
  var NL = lang === "ar" ? "ar-u-nu-latn" : lang;
  var RTL = document.documentElement.dir === "rtl";
  var REDUCE = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var charts = {};

  function css(name, fallback) {
    var v = getComputedStyle(document.body).getPropertyValue(name);
    return (v && v.trim()) || fallback;
  }
  function palette() {
    var out = [];
    for (var i = 1; i <= 8; i++) out.push(css("--an-" + i, "#3366cc"));
    return out;
  }
  var ROLE = { base: "--an-1", hot: "--tc-red", muted: "--an-muted", good: "--ok" };

  function num(v, unit) {
    if (v === null || v === undefined || isNaN(v)) return "—";
    var d = unit === "EGP" ? 0 : (unit === "" ? 2 : 1);
    var s = new Intl.NumberFormat(NL, { maximumFractionDigits: d }).format(v);
    if (unit === "%") return s + "%";
    if (unit === "h") return s + " " + (L.h || "h");
    if (unit === "days") return s + " " + (L.days || "days");
    return s;
  }
  function compact(v) {
    return new Intl.NumberFormat(NL, { notation: "compact", maximumFractionDigits: 1 }).format(v);
  }
  function short(s) { s = String(s == null ? "" : s); return s.length > 20 ? s.slice(0, 19) + "…" : s; }

  /* ---------- drill-down ---------- */
  function drillUrl(spec, i, j) {
    if (!spec.dp || !DATA.list) return null;
    var a = spec.dp[i], b = spec.sp ? spec.sp[j] : null;
    if (!a) return null;
    var p = {}, k;
    for (k in (spec.base || {})) p[k] = spec.base[k];
    [a, b].forEach(function (x) {
      if (!x) return;
      for (var key in x) {
        if (key === "from" && p.from) p.from = x.from > p.from ? x.from : p.from;
        else if (key === "to" && p.to) p.to = x.to < p.to ? x.to : p.to;
        else p[key] = x[key];
      }
    });
    return DATA.list + "?" + new URLSearchParams(p).toString();
  }

  /* ---------- table fallback (DOM methods only: labels are user data) ---------- */
  function buildTable(box, spec) {
    box.textContent = "";
    var multi = spec.series.length > 1;
    var head = multi ? [(spec.cols && spec.cols[0]) || ""].concat(spec.series.map(function (s) { return s.label; }))
                     : (spec.cols || ["", spec.series[0] ? spec.series[0].label : ""]);
    var t = document.createElement("table"); t.className = "tbl an-tbl";
    var tr = document.createElement("tr");
    head.forEach(function (h, i) {
      var th = document.createElement("th"); th.scope = "col"; th.textContent = h;
      if (i) th.className = "num"; tr.appendChild(th);
    });
    var thead = document.createElement("thead"); thead.appendChild(tr); t.appendChild(thead);
    var tb = document.createElement("tbody");
    spec.labels.forEach(function (lab, i) {
      var row = document.createElement("tr");
      var th = document.createElement("th"); th.scope = "row";
      var url = drillUrl(spec, i, 0);
      if (url) { var a = document.createElement("a"); a.href = url; a.textContent = lab; th.appendChild(a); }
      else th.textContent = lab;
      row.appendChild(th);
      var vals = multi ? spec.series.map(function (s) { return num(s.data[i], spec.unit); })
                       : [num(spec.series[0].data[i], spec.unit)].concat(spec.n ? [num(spec.n[i], "")] : []);
      vals.forEach(function (v) { var td = document.createElement("td"); td.className = "num"; td.textContent = v; row.appendChild(td); });
      tb.appendChild(row);
    });
    t.appendChild(tb); box.appendChild(t);
  }

  /* ---------- one chart ---------- */
  function draw(card, spec) {
    var key = card.getAttribute("data-spec");
    if (charts[key]) { charts[key].destroy(); delete charts[key]; }
    var canvasBox = card.querySelector(".an-canvas"), empty = card.querySelector(".an-empty"),
        tableBox = card.querySelector(".an-table"), toggle = card.querySelector(".an-toggle");
    var has = spec && spec.labels && spec.labels.length &&
              spec.series.some(function (s) { return s.data.some(function (v) { return v !== null && v !== 0; }); });
    empty.hidden = !!has;
    if (!has) { canvasBox.hidden = true; tableBox.hidden = true; if (toggle) toggle.hidden = true; return; }
    if (toggle) toggle.hidden = false;
    if (!tableBox.hasAttribute("data-server")) buildTable(tableBox, spec);
    var view = card.getAttribute("data-view") || card.getAttribute("data-default-view") ||
               (spec.chart === "table" ? "table" : "chart");
    setView(card, view);
    if (spec.chart === "table") { if (toggle) toggle.hidden = true; return; }

    var hbar = spec.chart === "hbar", round = spec.chart === "pie" || spec.chart === "doughnut",
        line = spec.chart === "line";
    var pal = palette(), muted = css("--an-muted", "#94a3b8"), grid = css("--an-grid", "rgba(148,163,184,.18)");
    var panel = css("--panel", "#fff");
    canvasBox.style.height = hbar ? Math.max(220, spec.labels.length * 30 + 56) + "px" : "";
    var perLabel = spec.colors && spec.series.length === 1;
    var datasets = spec.series.map(function (s, j) {
      var c = spec.colors && !perLabel ? css(ROLE[spec.colors[j]] || "--an-1", pal[j % 8]) : pal[j % 8];
      var bg = round ? spec.labels.map(function (lab, i) { return lab === L.others ? muted : pal[i % 8]; })
             : perLabel ? spec.labels.map(function (lab, i) { return css(ROLE[spec.colors[i]] || "--an-1", pal[0]); })
             : spec.series.length === 1 ? spec.labels.map(function (lab) { return lab === L.others ? muted : c; }) : c;
      return {
        label: s.label, data: s.data, backgroundColor: line ? c + "22" : bg, borderColor: round ? panel : c,
        borderWidth: round ? 2 : (line ? 2.5 : 0), borderRadius: round ? 0 : 6, maxBarThickness: 44,
        fill: line && spec.series.length === 1, tension: 0.3, cubicInterpolationMode: "monotone", pointRadius: line ? 3 : 0, pointHoverRadius: 5,
        spanGaps: true
      };
    });
    var drillable = !!spec.dp;
    var narrow = window.matchMedia("(max-width: 767.98px)").matches;
    var valueAxis = hbar ? "x" : "y", catAxis = hbar ? "y" : "x";
    var scales = {};
    if (!round) {
      scales[valueAxis] = { beginAtZero: true, grid: { color: grid }, border: { display: false },
        ticks: { callback: function (v) { return spec.unit === "%" ? v + "%" : compact(v); } },
        reverse: hbar && RTL, max: spec.unit === "%" ? 100 : undefined,
        position: hbar ? undefined : (RTL ? "right" : "left") };
      scales[catAxis] = { grid: { display: false }, reverse: !hbar && RTL,
        position: hbar ? (RTL ? "right" : "left") : undefined,
        ticks: { autoSkip: hbar || line, maxRotation: hbar || line ? 0 : 45,
          callback: function (v) { return short(this.getLabelForValue(v)); } } };
    }
    Chart.defaults.color = css("--muted", "#667085");
    Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;
    charts[key] = new Chart(card.querySelector("canvas"), {
      type: round ? spec.chart : (line ? "line" : "bar"),
      data: { labels: spec.labels, datasets: datasets },
      options: {
        responsive: true, maintainAspectRatio: false, animation: REDUCE ? false : { duration: 350 },
        indexAxis: hbar ? "y" : "x", locale: NL, cutout: spec.chart === "doughnut" ? "60%" : undefined,
        interaction: { mode: round ? "point" : "nearest", intersect: round || !line },
        onClick: function (e, els) {
          if (!els.length) return;
          var url = drillUrl(spec, els[0].index, els[0].datasetIndex);
          if (url) location.href = url;
        },
        onHover: function (e, els) {
          e.native.target.style.cursor = drillable && els.length && drillUrl(spec, els[0].index, els[0].datasetIndex) ? "pointer" : "default";
        },
        plugins: {
          legend: { display: round || spec.series.length > 1, rtl: RTL, textDirection: RTL ? "rtl" : "ltr",
                    position: round ? (narrow ? "bottom" : (RTL ? "left" : "right")) : "top",
                    labels: { boxWidth: 12, usePointStyle: true, padding: 14,
                              generateLabels: round ? function (ch) {
                                var base = Chart.overrides[ch.config.type].plugins.legend.labels.generateLabels(ch);
                                base.forEach(function (it) { it.text = short(it.text); }); return base;
                              } : undefined } },
          tooltip: { rtl: RTL, textDirection: RTL ? "rtl" : "ltr",
                     callbacks: { label: function (ctx) {
                       var v = ctx.raw, n = spec.n && spec.series.length === 1 ? spec.n[ctx.dataIndex] : null;
                       return (spec.series.length > 1 || round ? ctx.dataset.label + ": " : "") + num(v, spec.unit)
                         + (n != null ? "  ·  " + num(n, "") + " " + (L.n || "") : "");
                     } } }
        },
        scales: scales
      }
    });
  }

  function setView(card, view) {
    card.setAttribute("data-view", view);
    card.querySelector(".an-canvas").hidden = view !== "chart";
    card.querySelector(".an-table").hidden = view !== "table";
    card.querySelectorAll(".an-toggle [data-view]").forEach(function (b) {
      var on = b.getAttribute("data-view") === view;
      b.classList.toggle("on", on); b.setAttribute("aria-pressed", on ? "true" : "false");
    });
  }

  function drawAll() {
    document.querySelectorAll("[data-spec]").forEach(function (card) {
      var spec = (DATA.specs || {})[card.getAttribute("data-spec")];
      if (spec) draw(card, spec);
    });
  }

  document.addEventListener("click", function (e) {
    var b = e.target.closest && e.target.closest(".an-toggle [data-view]");
    if (!b) return;
    var card = b.closest("[data-spec]");
    setView(card, b.getAttribute("data-view"));
    var c = charts[card.getAttribute("data-spec")];
    if (c) c.resize();
  });

  /* ---------- filter bar: bottom sheet on phones, auto-apply on desktop ---------- */
  var bar = document.querySelector("[data-an-filterbar]");
  var sheet = bar && bar.querySelector(".an-sheet"), opener = bar && bar.querySelector("[data-an-sheet-open]");
  var backdrop = bar && bar.querySelector(".an-sheet-backdrop");
  function isPhone() { return window.matchMedia("(max-width: 767.98px)").matches; }
  function openSheet(on) {
    if (!sheet) return;
    sheet.classList.toggle("open", on); backdrop.hidden = !on;
    opener.setAttribute("aria-expanded", on ? "true" : "false");
    document.body.classList.toggle("an-lock", on);
    if (on) { var f = sheet.querySelector("input,select"); if (f) f.focus(); } else opener.focus();
  }
  if (bar) {
    opener.addEventListener("click", function () { openSheet(true); });
    bar.querySelectorAll("[data-an-sheet-close]").forEach(function (b) { b.addEventListener("click", function () { openSheet(false); }); });
    document.addEventListener("keydown", function (e) { if (e.key === "Escape" && sheet.classList.contains("open")) openSheet(false); });
    bar.querySelectorAll('input[name="period"]').forEach(function (r) {
      r.addEventListener("change", function () {
        bar.querySelectorAll(".an-custom").forEach(function (c) { c.hidden = r.value !== "custom"; });
      });
    });
  }
  var filters = document.querySelector("[data-an-filters]:not([data-an-builder])");
  if (filters) {
    filters.addEventListener("change", function (e) {
      var t = e.target;
      if (!t.hasAttribute || !t.hasAttribute("data-an-auto") || isPhone()) return;
      if (t.name === "period" && t.value === "custom") return;
      filters.requestSubmit ? filters.requestSubmit() : filters.submit();
    });
  }
  // A clean, shareable URL: empty fields are not sent.
  document.querySelectorAll("form.an-filters").forEach(function (f) {
    f.addEventListener("submit", function () {
      f.querySelectorAll("input[name],select[name]").forEach(function (i) { if (i.value === "" && !i.disabled) i.disabled = true; });
      setTimeout(function () { f.querySelectorAll("[disabled]").forEach(function (i) { i.disabled = false; }); }, 0);
    });
  });

  /* ---------- report builder: live result ---------- */
  var bform = document.querySelector("[data-an-builder]");
  if (bform) {
    var errBox = bform.querySelector("[data-an-error]"), timer = null, seq = 0;
    var resultCard = document.querySelector('[data-spec="result"]');
    var qsInput = document.querySelector("[data-an-qs]");
    var aggSel = bform.querySelector("#b-agg");
    function qs(withRid) {
      var p = new URLSearchParams();
      new FormData(bform).forEach(function (v, k) { if (v !== "" && (withRid || k !== "rid")) p.append(k, v); });
      return p;
    }
    function showErr(msg) { errBox.textContent = msg || ""; errBox.hidden = !msg; }
    function syncAgg() {
      var m = bform.querySelector("#b-m").value, cur = aggSel.value, list = (DATA.aggs || {})[m] || [];
      aggSel.textContent = "";
      list.forEach(function (a) {
        var o = document.createElement("option"); o.value = a; o.textContent = (DATA.aggLabels || {})[a] || a;
        if (a === cur) o.selected = true; aggSel.appendChild(o);
      });
    }
    function run() {
      var m = bform.querySelector("#b-m").value;
      var dims = [bform.querySelector("#b-d1").value, bform.querySelector("#b-d2").value];
      if ((DATA.shareOnly || []).indexOf(m) >= 0 && dims.some(function (d) { return (DATA.lineDims || []).indexOf(d) >= 0; })) {
        showErr(DATA.errLine); return;
      }
      var p = qs(false), mine = ++seq;
      resultCard.classList.add("an-busy");
      fetch(bform.getAttribute("data-run") + "?" + p.toString(), { credentials: "same-origin", headers: { "Accept": "application/json" } })
        .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j }; }); })
        .then(function (res) {
          if (mine !== seq) return;
          resultCard.classList.remove("an-busy");
          if (!res.ok) { showErr(res.j.error || DATA.errRun); return; }
          showErr("");
          res.j.key = "result"; res.j.title = (DATA.specs.result || {}).title;
          DATA.specs.result = res.j;
          resultCard.setAttribute("data-view", res.j.chart === "table" ? "table" : "chart");
          draw(resultCard, res.j);
          var dls = resultCard.querySelectorAll(".an-dl");
          if (dls[0]) dls[0].href = bform.getAttribute("data-xlsx") + "?" + p.toString();
          if (dls[1]) dls[1].href = bform.getAttribute("data-csv") + "?" + p.toString();
          if (qsInput) qsInput.value = p.toString();
          history.replaceState(null, "", location.pathname + "?" + qs(true).toString());
          var act = document.querySelector(".an-active"); if (act) act.hidden = true;
        })
        .catch(function () { if (mine === seq) { resultCard.classList.remove("an-busy"); showErr(DATA.errRun); } });
    }
    bform.addEventListener("change", function (e) {
      if (e.target.id === "b-m") syncAgg();
      if (e.target.name === "period" && e.target.value === "custom") return;
      clearTimeout(timer); timer = setTimeout(run, 120);
    });
    bform.addEventListener("input", function (e) {
      if (e.target.type !== "number") return;
      clearTimeout(timer); timer = setTimeout(run, 400);
    });
    bform.addEventListener("submit", function (e) { e.preventDefault(); openSheet(false); run(); });
  }

  document.querySelectorAll("[data-an-confirm]").forEach(function (f) {
    f.addEventListener("submit", function (e) { if (!window.confirm(f.getAttribute("data-an-confirm"))) e.preventDefault(); });
  });

  drawAll();
  // Theme switch: the palette is read from CSS variables, so redraw.
  new MutationObserver(drawAll).observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
})();
