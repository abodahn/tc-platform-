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
    navOpen: "tc_nav_open",
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

    // Sections render collapsed by default (server-side) except the one holding the
    // current page. Re-open any the user explicitly expanded before, and persist that set.
    // Open-by-exception keeps a 10-section / 60-item nav scannable instead of a wall.
    let opened = [];
    try { opened = JSON.parse(localStorage.getItem(LS.navOpen) || "[]"); } catch (e) {}
    document.querySelectorAll(".nav-section[data-section]").forEach(sec => {
      const key = sec.getAttribute("data-section");
      const hasActive = sec.querySelector(".nav-item.active");
      if (opened.includes(key) && !hasActive) sec.classList.remove("sec-collapsed");
      const label = sec.querySelector("[data-sec-toggle]");
      if (label) label.addEventListener("click", () => {
        if (app.classList.contains("collapsed")) return; // ignore in rail mode
        sec.classList.toggle("sec-collapsed");
        let cur = [];
        try { cur = JSON.parse(localStorage.getItem(LS.navOpen) || "[]"); } catch (e) {}
        const isOpen = !sec.classList.contains("sec-collapsed");
        cur = cur.filter(k => k !== key);
        if (isOpen) cur.push(key);
        localStorage.setItem(LS.navOpen, JSON.stringify(cur));
      });
    });

    // Keep the ACTIVE item visible after a full-page navigation. Without this the
    // sidebar re-renders scrolled to the top, hiding the current item when it's
    // in a lower section (e.g. Admin). Center it in the nav viewport instead.
    const navEl = document.querySelector(".nav");
    const activeItem = document.querySelector(".nav-item.active");
    if (navEl && activeItem) {
      const ar = activeItem.getBoundingClientRect();
      const nr = navEl.getBoundingClientRect();
      const target = navEl.scrollTop + (ar.top - nr.top) - (navEl.clientHeight - activeItem.clientHeight) / 2;
      navEl.scrollTop = Math.max(0, target);
    }

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

  /* ---------------- Live notifications (sound + popup + bell) ---------------- */
  function initLiveNotifications() {
    const bell = document.getElementById("notifBtn");
    const panel = document.getElementById("notifPanel");
    if (!bell || !panel) return;                       // only on authenticated pages
    const KNOWN = ["itsm", "assets", "monitoring", "commandtrack", "production"];
    const listBox = panel.querySelector('div[style*="overflow"]');
    const hrefFor = (m) => KNOWN.indexOf(m) >= 0 ? "/module/" + m : "/";
    const iconOf = (sev) => sev === "critical" ? "alert" : (sev === "warning" ? "bell" : "check");

    // highest notification id already shown on the page
    let lastSeen = 0;
    document.querySelectorAll('#notifPanel .notif[data-nid]').forEach(a => {
      const id = +a.getAttribute("data-nid"); if (id > lastSeen) lastSeen = id;
    });

    // ---- sound: a synthesized chime (no audio file needed, works offline) ----
    const SKEY = "tc_notif_sound";
    let soundOn = localStorage.getItem(SKEY) !== "0";
    let actx = null;
    function arm() { try { actx = actx || new (window.AudioContext || window.webkitAudioContext)(); if (actx.state === "suspended") actx.resume(); } catch (e) {} }
    document.addEventListener("click", function once() { arm(); document.removeEventListener("click", once, true); }, true);
    function chime(sev) {
      if (!soundOn) return; arm(); if (!actx) return;
      try {
        const now = actx.currentTime;
        const tones = sev === "critical" ? [880, 1245, 880, 1245] : (sev === "warning" ? [784, 1047] : [659, 988]);
        tones.forEach((f, i) => {
          const o = actx.createOscillator(), g = actx.createGain();
          o.type = "sine"; o.frequency.value = f; o.connect(g); g.connect(actx.destination);
          const ts = now + i * 0.15;
          g.gain.setValueAtTime(0.0001, ts);
          g.gain.exponentialRampToValueAtTime(0.22, ts + 0.02);
          g.gain.exponentialRampToValueAtTime(0.0001, ts + 0.16);
          o.start(ts); o.stop(ts + 0.18);
        });
      } catch (e) {}
    }
    const soundBtn = document.getElementById("notifSound");
    function paintSound() { if (soundBtn) soundBtn.textContent = soundOn ? "🔔" : "🔕"; }
    paintSound();
    if (soundBtn) soundBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      soundOn = !soundOn; localStorage.setItem(SKEY, soundOn ? "1" : "0"); paintSound();
      if (soundOn) { arm(); chime("info"); toast(t("top.sound_on"), "success"); } else { toast(t("top.sound_off"), ""); }
    });

    // ---- desktop notification (asks permission the first time the bell opens) ----
    bell.addEventListener("click", () => {
      try { if ("Notification" in window && Notification.permission === "default") Notification.requestPermission(); } catch (e) {}
      poll();   // opening the bell pulls the latest immediately
    });
    function desktop(n) {
      try {
        if (!("Notification" in window) || Notification.permission !== "granted") return;
        const dn = new Notification(n.title || "TC Platform", { body: n.message || "", tag: "tc-" + n.id, requireInteraction: n.severity === "critical" });
        dn.onclick = () => { window.focus(); markAndGo(n.id, hrefFor(n.module)); dn.close(); };
      } catch (e) {}
    }

    function markAndGo(id, href) {
      fetch("/notifications/read", { method: "POST", headers: { "Content-Type": "application/json", "X-CSRF-Token": CSRF }, body: JSON.stringify({ id }) })
        .then(() => { window.location.href = href; }, () => { window.location.href = href; });
    }

    // ---- rich slide-in popup ----
    function popup(n) {
      let wrap = document.querySelector(".npops");
      if (!wrap) { wrap = document.createElement("div"); wrap.className = "npops"; document.body.appendChild(wrap); }
      const el = document.createElement("div");
      el.className = "npop sev-" + n.severity;
      el.innerHTML = '<div class="nicon"><svg class="ico" style="width:18px;height:18px"><use href="#i-' + iconOf(n.severity) + '"></use></svg></div>' +
        '<div class="npop-body"><div class="npop-title"></div><div class="npop-msg"></div></div>' +
        '<button class="npop-x" type="button" aria-label="close">&times;</button>';
      el.querySelector(".npop-title").textContent = n.title || "Notification";
      el.querySelector(".npop-msg").textContent = n.message || "";
      el.addEventListener("click", (e) => { if (e.target.closest(".npop-x")) { el.classList.remove("show"); setTimeout(() => el.remove(), 300); return; } markAndGo(n.id, hrefFor(n.module)); });
      wrap.appendChild(el);
      requestAnimationFrame(() => el.classList.add("show"));
      setTimeout(() => { el.classList.remove("show"); setTimeout(() => el.remove(), 350); }, n.severity === "critical" ? 9000 : 6000);
    }

    function prependBell(n) {
      if (!listBox) return;
      const empty = listBox.querySelector(".empty"); if (empty) empty.remove();
      const a = document.createElement("a");
      a.className = "notif sev-" + n.severity;
      a.setAttribute("href", hrefFor(n.module));
      a.setAttribute("data-nid", n.id);
      a.innerHTML = '<div class="nicon"><svg class="ico" style="width:17px;height:17px"><use href="#i-' + iconOf(n.severity) + '"></use></svg></div>' +
        '<div><div class="ntitle"></div><div class="nmsg"></div><div class="ntime"></div></div>' +
        '<span class="ndot" aria-hidden="true"></span>';
      a.querySelector(".ntitle").textContent = n.title || "";
      a.querySelector(".nmsg").textContent = n.message || "";
      a.querySelector(".ntime").textContent = (n.module || "") + " · " + (n.created_at || "");
      a.addEventListener("click", (e) => { e.preventDefault(); markAndGo(n.id, a.getAttribute("href")); });
      listBox.insertBefore(a, listBox.firstChild);
    }

    function setBadge(unread) {
      let badge = bell.querySelector(".badge-count");
      if (unread > 0) {
        if (!badge) { badge = document.createElement("span"); badge.className = "badge-count"; bell.appendChild(badge); }
        badge.textContent = unread < 100 ? String(unread) : "99+";
      } else if (badge) { badge.remove(); }
    }

    async function poll() {
      try {
        const res = await fetch("/notifications/feed", { credentials: "same-origin", headers: { "X-CSRF-Token": CSRF } });
        if (!res.ok) return;
        const data = await res.json();
        setBadge(data.unread);
        if (data.max_id > lastSeen) {
          const fresh = (data.items || []).filter(n => n.id > lastSeen).sort((a, b) => a.id - b.id);
          fresh.forEach(prependBell);                  // ascending insert -> newest ends on top
          if (fresh.length) {
            const top = fresh[fresh.length - 1];
            chime(top.severity);
            bell.classList.add("ring"); setTimeout(() => bell.classList.remove("ring"), 900);
            fresh.forEach(popup);
            fresh.forEach(desktop);
          }
          lastSeen = data.max_id;
        }
      } catch (e) {}
    }
    poll();
    setInterval(poll, 20000);
  }

  /* ---------------- Consolidated business overview ---------------- */
  function initBusinessOverview() {
    const box = document.getElementById("bizOverview");
    if (!box) return;
    const meta = document.getElementById("bizOverviewMeta");
    const esc = (s) => String(s == null ? "" : s).replace(/[&<>"]/g, c => (
      { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
    async function load() {
      try {
        const res = await fetch("/api/overview", { credentials: "same-origin" });
        if (!res.ok) return;
        const data = await res.json();
        const systems = (data.systems || []);
        const online = systems.filter(s => s.online).length;
        if (meta) meta.textContent = online + "/" + systems.length + " " + t("dash.system_health");
        box.innerHTML = systems.map(s => {
          const kpis = (s.kpis || []).map(k => {
            const sev = k.severity === "crit" ? "crit" : (k.severity === "warn" ? "warn" : "");
            return `<div class="ov-kpi ${sev}"><div class="v">${esc(k.value)}</div><div class="l">${esc(k.label)}</div></div>`;
          }).join("");
          return `<div class="ov-sys ${s.online ? "" : "off"}"><div class="ov-h"><span class="ov-dot"></span>${esc(s.name)}</div>`
            + (kpis ? `<div class="ov-kpis">${kpis}</div>` : `<div class="ov-empty">${s.online ? "No data" : "Offline"}</div>`)
            + `</div>`;
        }).join("");
      } catch (e) { /* leave placeholder */ }
    }
    load();
    setInterval(load, 60000);
  }

  /* ---------------- Install as app (PWA) ---------------- */
  function initPwaInstall() {
    const btns = document.querySelectorAll(".pwa-install");
    if (!btns.length) return;
    const hide = () => btns.forEach(b => b.classList.add("pwa-hidden"));
    const standalone = window.matchMedia("(display-mode: standalone)").matches ||
                       window.navigator.standalone === true;  // already installed
    if (standalone) { hide(); return; }   // installed -> nothing to offer

    const ua = navigator.userAgent || "";
    const isIOS = /iphone|ipad|ipod/i.test(ua) ||
                  (/macintosh/i.test(ua) && "ontouchend" in document);  // iPadOS
    let deferred = null;

    // The button is ALWAYS visible (unless installed). If the browser supports
    // one-tap install it captures the event for a direct prompt; otherwise the
    // click explains how to install on that browser.
    window.addEventListener("beforeinstallprompt", (e) => { e.preventDefault(); deferred = e; });
    window.addEventListener("appinstalled", () => { hide(); try { toast(t("pwa.installed"), "success"); } catch (e) {} });

    btns.forEach(btn => btn.addEventListener("click", async () => {
      if (deferred) {
        deferred.prompt();
        try { await deferred.userChoice; } catch (e) {}
        deferred = null; hide();
      } else if (isIOS) {
        showIosInstallSheet();
      } else {
        alert(t("pwa.generic_hint"));
      }
    }));
  }

  function showIosInstallSheet() {
    if (document.querySelector(".ios-sheet-backdrop")) return;
    const shareSvg = '<svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 15V4"/><path d="M8.5 7.5 12 4l3.5 3.5"/><path d="M6 12v6a1 1 0 0 0 1 1h10a1 1 0 0 0 1-1v-6"/></svg>';
    const bd = document.createElement("div");
    bd.className = "ios-sheet-backdrop";
    bd.innerHTML =
      '<div class="ios-sheet" role="dialog" aria-modal="true">' +
        '<button class="ios-sheet-x" aria-label="close" type="button">&times;</button>' +
        '<div class="ios-sheet-emoji">📲</div>' +
        '<h3>' + t("pwa.ios_title") + '</h3>' +
        '<ol class="ios-steps">' +
          '<li><span class="ios-step-ic">' + shareSvg + '</span><span>' + t("pwa.ios_step1") + '</span></li>' +
          '<li><span class="ios-step-ic">+</span><span>' + t("pwa.ios_step2") + '</span></li>' +
          '<li><span class="ios-step-ic">&#10003;</span><span>' + t("pwa.ios_step3") + '</span></li>' +
        '</ol>' +
        '<div class="ios-note">' + t("pwa.ios_safari") + '</div>' +
      '</div>';
    const close = () => bd.remove();
    bd.addEventListener("click", (e) => { if (e.target === bd || e.target.closest(".ios-sheet-x")) close(); });
    document.body.appendChild(bd);
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
    initLiveNotifications();
    initBusinessOverview();
    initPwaInstall();
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

// ---------------- Top loading progress bar ----------------
// Shows on initial page load, same-origin navigation, and form submits. Uses a
// CSS transform animation (see #tcbar in app.css); a safety timeout guarantees it
// never sticks. Exposes window.TCBar.start()/done() for AJAX flows to hook.
(function () {
  var safety = null;
  function bar() { return document.getElementById("tcbar"); }
  function start() {
    var b = bar(); if (!b) return;
    b.classList.remove("done");
    void b.offsetWidth;                 // reflow so the animation restarts
    b.classList.add("run");
    clearTimeout(safety); safety = setTimeout(done, 15000);
  }
  function done() {
    var b = bar(); if (!b) return;
    clearTimeout(safety);
    b.classList.remove("run");
    b.classList.add("done");
    setTimeout(function () { b.classList.remove("done"); }, 650);
  }
  window.TCBar = { start: start, done: done };

  // finish the initial-load bar (started inline in the template)
  if (document.readyState === "complete") done();
  else window.addEventListener("load", done);

  // start on a genuine same-origin navigation
  document.addEventListener("click", function (e) {
    var a = e.target.closest && e.target.closest('a[href]');
    if (!a || e.defaultPrevented || e.button !== 0) return;
    if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    if (a.target === "_blank" || a.hasAttribute("download")) return;
    var href = a.getAttribute("href") || "";
    if (!href || href.charAt(0) === "#") return;
    if (/^(javascript|mailto|tel):/i.test(href)) return;
    try {
      var u = new URL(a.href, location.href);
      if (u.origin !== location.origin) return;               // external link
      if (u.pathname === location.pathname && u.hash) return; // in-page anchor
    } catch (_) { return; }
    start();
  }, true);

  // start on a real (navigating) form submit
  document.addEventListener("submit", function (e) {
    if (!e.defaultPrevented) start();
  }, true);
})();

// ---------------- Sidebar: filter + pinned favorites ----------------
(function () {
  var LS = "tc_nav_pins";
  function load() { try { return JSON.parse(localStorage.getItem(LS) || "[]"); } catch (e) { return []; } }
  function save(v) { try { localStorage.setItem(LS, JSON.stringify(v)); } catch (e) {} }
  function esc(s){ var d=document.createElement('div'); d.textContent=(s==null?'':String(s)); return d.innerHTML; }

  function itemHtml(p) {
    return '<a class="nav-item pinned" href="' + esc(p.href) + '" data-navkey="' + esc(p.key) + '">'
      + '<svg class="ico"><use href="#i-' + esc(p.icon) + '"></use></svg>'
      + '<span class="lbl">' + esc(p.label) + '</span>'
      + '<button type="button" class="nav-star" data-star title="Unpin" aria-label="Unpin"><svg class="ico"><use href="#i-star"></use></svg></button>'
      + '</a>';
  }
  function render() {
    var pins = load();
    var cont = document.getElementById("navPinnedItems");
    var wrap = document.getElementById("navPinned");
    if (cont) cont.innerHTML = pins.map(itemHtml).join("");
    if (wrap) wrap.style.display = pins.length ? "" : "none";
    var keys = pins.map(function (p) { return p.key; });
    document.querySelectorAll(".nav-item[data-navkey]").forEach(function (it) {
      if (it.closest("#navPinned")) return;               // skip the clones
      it.classList.toggle("pinned", keys.indexOf(it.getAttribute("data-navkey")) >= 0);
    });
  }

  function toggle(item) {
    var key = item.getAttribute("data-navkey");
    if (!key) return;
    var pins = load();
    var i = pins.map(function (p) { return p.key; }).indexOf(key);
    if (i >= 0) { pins.splice(i, 1); }
    else {
      var lbl = item.querySelector(".lbl");
      pins.push({ key: key, href: item.getAttribute("href"),
                  icon: item.getAttribute("data-navicon") || "grid",
                  label: lbl ? lbl.textContent.trim() : key });
    }
    save(pins); render();
  }

  document.addEventListener("click", function (e) {
    var star = e.target.closest && e.target.closest("[data-star]");
    if (!star) return;
    e.preventDefault(); e.stopPropagation();
    toggle(star.closest(".nav-item"));
  }, true);

  // Restore each section to its default open/closed state (active or user-opened = open).
  function restoreSections() {
    var opened = [];
    try { opened = JSON.parse(localStorage.getItem("tc_nav_open") || "[]"); } catch (e) {}
    document.querySelectorAll(".nav-section[data-section]").forEach(function (sec) {
      var key = sec.getAttribute("data-section");
      var openIt = !!sec.querySelector(".nav-item.active") || opened.indexOf(key) >= 0;
      sec.classList.toggle("sec-collapsed", !openIt);
    });
  }
  var filter = document.getElementById("navFilter");
  if (filter) filter.addEventListener("input", function () {
    var q = (filter.value || "").toLowerCase().trim();
    if (!q) {
      // Cleared: show everything again and collapse back to the smart default.
      document.querySelectorAll(".nav-section").forEach(function (sec) {
        sec.style.display = "";
        sec.querySelectorAll(".nav-item").forEach(function (it) { it.classList.remove("nav-hidden"); });
      });
      restoreSections();
      return;
    }
    // Filtering: reveal matches and expand the sections that hold them.
    document.querySelectorAll(".nav-section").forEach(function (sec) {
      if (sec.id === "navPinned") return;
      var any = false;
      sec.querySelectorAll(".nav-item").forEach(function (it) {
        var lbl = it.querySelector(".lbl");
        var show = lbl && lbl.textContent.toLowerCase().indexOf(q) >= 0;
        it.classList.toggle("nav-hidden", !show);
        if (show) any = true;
      });
      sec.style.display = any ? "" : "none";
      if (any) sec.classList.remove("sec-collapsed");
    });
  });

  // "/" focuses the menu filter (desktop only; ignored while typing in a field).
  document.addEventListener("keydown", function (e) {
    if (e.key !== "/" || e.ctrlKey || e.metaKey || e.altKey) return;
    if (window.innerWidth <= 900) return;
    var t = e.target, tag = t && t.tagName;
    if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || (t && t.isContentEditable)) return;
    if (filter) { e.preventDefault(); filter.focus(); }
  });

  // pinned labels come from resolved i18n text, so render after translation
  if (document.readyState !== "loading") setTimeout(render, 0);
  else document.addEventListener("DOMContentLoaded", function () { setTimeout(render, 0); });
})();

// ---------------- Command palette (Ctrl / Cmd-K) ----------------
(function () {
  var modal = document.getElementById("cmdk");
  if (!modal) return;
  var input = document.getElementById("cmdkInput");
  var list = document.getElementById("cmdkList");
  var items = [], filtered = [], sel = 0, isOpen = false;
  function esc(s){ var d=document.createElement('div'); d.textContent=(s==null?'':String(s)); return d.innerHTML; }

  var records = [], recSeq = 0, recTimer = null;
  var RICON = { pr: "cart", ticket: "report", machine: "factory", spare: "boxes",
                module: "apps", production: "factory" };

  function build() {
    items = [];
    document.querySelectorAll(".nav .nav-item[data-navkey]").forEach(function (a) {
      if (a.closest("#navPinned")) return;
      var lbl = a.querySelector(".lbl");
      var sec = a.closest(".nav-section");
      var gEl = sec && sec.querySelector(".nav-label .nl-text");
      items.push({ label: lbl ? lbl.textContent.trim() : a.getAttribute("data-navkey"),
                   href: a.getAttribute("href"), icon: a.getAttribute("data-navicon") || "grid",
                   group: gEl ? gEl.textContent.trim() : "" });
    });
  }
  function render() {
    var q = (input.value || "").toLowerCase().trim();
    var navHits = items.filter(function (it) {
      return !q || it.label.toLowerCase().indexOf(q) >= 0 || it.group.toLowerCase().indexOf(q) >= 0;
    });
    filtered = navHits.concat(records);         // pages first, then records
    sel = 0;
    if (!filtered.length) { list.innerHTML = '<div class="cmdk-empty">No matches</div>'; return; }
    list.innerHTML = filtered.map(function (it, i) {
      return '<div class="cmdk-item' + (i === 0 ? " sel" : "") + '" data-i="' + i + '">'
        + '<svg class="ico"><use href="#i-' + esc(it.icon) + '"></use></svg>'
        + '<span>' + esc(it.label) + '</span>'
        + (it.group ? '<span class="k-group">' + esc(it.group) + '</span>' : '')
        + '</div>';
    }).join("");
  }
  function fetchRecords() {
    var q = (input.value || "").trim();
    if (q.length < 2) { records = []; render(); return; }
    var mine = ++recSeq;
    fetch("/search?q=" + encodeURIComponent(q), { credentials: "same-origin" })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (mine !== recSeq) return;            // a newer query already fired
        records = (d.results || []).filter(function (r) { return r.type !== "module"; })
          .map(function (r) { return { label: r.name, href: r.url, group: r.sub || r.type,
                                       icon: RICON[r.type] || "grid" }; });
        render();
      }).catch(function () {});
  }
  function open() { build(); input.value = ""; records = []; render(); modal.classList.add("open"); isOpen = true; setTimeout(function () { input.focus(); }, 30); }
  function close() { modal.classList.remove("open"); isOpen = false; }
  function go(i) { var it = filtered[i]; if (it) { close(); window.location.href = it.href; } }
  function move(d) {
    var els = list.querySelectorAll(".cmdk-item"); if (!els.length) return;
    if (els[sel]) els[sel].classList.remove("sel");
    sel = (sel + d + els.length) % els.length;
    els[sel].classList.add("sel"); els[sel].scrollIntoView({ block: "nearest" });
  }
  document.addEventListener("keydown", function (e) {
    if ((e.metaKey || e.ctrlKey) && (e.key === "k" || e.key === "K")) { e.preventDefault(); isOpen ? close() : open(); return; }
    if (!isOpen) return;
    if (e.key === "Escape") { close(); }
    else if (e.key === "ArrowDown") { e.preventDefault(); move(1); }
    else if (e.key === "ArrowUp") { e.preventDefault(); move(-1); }
    else if (e.key === "Enter") { e.preventDefault(); go(sel); }
  });
  input.addEventListener("input", function () {
    render();                                   // instant nav filtering
    clearTimeout(recTimer); recTimer = setTimeout(fetchRecords, 180);  // debounced records
  });
  list.addEventListener("click", function (e) { var it = e.target.closest(".cmdk-item"); if (it) go(parseInt(it.getAttribute("data-i"), 10)); });
  modal.addEventListener("click", function (e) { if (e.target === modal) close(); });
})();
