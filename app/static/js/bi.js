/* TC Platform — BI dashboard interactivity (Chart.js, vanilla JS). */
(function () {
  "use strict";
  var boot = JSON.parse(document.getElementById("bi-boot").textContent);
  var DS = boot.dataset.id;
  var SPEC = (boot.saved && boot.saved.spec) ? boot.saved.spec : boot.dashboard;
  var COLUMNS = boot.columns || [];
  var SAVED_ID = boot.saved ? boot.saved.id : null;
  var CSRF = document.querySelector('meta[name="csrf-token"]').content;
  var FILTERS = [];
  var QSEQ = 0;              // monotonic counter for collision-free search-chart ids
  var CHARTS = {};           // cid -> Chart instance
  var PALETTE = ["#378ADD", "#1D9E75", "#D85A30", "#7F77DD", "#BA7517", "#C0507E", "#4CA3A3", "#888780"];
  var cs = getComputedStyle(document.documentElement);
  var AXIS = (cs.getPropertyValue("--muted") || "#8a8a8a").trim();
  var GRID = "rgba(130,130,140,0.15)";
  var LANG = (document.documentElement.getAttribute("lang") || "en").slice(0, 2);
  if (["en", "ar", "tr"].indexOf(LANG) < 0) LANG = "en";

  function post(url, body) {
    return fetch(url, { method: "POST", headers: { "X-CSRF-Token": CSRF, "Content-Type": "application/json" },
      body: JSON.stringify(body || {}) });
  }
  function esc(s) { var d = document.createElement("div"); d.textContent = s == null ? "" : String(s); return d.innerHTML; }
  function fmt(v, format) {
    if (v == null || isNaN(v)) return "—";
    if (format === "percent") return (Math.round(v * 10) / 10) + "%";
    var abs = Math.abs(v);
    if (abs >= 1e6) return (v / 1e6).toFixed(1) + "M";
    if (abs >= 1e3) return Math.round(v).toLocaleString();
    return (v === Math.round(v)) ? String(Math.round(v)) : v.toFixed(1);
  }

  /* ---------- KPIs ---------- */
  function renderKpis(values) {
    var host = document.getElementById("biKpis");
    var byId = {};
    (values || []).forEach(function (k) { byId[k.id] = k.value; });
    host.innerHTML = (SPEC.kpis || []).map(function (k) {
      var val = (k.id in byId) ? byId[k.id] : k.value;
      return '<div class="bi-kpi"><div class="lbl">' + esc(k.label) + '</div>' +
             '<div class="val">' + fmt(val, k.format) + '</div></div>';
    }).join("");
  }
  function refreshKpis() {
    post("/bi/api/dataset/" + DS + "/kpis", { kpis: SPEC.kpis, filters: FILTERS })
      .then(function (r) { return r.json(); }).then(function (j) { renderKpis(j.kpis); })
      .catch(function () {});
  }

  /* ---------- insights ---------- */
  function renderInsights() {
    fetch("/bi/api/dataset/" + DS + "/insights?lang=" + LANG)
      .then(function (r) { return r.json(); }).then(function (j) {
        var host = document.getElementById("biInsights");
        if (!j.insights || !j.insights.length) { host.innerHTML = '<div class="bi-none">No insights.</div>'; return; }
        host.innerHTML = j.insights.map(function (i) {
          return '<div class="row"><svg class="ico"><use href="#i-' + esc(iconFor(i.icon)) + '"></use></svg><span>' + esc(i.text) + "</span></div>";
        }).join("");
      })
      .catch(function () {
        var h = document.getElementById("biInsights");
        if (h) h.innerHTML = '<div class="bi-none">Insights unavailable right now.</div>';
      });
  }
  function iconFor(name) {  // map insight icon names to the platform sprite (safe fallbacks)
    var m = { "database": "chart", "trending-up": "activity", "trending-down": "activity",
      "arrow-right": "target", "flame": "target", "alert-triangle": "alert", "crown": "check",
      "chart-pie": "chart", "link": "external", "alert-circle": "alert" };
    return m[name] || "sparkles";
  }

  /* ---------- charts ---------- */
  function chartCardHtml(spec) {
    return '<div class="panel bi-chart" data-cid="' + esc(spec.id) + '">' +
      '<div class="panel-head flex between center"><h3 style="font-size:15px">' + esc(spec.title || "Chart") + "</h3>" +
      '<div class="flex gap center"><button class="btn btn-ghost btn-sm" data-png="' + esc(spec.id) + '">PNG</button>' +
      '<button class="btn btn-ghost btn-sm" data-rm="' + esc(spec.id) + '" aria-label="Remove">✕</button></div></div>' +
      '<div class="panel-pad"><div class="wrap"><canvas></canvas></div></div></div>';
  }
  function renderCharts() {
    var host = document.getElementById("biCharts");
    host.innerHTML = (SPEC.charts || []).map(chartCardHtml).join("");
    (SPEC.charts || []).forEach(loadChart);
    host.querySelectorAll("[data-rm]").forEach(function (b) {
      b.addEventListener("click", function () { removeChart(b.getAttribute("data-rm")); });
    });
    host.querySelectorAll("[data-png]").forEach(function (b) {
      b.addEventListener("click", function () { downloadPng(b.getAttribute("data-png")); });
    });
  }
  function loadChart(spec) {
    post("/bi/api/dataset/" + DS + "/chart", { spec: spec, filters: FILTERS })
      .then(function (r) { return r.json(); })
      .then(function (data) { drawChart(spec, data); })
      .catch(function () {});
  }
  function drawChart(spec, data) {
    var card = document.querySelector('.bi-chart[data-cid="' + spec.id + '"]');
    if (!card) return;
    var canvas = card.querySelector("canvas");
    if (CHARTS[spec.id]) { CHARTS[spec.id].destroy(); }
    var kind = data.kind || spec.type;
    var cfg;
    if (kind === "scatter") cfg = scatterCfg(spec, data);
    else if (kind === "line") cfg = lineCfg(spec, data);
    else if (kind === "pie" || kind === "doughnut") cfg = pieCfg(spec, data, kind);
    else cfg = barCfg(spec, data);
    CHARTS[spec.id] = new Chart(canvas.getContext("2d"), cfg);
  }

  function baseScales() {
    return { x: { grid: { color: GRID }, ticks: { color: AXIS } },
             y: { grid: { color: GRID }, ticks: { color: AXIS }, beginAtZero: true } };
  }
  function lineCfg(spec, data) {
    var labels = (data.labels || []).slice();
    var d0 = (data.datasets && data.datasets[0]) || {};
    var vals = (d0.data || []).slice();
    var anom = {}; (data.anomalies || []).forEach(function (a) { anom[a.index] = true; });
    var ptColors = vals.map(function (_, i) { return anom[i] ? "#E24B4A" : "#378ADD"; });
    var ptRadius = vals.map(function (_, i) { return anom[i] ? 5 : 2; });
    var ds = [{ label: d0.label || "", data: vals, borderColor: "#378ADD",
      backgroundColor: "#378ADD", tension: 0.35, pointRadius: ptRadius,
      pointBackgroundColor: ptColors, borderWidth: 2, fill: false }];
    if (vals.length && data.forecast && data.forecast.points) {
      var pad = vals.map(function () { return null; });
      var fpts = data.forecast.points;
      for (var k = 0; k < fpts.length; k++) { labels.push("→ +" + (k + 1)); }
      // connect: last real point then forecast
      var fdata = pad.slice();
      fdata[vals.length - 1] = vals[vals.length - 1];
      ds.push({ label: "Forecast", data: fdata.concat(fpts), borderColor: "#1D9E75",
        borderDash: [6, 5], pointRadius: 0, borderWidth: 2, fill: false });
    }
    return { type: "line", data: { labels: labels, datasets: ds },
      options: { maintainAspectRatio: false, plugins: { legend: legend(data) }, scales: baseScales() } };
  }
  function barCfg(spec, data) {
    var vals = data.datasets && data.datasets[0] ? data.datasets[0].data : [];
    return { type: "bar", data: { labels: data.labels || [],
        datasets: [{ label: (data.datasets[0] || {}).label || "", data: vals, backgroundColor: "#378ADD" }] },
      options: { maintainAspectRatio: false, plugins: { legend: { display: false } },
        scales: baseScales(), onClick: clickHandler(spec, data) } };
  }
  function pieCfg(spec, data, kind) {
    var vals = data.datasets && data.datasets[0] ? data.datasets[0].data : [];
    return { type: kind, data: { labels: data.labels || [],
        datasets: [{ data: vals, backgroundColor: PALETTE, borderWidth: 0 }] },
      options: { maintainAspectRatio: false, cutout: kind === "doughnut" ? "60%" : 0,
        plugins: { legend: { position: "right", labels: { color: AXIS, boxWidth: 10, usePointStyle: true, font: { size: 12 } } } },
        onClick: clickHandler(spec, data) } };
  }
  function scatterCfg(spec, data) {
    return { type: "scatter", data: { datasets: [{ label: spec.title, data: data.points || [],
        backgroundColor: "#7F77DD" }] },
      options: { maintainAspectRatio: false, plugins: { legend: { display: false } },
        scales: { x: { title: { display: true, text: data.x, color: AXIS }, grid: { color: GRID }, ticks: { color: AXIS } },
                  y: { title: { display: true, text: data.y, color: AXIS }, grid: { color: GRID }, ticks: { color: AXIS } } } } };
  }
  function legend(data) { return { display: (data.datasets || []).length > 1, position: "bottom",
    labels: { color: AXIS, boxWidth: 10, usePointStyle: true } }; }

  /* ---------- cross-filter drill-down ---------- */
  function clickHandler(spec, data) {
    return function (evt, els) {
      if (!els || !els.length || data.is_date) return;   // dates aren't useful filters
      var label = (data.labels || [])[els[0].index];
      if (label == null || label === "Other" || label === "(blank)") return;
      addFilter(spec.dim, label);
    };
  }
  function addFilter(column, value) {
    if (!column) return;
    if (FILTERS.some(function (f) { return f.column === column && String(f.value) === String(value); })) return;
    FILTERS.push({ column: column, op: "=", value: value });
    refresh();
  }
  function removeFilter(i) { FILTERS.splice(i, 1); refresh(); }
  function renderChips() {
    var bar = document.getElementById("biFilterBar"), host = document.getElementById("biChips");
    if (!FILTERS.length) { bar.style.display = "none"; host.innerHTML = ""; return; }
    bar.style.display = "";
    host.innerHTML = FILTERS.map(function (f, i) {
      return '<span class="bi-chip">' + esc(f.column) + " = " + esc(f.value) +
        ' <button data-fi="' + i + '">✕</button></span>';
    }).join("");
    host.querySelectorAll("[data-fi]").forEach(function (b) {
      b.addEventListener("click", function () { removeFilter(parseInt(b.getAttribute("data-fi"), 10)); });
    });
  }
  function refresh() { renderChips(); refreshKpis(); (SPEC.charts || []).forEach(loadChart); }

  /* ---------- smart search ---------- */
  document.getElementById("biBtnSearch").addEventListener("click", function () {
    var p = document.getElementById("biSearchPanel");
    p.style.display = p.style.display === "none" ? "" : "none";
    if (p.style.display === "") document.getElementById("biSearchInput").focus();
  });
  document.getElementById("biSearchForm").addEventListener("submit", function (e) {
    e.preventDefault();
    var q = document.getElementById("biSearchInput").value.trim();
    var msg = document.getElementById("biSearchMsg");
    if (!q) return;
    post("/bi/api/dataset/" + DS + "/query", { q: q, filters: FILTERS })
      .then(function (r) { return r.json(); }).then(function (j) {
        if (!j.matched) { msg.style.display = ""; msg.textContent = "Couldn't map that to a chart. Try 'top 10 <thing> by <number>'."; return; }
        msg.style.display = "none";
        j.spec.id = "q" + Date.now() + "-" + (QSEQ++);
        SPEC.charts.unshift(j.spec);
        renderCharts();
      })
      .catch(function () { msg.style.display = ""; msg.textContent = "Search failed — please retry."; });
  });

  /* ---------- save / png / remove ---------- */
  document.getElementById("biBtnSave").addEventListener("click", function () {
    var name = document.getElementById("biName").textContent.trim() || "Dashboard";
    post("/bi/dashboard/save", { dataset_id: DS, name: name, spec: SPEC, lang: LANG, dash_id: SAVED_ID })
      .then(function (r) { return r.json(); }).then(function (j) {
        if (j.id) { SAVED_ID = j.id; toast("Saved."); if (!boot.saved) history.replaceState(null, "", j.redirect); }
        else { toast(j.error || "Save failed."); }
      })
      .catch(function () { toast("Save failed — please retry."); });
  });
  function removeChart(cid) {
    SPEC.charts = SPEC.charts.filter(function (c) { return c.id !== cid; });
    if (CHARTS[cid]) { CHARTS[cid].destroy(); delete CHARTS[cid]; }
    var card = document.querySelector('.bi-chart[data-cid="' + cid + '"]'); if (card) card.remove();
  }
  function downloadPng(cid) {
    var ch = CHARTS[cid]; if (!ch) return;
    var a = document.createElement("a"); a.href = ch.toBase64Image("image/png", 1); a.download = cid + ".png"; a.click();
  }

  /* ---------- alert + digest forms ---------- */
  document.getElementById("biBtnAlert").addEventListener("click", function () {
    document.getElementById("biAlertForm").classList.toggle("open");
  });
  document.getElementById("biBtnDigest").addEventListener("click", function () {
    document.getElementById("biDigestForm").classList.toggle("open");
  });
  (function fillAlertCols() {
    var sel = document.getElementById("alCol");
    COLUMNS.filter(function (c) { return c.type === "number"; }).forEach(function (c) {
      var o = document.createElement("option"); o.value = c.name; o.textContent = c.name; sel.appendChild(o);
    });
  })();
  document.getElementById("alSave").addEventListener("click", function () {
    var body = { dataset_id: DS, column_name: document.getElementById("alCol").value,
      agg: document.getElementById("alAgg").value, op: document.getElementById("alOp").value,
      threshold: parseFloat(document.getElementById("alVal").value || "0"),
      name: document.getElementById("alCol").value };
    post("/bi/alert", body).then(function (r) { return r.json(); }).then(function (j) {
      document.getElementById("alMsg").textContent = j.ok ? "Alert created — it will fire into the bell." : (j.error || "Failed.");
    }).catch(function () { document.getElementById("alMsg").textContent = "Failed — please retry."; });
  });
  document.getElementById("dgSave").addEventListener("click", function () { saveDigest(false); });
  document.getElementById("dgSend").addEventListener("click", function () { saveDigest(true); });
  function saveDigest(sendNow) {
    var msg = document.getElementById("dgMsg");
    if (!SAVED_ID) { msg.textContent = "Save the dashboard first."; return; }
    post("/bi/digest", { dashboard_id: SAVED_ID, recipients: document.getElementById("dgTo").value,
      cadence: document.getElementById("dgCad").value })
      .then(function (r) { return r.json(); }).then(function (j) {
        if (!j.id) { msg.textContent = j.error || "Failed."; return; }
        if (sendNow) {
          post("/bi/digest/" + j.id + "/send", {}).then(function (r) { return r.json(); }).then(function (s) {
            msg.textContent = s.ok ? "Digest scheduled and sent." : (s.configured ? "Scheduled. Send failed." : "Scheduled. (Email not configured yet.)");
          }).catch(function () { msg.textContent = "Scheduled. Send failed — please retry."; });
        } else { msg.textContent = "Digest scheduled."; }
      })
      .catch(function () { msg.textContent = "Failed — please retry."; });
  }

  document.getElementById("biClearFilters").addEventListener("click", function () { FILTERS = []; refresh(); });

  function toast(t) {
    var el = document.createElement("div");
    el.textContent = t;
    el.style.cssText = "position:fixed;bottom:22px;left:50%;transform:translateX(-50%);" +
      "background:var(--tc-red,#c8102e);color:#fff;padding:10px 18px;border-radius:10px;" +
      "font-size:14px;z-index:9999;box-shadow:0 4px 16px rgba(0,0,0,.2);opacity:0;transition:opacity .2s";
    document.body.appendChild(el);
    requestAnimationFrame(function () { el.style.opacity = "1"; });
    setTimeout(function () { el.style.opacity = "0"; setTimeout(function () { el.remove(); }, 250); }, 2200);
  }

  /* ---------- go ---------- */
  renderKpis();
  renderInsights();
  renderCharts();
})();
