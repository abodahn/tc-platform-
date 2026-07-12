/* Accounts / Auth — client helpers: show/hide password, Caps Lock warning,
   live password strength + requirement checklist, and double-submit prevention.
   The server re-validates everything; this is UX only. */
(function () {
  "use strict";
  var LEVELS = ["acc.pw.very_weak", "acc.pw.weak", "acc.pw.fair", "acc.pw.good", "acc.pw.strong"];

  // --- show / hide password ---
  document.querySelectorAll("[data-pw-toggle]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var inp = document.getElementById(btn.getAttribute("data-pw-toggle"));
      if (!inp) return;
      var showing = inp.type === "password";
      inp.type = showing ? "text" : "password";
      btn.textContent = showing ? "🙈" : "👁";
      btn.setAttribute("aria-label", showing ? "Hide password" : "Show password");
    });
  });

  // --- Caps Lock warning ---
  document.querySelectorAll('input[type="password"]').forEach(function (inp) {
    function caps(e) {
      var on = e.getModifierState && e.getModifierState("CapsLock");
      var field = inp.closest(".field");
      var el = field && field.querySelector("[data-caps]");
      if (el) el.classList.toggle("on", !!on);
    }
    inp.addEventListener("keyup", caps);
    inp.addEventListener("keydown", caps);
  });

  // --- password strength + checklist ---
  var pw = document.getElementById("pw");
  if (pw) {
    var meter = document.querySelector(".pw-meter");
    var stext = document.querySelector("[data-pw-strength]");
    var list = document.getElementById("pwlist");

    function score(v) {
      if (!v) return 0;
      var rules = {
        len: v.length >= 12, upper: /[A-Z]/.test(v), lower: /[a-z]/.test(v),
        digit: /[0-9]/.test(v), special: /[^A-Za-z0-9]/.test(v)
      };
      if (list) list.querySelectorAll("[data-rule]").forEach(function (li) {
        li.classList.toggle("ok", !!rules[li.getAttribute("data-rule")]);
      });
      var met = Object.keys(rules).filter(function (k) { return rules[k]; }).length;
      var s = Math.max(0, Math.min(4, met - 1));
      if (v.length >= 16 && met >= 4) s = 4;
      if (v.length < 8) s = Math.min(s, 1);
      return s;
    }

    function update() {
      var s = score(pw.value || "");
      if (meter) meter.setAttribute("data-score", String(s));
      if (stext) {
        stext.setAttribute("data-i18n", LEVELS[s]);
        if (window.tcApplyI18n) window.tcApplyI18n();
      }
    }
    pw.addEventListener("input", update);
    update();
  }

  // --- prevent accidental double submission ---
  document.querySelectorAll("form").forEach(function (f) {
    f.addEventListener("submit", function () {
      var b = f.querySelector("[data-submit]");
      if (b && !b.disabled) {
        // defer so the value still posts, then lock the button
        setTimeout(function () { b.disabled = true; b.style.opacity = ".7"; b.style.cursor = "wait"; }, 0);
      }
    });
  });
})();
