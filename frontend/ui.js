(function () {
  "use strict";
  var B = window.BF = window.BF || {};
  B.el = function (tag, props, children) {
    var n = document.createElement(tag);
    Object.keys(props || {}).forEach(function (k) {
      var v = props[k];
      if (v === null || v === undefined || v === false) return;
      if (k === "text") n.textContent = v;
      else if (k === "class") n.className = v;
      else if (k.slice(0, 2) === "on") n.addEventListener(k.slice(2), v);
      else if (k === "value" || k === "checked" || k === "disabled") n[k] = v;
      else n.setAttribute(k, v === true ? "" : String(v));
    });
    (Array.isArray(children) ? children : [children]).forEach(function (c) {
      if (c !== null && c !== undefined) n.appendChild(c.nodeType ? c : document.createTextNode(String(c)));
    });
    return n;
  };
  B.clear = function (n) { while (n.firstChild) n.removeChild(n.firstChild); };
  B.text = function (tag, s, cls) { return B.el(tag, { text: s === null || s === undefined ? "Not recorded" : s, class: cls }); };
  B.link = function (label, href, cls) { return B.el("a", { text: label, href: href, class: cls }); };
  B.button = function (label, fn, cls) { return B.el("button", { text: label, type: "button", onclick: fn, class: cls }); };
  B.uuid = function () {
    var a = new Uint8Array(16); window.crypto.getRandomValues(a);
    a[6] = (a[6] & 15) | 64; a[8] = (a[8] & 63) | 128;
    var h = Array.prototype.map.call(a, function (v) { return ("0" + v.toString(16)).slice(-2); }).join("");
    return h.slice(0, 8) + "-" + h.slice(8, 12) + "-" + h.slice(12, 16) + "-" + h.slice(16, 20) + "-" + h.slice(20);
  };
  B.human = function (s) { return String(s || "Not recorded").replace(/_/g, " ").replace(/\b\w/g, function (c) { return c.toUpperCase(); }); };
  B.money = function (minor, currency) {
    if (!Number.isInteger(minor)) return "Amount unavailable";
    try { return new Intl.NumberFormat("en-US", { style: "currency", currency: currency || "USD" }).format(minor / 100); }
    catch (_) { return (minor / 100).toFixed(2) + " " + currency; }
  };
  B.date = function (s) {
    if (!s) return "Not recorded";
    var d = new Date(s); if (isNaN(d.getTime())) return "Date unavailable";
    return new Intl.DateTimeFormat("en-US", { timeZone: "America/New_York", month: "short", day: "numeric", year: "numeric", hour: "numeric", minute: "2-digit", timeZoneName: "short" }).format(d);
  };
  B.known = function (k) { return !k || !k.known ? "Unknown" : k.value === null ? "None recorded" : k.value; };
  B.device = function (d) { return d.label || [B.known(d.make), B.known(d.model)].join(" · "); };
  B.callout = function (title, body, kind) {
    return B.el("div", { class: "callout " + (kind || "") }, [B.text("strong", title), body ? B.text("p", body) : null]);
  };
  B.empty = function (s) { return B.text("p", s, "empty"); };
  B.facts = function (pairs) {
    var dl = B.el("dl", { class: "facts" });
    pairs.forEach(function (p) { dl.appendChild(B.text("dt", p[0])); dl.appendChild(B.el("dd", {}, p[1] && p[1].nodeType ? p[1] : String(p[1] === null || p[1] === undefined ? "Not recorded" : p[1]))); });
    return dl;
  };
  B.panel = function (title, children) { return B.el("section", { class: "panel" }, [B.text("h2", title)].concat(children || [])); };
  B.badges = function (items) {
    return B.el("div", { class: "badges" }, (items || []).map(function (b) { return B.text("span", b.label, "badge caution"); }));
  };
  B.blockers = function (items) {
    if (!items || !items.length) return B.callout("No blockers reported", "Actions are still checked by the server before being recorded.");
    return B.el("div", {}, items.map(function (b) { return B.callout("Blocked · " + b.label, b.detail || null, "caution"); }));
  };
  B.notice = function (title, body, kind, code) {
    var n = document.getElementById("notice"); if (!n) return;
    n.dataset.code = code || "";
    B.clear(n); n.appendChild(B.callout(title, body, kind));
  };
  B.focusHeading = function (root) {
    var h = root.querySelector("h1,h2"); if (h) { h.setAttribute("tabindex", "-1"); h.focus(); }
  };
  B.errorText = function (e) {
    if (e.code === "FORBIDDEN") return "Your assigned access does not allow this action. Contact a manager. Nothing was changed.";
    if (e.status === 404) return "This record is unavailable or you do not have access.";
    if (e.status === 401) return "Your session is unavailable. Sign in again and review current details before confirming an action.";
    return e.message || "The server could not be reached. No confirmed result is available.";
  };
  B.dialog = function (title, content) {
    var d = document.getElementById("action-dialog"), invoker = document.activeElement;
    // A transition is not a close. Native close events are queued: closing here
    // can erase the replacement form after its choice listener has returned.
    if (!d.open) d._invoker = invoker;
    d._transition = (d._transition || 0) + 1;
    B.captureDraft = null; // never retain a reader belonging to a replaced form
    B.clear(d);
    var h = B.text("h2", title); h.id = "action-title"; h.tabIndex = -1;
    d.appendChild(B.el("div", { class: "dialog-head" }, [h, B.button("Close", function () { d.close(); }, "dialog-close")]));
    d.appendChild(B.el("div", { class: "dialog-body" }, content));
    d.onclose = function () {
      if (d.open) return; // a queued close must not clear a newly opened dialog
      B.clear(d); B.captureDraft = null;
      var notice = document.getElementById("notice");
      if (notice && notice.dataset.code === "draft-open") { B.clear(notice); notice.dataset.code = ""; }
      if (d._invoker && d._invoker.isConnected) d._invoker.focus();
    };
    d.oncancel = function (event) { if (d.dataset.pending) event.preventDefault(); };
    if (!d.open) d.showModal();
    h.focus(); return d;
  };
  B.taskPurpose = function (task, job) {
    // WorkTaskView exposes no planned summary. Log summaries are activity,
    // not identity (even a "note" can be added after work starts).
    if (task.scope_kind === "diagnostic") {
      var permission = job.diagnostic && job.diagnostic.current;
      return permission && permission.permission_id === task.scope_ref && permission.scope
        ? "Diagnostic inspection · " + permission.scope : "Diagnostic inspection";
    }
    var versions = (job.estimates || []).slice().reverse();
    for (var i = 0; i < versions.length; i++) {
      var line = (versions[i].lines || []).find(function (l) { return l.scope_ref === task.scope_ref; });
      if (line && line.description) return line.description + " · estimate v" + versions[i].version;
    }
    return "Repair within recorded scope";
  };
  B.taskScope = function (task, job) {
    if (task.scope_kind === "diagnostic") return job.diagnostic && job.diagnostic.current ? job.diagnostic.current.scope : "Diagnostic permission";
    var versions = (job.estimates || []).slice().reverse();
    for (var i = 0; i < versions.length; i++) {
      var line = (versions[i].lines || []).find(function (l) { return l.scope_ref === task.scope_ref; });
      if (line) return "Estimate v" + versions[i].version + " · " + line.description;
    }
    return "Historical repair scope · see references";
  };
  // The contract encodes comparison values as JSON strings. Decode only the
  // commercial fields we understand; never print arbitrary keys/raw payloads.
  B.estimateChanges = function (estimate) {
    function decode(raw) { if (raw === null) return null; try { return JSON.parse(raw); } catch (_) { return undefined; } }
    var changes = estimate.change_from_previous || [], currencyChange = changes.find(function (x) { return x.field === "currency"; });
    var oldCurrency = currencyChange && decode(currencyChange.before) || estimate.currency;
    function value(field, raw, currency, otherRaw, previous) {
      var v = decode(raw);
      if (v === null) return B.text("p", "Not present");
      if (field === "lines" && Array.isArray(v)) return B.el("div", {}, v.map(function (line) {
        if (!line || typeof line.description !== "string" || !Number.isInteger(line.quantity) || !Number.isInteger(line.unit_amount_minor)) return B.text("p", "Line details unavailable · ask the shop to clarify before deciding.");
        var other = decode(otherRaw), counterpart = Array.isArray(other) && other.find(function (l) { return l && l.scope_ref && l.scope_ref === line.scope_ref; });
        var scopeNote = line.scope_ref && Array.isArray(other) && !counterpart ? (previous ? "Removed or replaced scope" : "New or replacement scope") :
          counterpart && counterpart.part_id !== line.part_id ? "Part selection changed · confirm the replacement description with the shop" :
          counterpart && (counterpart.description !== line.description || counterpart.quantity !== line.quantity || counterpart.unit_amount_minor !== line.unit_amount_minor) ? "Description, quantity or price changed" : null;
        return B.el("div", { class: "line-item" }, B.el("div", {}, [B.text("strong", (line.kind === "part" ? "Part · " : line.kind === "labor" ? "Work · " : "") + line.description),
          scopeNote ? B.text("p", scopeNote, "small") : null,
          B.text("p", line.quantity + " × " + B.money(line.unit_amount_minor, currency))]));
      }));
      if (field === "tax_minor" && Number.isInteger(v)) return B.text("p", B.money(v, currency));
      if (field === "tax_enabled" && (typeof v === "boolean" || v === 0 || v === 1)) return B.text("p", v ? "Tax included" : "No tax applied");
      if (field === "tax_source" && ["not_configured", "manual_shop_entry"].indexOf(v) !== -1) return B.text("p", B.human(v));
      if ((field === "tax_label" || field === "currency") && typeof v === "string") return B.text("p", v);
      return B.text("p", "Comparison details unavailable · ask the shop to clarify before deciding.");
    }
    var labels = { lines: "Parts, work & scope", currency: "Currency", tax_minor: "Tax amount", tax_label: "Tax description", tax_enabled: "Tax application", tax_source: "Tax source" };
    return B.el("div", {}, changes.map(function (change) {
      return B.el("section", { class: "panel inset" }, [B.text("h4", labels[change.field] || "Commercial terms changed"),
        B.text("strong", "Previous"), value(change.field, change.before, oldCurrency, change.after, true),
        B.text("strong", "This version"), value(change.field, change.after, estimate.currency, change.before, false),
        change.field === "lines" ? B.text("p", "Compare the descriptions, quantities and prices above. Added or removed work requires a decision on this exact version.", "small muted") : null]);
    }));
  };
}());
