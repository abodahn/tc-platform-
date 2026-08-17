/* ============================================================
   TC Platform — searchable combobox (progressive enhancement)

   Every <select> with more than 8 options, plus anything carrying data-search,
   gets a type-to-filter field with a keyboard-driven listbox.

   THE ORIGINAL <select> STAYS IN THE DOM and stays the form control. It is
   visually hidden but NOT display:none (required selects must still be
   focusable for constraint validation) and its value is kept in sync, so every
   existing POST handler, every onchange="this.form.submit()", and every page
   script that reads the select keeps working with zero template changes.

   Markup hooks
     data-no-search        never enhance this select
     data-search           always enhance, whatever the option count
     data-src="/api/..."   fetch matches from the server instead of filtering
                           the DOM options (?q=…, see app/routes/lookup.py)
     data-hint="…"         on an <option>: secondary text shown after the label

   Public API: window.tcCombobox.scan(root) — enhance selects inside root.
   ============================================================ */
(function () {
  "use strict";

  var MIN_OPTIONS = 8;      // below this a native select is genuinely fine
  var MAX_NODES = 200;      // never build more option rows than this in one go
  var DEBOUNCE = 180;       // ms before a server lookup fires
  var seq = 0;
  var open = null;          // the one open instance, or null

  function t(key, fallback) {
    var fn = window.tcT;
    var val = fn ? fn(key) : key;
    return (val && val !== key) ? val : fallback;
  }

  /* ---------- normalisation & ranking ---------- */

  // Combining marks: Latin diacritics, Arabic tashkeel, hamza, dagger alef.
  var MARKS = /[̀-ًͯ-ٰٕ]/g;

  function norm(s) {
    s = (s == null ? "" : String(s));
    if (s.normalize) s = s.normalize("NFD");
    return s.replace(MARKS, "")
            .replace(/[أإآٱ]/g, "ا")   // alef variants
            .replace(/ى/g, "ي")                          // alef maksura -> ya
            .replace(/ة/g, "ه")                          // ta marbuta -> ha
            .toLowerCase().replace(/\s+/g, " ").trim();
  }

  var WORDCHAR = /[a-z0-9؀-ۿ]/;

  // 0 exact, 1 prefix, 2 starts a word (so "6204" finds "Bearing 6204"),
  // 3 anywhere, -1 no match.
  function rank(hay, needle) {
    if (!needle) return 0;
    var i = hay.indexOf(needle);
    if (i < 0) return -1;
    if (i === 0) return hay.length === needle.length ? 0 : 1;
    return WORDCHAR.test(hay.charAt(i - 1)) ? 3 : 2;
  }

  /* ---------- the one shared popup ---------- */

  var pop, live, rafPending = false;

  function popup() {
    if (pop) return pop;
    pop = document.createElement("div");
    pop.className = "tc-cb-pop";
    pop.id = "tc-cb-pop";
    pop.setAttribute("role", "listbox");
    pop.hidden = true;
    document.body.appendChild(pop);

    live = document.createElement("div");
    live.className = "tc-cb-live";
    live.setAttribute("aria-live", "polite");
    live.setAttribute("role", "status");
    document.body.appendChild(live);

    // Keeps focus in the input: without this, mousedown blurs the field and the
    // list closes before the click ever lands on an option.
    pop.addEventListener("mousedown", function (e) { e.preventDefault(); });
    pop.addEventListener("click", function (e) {
      var row = e.target.closest ? e.target.closest(".tc-cb-opt") : null;
      if (row && open) open.commit(row);
    });
    return pop;
  }

  function reposition() {
    if (rafPending || !open) return;
    rafPending = true;
    requestAnimationFrame(function () { rafPending = false; if (open) open.place(); });
  }

  addEventListener("scroll", reposition, true);
  addEventListener("resize", reposition);

  /* ---------- instance ---------- */

  function build(sel, box) {
    var id = "tc-cb-" + (++seq);
    var inst = { sel: sel, items: null, matches: [], active: -1, req: null, timer: null };

    var wrap = document.createElement("div");
    wrap.className = "tc-cb" + (box.inline ? " is-inline" : "");
    // A select measured at zero width is inside something hidden (a modal, a
    // collapsed panel); fall back to fluid rather than freezing 0px in place.
    wrap.style.width = (box.full || !box.w) ? "100%" : box.w + "px";

    var input = document.createElement("input");
    input.type = "text";
    input.className = "tc-cb-input";
    input.id = id;
    input.autocomplete = "off";
    input.spellcheck = false;
    input.setAttribute("role", "combobox");
    input.setAttribute("aria-expanded", "false");
    input.setAttribute("aria-autocomplete", "list");
    input.setAttribute("aria-controls", "tc-cb-pop");
    input.setAttribute("data-i18n-ph", "cb.search");
    input.placeholder = t("cb.search", "Search…");
    if (box.h) input.style.height = box.h + "px";
    if (box.fs) input.style.fontSize = box.fs;
    if (sel.disabled) input.disabled = true;
    if (sel.id) input.setAttribute("aria-labelledby", labelFor(sel));

    var clear = document.createElement("button");
    clear.type = "button";
    clear.className = "tc-cb-clear";
    clear.tabIndex = -1;
    clear.setAttribute("data-i18n-title", "cb.clear");
    clear.title = t("cb.clear", "Clear");
    clear.setAttribute("aria-label", t("cb.clear", "Clear"));
    clear.textContent = "×";

    var caret = document.createElement("span");
    caret.className = "tc-cb-caret";
    caret.setAttribute("aria-hidden", "true");

    sel.parentNode.insertBefore(wrap, sel);
    wrap.appendChild(sel);
    wrap.appendChild(input);
    wrap.appendChild(caret);
    wrap.appendChild(clear);
    sel.classList.add("tc-cb-native");
    sel.setAttribute("tabindex", "-1");
    sel.dataset.tcCb = id;

    inst.wrap = wrap; inst.input = input; inst.clear = clear;
    Object.assign(inst, api);
    inst.syncFromSelect();

    input.addEventListener("focus", function () { inst.openList(true); });
    input.addEventListener("mousedown", function () {
      if (open === inst) { inst.close(true); } else { setTimeout(function () { inst.openList(true); }, 0); }
    });
    input.addEventListener("input", function () { inst.filter(input.value); });
    input.addEventListener("keydown", function (e) { inst.key(e); });
    input.addEventListener("blur", function () {
      // The popup swallows its own mousedown, so a blur here is always real.
      if (open === inst) inst.close(true); else inst.syncFromSelect();
    });
    clear.addEventListener("click", function () {
      inst.setValue("", "");
      input.focus();
    });
    // Something else changed the select (a page script, a reset, back/forward).
    sel.addEventListener("change", function () {
      if (open !== inst) { inst.items = null; inst.syncFromSelect(); }
    });

    return inst;
  }

  function labelFor(sel) {
    var lbl = sel.closest(".field") ? sel.closest(".field").querySelector("label") : null;
    if (!lbl) return "";
    if (!lbl.id) lbl.id = "tc-cb-lbl-" + (++seq);
    return lbl.id;
  }

  var api = {

    /* --- option cache (local mode) --- */
    read: function () {
      if (this.items) return this.items;
      var out = [];
      for (var i = 0; i < this.sel.options.length; i++) {
        var o = this.sel.options[i];
        var label = (o.textContent || "").replace(/\s+/g, " ").trim();
        var hint = o.getAttribute("data-hint") || "";
        out.push({ value: o.value, label: label, hint: hint, disabled: o.disabled,
                   n: norm(label + " " + hint + " " + o.value) });
      }
      this.items = out;
      return out;
    },

    label: function () {
      var o = this.sel.selectedIndex >= 0 ? this.sel.options[this.sel.selectedIndex] : null;
      return o ? (o.textContent || "").replace(/\s+/g, " ").trim() : "";
    },

    hasEmpty: function () {
      for (var i = 0; i < this.sel.options.length; i++) {
        if (this.sel.options[i].value === "") return true;
      }
      return false;
    },

    syncFromSelect: function () {
      var lbl = this.label();
      this.input.value = (this.sel.value === "" && /^[\s—–-]*$/.test(lbl)) ? "" : lbl;
      this.clear.hidden = !(this.hasEmpty() && this.sel.value !== "");
    },

    /** Set the select's value (creating the option if a server result brought a
     *  value the DOM has never seen) and tell the page about it. */
    setValue: function (value, label, data) {
      var sel = this.sel;
      sel.value = value;
      if (sel.value !== value && value !== "") {
        var o = document.createElement("option");
        o.value = value;
        o.textContent = label || value;
        if (data) { for (var k in data) { if (data[k] != null) o.dataset[k] = data[k]; } }
        sel.appendChild(o);
        sel.value = value;
        this.items = null;
      }
      this.syncFromSelect();
      sel.dispatchEvent(new Event("input", { bubbles: true }));
      sel.dispatchEvent(new Event("change", { bubbles: true }));
    },

    /* --- open / close --- */
    openList: function (selectAll) {
      if (this.sel.disabled || open === this) return;
      if (open) open.close(true);
      open = this;
      // A page script may have changed the select since this instance last
      // looked — a new value, or options appended after enhancement. Re-read
      // instead of trusting the cache; it is one pass over the options.
      this.items = null;
      this.syncFromSelect();
      popup().hidden = false;
      this.input.setAttribute("aria-expanded", "true");
      this.wrap.classList.add("is-open");
      if (selectAll) this.input.select();
      this.filter("");
      this.place();
    },

    close: function (restore) {
      if (open !== this) return;
      open = null;
      if (this.req) { this.req.abort(); this.req = null; }
      clearTimeout(this.timer);
      pop.hidden = true;
      pop.textContent = "";
      this.input.setAttribute("aria-expanded", "false");
      this.input.removeAttribute("aria-activedescendant");
      this.wrap.classList.remove("is-open");
      this.active = -1;
      if (restore) this.syncFromSelect();
    },

    /* --- positioning: fixed to the viewport so no ancestor with
       overflow:hidden/auto (panels, table wrappers, modals) can clip it --- */
    place: function () {
      var r = this.input.getBoundingClientRect();
      var vw = document.documentElement.clientWidth;
      var vh = window.innerHeight;
      var w = Math.min(Math.max(r.width, 240), vw - 16);
      var below = vh - r.bottom - 8, above = r.top - 8;
      var up = below < 180 && above > below;
      var h = Math.min(340, Math.max(120, up ? above : below));
      // Physical left/top on purpose: these are viewport coordinates read from
      // getBoundingClientRect, which is already direction-aware. Logical
      // properties would re-mirror them a second time under RTL.
      pop.style.width = w + "px";
      pop.style.maxHeight = h + "px";
      pop.style.left = Math.round(Math.max(8, Math.min(r.left, vw - w - 8))) + "px";
      pop.style.top = Math.round(up ? (r.top - h - 4) : (r.bottom + 4)) + "px";
      pop.classList.toggle("is-up", up);
    },

    /* --- filtering --- */
    filter: function (q) {
      if (this.sel.dataset.src) return this.remote(q);
      this.render(this.match(this.read(), q), q);
    },

    match: function (items, q) {
      var n = norm(q), out = [];
      for (var i = 0; i < items.length; i++) {
        var it = items[i];
        if (it.disabled) continue;
        var r = rank(it.n, n);
        if (r >= 0) out.push({ it: it, r: r, i: i });
      }
      out.sort(function (a, b) { return a.r - b.r || a.i - b.i; });
      return out.map(function (x) { return x.it; });
    },

    remote: function (q) {
      var self = this;
      clearTimeout(this.timer);
      if (this.req) { this.req.abort(); this.req = null; }
      this.msg("cb.loading", "Loading…");
      this.timer = setTimeout(function () {
        var ctrl = new AbortController();
        self.req = ctrl;
        var url = self.sel.dataset.src;
        url += (url.indexOf("?") < 0 ? "?" : "&") + "q=" + encodeURIComponent(q || "");
        fetch(url, { credentials: "same-origin", signal: ctrl.signal })
          .then(function (res) { if (!res.ok) throw 0; return res.json(); })
          .then(function (d) {
            if (self.req !== ctrl || open !== self) return;   // a newer request won
            self.req = null;
            self.render((d.results || []).map(function (r) {
              return { value: String(r.value), label: r.label || String(r.value),
                       hint: r.hint || "", data: r.data || null };
            }), q);
          })
          .catch(function (err) {
            if (err && err.name === "AbortError") return;
            if (self.req !== ctrl || open !== self) return;
            self.req = null;
            // Offline or the endpoint is down: fall back to whatever options the
            // page already rendered rather than showing an empty picker.
            self.render(self.match(self.read(), q), q);
          });
      }, q ? DEBOUNCE : 0);
    },

    msg: function (key, fallback) {
      pop.textContent = "";
      var d = document.createElement("div");
      d.className = "tc-cb-msg";
      d.setAttribute("data-i18n", key);
      d.textContent = t(key, fallback);
      pop.appendChild(d);
    },

    /* --- rendering --- */
    render: function (list, q) {
      this.matches = list.slice(0, MAX_NODES);
      pop.textContent = "";
      if (!this.matches.length) {
        this.msg("cb.no_results", "No matches");
        live.textContent = t("cb.no_results", "No matches");
        this.active = -1;
        this.input.removeAttribute("aria-activedescendant");
        return;
      }
      var frag = document.createDocumentFragment();
      var cur = this.sel.value;
      for (var i = 0; i < this.matches.length; i++) {
        var m = this.matches[i];
        var row = document.createElement("div");
        row.className = "tc-cb-opt";
        row.id = "tc-cb-o" + i;
        row.setAttribute("role", "option");
        row.setAttribute("aria-selected", m.value === cur ? "true" : "false");
        row.dataset.idx = i;
        if (m.value === cur) row.classList.add("is-current");
        row.appendChild(mark(m.label, q));
        if (m.hint) {
          var h = document.createElement("span");
          h.className = "tc-cb-hint";
          h.textContent = m.hint;
          row.appendChild(h);
        }
        frag.appendChild(row);
      }
      pop.appendChild(frag);
      if (list.length > MAX_NODES) {
        var more = document.createElement("div");
        more.className = "tc-cb-msg";
        more.textContent = t("cb.more", "Keep typing to narrow this down…");
        more.setAttribute("data-i18n", "cb.more");
        pop.appendChild(more);
      }
      live.textContent = this.matches.length + " " + t("cb.results", "results");
      this.setActive(0);
    },

    setActive: function (i) {
      var rows = pop.querySelectorAll(".tc-cb-opt");
      if (!rows.length) return;
      i = Math.max(0, Math.min(i, rows.length - 1));
      if (this.active >= 0 && rows[this.active]) rows[this.active].classList.remove("is-active");
      this.active = i;
      rows[i].classList.add("is-active");
      this.input.setAttribute("aria-activedescendant", rows[i].id);
      var r = rows[i], top = r.offsetTop, bot = top + r.offsetHeight;
      if (top < pop.scrollTop) pop.scrollTop = top;
      else if (bot > pop.scrollTop + pop.clientHeight) pop.scrollTop = bot - pop.clientHeight;
    },

    commit: function (row) {
      var m = this.matches[+row.dataset.idx];
      if (!m) return;
      this.setValue(m.value, m.label, m.data);
      this.close(false);
      this.input.focus();
    },

    /* --- keyboard --- */
    key: function (e) {
      var k = e.key;
      if (k === "ArrowDown" || k === "ArrowUp") {
        e.preventDefault();
        if (open !== this) return this.openList(false);
        return this.setActive(this.active + (k === "ArrowDown" ? 1 : -1));
      }
      if (open !== this) {
        if (k === "Enter" && this.input.value === "") return;   // let the form submit
        return;
      }
      if (k === "Enter") {
        e.preventDefault();
        var row = pop.querySelector(".tc-cb-opt.is-active");
        if (row) this.commit(row);
        return;
      }
      if (k === "Escape") { e.preventDefault(); e.stopPropagation(); return this.close(true); }
      if (k === "Home") { e.preventDefault(); return this.setActive(0); }
      if (k === "End") { e.preventDefault(); return this.setActive(this.matches.length - 1); }
      if (k === "Tab") this.close(true);
    }
  };

  /** Highlight the typed run inside the label. Matched on the RAW lowercased
   *  text, so an accent-insensitive hit simply renders unhighlighted rather
   *  than highlighting the wrong characters (normalisation shifts indices). */
  function mark(label, q) {
    var span = document.createElement("span");
    span.className = "tc-cb-label";
    var i = q ? label.toLowerCase().indexOf(q.toLowerCase().trim()) : -1;
    if (i < 0 || !q.trim()) { span.textContent = label; return span; }
    var n = q.trim().length;
    span.appendChild(document.createTextNode(label.slice(0, i)));
    var em = document.createElement("mark");
    em.textContent = label.slice(i, i + n);
    span.appendChild(em);
    span.appendChild(document.createTextNode(label.slice(i + n)));
    return span;
  }

  /* ---------- discovery ---------- */

  function wanted(s) {
    if (s.multiple || s.dataset.tcCb || s.hasAttribute("data-no-search")) return false;
    if (s.hasAttribute("data-search") || s.dataset.src) return true;
    return s.options.length > MIN_OPTIONS;
  }

  function scan(root) {
    var all = (root || document).querySelectorAll("select");
    var todo = [], i;
    for (i = 0; i < all.length; i++) if (wanted(all[i])) todo.push(all[i]);
    if (!todo.length) return;
    // Measure everything first, mutate second: interleaving the two would force
    // a layout per select, and some pages carry dozens.
    var boxes = todo.map(function (s) {
      var cs = getComputedStyle(s), p = s.parentElement;
      return {
        inline: cs.display.indexOf("inline") === 0,
        full: !!p && s.offsetWidth >= p.clientWidth - 2,
        w: s.offsetWidth, h: s.offsetHeight, fs: cs.fontSize
      };
    });
    todo.forEach(function (s, n) { build(s, boxes[n]); });
    if (window.tcApplyI18n) window.tcApplyI18n();
  }

  function start() {
    scan(document);
    // Forms in this app grow rows client-side (PR lines, checklist rows). Watch
    // for selects that arrive later instead of asking every page to call scan().
    if (window.MutationObserver) {
      new MutationObserver(function (muts) {
        for (var i = 0; i < muts.length; i++) {
          var added = muts[i].addedNodes;
          for (var j = 0; j < added.length; j++) {
            var n = added[j];
            if (n.nodeType !== 1) continue;
            if (n.tagName === "SELECT") { if (wanted(n)) scan(n.parentNode); }
            else if (n.querySelector && n.querySelector("select")) scan(n);
          }
        }
      }).observe(document.body, { childList: true, subtree: true });
    }
  }

  document.addEventListener("click", function (e) {
    if (open && !open.wrap.contains(e.target) && !pop.contains(e.target)) open.close(true);
  });

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();

  window.tcCombobox = { scan: scan };
})();
