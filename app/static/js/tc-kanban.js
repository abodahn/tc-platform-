/* ============================================================
   TC Platform — maintenance kanban drag & drop (/maintenance/tickets)
   Native HTML5 drag-and-drop, no library. The card stays a link: a plain
   click still opens the ticket, only a real drag moves it.
   The allowed targets come from the SERVER (data-allow on each card, refreshed
   from the move response) — the transition table is never mirrored here.
   ponytail: HTML5 DnD does not fire on touch. Phones/tablets keep the keyboard
   path and tap-to-open; add a pointer-events fallback only if the floor asks.
   ============================================================ */
(function () {
  "use strict";

  var board = document.querySelector("[data-kanban]");
  if (!board) return;

  var CSRF = (document.querySelector('meta[name="csrf-token"]') || {}).content || "";
  var cols = Array.prototype.slice.call(board.querySelectorAll(".mkcol"));
  var live = document.getElementById("mkLive");
  var dragged = null;   // card held by the mouse
  var picked = null;    // card held by the keyboard
  var pickedAt = 0;     // candidate column index while picked

  function T(key) { return window.tcT ? window.tcT(key) : key; }
  function tr(key, fallbackKey) {          // translated, or a generic fallback
    var v = T(key);
    return v === key ? T(fallbackKey) : v;
  }
  function toast(msg, kind) { if (window.tcToast) window.tcToast(msg, kind || ""); }
  function say(msg) { if (live) live.textContent = msg; }

  function bodyOf(col) { return col.querySelector(".mkbody"); }
  function titleOf(col) { return (col.querySelector("h4 span") || {}).textContent || ""; }
  function allowed(card) {
    return (card.getAttribute("data-allow") || "").trim().split(/\s+/)
      .filter(Boolean).map(Number);
  }

  function recount() {
    cols.forEach(function (col) {
      var n = bodyOf(col).querySelectorAll(".mkcard").length;
      var tag = col.querySelector("[data-mk-count]");
      if (tag) tag.textContent = n;
      var empty = col.querySelector(".empty");
      if (empty) empty.hidden = n > 0;
    });
  }

  function clearMarks() {
    cols.forEach(function (c) { c.classList.remove("mk-drop", "mk-inert", "mk-target"); });
  }

  function markFor(card) {                 // dim every column that can't take it
    var ok = allowed(card);
    cols.forEach(function (c, i) { c.classList.toggle("mk-inert", ok.indexOf(i) < 0); });
  }

  function applyResult(card, res) {
    card.setAttribute("data-status", res.status || "");
    card.setAttribute("data-allow", (res.allow || []).join(" "));
    var badge = card.querySelector("[data-mk-status]");
    if (badge && res.status) {
      badge.className = "badge " + (res.badge || "b-unknown");
      var label = badge.firstElementChild || badge;
      label.setAttribute("data-i18n", "mx." + res.status);
      var t = T("mx." + res.status);
      label.textContent = t === "mx." + res.status ? (res.status_label || res.status) : t;
    }
  }

  /* Optimistic move: put the card in the column now, roll it back if the
     server refuses (and say why). */
  function move(card, col, byKeyboard) {
    var home = card.parentNode, after = card.nextSibling;
    bodyOf(col).appendChild(card);
    recount();
    if (byKeyboard) card.focus();

    fetch(card.getAttribute("data-move-url"), {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": CSRF },
      body: JSON.stringify({ col: cols.indexOf(col) })
    }).then(function (r) { return r.json(); })
      .catch(function () { return null; })
      .then(function (res) {
        if (res && res.ok) {
          applyResult(card, res);
          say(T("m.kb_moved") + " " + titleOf(col));
          return;
        }
        home.insertBefore(card, after);    // roll back
        recount();
        if (byKeyboard) card.focus();
        if (res) applyResult(card, res);
        var why = res && res.error ? tr(res.error, "m.kb_failed") : T("m.kb_failed");
        toast(why, "error");
        say(why);
      });
  }

  /* ---------------- mouse ---------------- */
  board.addEventListener("dragstart", function (e) {
    var card = e.target.closest && e.target.closest(".mkcard[draggable]");
    if (!card) return;
    dragged = card;
    card.classList.add("mk-dragging");
    markFor(card);
    if (e.dataTransfer) {
      e.dataTransfer.effectAllowed = "move";
      try { e.dataTransfer.setData("text/plain", card.getAttribute("data-no") || ""); } catch (err) {}
    }
  });

  board.addEventListener("dragend", function () {
    if (dragged) dragged.classList.remove("mk-dragging");
    dragged = null;
    clearMarks();
  });

  cols.forEach(function (col, i) {
    col.addEventListener("dragover", function (e) {
      if (!dragged || allowed(dragged).indexOf(i) < 0) return;  // no preventDefault -> not a drop target
      e.preventDefault();
      if (e.dataTransfer) e.dataTransfer.dropEffect = "move";
      col.classList.add("mk-drop");
    });
    col.addEventListener("dragleave", function (e) {
      if (!col.contains(e.relatedTarget)) col.classList.remove("mk-drop");
    });
    col.addEventListener("drop", function (e) {
      e.preventDefault();
      col.classList.remove("mk-drop");
      var card = dragged;
      if (!card || allowed(card).indexOf(i) < 0) return;
      if (card.parentNode === bodyOf(col)) return;
      move(card, col, false);
    });
  });

  /* ---------------- keyboard ---------------- */
  function highlight() {
    cols.forEach(function (c, i) { c.classList.toggle("mk-target", i === pickedAt); });
  }

  function pick(card) {
    var ok = allowed(card);
    if (!ok.length) { toast(T("m.kb_blocked"), "error"); say(T("m.kb_blocked")); return; }
    picked = card;
    pickedAt = ok[0];
    card.classList.add("mk-dragging");
    markFor(card);
    highlight();
    say(T("m.kb_picked") + " " + (card.getAttribute("data-no") || "") + ". " + titleOf(cols[pickedAt]));
  }

  function release(announce) {
    if (!picked) return;
    picked.classList.remove("mk-dragging");
    picked = null;
    clearMarks();
    if (announce) say(T("m.kb_cancelled"));
  }

  function step(delta) {
    var ok = allowed(picked);
    var at = ok.indexOf(pickedAt);
    pickedAt = ok[(at + delta + ok.length) % ok.length];
    highlight();
    say(titleOf(cols[pickedAt]));
  }

  board.addEventListener("keydown", function (e) {
    var card = e.target.closest && e.target.closest(".mkcard[draggable]");
    if (!card) return;
    if (e.key === " " || e.key === "Spacebar") {
      e.preventDefault();
      if (picked === card) release(true); else { release(false); pick(card); }
      return;
    }
    if (picked !== card) return;
    if (e.key === "Escape") { e.preventDefault(); release(true); return; }
    if (e.key === "Enter") {
      e.preventDefault();                       // don't follow the link while holding
      var col = cols[pickedAt];
      release(false);
      if (col && card.parentNode !== bodyOf(col)) move(card, col, true);
      else say(T("m.kb_cancelled"));
      return;
    }
    // Logical direction: in RTL the board mirrors, so ArrowRight goes backwards.
    if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
      e.preventDefault();
      var rtl = document.documentElement.dir === "rtl";
      step((e.key === "ArrowRight") !== rtl ? 1 : -1);
    }
  });

  board.addEventListener("focusout", function (e) {
    if (picked && e.target === picked) release(false);
  });
})();
