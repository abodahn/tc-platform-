/* English-only purchase requests: flag Arabic as it is typed and stop the
   submit, so nothing typed is lost to a server refusal. The server checks too. */
(function () {
  var form = document.getElementById("prForm");
  if (!form) return;
  var RE = /[؀-ۿݐ-ݿﭐ-﷿ﹰ-﻿]/;
  function msg() {
    var m = document.getElementById("enOnlyMsg");
    return (m && m.textContent.trim()) || "English only, please. Arabic is not accepted.";
  }
  function typed(el) {
    // Only fields that are saved: search boxes (no name) may be typed in Arabic.
    if (!el.matches || !el.matches("input, textarea") || !el.name || el.disabled || el.readOnly) return false;
    var t = (el.type || "text").toLowerCase();
    return t === "text" || t === "search" || el.tagName === "TEXTAREA";
  }
  function showTip(owner, after, bad) {
    var tip = owner._enTip;
    if (bad && !tip) {
      tip = owner._enTip = document.createElement("div");
      tip.className = "en-only-tip";
      tip.setAttribute("role", "alert");
      tip.textContent = msg();
      after.insertAdjacentElement(owner === after ? "afterend" : "beforeend", tip);
    } else if (!bad && tip) {
      tip.remove(); owner._enTip = null;
    }
  }
  function mark(el) {
    var bad = RE.test(el.value || "");
    el.classList.toggle("en-only-bad", bad);
    el.setAttribute("aria-invalid", bad ? "true" : "false");
    // An item line is a grid: a message slipped in beside the field becomes an
    // extra cell and shoves the unit / qty / delete cells onto the next line.
    // One message per line instead, appended at the end and spanning the row.
    var row = el.closest && el.closest(".li-row");
    if (row) {
      var rowBad = Array.prototype.some.call(row.querySelectorAll("input, textarea"),
        function (f) { return typed(f) && RE.test(f.value || ""); });
      showTip(row, row, rowBad);
    } else {
      showTip(el, el, bad);
    }
    return bad;
  }
  function onEdit(e) { if (typed(e.target)) mark(e.target); }
  form.addEventListener("input", onEdit);
  // the item picker fills / clears the field from code (no input event)
  form.addEventListener("change", onEdit);
  form.addEventListener("focusout", onEdit);
  form.addEventListener("submit", function (e) {
    var first = null;
    form.querySelectorAll("input, textarea").forEach(function (el) {
      if (typed(el) && mark(el) && !first) first = el;
    });
    if (first) {
      e.preventDefault();
      e.stopImmediatePropagation();
      first.focus();
      if (first.scrollIntoView) first.scrollIntoView({ block: "center", behavior: "smooth" });
      if (window.tcToast) tcToast(msg(), "error");
    }
  }, true);
})();
