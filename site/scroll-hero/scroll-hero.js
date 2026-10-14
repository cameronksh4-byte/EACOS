/* Valley AgentWorks — scroll-through hero.
   Scrubs an image sequence on a <canvas> as the visitor scrolls through .sh.
   Captions are shown by scroll progress (data-from / data-to, 0–1).
   No dependencies. Respects prefers-reduced-motion (see the <head> snippet in README). */
(function () {
  "use strict";

  var root = document.querySelector(".sh");
  if (!root || !document.documentElement.classList.contains("sh-js")) return;

  var stage = root.querySelector(".sh__stage");
  var canvas = root.querySelector(".sh__canvas");
  var ctx = canvas.getContext("2d");
  var captions = root.querySelectorAll(".sh__caption");

  var count = parseInt(root.dataset.frames, 10);
  var small = window.matchMedia("(max-width: 760px)").matches;
  var dir = root.dataset.src + (small ? "mobile/" : "desktop/");
  var frames = new Array(count);
  var current = -1;
  var failed = 0;
  var ticking = false;

  function url(i) {
    return dir + "f" + String(i + 1).padStart(3, "0") + ".webp";
  }

  // Load order: first frame, then every 16th, 8th, 4th, 2nd, then the rest,
  // so a coarse version of the whole scroll works early and fills in.
  function loadOrder() {
    var seen = new Uint8Array(count), order = [];
    [count, 16, 8, 4, 2, 1].forEach(function (step) {
      for (var i = 0; i < count; i += step) {
        if (!seen[i]) { seen[i] = 1; order.push(i); }
      }
    });
    return order;
  }

  function load(i) {
    return new Promise(function (resolve) {
      var img = new Image();
      img.decoding = "async";
      img.onload = function () { frames[i] = img; resolve(); };
      img.onerror = function () { failed++; resolve(); };
      img.src = url(i);
    });
  }

  // Load with a few requests in flight at a time.
  function loadAll() {
    var queue = loadOrder();
    function worker() {
      var i = queue.shift();
      if (i === undefined) return Promise.resolve();
      return load(i).then(function () {
        if (i === 0) draw(true);
        else if (!ticking) requestAnimationFrame(update);
        return worker();
      });
    }
    return Promise.all([worker(), worker(), worker(), worker()]).then(function () {
      if (failed > count / 2) root.classList.add("sh--off");
    });
  }

  // Nearest loaded frame to the one we want.
  function nearest(i) {
    for (var d = 0; d < count; d++) {
      if (frames[i - d]) return frames[i - d];
      if (frames[i + d]) return frames[i + d];
    }
    return null;
  }

  function resize() {
    var dpr = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = Math.round(stage.clientWidth * dpr);
    canvas.height = Math.round(stage.clientHeight * dpr);
    draw(true);
  }

  function progress() {
    var rect = root.getBoundingClientRect();
    var total = root.offsetHeight - stage.offsetHeight;
    return total > 0 ? Math.min(1, Math.max(0, -rect.top / total)) : 0;
  }

  function draw(force) {
    var p = progress();
    var i = Math.min(count - 1, Math.round(p * (count - 1)));
    var img = nearest(i);
    if (img && (force || i !== current)) {
      current = frames[i] ? i : -1;
      // object-fit: cover
      var scale = Math.max(canvas.width / img.naturalWidth, canvas.height / img.naturalHeight);
      var w = img.naturalWidth * scale, h = img.naturalHeight * scale;
      ctx.drawImage(img, (canvas.width - w) / 2, (canvas.height - h) / 2, w, h);
    }
    for (var c = 0; c < captions.length; c++) {
      var el = captions[c];
      var on = p >= parseFloat(el.dataset.from) && p <= parseFloat(el.dataset.to);
      el.classList.toggle("is-active", on);
    }
    root.classList.toggle("is-scrolled", p > 0.02);
  }

  function update() {
    ticking = false;
    draw(false);
  }

  window.addEventListener("scroll", function () {
    if (!ticking) { ticking = true; requestAnimationFrame(update); }
  }, { passive: true });
  window.addEventListener("resize", resize);

  // Keep the final caption's link reachable by keyboard even while it's hidden:
  // tabbing to it scrolls to the end of the section.
  var cta = root.querySelector(".sh__cta");
  if (cta) {
    cta.addEventListener("focus", function () {
      if (progress() < 0.85) window.scrollTo({ top: root.offsetTop + root.offsetHeight - stage.offsetHeight, behavior: "instant" });
    });
  }

  // English / Spanish: elements carry data-es; swap when <html lang> changes.
  var i18n = root.querySelectorAll("[data-es]");
  i18n.forEach(function (el) { el.dataset.en = el.innerHTML; });
  function applyLang() {
    var es = (document.documentElement.lang || "en").toLowerCase().indexOf("es") === 0;
    i18n.forEach(function (el) { el.innerHTML = es ? el.dataset.es : el.dataset.en; });
  }
  new MutationObserver(applyLang).observe(document.documentElement, { attributes: true, attributeFilter: ["lang"] });
  applyLang();

  resize();
  loadAll();
})();
