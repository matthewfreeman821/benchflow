(function () {
  "use strict";
  var B = window.BF, reveal = document.getElementById("reveal"), secrets = document.getElementById("secrets"), status = document.getElementById("demo-status"), generation = 0;
  function clear() {
    generation++; B.clear(secrets); B.clear(status);
    status.appendChild(B.text("p", "Displayed secrets cleared. A consumed response cannot be retrieved again."));
  }
  document.getElementById("clear").addEventListener("click", clear);
  window.addEventListener("pagehide", function () { clear(); B.session = null; });
  window.addEventListener("hashchange", clear);
  document.addEventListener("visibilitychange", function () { if (document.hidden) clear(); });
  reveal.addEventListener("click", async function () {
    var token = ++generation; reveal.disabled = true; B.clear(status); status.appendChild(B.text("p", "Requesting one-time credentials…"));
    try {
      var s = await B.getSession();
      if (s.authenticated) throw new Error("Sign out first. Credential retrieval requires an anonymous CSRF-protected session.");
      var result = await B.request("/demo/credentials", {});
      if (token !== generation || document.hidden) { result = null; return; }
      B.clear(status); status.appendChild(B.callout("One-time response consumed", "NOT SENT. These are fictional local-demo identities. Clear this screen before leaving.", "caution"));
      result.accounts.forEach(function (a) {
        secrets.appendChild(B.panel(B.human(a.account_key.replace(/\./g, " ")), [
          B.facts([["Login ID", a.login_id]]), B.text("p", a.password, "secret")
        ]));
      });
      secrets.appendChild(B.panel("Invited fictional customer", [
        B.callout(result.activation.label, "Choose a login ID and password on the activation page."),
        B.text("p", result.activation.code, "secret")
      ]));
      result = null;
    } catch (e) {
      if (token !== generation) return;
      B.clear(status); status.appendChild(B.callout("Credentials unavailable", e.message, "error"));
      status.appendChild(B.text("p", "If the one-time request was consumed or its response lost, ask the host operator to restart isolated demo mode. Fictional story records must be preserved."));
      reveal.disabled = false;
    }
  });
}());
