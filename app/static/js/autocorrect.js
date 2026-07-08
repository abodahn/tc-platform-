/* ============================================================
   TC Platform — text assist
   1) Live offline autocorrect for common typos (data-ac fields)
   2) Garamento ✨ AI-polish button (data-polish fields)
   ============================================================ */
(function () {
  "use strict";

  var CSRF = (document.querySelector('meta[name="csrf-token"]') || {}).content || "";
  function toast(m, t) { if (window.tcToast) window.tcToast(m, t || ""); }
  function fireInput(el) { el.dispatchEvent(new Event("input", { bubbles: true })); }

  /* ---------------- 1) Live offline autocorrect ---------------- */
  // whole-word misspelling -> correction (lowercase keys). Common English +
  // textile / procurement domain terms. Only known words are ever changed.
  var TYPOS = {
    teh: "the", thsi: "this", taht: "that", adn: "and", nad: "and", "hte": "the",
    recieve: "receive", recieved: "received", seperate: "separate", definately: "definitely",
    occured: "occurred", untill: "until", wich: "which", thier: "their", becuase: "because",
    beleive: "believe", buisness: "business", tommorow: "tomorrow", wierd: "weird",
    alot: "a lot", accross: "across", agan: "again", allready: "already", arent: "aren't",
    calender: "calendar", cant: "can't", commited: "committed", completly: "completely",
    dont: "don't", enviroment: "environment", existance: "existence", experiance: "experience",
    goverment: "government", immediatly: "immediately", neccessary: "necessary", occassion: "occasion",
    paymnet: "payment", posible: "possible", recomend: "recommend", refered: "referred",
    succesful: "successful", useing: "using", verison: "version", wont: "won't", youre: "you're",
    adress: "address", availabe: "available", avaliable: "available", quantiy: "quantity",
    quanity: "quantity", aproximate: "approximate", aproximatly: "approximately",
    // procurement / textile
    purchace: "purchase", purchse: "purchase", purchease: "purchase", invioce: "invoice",
    invoce: "invoice", delevery: "delivery", deliverry: "delivery", vender: "vendor",
    supplyer: "supplier", warehse: "warehouse", warehous: "warehouse",
    maintaince: "maintenance", maintainance: "maintenance", maintenence: "maintenance",
    departement: "department", deptartment: "department", aproval: "approval", aprove: "approve",
    // fabrics / garment
    polyster: "polyester", polyseter: "polyester", cotten: "cotton", cottton: "cotton",
    embroidary: "embroidery", embroidory: "embroidery", stiching: "stitching", stichting: "stitching",
    threed: "thread", needel: "needle", zippper: "zipper", ziper: "zipper", guage: "gauge",
    denin: "denim", fabricc: "fabric", garmet: "garment", garement: "garment", textil: "textile",
    buttton: "button", buton: "button", coller: "collar", sleave: "sleeve", sleeve: "sleeve"
  };

  function recase(orig, fixed) {
    if (orig === orig.toUpperCase() && orig.length > 1) return fixed.toUpperCase();
    if (orig[0] === orig[0].toUpperCase()) return fixed.charAt(0).toUpperCase() + fixed.slice(1);
    return fixed;
  }
  function correctWord(w) {
    var key = w.toLowerCase();
    return TYPOS.hasOwnProperty(key) ? recase(w, TYPOS[key]) : w;
  }

  // fix the word the user just finished typing (right after a boundary char)
  function onInput(el) {
    if (el._acBusy) return;
    var val = el.value, pos = el.selectionStart;
    if (pos == null) return;
    var prev = val.charAt(pos - 1);
    if (!prev || !/[\s.,;:!?)\]]/.test(prev)) return;
    var m = val.slice(0, pos - 1).match(/([A-Za-z][A-Za-z']*)$/);
    if (!m) return;
    var word = m[1], fixed = correctWord(word);
    if (fixed === word) return;
    var start = pos - 1 - word.length;
    el._acBusy = true;
    el.value = val.slice(0, start) + fixed + val.slice(pos - 1);
    var delta = fixed.length - word.length;
    try { el.setSelectionRange(pos + delta, pos + delta); } catch (e) {}
    el._acBusy = false;
  }

  // full pass when leaving the field: fix all words, tidy spacing & capitals
  function onBlur(el) {
    if (el._acBusy) return;
    var val = el.value;
    if (!val.trim()) return;
    var out = val.replace(/[A-Za-z][A-Za-z']*/g, function (w) { return correctWord(w); });
    out = out.replace(/ {2,}/g, " ").replace(/\bi\b/g, "I");
    // capitalize first alphabetic character
    out = out.replace(/^(\s*)([a-z])/, function (_, sp, c) { return sp + c.toUpperCase(); });
    if (out !== val) {
      el._acBusy = true;
      var pos = el.selectionStart;
      el.value = out;
      try { el.setSelectionRange(pos, pos); } catch (e) {}
      el._acBusy = false;
      fireInput(el);
    }
  }

  // Delegated so dynamically-added fields (e.g. line-item rows) are covered.
  document.addEventListener("input", function (e) {
    var el = e.target;
    if (el && el.matches && el.matches("[data-ac]")) onInput(el);
  }, true);
  document.addEventListener("blur", function (e) {
    var el = e.target;
    if (el && el.matches && el.matches("[data-ac]")) onBlur(el);
  }, true);

  /* ---------------- 2) Garamento ✨ polish button ---------------- */
  function attachPolish(el) {
    if (el._polishReady) return;
    el._polishReady = true;
    var kind = el.getAttribute("data-polish") || "generic";
    var wrap = document.createElement("span");
    wrap.className = "tc-polish-wrap" + (el.tagName === "TEXTAREA" ? " ta" : "");
    el.parentNode.insertBefore(wrap, el);
    wrap.appendChild(el);
    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "tc-polish-btn";
    btn.title = "Fix & polish with Garamento";
    btn.setAttribute("aria-label", "Fix and polish text with Garamento");
    btn.innerHTML = "✨";
    wrap.appendChild(btn);
    btn.addEventListener("click", function () { runPolish(el, kind, btn); });
  }

  function runPolish(el, kind, btn) {
    var text = (el.value || "").trim();
    if (!text) { toast("Write something first, then I’ll polish it. ✨", ""); el.focus(); return; }
    if (btn.classList.contains("busy")) return;
    btn.classList.add("busy"); btn.disabled = true;
    fetch("/garamento/polish", {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": CSRF },
      body: JSON.stringify({ text: el.value, kind: kind })
    }).then(function (r) { return r.json(); }).then(function (d) {
      if (d && d.ok) {
        if (d.text && d.text !== el.value) { el.value = d.text; fireInput(el); toast("Polished by Garamento ✨", "success"); }
        else { toast("Already looking sharp ✨", ""); }
      } else if (d && d.offline) {
        toast(d.message || "Polish is offline.", "");
      } else {
        toast("Couldn’t polish that right now.", "");
      }
    }).catch(function () { toast("Couldn’t polish that right now.", ""); })
      .finally(function () { btn.classList.remove("busy"); btn.disabled = false; });
  }

  function scan(root) {
    (root || document).querySelectorAll("[data-polish]").forEach(attachPolish);
  }

  window.TCText = { scan: scan, correctWord: correctWord };

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", function () { scan(); });
  else scan();
})();
