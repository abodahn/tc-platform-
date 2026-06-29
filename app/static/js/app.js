/* ============================================================
   TC Platform — front-end controller
   i18n (EN/AR/TR + RTL), theme, sidebar, notifications, search
   ============================================================ */
(function () {
  "use strict";

  const LS = {
    lang: "tc_lang",
    theme: "tc_theme",
    collapsed: "tc_sidebar_collapsed",
    sections: "tc_collapsed_sections",
  };
  const RTL = new Set(["ar"]);
  let DICT = {};
  const cfg = window.TC_CFG || {};
  const CSRF = (document.querySelector('meta[name="csrf-token"]') || {}).content || "";
  let STATIC = cfg.staticBase || "static";
  if (!STATIC.startsWith("/")) STATIC = "/" + STATIC;
  STATIC = STATIC.replace(/\/+$/, "");   // -> "/static" (absolute, no trailing slash)

  /* ---------------- i18n ---------------- */
  function t(key) { return (DICT && DICT[key]) || key; }

  async function loadDict(lang) {
    try {
      const res = await fetch(`${STATIC}/i18n/${lang}.json`, { cache: "no-cache" });
      DICT = await res.json();
    } catch (e) { DICT = {}; }
  }

  function humanizeSlug(s) {
    return s.replace(/_/g, " ").replace(/\b\w/g, c => c.toUpperCase());
  }

  function applyI18n() {
    document.querySelectorAll("[data-i18n]").forEach(el => {
      const k = el.getAttribute("data-i18n");
      let val = t(k);
      // graceful fallback for enum labels (mx.<value>) with no translation
      if (val === k && k.indexOf("mx.") === 0) val = humanizeSlug(k.slice(3));
      if (val) el.textContent = val;
    });
    document.querySelectorAll("[data-i18n-ph]").forEach(el => {
      const k = el.getAttribute("data-i18n-ph");
      const val = t(k);
      if (val) el.setAttribute("placeholder", val);
    });
    document.querySelectorAll("[data-i18n-title]").forEach(el => {
      const v = t(el.getAttribute("data-i18n-title"));
      el.setAttribute("title", v);
      if (el.classList.contains("nav-item")) el.setAttribute("data-tip", v);
    });
    // Dynamic DB content: <el data-loc-en data-loc-ar data-loc-tr>
    const lang = localStorage.getItem(LS.lang) || "en";
    document.querySelectorAll("[data-loc-en]").forEach(el => {
      const v = el.getAttribute("data-loc-" + lang) || el.getAttribute("data-loc-en");
      if (v) el.textContent = v;
    });
  }

  async function setLanguage(lang, persist = true) {
    lang = ["en", "ar", "tr"].includes(lang) ? lang : "en";
    localStorage.setItem(LS.lang, lang);
    document.documentElement.lang = lang;
    document.documentElement.dir = RTL.has(lang) ? "rtl" : "ltr";
    await loadDict(lang);
    applyI18n();
    document.querySelectorAll("[data-lang-btn]").forEach(b =>
      b.classList.toggle("on", b.getAttribute("data-lang-btn") === lang));
    if (persist && cfg.authed) savePrefs({ lang });
  }

  /* ---------------- Theme ---------------- */
  function effectiveTheme(theme) {
    if (theme === "auto") {
      return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
    }
    return theme;
  }
  function setTheme(theme, persist = true) {
    theme = ["light", "dark", "auto"].includes(theme) ? theme : "light";
    localStorage.setItem(LS.theme, theme);
    document.documentElement.setAttribute("data-theme", effectiveTheme(theme));
    document.querySelectorAll("[data-theme-btn]").forEach(b =>
      b.classList.toggle("on", b.getAttribute("data-theme-btn") === theme));
    if (persist && cfg.authed) savePrefs({ theme });
  }

  function savePrefs(payload) {
    fetch("/prefs", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": CSRF },
      body: JSON.stringify(payload),
    }).catch(() => {});
  }

  // Add a hidden _csrf field to every form (covers all POST forms uniformly).
  function injectCsrfIntoForms() {
    document.querySelectorAll("form").forEach(form => {
      const method = (form.getAttribute("method") || "get").toLowerCase();
      if (method === "get") return;
      if (form.querySelector('input[name="_csrf"]')) return;
      const input = document.createElement("input");
      input.type = "hidden"; input.name = "_csrf"; input.value = CSRF;
      form.appendChild(input);
    });
  }

  /* ---------------- Sidebar ---------------- */
  function initSidebar() {
    const app = document.querySelector(".app");
    if (!app) return;

    // Restore rail (collapsed) state
    if (localStorage.getItem(LS.collapsed) === "1") app.classList.add("collapsed");

    function toggleRail() {
      if (window.innerWidth <= 900) {
        app.classList.toggle("mobile-open");
      } else {
        app.classList.toggle("collapsed");
        localStorage.setItem(LS.collapsed, app.classList.contains("collapsed") ? "1" : "0");
      }
    }
    const topToggle = document.getElementById("sidebarToggle");
    const railToggle = document.getElementById("railToggle");
    if (topToggle) topToggle.addEventListener("click", toggleRail);
    if (railToggle) railToggle.addEventListener("click", toggleRail);

    // On mobile, close the drawer when tapping the backdrop or a nav link
    document.addEventListener("click", function (e) {
      if (window.innerWidth > 900 || !app.classList.contains("mobile-open")) return;
      if (e.target.closest("#sidebarToggle, #railToggle")) return;
      if (!e.target.closest(".sidebar") || e.target.closest(".nav-item")) {
        app.classList.remove("mobile-open");
      }
    });

    // Collapsible sections (persisted by section key)
    let collapsed = [];
    try { collapsed = JSON.parse(localStorage.getItem(LS.sections) || "[]"); } catch (e) {}
    document.querySelectorAll(".nav-section").forEach(sec => {
      const key = sec.getAttribute("data-section");
      const hasActive = sec.querySelector(".nav-item.active");
      if (collapsed.includes(key) && !hasActive) sec.classList.add("sec-collapsed");
      const label = sec.querySelector("[data-sec-toggle]");
      if (label) label.addEventListener("click", () => {
        if (app.classList.contains("collapsed")) return; // ignore in rail mode
        sec.classList.toggle("sec-collapsed");
        let cur = [];
        try { cur = JSON.parse(localStorage.getItem(LS.sections) || "[]"); } catch (e) {}
        const on = sec.classList.contains("sec-collapsed");
        cur = cur.filter(k => k !== key);
        if (on) cur.push(key);
        localStorage.setItem(LS.sections, JSON.stringify(cur));
      });
    });

    // Close mobile drawer when a link is tapped
    document.querySelectorAll(".nav-item").forEach(a =>
      a.addEventListener("click", () => {
        if (window.innerWidth <= 900) app.classList.remove("mobile-open");
      }));
  }

  /* ---------------- Popovers (menu / notifications) ---------------- */
  function initPopovers() {
    const toggles = [
      ["userBtn", "userMenu"],
      ["notifBtn", "notifPanel"],
    ];
    toggles.forEach(([btnId, panelId]) => {
      const btn = document.getElementById(btnId);
      const panel = document.getElementById(panelId);
      if (!btn || !panel) return;
      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        document.querySelectorAll(".menu.open,.panel-pop.open").forEach(p => {
          if (p !== panel) p.classList.remove("open");
        });
        panel.classList.toggle("open");
      });
    });
    document.addEventListener("click", () =>
      document.querySelectorAll(".menu.open,.panel-pop.open").forEach(p => p.classList.remove("open")));
    document.querySelectorAll(".menu,.panel-pop").forEach(p =>
      p.addEventListener("click", e => e.stopPropagation()));

    const markRead = document.getElementById("markRead");
    if (markRead) markRead.addEventListener("click", () => {
      fetch("/notifications/read", { method: "POST", headers: { "X-CSRF-Token": CSRF } }).then(() => {
        document.querySelectorAll("#notifBtn .badge-count, #notifBtn .badge-dot").forEach(e => e.remove());
        document.querySelectorAll("#notifPanel .notif").forEach(n => { n.classList.add("is-read"); const d = n.querySelector(".ndot"); if (d) d.remove(); });
        toast(t("top.mark_read"), "success");
      });
    });

    // Click a single notification -> mark just it read, then open its module.
    document.querySelectorAll("#notifPanel .notif[data-nid]").forEach(a => {
      a.addEventListener("click", (e) => {
        const nid = a.getAttribute("data-nid");
        const href = a.getAttribute("href");
        e.preventDefault();
        let navigated = false;
        const go = () => { if (!navigated) { navigated = true; window.location.href = href; } };
        fetch("/notifications/read", {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-CSRF-Token": CSRF },
          body: JSON.stringify({ id: nid })
        }).then(go, go);
        setTimeout(go, 1200); // fallback so navigation never gets stuck on a slow/failed request
      });
    });
  }

  /* ---------------- Keyboard shortcuts ---------------- */
  function initKeyboard() {
    document.addEventListener("keydown", (e) => {
      const tag = (e.target.tagName || "").toLowerCase();
      const typing = tag === "input" || tag === "textarea" || tag === "select" || e.target.isContentEditable;
      if (e.key === "/" && !typing) {
        const s = document.getElementById("globalSearch");
        if (s) { e.preventDefault(); s.focus(); }
      } else if (e.key === "Escape") {
        document.querySelectorAll(".menu.open,.panel-pop.open").forEach(p => p.classList.remove("open"));
        const box = document.getElementById("searchResults");
        if (box) box.style.display = "none";
      }
    });
  }

  /* ---------------- Global search ---------------- */
  function initSearch() {
    const input = document.getElementById("globalSearch");
    const box = document.getElementById("searchResults");
    if (!input || !box) return;
    let timer = null;
    input.addEventListener("input", () => {
      clearTimeout(timer);
      const q = input.value.trim();
      if (!q) { box.style.display = "none"; return; }
      timer = setTimeout(async () => {
        try {
          const res = await fetch(`/search?q=${encodeURIComponent(q)}`);
          const data = await res.json();
          if (!data.results.length) {
            box.innerHTML = `<a><small>${t("common.no_results")}</small></a>`;
          } else {
            box.innerHTML = data.results.map(r =>
              `<a href="${r.url}"><b>${r.name}</b><br><small>${r.sub || ""}</small></a>`
            ).join("");
          }
          box.style.display = "block";
        } catch (e) { box.style.display = "none"; }
      }, 200);
    });
    input.addEventListener("blur", () => setTimeout(() => box.style.display = "none", 180));
  }

  /* ---------------- Animated counters ---------------- */
  function animateCounters() {
    document.querySelectorAll("[data-count]").forEach(el => {
      const target = parseFloat(el.getAttribute("data-count"));
      if (isNaN(target)) return;
      const dur = 900, start = performance.now();
      const suffix = el.getAttribute("data-suffix") || "";
      function step(now) {
        const p = Math.min((now - start) / dur, 1);
        const eased = 1 - Math.pow(1 - p, 3);
        el.textContent = Math.round(target * eased).toLocaleString() + suffix;
        if (p < 1) requestAnimationFrame(step);
      }
      requestAnimationFrame(step);
    });
  }

  /* ---------------- Live status polling ---------------- */
  function initStatusPolling() {
    const nodes = document.querySelectorAll("[data-status-key]");
    if (!nodes.length) return;
    async function poll() {
      try {
        const res = await fetch("/api/status");
        if (!res.ok) return;
        const data = await res.json();
        nodes.forEach(node => {
          const key = node.getAttribute("data-status-key");
          const s = data[key];
          if (!s) return;
          node.className = node.className.replace(/b-(online|offline|warning|unknown)/g, "") + " b-" + s.status;
          const label = node.querySelector("[data-status-label]");
          if (label) label.textContent = t("status." + s.status);
        });
      } catch (e) {}
    }
    setInterval(poll, 20000);
  }

  /* ---------------- Toasts ---------------- */
  function toast(msg, type = "") {
    let wrap = document.querySelector(".toasts");
    if (!wrap) { wrap = document.createElement("div"); wrap.className = "toasts"; document.body.appendChild(wrap); }
    const el = document.createElement("div");
    el.className = "toast " + type;
    el.textContent = msg;
    wrap.appendChild(el);
    setTimeout(() => { el.style.opacity = "0"; setTimeout(() => el.remove(), 300); }, 3200);
  }
  window.tcToast = toast;
  window.tcT = t;                 // translate a key (for dynamic content)
  window.tcApplyI18n = applyI18n; // re-translate after injecting DOM

  /* ---------------- Boot ---------------- */
  document.addEventListener("DOMContentLoaded", async () => {
    const initTheme = cfg.theme || localStorage.getItem(LS.theme) || "light";
    setTheme(initTheme, false);
    window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
      if ((localStorage.getItem(LS.theme) || "light") === "auto") setTheme("auto", false);
    });

    const initLang = cfg.lang || localStorage.getItem(LS.lang) || "en";
    await setLanguage(initLang, false);

    document.querySelectorAll("[data-lang-btn]").forEach(b =>
      b.addEventListener("click", () => setLanguage(b.getAttribute("data-lang-btn"))));
    document.querySelectorAll("[data-theme-btn]").forEach(b =>
      b.addEventListener("click", () => setTheme(b.getAttribute("data-theme-btn"))));

    injectCsrfIntoForms();
    initSidebar();
    initPopovers();
    initSearch();
    initKeyboard();
    initStatusPolling();
    animateCounters();

    // flash messages -> toasts
    (window.TC_FLASH || []).forEach(f => toast(t(f.msg) !== f.msg ? t(f.msg) : f.msg, f.cat));
  });
})();

