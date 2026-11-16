// Minimal chat widget for the help desk free trial. Mount with:
//   <div id="helpdesk"></div><script src="/assets/helpdesk/helpdesk-widget.js" defer></script>
// Replies are always inserted as text (textContent), never as HTML.
(function () {
  "use strict";
  var root = document.getElementById("helpdesk");
  if (!root) return;
  var T = window.HELPDESK_TEXT || {
    placeholder: "Describe your IT problem…", send: "Send", thinking: "Looking that up…",
    left: function (n) { return n + (n === 1 ? " free question left" : " free questions left"); },
    leftAccount: function (n) { return n + (n === 1 ? " question left" : " questions left"); },
    signupTitle: "Create a free account to keep going", email: "Work email", name: "Your name",
    signupSend: "Email me a sign-in link", signupSent: "Check your inbox for the link. It works for 20 minutes.",
    upgrade: "See plans and credit packs", error: "Something went wrong. Please try again.",
  };
  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }
  var log = el("div", "hd-log"); log.setAttribute("aria-live", "polite");
  var meter = el("div", "hd-meter");
  var form = el("form", "hd-form");
  var input = el("textarea", "hd-input"); input.maxLength = 1000; input.rows = 2; input.placeholder = T.placeholder;
  input.setAttribute("aria-label", T.placeholder);
  var trap = el("input", "hd-website"); trap.name = "website"; trap.tabIndex = -1; trap.autocomplete = "off";
  trap.style.cssText = "position:absolute;left:-9999px"; trap.setAttribute("aria-hidden", "true");
  var send = el("button", "hd-send", T.send); send.type = "submit";
  form.append(input, trap, send);
  var extra = el("div", "hd-extra");
  root.append(log, meter, form, extra);

  function say(who, text) {
    var row = el("div", "hd-msg hd-" + who, text);
    log.append(row); row.scrollIntoView({ block: "nearest" });
    return row;
  }
  function post(path, body) {
    return fetch(path, { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body) }).then(function (r) { return r.json(); });
  }
  function showMeter(d) {
    var left = typeof d.remaining === "number" ? d.remaining
      : Math.max(0, (d.allowance || 0) - (d.used || 0)) + (d.credits || 0); // /me has no "remaining"
    meter.textContent = d.signed_in ? T.leftAccount(left) : T.left(left);
  }
  function showSignup() {
    extra.replaceChildren();
    var f = el("form", "hd-signup");
    var email = el("input"); email.type = "email"; email.required = true; email.placeholder = T.email; email.setAttribute("aria-label", T.email);
    var name = el("input"); name.type = "text"; name.placeholder = T.name; name.setAttribute("aria-label", T.name);
    var go = el("button", null, T.signupSend); go.type = "submit";
    f.append(el("strong", null, T.signupTitle), email, name, go);
    f.addEventListener("submit", function (ev) {
      ev.preventDefault(); go.disabled = true;
      post("/api/helpdesk/signup", { email: email.value, name: name.value, lang: document.documentElement.lang })
        .then(function () { f.replaceChildren(el("p", null, T.signupSent)); })
        .catch(function () { go.disabled = false; });
    });
    extra.append(f); input.disabled = true; send.disabled = true;
  }
  function showUpgrade(url) {
    extra.replaceChildren();
    if (!url) return;
    var a = el("a", "hd-upgrade", T.upgrade); a.href = url; extra.append(a);
  }
  form.addEventListener("submit", function (ev) {
    ev.preventDefault();
    var text = input.value.trim();
    if (text.length < 2) return;
    say("user", text); input.value = ""; send.disabled = true;
    var pending = say("bot", T.thinking);
    post("/api/helpdesk/ask", { message: text, website: trap.value }).then(function (d) {
      pending.textContent = d.reply || T.error; showMeter(d);
      if (d.status === "signup_required") showSignup();
      else if (d.status === "limit_reached") showUpgrade(d.upgrade_url);
    }).catch(function () { pending.textContent = T.error; })
      .finally(function () { if (!input.disabled) send.disabled = false; });
  });
  fetch("/api/helpdesk/me", { credentials: "same-origin" }).then(function (r) { return r.json(); })
    .then(function (d) { if (d.signed_in) showMeter(d); }).catch(function () {});
})();