// Password show/hide eye toggle (adds a clickable eye to every password field)
(function () {
  function addEye(inp) {
    if (inp.dataset.eyeDone) return;
    inp.dataset.eyeDone = "1";
    var wrap = document.createElement("span");
    wrap.style.cssText = "position:relative;display:block;";
    inp.parentNode.insertBefore(wrap, inp);
    wrap.appendChild(inp);
    inp.style.paddingInlineEnd = "42px";
    var btn = document.createElement("button");
    btn.type = "button";
    btn.tabIndex = -1;
    btn.setAttribute("aria-label", "Show or hide password");
    btn.title = "Show / hide password";
    btn.innerHTML = "👁"; // eye
    btn.style.cssText = "position:absolute;top:50%;inset-inline-end:10px;transform:translateY(-50%);" +
      "background:none;border:0;cursor:pointer;font-size:17px;line-height:1;opacity:.55;padding:4px;color:inherit;";
    btn.addEventListener("click", function () {
      var hidden = inp.type === "password";
      inp.type = hidden ? "text" : "password";
      btn.style.opacity = hidden ? "1" : ".55";
    });
    wrap.appendChild(btn);
  }
  function run() { document.querySelectorAll('input[type="password"]').forEach(addEye); }
  if (document.readyState !== "loading") run();
  else document.addEventListener("DOMContentLoaded", run);
})();
