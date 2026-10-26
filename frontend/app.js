(function () {
  "use strict";
  var B = window.BF, main = document.getElementById("main"), routeEpoch = 0, lastRoute = "", queueState = { bucket: "active", query: "" }, sessionTimer, returnPoints = {}, confirmedView = null, confirmedHash = "", confirmedAccount = null;
  var role = function (r) { return !!(B.session && B.session.account && B.session.account.roles.indexOf(r) !== -1); };
  var customer = function () { return !!(B.session && B.session.account && B.session.account.kind === "customer"); };
  var desk = function () { return role("manager") || role("intake"); };
  function pathJob(id, section) { return "#/jobs/" + encodeURIComponent(id) + (section ? "/" + section : ""); }
  function list(items, render, empty) { return items && items.length ? B.el("div", {}, items.map(render)) : B.empty(empty || "Nothing recorded yet."); }
  function viewTitle(eyebrow, title, text, actions) {
    return B.el("div", { class: "page-head" }, [B.el("div", {}, [B.text("p", eyebrow, "eyebrow"), B.text("h1", title), text ? B.text("p", text, "muted") : null]), actions || null]);
  }
  function actionBar(names, context) { return B.actionButtons(names, context || {}); }
  function group(title, names, context) { return B.actionSection(title, names, context); }
  function row(title, text, right) {
    return B.el("div", { class: "line-item" }, [B.el("div", {}, [B.text("strong", title), text ? B.text("p", text, "small muted") : null]), right || null]);
  }
  function timeline(items) {
    return items && items.length ? B.el("ol", { class: "list-reset timeline" }, items.map(function (e) {
      return B.el("li", {}, [B.text("time", B.date(e.occurred_at || e.recorded_at || e.at)),
        B.text("strong", e.label || B.human(e.summary_code || e.decision || e.event_type)),
        e.explanation ? B.text("p", e.explanation) : null,
        e.actor ? B.text("p", e.actor.display, "small muted") : null,
        e.method ? B.text("p", B.human(e.method) + (e.estimate_version ? " · v" + e.estimate_version : ""), "small muted") : null,
        e.decision_id ? B.text("p", "Decision reference: " + e.decision_id, "small muted") : null,
        e.event_id && !customer() ? B.link("Event reference", "#/audit?event_id=" + encodeURIComponent(e.event_id), "small") : null]);
    })) : B.empty("No history recorded yet.");
  }
  B.estimateCard = function (e, withHeading) {
    var decision = typeof e.decision === "string" ? e.decision : e.decision.value;
    var card = B.el("section", { class: "panel" });
    card.appendChild(B.el("div", { class: "panel-head" }, [B.el("div", {}, [
      B.text(withHeading === false ? "h3" : "h2", "Estimate v" + e.version),
      B.text("p", B.human(e.state) + " · " + B.human(decision), "small muted")
    ]), B.text("p", B.money(e.total_minor, e.currency), "balance money")]));
    card.appendChild(B.facts([["Issued", B.date(e.issued_at)], ["Accept before", B.date(e.expires_at)], ["Timezone", e.shop_timezone]]));
    card.appendChild(list(e.lines, function (l) {
      var unknownPart = l.kind === "part" && !l.part_id;
      return row(l.description, l.quantity + " × " + B.money(l.unit_amount_minor, e.currency) +
        (unknownPart ? " · Catalog identity was not recorded; no match is inferred." : ""),
        unknownPart ? B.text("span", "Historical catalog identity unknown", "badge caution") : B.text("span", B.money(l.line_amount_minor, e.currency), "money"));
    }));
    card.appendChild(row(e.tax_label, null, B.text("span", B.money(e.tax_minor, e.currency), "money")));
    card.appendChild(B.text("p", "Tax: " + B.human(e.tax_source) + " · policy revision " + (e.tax_policy_rev || "unknown historical provenance") + ". No tax is calculated automatically.", "small muted"));
    if (decision === "approved") card.appendChild(B.callout("Recorded approval", "A timely approval survives its proposal deadline while scope is unchanged. A later material revision requires new current-version approval."));
    else if (e.state === "expired_unapproved") card.appendChild(B.callout("No longer open for acceptance", "Ask the shop to issue an updated estimate. The server enforces the exact deadline.", "caution"));
    if (e.change_from_previous && e.change_from_previous.length) {
      card.appendChild(B.text("h3", "What changed"));
      card.appendChild(B.estimateChanges(e));
    }
    if (e.decision && e.decision.decided_at) card.appendChild(B.text("p", "Decision recorded " + B.date(e.decision.decided_at) + " · " + B.human(e.decision.method) + " · reference " + e.decision.decision_id, "small muted"));
    return card;
  };
  function shell() {
    var identity = document.getElementById("identity"), nav = document.getElementById("navigation"); B.clear(identity); B.clear(nav);
    var s = B.session;
    if (!s || !s.authenticated) { nav.appendChild(B.link("Sign in", "#/login")); nav.appendChild(B.link("Activate invitation", "#/activate")); nav.appendChild(B.link("Recover access", "#/recover")); return; }
    var a = s.account;
    identity.appendChild(B.el("div", { class: "identity" }, [
      B.el("p", {}, [B.text("strong", a.display_name), B.text("small", a.kind === "customer" ? "Your private repair account" : a.roles.map(B.human).join(" · "))]),
      B.button("Sign out", async function (event) {
        var button = event.currentTarget; button.disabled = true;
        try {
          await B.request("/auth/logout", {}); B.session = null; B.safeDraft = null; returnPoints = {}; confirmedView = null; confirmedHash = ""; confirmedAccount = null; clearTimeout(sessionTimer);
          var d = document.getElementById("action-dialog"); if (d.open) d.close();
          B.clear(main); B.clear(document.getElementById("notice")); location.hash = "#/login"; await B.getSession(); shell(); B.refresh();
        } catch (e) { B.notice("Sign-out not confirmed", "The server could not confirm logout. Close this browser if you are leaving a shared device, then retry.", "error"); button.disabled = false; }
      })
    ]));
    var links = customer() ? [["My jobs", "#/"], ["Account", "#/account"]] : [["Actions", "#/"], ["Jobs", "#/search"], ["Parts", "#/parts"]];
    if (desk()) links.push(["Customers", "#/customers"], ["Contact requests", "#/contacts"]);
    if (role("manager")) links.push(["Shop", "#/shop"]);
    links.forEach(function (l) { var n = B.link(l[0], l[1]); var current = location.hash || "#/"; if (current === l[1] || (l[1] === "#/shop" && /^#\/(shop|accounts|profiles|policy|backups|audit)/.test(current))) n.setAttribute("aria-current", "page"); nav.appendChild(n); });
    if (desk()) nav.appendChild(B.link("New intake", "#/intake", "button primary"));
  }
  B.expired = function () {
    clearTimeout(sessionTimer);
    confirmedView = null; confirmedHash = ""; confirmedAccount = null;
    if (B.captureDraft) B.captureDraft();
    var d = document.getElementById("action-dialog"); if (d.open) d.close();
    B.clear(main); main.appendChild(viewTitle("Access paused", "Sign in again", "Only safe categorical draft choices are held in this tab. Contact details, notes and secrets are not retained."));
    shell();
    B.notice("Sign in again to continue", "Your session expired or access changed. No action is auto-submitted. Reopen the current record after signing in and review consequential actions again.", "caution");
    var n = document.getElementById("notice"); n.appendChild(B.link("Sign in again", "#/login", "button primary"));
  };
  function offerDraft() {
    var draft = B.safeDraft;
    if (!draft) return;
    if (draft.issuer !== B.session.account.id) { B.safeDraft = null; return; }
    B.notice("Safe draft choices available", "Only categorical/numeric choices were kept in this tab. Text and sensitive values must be entered again. Current revisions and permissions will be fetched; nothing will be submitted automatically.");
    document.getElementById("notice").appendChild(B.button("Reopen " + B.actions[draft.name].label, function () {
      B.safeDraft = null; B.openAction(draft.name, draft.base);
    }));
    document.getElementById("notice").appendChild(B.button("Discard draft", function () { B.safeDraft = null; B.clear(document.getElementById("notice")); }));
  }
  function watchSession() {
    clearTimeout(sessionTimer);
    if (!B.session || !B.session.authenticated) return;
    var end = Math.min(Date.parse(B.session.idle_expires_at), Date.parse(B.session.absolute_expires_at));
    var ms = end - Date.now();
    if (isFinite(ms) && ms > 0) sessionTimer = setTimeout(async function () {
      // Read the server: another tab/request may have advanced idle expiry.
      try { var s = await B.getSession(); if (!s.authenticated) B.expired(); else watchSession(); } catch (_) { B.notice("Session status unavailable", "Reconnect before recording changes. Your session is always checked by the server.", "caution"); }
    }, ms + 200);
  }
  function authPage(mode) {
    var activate = mode === "activate", recover = mode === "recover";
    var fields = [];
    if (activate || recover) fields.push(B.field("code", "One-time " + (activate ? "invitation" : "recovery") + " code", "code", { hint: "Use the code given to you by the shop. Never place a code in a URL." }));
    if (!recover) fields.push(B.field("login_id", activate ? "Choose a login ID" : "Login ID", "text", { hint: activate ? "3–64 letters, digits, dots, underscores or hyphens. This is not an email address." : "Use your individual account, not a role." }));
    fields.push(B.field(recover ? "new_password" : "password", activate || recover ? "Choose a new password" : "Password", "password", { minLength: activate || recover ? 12 : 1, autocomplete: activate || recover ? "new-password" : "current-password", hint: activate || recover ? "At least 12 characters. Passwords are never trimmed or saved by this app." : "No public registration or role selection." }));
    var form = B.form(fields, {}), label = activate ? "Activate invitation" : recover ? "Reset password" : "Sign in";
    var submit = B.el("button", { text: label, type: "submit", class: "primary" }); form.appendChild(submit);
    form.addEventListener("submit", async function (event) {
      event.preventDefault(); var data = form.read(); if (!data) return; submit.disabled = true;
      try {
        if (!B.session || !B.session.csrf_token) await B.getSession();
        var res = await B.request("/auth/" + (activate ? "activate" : recover ? "recover" : "login"), data);
        form.clearSecrets();
        if (activate || recover) {
          await B.getSession(); B.notice(activate ? "Account activated" : "Password reset", "Sign in with your login ID and new password. Previous sessions and codes cannot be used.", "success"); location.hash = "#/login";
        } else {
          B.session = res; shell(); watchSession(); B.clear(document.getElementById("notice")); location.hash = "#/"; offerDraft();
        }
      } catch (e) {
        form.clearSecrets();
        form.showErrors(e.fields || [], e.code === "AUTH_INVALID" ? "Sign-in could not be completed. Check details or contact the shop." : B.errorText(e));
      } finally { submit.disabled = false; }
    });
    var card = B.el("div", { class: "login" }, [
      B.text("p", "Individual access / no role picker", "eyebrow"), B.text("h1", label),
      B.text("p", activate ? "Your invitation is already bound to the right service record. Activation does not claim jobs by email or reference." : recover ? "Ask the shop to verify your identity and provide a one-time recovery code. There is no automated email reset." : "Your repair, its permission and the next step—kept together."),
      B.el("section", { class: "panel" }, form),
      B.el("div", { class: "actions" }, [B.link("Sign in", "#/login"), B.link("Activate invitation", "#/activate"), B.link("Recover access", "#/recover")]),
      B.callout("Need help?", "Contact the shop if access cannot be restored. The host operator recovers the last manager offline; nobody should ask you to disclose your password.")
    ]); return card;
  }
  function searchForm(label, initial, submit, extra) {
    var input = B.el("input", { type: "search", value: initial || "", maxlength: 120, placeholder: "Name, device or docket code" });
    var form = B.el("form", { class: "search" }, [B.el("label", {}, [B.text("span", label), input]), extra || null, B.el("button", { text: "Search", type: "submit", class: "primary" })]);
    form.addEventListener("submit", function (e) { e.preventDefault(); submit(input.value.trim()); }); return form;
  }
  function pager(page, fetchNext, renderItem, host) {
    if (!page.next_cursor) return;
    var button = B.button("Load more", async function () {
      button.disabled = true;
      try {
        var next = await fetchNext(page.next_cursor);
        next.items.forEach(function (item) { host.insertBefore(renderItem(item), button); });
        button.remove(); pager(next, fetchNext, renderItem, host);
      } catch (e) { B.notice("More results unavailable", B.errorText(e), "error"); button.disabled = false; }
    });
    host.appendChild(button);
  }
  function queueCard(q) {
    return B.el("article", { class: "queue-card", id: "job-" + q.job_id }, [
      B.text("p", q.job_code + " / " + B.human(q.phase || q.lifecycle), "job-code"),
      B.el("h2", {}, B.link(q.customer_label, pathJob(q.job_id))), B.text("p", q.device_label, "small muted"),
      B.badges(q.blockers), B.el("div", { class: "next" }, [B.text("strong", q.lifecycle === "closed" ? "Closed / no pending repair work" : q.next_action), q.lifecycle === "closed" ? null : B.text("p", B.date(q.next_action_due_at), "small")]),
      B.text("p", "Owner · " + q.owner.label, "small muted"), B.link("Open docket →", pathJob(q.job_id), "small")
    ]);
  }
  async function actionsPage() {
    var bucket = queueState.bucket, options = ["active","due","overdue"].concat(role("manager") ? ["reassignment","manager_intake"] : []);
    var filter = B.el("select", { "aria-label": "Action queue view" }, options.map(function (s) { return B.el("option", { value: s, text: B.human(s) }); }));
    filter.value = bucket; filter.addEventListener("change", function () { queueState.bucket = filter.value; B.refresh(); });
    var page = await B.request(B.query("/staff/queue", { bucket: bucket }));
    var queue = B.el("section", { class: "panel", "aria-label": "Action queue" }, [
      B.el("div", { class: "panel-head" }, [B.text("h2", B.human(bucket) + " actions"), B.text("span", page.items.length + (page.next_cursor ? "+" : "") + " shown", "badge")]),
      filter, list(page.items, queueCard, "No actions due in this view. This does not mean every repair is complete.")
    ]);
    pager(page, function (cursor) { return B.request(B.query("/staff/queue", { bucket: bucket, cursor: cursor })); }, queueCard, queue);
    return B.el("div", {}, [
      viewTitle("Workbench / action queue", "What needs a next step?", "A compact, accountable queue. Open a docket to see every blocker before acting.", desk() ? B.link("Receive a device", "#/intake", "button primary") : null),
      searchForm("Find a docket", queueState.query, function (q) { queueState.query = q; location.hash = "#/search?query=" + encodeURIComponent(q); }),
      B.el("div", { class: "queue-layout" }, [queue, B.el("div", {}, [
        B.el("section", { class: "panel docket" }, [B.text("p", "Permission → Scope → Stock → Quality → Handover", "eyebrow"), B.text("h2", "The docket is the source of truth."),
          B.text("p", "Start with the next action, not a status change. Each repair keeps its current approval, immutable estimate versions, parts, accountable owner and property record together."),
          B.callout("Several blockers can be true at once", "Approval clears only its permission gate. A missing part, QC failure or safety stop remains visible and enforced."),
          B.link("Search all permitted jobs", "#/search", "button")]),
        B.panel("Manual by design", [B.text("p", "Phone contact, supplier orders and payments are attributable records. BenchFlow does not send messages, place supplier orders or process money.")])
      ])])
    ]);
  }
  async function jobsSearch(query) {
    var wrap = B.el("div", {}, [viewTitle("Workbench / jobs", "Find the repair", "Search the jobs your assigned access permits.")]);
    wrap.appendChild(searchForm("Job, customer or device", query, function (q) { location.hash = "#/search?query=" + encodeURIComponent(q); }));
    if (!query) { wrap.appendChild(B.empty("Enter a job code, customer contact/name or device identity to search.")); return wrap; }
    var page = await B.request(B.query("/staff/jobs", { query: query }));
    var host = B.el("section", { class: "panel" }, list(page.items, queueCard, "No jobs found for “" + query + "”. Try a different term; nothing has been created."));
    pager(page, function (cursor) { return B.request(B.query("/staff/jobs", { query: query, cursor: cursor })); }, queueCard, host);
    wrap.appendChild(host); return wrap;
  }
  async function customersPage(query, intake) {
    var wrap = B.el("div", {}, [viewTitle(intake ? "New intake / 1 · match the person" : "Workbench / customers", intake ? "Find the customer's record first" : "Customer records",
      "An invitation links history only after manual identity verification. Name or email alone never proves ownership.", actionBar(["customer.create"]))]);
    wrap.appendChild(searchForm("Name, existing phone or email", query, function (q) { location.hash = (intake ? "#/intake" : "#/customers") + "?query=" + encodeURIComponent(q); }));
    if (!query) { wrap.appendChild(B.empty("Search existing customers before opening a new record. Unknown contact details can be recorded explicitly.")); return wrap; }
    var page = await B.request(B.query("/staff/customers", { query: query }));
    function item(c) { return B.el("article", { class: "queue-card" }, [
      B.el("h2", {}, B.link(c.display_name, "#/customers/" + c.customer_id)),
      B.text("p", (c.phone_masked || "No phone") + " · " + (c.email_masked || "No email"), "small muted"),
      c.possible_duplicate ? B.text("span", "Possible duplicate · verify, do not auto-merge", "badge caution") : null,
      B.text("p", "Account · " + B.human(c.account_state), "small"),
      B.link(intake ? "Review record & receive device →" : "Open customer →", "#/customers/" + c.customer_id)
    ]); }
    var host = B.el("section", { class: "panel" }, list(page.items, item, "No matching records. Check spelling and known contacts before creating a record."));
    pager(page, function (cursor) { return B.request(B.query("/staff/customers", { query: query, cursor: cursor })); }, item, host); wrap.appendChild(host); return wrap;
  }
  async function customerDetail(id) {
    var c = await B.request("/staff/customers/" + encodeURIComponent(id)), ctx = { customer: c };
    var wrap = B.el("div", {}, [B.link("← Customer search", "#/customers"),
      viewTitle("Customer record / revision " + c.customer_rev, c.display_name, null, actionBar(["job.create"], ctx)),
      B.panel("Contact & account", [B.facts([["Phone", B.known(c.phone)], ["Email", B.known(c.email)], ["Account", c.account_id ? c.account_id : "Not invited"]]),
        c.notes ? B.el("div", { class: "callout" }, [B.text("strong", "Internal service note"), B.text("p", c.notes)]) : null,
        group("Contact correction & invitation", ["customer.update_contact","account.invite_customer"], ctx),
        role("manager") && c.account_id ? B.link("Manage linked account", "#/accounts/" + c.account_id, "button") : null,
        c.account_id ? group("Verified linked-customer recovery", ["account.issue_recovery"], { customer: c, linkedCustomerId: c.customer_id }) : null
      ]),
      c.possible_duplicates.length ? B.panel("Possible duplicates · not proof of identity", c.possible_duplicates.map(function (x) {
        return B.el("p", {}, [B.link(x.display_name, "#/customers/" + x.customer_id), " · " + x.match_reasons.map(B.human).join(", ")]);
      }).concat([group("Verified manager correction", ["customer.merge"], ctx)])) : group("Verified manager correction", ["customer.merge"], ctx),
      B.panel("Repair history", [list(c.jobs, queueCard, "No devices received for this customer yet.")])
    ]); return wrap;
  }
  function jobHeader(j) {
    var customerView = customer(), summary = customerView ? j.status : j.summary, ctx = { job: j };
    var header = B.el("div", {}, [B.link(customerView ? "← My jobs" : "← Actions", "#/"),
      viewTitle(j.job_code + (customerView ? "" : " / revision " + j.job_rev), B.device(j.device),
        customerView ? summary.explanation : j.customer.display_name + " · " + B.human(j.lifecycle) + " / " + B.human(summary.phase), B.el("button", { id: "refresh-docket", type: "button", text: "Refresh docket", onclick: B.refresh })),
      customerView ? B.callout(summary.label, summary.explanation) : null,
      B.blockers(summary.blockers)]);
    if (!customerView) {
      header.appendChild(B.el("section", { class: "panel docket" }, [
        B.facts([["Owner", j.assignment.owner.label], [j.lifecycle === "closed" ? "Last recorded action" : "Next action", j._queueSummary ? j._queueSummary.next_action + " · " + j._queueSummary.owner.label + " · due " + B.date(j._queueSummary.next_action_due_at) :
          "Current queue action unavailable. See Actions for the assigned next step."],
          ["Follow-ups", j.follow_ups.filter(function (x) { return x.state !== "resolved"; }).map(function (x) { return x.next_action + " · " + x.owner.label + " · due " + B.date(x.due_at) + " · " + B.human(x.state); }).join("; ") || "No unresolved follow-up records"],
          ["Repair authorization", B.human(summary.repair_gate)], ["Diagnostic permission", B.human(summary.diagnostic_permission)], ["Balance", B.money(summary.balance.amount_minor, summary.balance.currency)]]),
        group("Next action & contact", ["job.update_follow_up","job.record_contact_attempt","job.assign"], ctx)
      ]));
    }
    return header;
  }
  function jobOverview(j) {
    var ctx = { job: j };
    return B.el("div", { class: "split" }, [
      B.el("div", {}, [
        B.panel("Received condition & limits", [B.facts([["Reported issue", j.intake.reported_issue], ["Condition", B.known(j.intake.condition)], ["Accessories", j.intake.accessories.known ? j.intake.accessories.items.join(", ") || "None" : "Unknown"], ["Testing constraints", B.known(j.intake.testing_constraints)],
          ["Received", B.date(j.intake.received_at)], ["Acknowledgement", j.intake.acknowledgement.text_version + " · " + B.human(j.intake.acknowledgement.method)]]),
          group("Cancellation / correction", ["job.cancel","job.reopen"], ctx)]),
        B.panel("Findings & service disputes", [
          B.callout("Separate records, separate gates", "No-fault and unrepairable findings do not cancel, add fees or release property. A dispute and its resolution never replace consent, QC, money or coverage decisions."),
          actionBar(["job.record_disposition","job.open_dispute"], ctx),
          j.dispositions ? list(j.dispositions, function (d) {
            return B.el("article", { class: "callout" }, [
              B.text("h3", B.human(d.kind) + " · " + B.human(d.state)), B.text("p", d.summary),
              B.facts([["Reference", d.disposition_id], ["Revision", d.disposition_rev], ["Owner", d.owner_staff_id], ["Next action", d.next_action], ["Due", B.date(d.next_action_due_at)], ["Recorded by", d.actor_staff_id], ["Recorded", B.date(d.recorded_at)]]),
              d.state === "resolved" ? B.facts([["Resolution", d.resolution_summary], ["Resolved by", d.resolved_by_staff_id], ["Resolved", B.date(d.resolved_at)]]) : null,
              d.kind === "dispute" && d.state === "open" ? actionBar(["job.resolve_dispute"], { job: j, dispositionId: d.disposition_id }) : null
            ]);
          }, "No finding disposition or dispute recorded.") : B.callout("Disposition history unavailable", "The backend did not supply the contracted history. No empty history is inferred.", "caution"),
          group("Manager dispute resolution", ["job.resolve_dispute"], ctx)
        ]),
        B.panel("Follow-up & contact records", [list(j.follow_ups, function (fu) { return row(fu.next_action, fu.owner.label + " · " + B.date(fu.due_at), B.text("span", B.human(fu.state), "badge")); }, "No follow-up recorded."),
          B.text("p", "Contact attempts are recorded as attributable events in History; a contact attempt never becomes consent.", "small muted"), actionBar(["job.record_contact_attempt"], ctx)])
      ]),
      B.el("aside", { "aria-label": "Docket evidence" }, [
        B.panel("Current gates", [B.facts([["Parts", B.human(j.summary.parts_state)], ["Work", B.human(j.summary.work_state)], ["Quality", B.human(j.summary.qc_state)], ["Custody", B.human(j.summary.property_state)]])]),
        B.panel("Linked returns", [returnLinks(j), returnActions(j)]),
        desk() ? B.link("Open customer record", "#/customers/" + j.customer.customer_id, "button") : null
      ])
    ]);
  }
  function returnLinks(j) {
    return list(j.return_cases, function (r) {
      return B.el("div", { class: "callout" }, [B.text("strong", B.human(r.coverage || "Awaiting coverage review")),
        B.text("p", r.reported_recurrence), B.text("p", r.owner.label + " · " + r.next_action + " · " + B.date(r.next_action_due_at), "small"),
        B.facts([["Case", r.return_case_id], ["Case revision", r.return_rev], ["Original revision", r.original_job_rev], ["Return job revision", r.return_job_rev]]),
        B.el("div", { class: "actions" }, [B.link("Original repair", pathJob(r.original_job_id)), B.link("Open NEW return for inspection / coverage · " + r.reported_recurrence, pathJob(r.return_job_id, "returns"))])]);
    }, "No linked recurrence case. A return creates its own job and fresh permissions.");
  }
  function returnActions(j) {
    return actionBar((j.return_cases || []).some(function (r) { return r.return_job_id === j.job_id; }) ? ["return.create","return.assess"] : ["return.create"], { job: j });
  }
  function diagnosis(j) {
    var ctx = { job: j }, p = j.diagnostic.current;
    return B.el("div", {}, [
      B.panel("Separate diagnostic permission", [
        p ? B.facts([["Decision", B.human(p.state)], ["Scope", p.scope], ["Fee", B.money(p.fee.amount_minor, p.fee.currency)], ["Fee source", B.human(p.fee_source)], ["Fee policy revision", p.fee_policy_rev || "Unknown historical provenance"], ["Permission", p.permission_id], ["Method", B.human(p.method)], ["Attribution", p.attribution || "Customer account"], ["Recorded", B.date(p.decided_at)]]) : B.empty("Diagnostic permission has not been requested. Intake is not permission."),
        actionBar(["diagnostic.permission"], ctx)]),
      B.panel("Tests, findings & limitations", [
        actionBar(["diagnostic.record"], ctx),
        list(j.diagnostic.records, function (r) {
          return B.el("article", { class: "queue-card" }, [B.text("h3", B.human(r.kind)), B.text("p", r.summary),
            r.customer_visible ? B.callout("Customer-safe finding", r.customer_text) : B.text("p", "Internal only", "small muted"),
            r.uncertainty ? B.text("p", "Uncertainty: " + r.uncertainty) : null, B.text("time", B.date(r.recorded_at), "small muted")]);
        }, "No findings recorded. No diagnosis is inferred from the current phase.")])
    ]);
  }
  function estimates(j) {
    var ctx = { job: j }, current = B.currentEstimate(ctx), hasUnknownCurrentPart = B.hasUnknownCatalogPart(current);
    return B.el("div", {}, [
      B.panel("Draft → issue → explicit decision", [B.text("p", "Issued versions are immutable. A material revision holds all new repair starts job-wide and safely stops active work. Previous decisions and completed authorized charges remain in history."),
        hasUnknownCurrentPart ? B.callout("Historical catalog identity unknown", "One or more current estimate part lines have no recorded catalog item. Do not infer a match or issue/approve this historical scope as a catalog mapping. Create a new mapped draft, issue a fresh immutable version, and obtain a new explicit decision before part-dependent work.", "caution") : null,
        actionBar(["estimate.draft_save","estimate.issue"], ctx),
        group("Staff-recorded phone / in-person decision", ["estimate.decision","estimate.revoke"], ctx)]),
      current ? B.estimateCard(current) : B.empty("No estimate has been issued."),
      B.panel("Unissued drafts", [list(j.estimate_drafts, function (d) {
        return B.el("details", { class: "disclosure" }, [B.text("summary", "Draft " + d.draft_id.slice(0, 8) + " · updated " + B.date(d.updated_at)),
          B.el("div", {}, d.lines.map(function (l) {
            var unknownPart = l.kind === "part" && !l.part_id;
            return row(l.description, B.human(l.kind) + " · " + l.quantity + " × " + B.money(l.unit_amount_minor, d.currency) +
              (unknownPart ? " · Historical catalog identity unknown; no match is inferred." : ""),
              unknownPart ? B.text("span", "Read-only historical line", "badge caution") : null);
          })),
          B.text("p", d.tax_label + " · " + B.money(d.tax_minor, d.currency), "small")]);
      }, "No unissued draft. A saved draft is never an approval.")]),
      B.panel("Earlier immutable versions", [list(j.estimates.filter(function (e) { return !current || e.version !== current.version; }), function (e) {
        return B.el("details", { class: "disclosure" }, [B.text("summary", "v" + e.version + " · " + B.money(e.total_minor, e.currency) + " · " + B.human(e.decision.value)), B.estimateCard(e)]);
      }, "No earlier version.")])
    ]);
  }
  function jobParts(j) {
    var ctx = { job: j };
    return B.el("div", {}, [
      B.panel("Demand, orders & stock", [B.text("p", "Available, reserved and on-hand are different quantities. ETA and supplier confirmation are manual evidence, not guarantees."),
        B.hasUnknownCatalogPart(B.currentEstimate(ctx)) ? B.callout("Catalog-dependent controls unavailable", "This current estimate includes part lines with unknown historical catalog identity. Demand, reservation and repair start controls are disabled for this docket until a verified catalog mapping is supplied through a fresh draft and approval.", "caution") : null,
        B.callout("Exact catalog mapping is required", "Every approved part line needs its exact catalog item, scope-bound demand and full active reservation. Missing or partial demand/allocation blocks start. External sourcing requires a recorded catalog receipt, then ordinary demand and reservation; it is not a bypass."),
        list((B.currentEstimate(ctx) || {}).lines && B.currentEstimate(ctx).lines.filter(function (l) { return l.kind === "part"; }), function (l) {
          var unknownPart = !l.part_id;
          return row(l.description + " · " + l.quantity + " required", unknownPart ?
            "Historical catalog identity unknown · no catalog ID is recorded · scope " + l.scope_ref :
            "Catalog " + l.part_id + " · scope " + l.scope_ref,
            l.part_id ? B.link("Inspect catalog stock", "#/parts/" + l.part_id + "?job_id=" + j.job_id) : B.text("strong", "Mapping unavailable"));
        }, "No part lines in the current estimate. The server still determines work readiness."),
        actionBar(["parts.demand","parts.reserve"], ctx),
        group("Orders, physical receipt & release", ["parts.manual_order","parts.record_order_outcome","parts.receive","parts.release"], ctx)]),
      list(j.parts, function (p) {
        return B.panel(p.sku + " / " + p.description, [
          B.facts([["State", B.human(p.state)], ["Demand", p.required_qty], ["Demand references", p.requirement_ids ? p.requirement_ids.join(", ") : "Unavailable"], ["Stock revision", p.stock_rev], ["On hand", p.on_hand_qty], ["Reserved across stock", p.reserved_qty], ["Available", p.available_qty], ["Scope", p.scope_ref]]),
          B.link("Stock movements", "#/parts/" + p.part_id + "?job_id=" + encodeURIComponent(j.job_id)),
          B.text("h3", "This job's reservations"),
          list(p.reservations, function (r) { return row(r.reservation_id.slice(0, 8), "Reserved " + r.qty + " · consumed " + r.consumed_qty + " · released " + r.released_qty, B.text("span", B.human(r.state), "badge")); }, "No reservation."),
          B.text("h3", "Manual supplier orders"),
          list(p.orders, function (o) { return B.el("div", { class: "callout" }, [B.text("strong", o.supplier_label + " · " + B.human(o.state)),
            B.text("p", o.qty + " units · ETA " + B.date(o.eta_at)), B.text("p", o.eta_evidence || "No ETA evidence"), o.outcome_evidence ? B.text("p", o.outcome_evidence) : null]); }, "No order recorded.")
        ]);
      }, "No parts demand recorded. This is not evidence that a repair needs no parts.")
    ]);
  }
  function work(j) {
    var ctx = { job: j };
    return B.el("div", {}, [
      B.panel("Authorized work", [B.text("p", "Each start or resume must pass current diagnostic / repair scope and stock gates. Task completion is separate from quality readiness."),
        actionBar(["work.task_create","work.start"], ctx), group("Work log, safe pause, completion & consumption", ["work.log","work.consume"], ctx)]),
      list(j.work, function (w) {
        var assignee = (j._staff || []).find(function (person) { return person.staff_id === w.assigned_staff_id; });
        var taskActions = ["work.start","work.log","work.consume"];
        if (!w.rework_of_task_id) taskActions.push("qc.record");
        taskActions.push("work.task_reassign");
        return B.panel(B.taskPurpose(w, j) + " · " + B.human(w.state), [
          B.facts([["Scope", B.taskScope(w, j)], ["Assigned", assignee ? assignee.display_name : w.assigned_staff_id ? "Name unavailable in authorized directory" : "Not assigned"]]),
          B.el("details", { class: "disclosure" }, [B.text("summary", "Task references"), B.facts([["Task", w.task_id], ["Scope", w.scope_ref], ["Task revision", w.task_rev], ["Assigned reference", w.assigned_staff_id]])]),
          list(w.logs, function (l) { return row(B.human(l.kind) + " · " + B.date(l.recorded_at), l.summary + (l.next_action ? " / Next: " + l.next_action + " · " + B.date(l.next_action_due_at) : "")); }, "Not started."),
          actionBar(taskActions, { job: j, taskId: w.task_id })
        ]);
      }, "No tasks planned. Planning is not permission to begin."),
      B.panel("Quality, rework & retest", [
        B.callout("Required checks are explicit", "Failed or not-tested checks block repaired readiness. Complete linked rework, then record the fresh retest on the original task. The correction task is not a QC target; no manager pass override."),
        actionBar(["qc.record"], ctx),
        list(j.qc, function (q) { return row(q.label + (q.required ? " · required" : " · optional"), "Run " + q.run_id + " · check " + q.check_id + " · Profile v" + q.profile_version + " · " + (q.result_note || "No result note") + " · " + B.date(q.recorded_at), B.text("span", B.human(q.state), "badge " + (q.state === "passed" ? "" : "caution"))); }, "No configured quality checks reported. Do not infer a pass.")
      ]),
      B.panel("Immutable QC runs", [
        j.qc_history ? list(j.qc_history, function (run) {
          return B.el("details", { class: "disclosure" }, [
            B.text("summary", B.human(run.overall) + " · " + B.date(run.recorded_at) + " · run " + run.run_id),
            B.facts([["Task", run.task_id], ["Profile version", run.profile_version], ["Recorded by", run.actor_staff_id], ["Reason", run.reason || "Not supplied"]]),
            list(run.checks, function (check) { return row(check.check_id, check.note || "No note", B.text("strong", B.human(check.result))); })
          ]);
        }, "No QC runs recorded. No pass is inferred.") : B.callout("QC history unavailable", "The backend did not supply immutable runs. Current checks are not a substitute for historical results.", "caution")
      ])
    ]);
  }
  function settlement(j) {
    var s = j.settlement, ctx = { job: j }, currency = s.currency;
    function entries(title, items, idKey) { return B.panel(title, [list(items, function (e) {
      return row(e[idKey].slice(0, 8) + " · " + (e.simulated ? "Simulated (fictional)" : B.human(e.kind || e.method || e.source || "Recorded")),
        B.date(e.posted_at || e.occurred_at || e.recorded_at) + (e.external_reference ? " · " + e.external_reference : "") + (e.estimate_version ? " · estimate v" + e.estimate_version : ""),
        B.text("span", B.money(e.amount ? e.amount.amount_minor : e.delta_minor, e.amount ? e.amount.currency : e.currency || currency), "money"));
    }, "No " + title.toLowerCase() + " recorded.")]); }
    return B.el("div", {}, [
      B.panel("Settlement / records, not processing", [
        B.text("p", B.money(s.balance_minor, currency), "balance money"), B.text("p", "Server-calculated balance. Charges − credits − payments + refunds, including attributed corrections."),
        B.session.demo_mode ? B.callout("Fictional demo only", "Simulated payments change only the fictional workflow balance; they do not represent money received.", "caution") : null,
        actionBar(B.session.demo_mode ? ["settlement.payment_simulate"] : ["settlement.payment_record"], ctx),
        group("Charges, credits, refunds & corrections", ["settlement.charge","settlement.credit","settlement.refund_record","settlement.correct"], ctx)
      ]),
      B.el("div", { class: "grid" }, [entries("Charges", s.charges, "charge_id"), entries("Credits", s.credits, "credit_id"), entries("Payments", s.payments, "payment_id"), entries("Refunds", s.refunds, "refund_id")]),
      entries("Corrections", s.corrections, "correction_id"),
      B.panel(B.session.demo_mode ? "Property custody & simulated collection" : "Property custody & verified handover", [
        B.facts([["Custody", B.human(j.property.state)]]),
        list(j.property.authorized_collectors, function (c) { return row(c.collector_name, "Verified " + B.human(c.method) + " · " + B.date(c.verified_at)); }, "No authorized collector recorded."),
        j.property.handover ? (j.property.handover.simulated ?
          B.el("div", {}, [B.callout("Simulated demo collection", "Fictional workflow only. No collector was verified and no device was physically transferred.", "caution"),
            B.facts([["Fictional collector name", j.property.handover.collector_name], ["Recorded", B.date(j.property.handover.recorded_at)]])]) :
          B.facts([["Handed to", j.property.handover.collector_name], ["Acknowledgement", j.property.handover.acknowledgement], ["Recorded", B.date(j.property.handover.recorded_at)], ["Balance at handover", B.money(j.property.handover.balance_minor_at_handover, currency)]])) :
          B.callout(B.session.demo_mode ? "Still in custody" : "Still in custody until confirmed", "Cancellation or a settled balance alone never records handover. Verify collector and the correct repaired / unrepaired disposition.", "caution"),
        actionBar(B.session.demo_mode ? ["property.handover_simulate"] : ["property.authorize_collector","property.handover"], ctx)
      ])
    ]);
  }
  async function staffJob(id, section) {
    var j = await B.request("/staff/jobs/" + encodeURIComponent(id));
    // Detail omits the queue's job-level next action. Read the existing
    // authorized search contract rather than treating follow-up rows as it.
    await Promise.all([
      (async function () {
        try {
          var cursor = null;
          do {
            var page = await B.request(B.query("/staff/jobs", { query: j.job_code, cursor: cursor }));
            j._queueSummary = page.items.find(function (item) { return item.job_id === j.job_id; });
            cursor = page.next_cursor;
          } while (!j._queueSummary && cursor);
        } catch (_) { /* named unavailable state in the header */ }
      }()),
      (async function () {
        try { j._staff = (await B.request(B.query("/staff/assignable-staff", { job_id: j.job_id }))).items; }
        catch (_) { j._staff = []; }
      }())
    ]);
    var routes = [["overview","Overview"],["diagnosis","Diagnosis"],["estimates","Estimates"],["parts","Parts"],["work","Work & QC"],["settlement","Settlement & handover"],["returns","Returns"],["history","History"]];
    var wrap = B.el("div", {}, jobHeader(j));
    wrap.appendChild(B.el("nav", { class: "section-nav", "aria-label": "Docket sections" }, routes.map(function (r) {
      var a = B.link(r[1], pathJob(id, r[0])); if (r[0] === section) a.setAttribute("aria-current", "page"); return a;
    })));
    var content = section === "diagnosis" ? diagnosis(j) : section === "estimates" ? estimates(j) : section === "parts" ? jobParts(j) : section === "work" ? work(j) : section === "settlement" ? settlement(j) : section === "history" ? B.panel("Attributable history", [timeline(j.timeline)]) : section === "returns" ? B.panel("Linked recurrence & coverage", [returnLinks(j), returnActions(j), B.text("p", "Open the NEW return job for inspection permission and coverage assessment. Neither original approval nor warranty coverage grants new work authorization.")]) : jobOverview(j);
    wrap.appendChild(content); return wrap;
  }
  function customerJobCard(j) {
    return B.el("article", { class: "queue-card" }, [B.text("p", j.job_code, "job-code"), B.el("h2", {}, B.link(j.device_label, pathJob(j.job_id))),
      B.text("strong", j.customer_status.label), B.text("p", j.customer_status.explanation), B.badges(j.customer_status.blockers),
      B.text("p", "Updated " + B.date(j.updated_at), "small muted"), B.link("View repair & next step →", pathJob(j.job_id))]);
  }
  async function myJobs() {
    var page = await B.request("/me/jobs"), host = B.el("section", { class: "panel" }, list(page.items, customerJobCard, "No jobs are linked to this account yet. Contact the shop if you expected to see one."));
    pager(page, function (cursor) { return B.request(B.query("/me/jobs", { cursor: cursor })); }, customerJobCard, host);
    return B.el("div", {}, [viewTitle("Your private repair account", "My jobs", "Current repairs and previous service, linked to your verified account."), host]);
  }
  async function myJob(id) {
    var j = await B.request("/me/jobs/" + encodeURIComponent(id)), ctx = { job: j }, p = j.diagnostic_permission;
    return B.el("div", {}, [
      jobHeader(j),
      B.panel("Diagnostic permission · separate from repair", [
        p ? B.facts([["Decision", B.human(p.state)], ["Scope", p.scope], ["Diagnostic fee", B.money(p.fee.amount_minor, p.fee.currency)], ["Recorded", B.date(p.decided_at)]]) : B.empty("No diagnostic request is available yet. Ask the shop to explain the scope and fee."),
        p ? actionBar(["diagnostic.permission"], ctx) : null
      ]),
      j.current_estimate ? B.el("div", {}, [B.estimateCard(j.current_estimate), B.panel("Your exact-version decision", [
        B.text("p", "Review every line, the version, total, changes and deadline before deciding. Approval does not remove other blockers."),
        actionBar(["estimate.decision","estimate.revoke"], ctx)
      ])]) : B.panel("Repair estimate", [B.empty("No issued estimate to review. Diagnostic permission does not authorize repair.")]),
      B.panel("Findings shared with you", [list(j.findings, function (r) { return row(r.text, B.date(r.recorded_at)); }, "The shop has not shared a finding yet.")]),
      B.panel("Collection information", [
        B.callout(j.collection.ready ? "Ready for collection" : "Not ready for collection", j.collection.what_to_bring, j.collection.ready ? "success" : ""),
        B.facts([["Balance", B.money(j.collection.balance.amount_minor, j.collection.balance.currency)], ["Authorized collector", j.collection.authorized_collector], ["Quality check", B.human(j.quality_check.state)]]),
        j.quality_check.disclosure ? B.callout("Inspection disclosure", j.quality_check.disclosure) : null,
        j.collection.handover.simulated ? B.callout("Simulated demo collection", "Fictional workflow only; no collector was verified and no property was physically transferred.", "caution") :
          j.collection.handover.recorded_at ? B.facts([["Collected", B.date(j.collection.handover.recorded_at)], ["Collector", j.collection.handover.collector_name], ["Acknowledgement", j.collection.handover.acknowledgement]]) : null
      ]),
      B.panel("Something has returned?", [
        B.text("p", "A recurrence creates a separate linked repair for review. It does not change your original history or carry over repair permission."),
        j.return_request.allowed ? actionBar(["return.create"], ctx) : B.text("p", "Contact the shop to discuss any concern with this repair.", "muted"),
        list(j.return_request.linked_return_job_ids, function (returnId) { return B.el("p", {}, B.link("Open linked return job", pathJob(returnId))); }, "No linked return job.")
      ]),
      B.panel("Your decision history", [timeline(j.decision_history)]),
      B.panel("Service history", [timeline(j.status_history)])
    ]);
  }
  async function accountPage() {
    var a = await B.request("/me"), page = await B.request("/customer/contact-change-requests");
    var host = B.panel("Contact-change requests", [requestList(page.items, false)]);
    pager(page, function (cursor) { return B.request(B.query("/customer/contact-change-requests", { cursor: cursor })); }, function (r) { return requestList([r], false); }, host);
    return B.el("div", {}, [
      viewTitle("Your account", a.display_name, "An individual login linked to your verified service history."),
      B.panel("Access & contact", [B.facts([["Login ID", a.login_id], ["Session idle expiry", B.date(B.session.idle_expires_at)], ["Absolute expiry", B.date(B.session.absolute_expires_at)]]),
        actionBar(["contact.change_request"]), B.link("Recover lost access", "#/recover")]),
      host
    ]);
  }
  function requestList(items, staff) {
    return list(items, function (r) {
      return row(staff ? r.customer_label || r.display_name || "Contact change request" : (r.requested_fields || []).map(B.human).join(", "),
        B.human(r.state) + " · " + B.date(r.created_at || r.requested_at),
        staff ? B.link("Review & confirm", "#/contacts/" + encodeURIComponent(r.request_id), "button") : null);
    }, "No contact-change requests recorded.");
  }
  async function contactsPage(state) {
    var current = state || "pending", page = await B.request(B.query("/staff/contact-change-requests", { state: current }));
    var host = B.el("section", { class: "panel" }, requestList(page.items, true));
    function item(r) { return row(r.customer_label || "Contact change", B.human(r.state) + " · " + B.date(r.created_at), B.link("Review", "#/contacts/" + r.request_id)); }
    pager(page, function (cursor) { return B.request(B.query("/staff/contact-change-requests", { state: current, cursor: cursor })); }, item, host);
    return B.el("div", {}, [viewTitle("Service desk / verification", "Contact-change requests", "Pending requests do not change the customer record or verify an electronic channel."),
      B.el("div", { class: "actions" }, [B.link("Pending", "#/contacts?state=pending", "button"), B.link("Resolved", "#/contacts?state=resolved", "button")]), host]);
  }
  async function contactDetail(id) {
    var r = await B.request("/staff/contact-change-requests/" + encodeURIComponent(id));
    var fields = r.requested_values;
    return B.el("div", {}, [B.link("← Contact requests", "#/contacts"), viewTitle("Manual verification", "Review contact change", "Use the person's existing service record to confirm this request."),
      B.panel("Requested changes", [B.facts([["State", B.human(r.state)], ["Requested fields", (r.requested_fields || []).map(B.human).join(", ")], ["Name", B.known(fields.display_name)], ["Phone", B.known(fields.phone)], ["Email", B.known(fields.email)]]),
        r.customer_id ? B.link("Open existing customer record", "#/customers/" + r.customer_id, "button") : null,
        r.state === "pending" ? actionBar(["contact.change_resolve"], { request: r }) : B.facts([["Resolved", B.date(r.resolved_at)], ["Confirmed", B.human(r.confirmation_method)], ["Reason", B.human(r.reason_code)]])])]);
  }
  function shopLinks() {
    return B.el("nav", { class: "section-nav", "aria-label": "Shop administration" }, [["People & invitations","accounts"],["QC profiles","profiles"],["Policy","policy"],["Audit","audit"],["Backups & offline restore","backups"]].map(function (r) { return B.link(r[0], "#/" + r[1]); }));
  }
  function shopPage() {
    return B.el("div", {}, [
      viewTitle("Manager / shop", "Keep the shop accountable", "Access, policy and recovery are explicit decisions—not repair-gate overrides."), shopLinks(),
      B.el("div", { class: "grid" }, [
        B.panel("People & ownership", [B.text("p", "Invite individuals, review pending handoffs, revoke codes, change roles and reassign suspended staff actions."), B.link("Manage people", "#/accounts", "button"), B.link("Reassignment queue", "#/?bucket=reassignment", "button")]),
        B.panel("Quality configuration", [B.text("p", "Versioned profiles apply to future tasks. Only assigned technicians record results."), B.link("Quality profiles", "#/profiles", "button")]),
        B.panel("Audit & exceptions", [B.text("p", "Review attributable events and closed reason codes. No internal free text or secrets belong in audit."), B.link("Audit trail", "#/audit", "button")]),
        B.panel("Backup is not restore", [B.text("p", "Create a consistent snapshot, inspect its receipt and guide an offline host-operator restore. The live web app never replaces its database."), B.link("Backups & restore guide", "#/backups", "button")])
      ])
    ]);
  }
  async function accountsPage(state) {
    var current = state || "active", page = await B.request(B.query("/staff/accounts", { state: current }));
    function item(a) { return row(a.display_name, B.human(a.kind) + " · " + (a.roles.map(B.human).join(", ") || "Customer") + " · " + B.human(a.invite_state || a.state),
      B.link("Open account", "#/accounts/" + a.id, "button")); }
    var host = B.el("section", { class: "panel" }, list(page.items, item, "No accounts in this state."));
    pager(page, function (cursor) { return B.request(B.query("/staff/accounts", { state: current, cursor: cursor })); }, item, host);
    return B.el("div", {}, [viewTitle("Manager / people", "Individual accounts", "No one chooses roles at sign in.", actionBar(["account.invite_staff"])), shopLinks(),
      B.el("div", { class: "actions" }, ["active","invited","suspended","closed"].map(function (s) { return B.link(B.human(s), "#/accounts?state=" + s, "button"); })), host]);
  }
  async function accountDetail(id) {
    var a = await B.request("/staff/accounts/" + encodeURIComponent(id)), ctx = { account: a };
    return B.el("div", {}, [B.link("← People", "#/accounts"),
      viewTitle("Account / revision " + a.account_rev, a.display_name, "Sensitive changes revoke existing sessions and preserve attributable history."),
      B.panel("Access & invitation", [
        B.facts([["Kind", B.human(a.kind)], ["State", B.human(a.state)], ["Login", a.login_id || "Not activated"], ["Roles", a.roles.map(B.human).join(", ") || "Customer"],
          ["Credentials", B.human(a.credential_state)], ["Invitation", B.human(a.invite_state)], ["Follow-up owner", a.invitation_owner_staff_id], ["Due", B.date(a.invitation_next_action_due_at)]]),
        a.customer_id ? B.link("Bound customer record", "#/customers/" + a.customer_id, "button") : null,
        group("Invitation handoff", ["account.reissue_invite","account.revoke_invite"], ctx),
        group("Verified recovery", ["account.issue_recovery"], ctx),
        a.kind === "staff" ? group("Assigned roles", ["account.change_roles"], ctx) : null,
        group("Suspend, reactivate or close access", ["account.suspend","account.reactivate","account.close"], ctx),
        a.kind === "customer" ? group("Mistaken history link · closes account", ["account.unlink_customer"], ctx) : null
      ]),
      B.panel("Suspension & last-manager recovery", [
        B.text("p", "After suspending staff, reassign their owned next actions from the manager queue. Reactivation does not restore an old session."),
        B.link("Open reassignment queue", "#/?bucket=reassignment", "button"),
        B.callout("Last / only manager", "Use the host-operator recovery procedure in docs/RUNBOOK.md after verified in-person/phone identity confirmation. The manager personally enters a new password twice into a no-echo host prompt. No plaintext password or recovery code should be printed.")
      ])
    ]);
  }
  async function partsPage(query, jobId) {
    if (!desk() && !jobId) return B.el("div", {}, [viewTitle("Parts / assignment scoped", "Open parts from a docket", "Technician stock access requires an assigned job. Open its Parts section; do not search another job's inventory."), B.link("Back to assigned actions", "#/", "button primary")]);
    var page = await B.request(B.query("/staff/parts", { query: query, job_id: jobId }));
    function item(p) { return B.el("article", { class: "queue-card" }, [
      B.el("h2", {}, B.link(p.sku + " · " + p.description, "#/parts/" + p.part_id + (jobId ? "?job_id=" + jobId : ""))),
      B.facts([["On hand", p.on_hand_qty + " " + p.unit], ["Reserved", p.reserved_qty], ["Available", p.available_qty]])]); }
    var host = B.el("section", { class: "panel" }, list(page.items, item, "No parts recorded in this view. Add stock or record job demand; do not infer availability."));
    pager(page, function (cursor) { return B.request(B.query("/staff/parts", { query: query, job_id: jobId, cursor: cursor })); }, item, host);
    return B.el("div", {}, [viewTitle("Stock / quantity ledger", "Parts", "Demand, reservation and consumption stay distinct.", actionBar(["parts.create"])),
      searchForm("Stock code or description", query, function (q) { location.hash = "#/parts?query=" + encodeURIComponent(q) + (jobId ? "&job_id=" + encodeURIComponent(jobId) : ""); }), host]);
  }
  async function partDetail(id, jobId) {
    var p = await B.request(B.query("/staff/parts/" + encodeURIComponent(id), { job_id: jobId })), ctx = { part: p, jobId: jobId };
    return B.el("div", {}, [B.link("← Parts", "#/parts" + (jobId ? "?job_id=" + jobId : "")),
      viewTitle("Stock / revision " + p.stock_rev, p.sku, p.description),
      B.panel("Physical stock", [B.facts([["On hand", p.on_hand_qty + " " + p.unit], ["Reserved", p.reserved_qty], ["Available", p.available_qty]]),
        actionBar(["parts.receive","parts.adjust"], ctx)]),
      B.panel("Append-only movements", [list(p.movements, function (m) {
        return row(B.human(m.kind), "On hand " + (m.on_hand_delta > 0 ? "+" : "") + m.on_hand_delta + " · Reserved " + (m.reserved_delta > 0 ? "+" : "") + m.reserved_delta + " · " + B.date(m.occurred_at),
          m.job_id ? B.link("Linked docket", pathJob(m.job_id)) : B.text("span", B.human(m.reason), "small"));
      }, "No stock movements.")])]);
  }
  async function profilesPage(id) {
    var page = id ? null : await B.request("/staff/qc-profiles"), profile = id ? await B.request("/staff/qc-profiles/" + encodeURIComponent(id)) : null;
    function card(p) { return B.panel(p.label + " · v" + p.version, [
      B.text("p", p.active_for_new_tasks ? "Used for new tasks" : "Historical configuration", "small muted"),
      list(p.checks, function (c) { return row(c.label, c.required ? "Required" : "Optional"); }),
      id ? actionBar(["qc.profile_update"], { profile: p }) : B.link("Profile & version history", "#/profiles/" + p.profile_id, "button")
    ]); }
    var wrap = B.el("div", {}, [viewTitle("Manager / quality configuration", "Versioned QC profiles", "Changing a profile does not rewrite prior tasks or results.", actionBar(["qc.profile_create"])), shopLinks()]);
    if (id) {
      if (!profile.profile || !Array.isArray(profile.history)) throw new Error("The backend did not supply the contracted QCProfileDetail profile/history wrapper. No current-only substitute is used.");
      wrap.appendChild(card(profile.profile));
      wrap.appendChild(B.panel("Immutable version history", [list(profile.history, function (p) {
        return B.el("details", { class: "disclosure" }, [B.text("summary", "Version " + p.version + " · aggregate revision " + p.profile_rev + (p.active_for_new_tasks ? " · current" : " · historical")),
          list(p.checks, function (check) { return row(check.label, check.required ? "Required" : "Optional"); })]);
      })]));
    } else {
      var host = B.el("div", {}, list(page.items, card, "No QC profiles configured."));
      pager(page, function (cursor) { return B.request(B.query("/staff/qc-profiles", { cursor: cursor })); }, card, host); wrap.appendChild(host);
    }
    return wrap;
  }
  async function policyPage() {
    var p = await B.request("/staff/policy");
    return B.el("div", {}, [viewTitle("Manager / policy", "Shop policy", "Current server configuration. Issued estimates retain their original terms."), shopLinks(),
      B.panel("Policy revision " + p.policy_rev, [B.facts([["Timezone", p.shop_timezone], ["Estimate window", p.estimate_window_days + " days"], ["Diagnostic fee", B.money(p.diagnostic_fee_minor, p.currency)], ["Tax", p.tax_enabled ? p.tax_label : "Not enabled"]]),
        actionBar(["policy.update_demo"], { policy: p })])]);
  }
  async function auditPage(params) {
    var event = params.get("event_id"), aggregate = params.get("aggregate_id"), query = event ? { event_id: event } : aggregate ? { aggregate_id: aggregate } : {};
    if (!role("manager") && !event && !aggregate) return B.el("div", {}, [viewTitle("Audit / access", "Open history from a docket", "Shop-wide audit is manager-only. Open a permitted job's History to follow its event references."), B.link("Actions", "#/", "button")]);
    var page = await B.request(B.query("/staff/audit", query)), host = B.panel("Allowlisted event history", [timeline(page.items)]);
    pager(page, function (cursor) { return B.request(B.query("/staff/audit", Object.assign({}, query, { cursor: cursor }))); }, function (e) { return timeline([e]); }, host);
    var filter = B.form([B.field("kind", "Find by", "select", { options: [["event_id","Exact event reference"],["aggregate_id","Exact job / aggregate reference"]] }), B.field("reference", "Reference UUID", "text")], {}, { kind: event ? "event_id" : "aggregate_id", reference: event || aggregate || "" });
    filter.appendChild(B.el("button", { type: "submit", text: "Find attributed events", class: "primary" }));
    filter.addEventListener("submit", function (e) { e.preventDefault(); var data = filter.read(); if (data) location.hash = "#/audit?" + data.kind + "=" + encodeURIComponent(data.reference); });
    return B.el("div", {}, [viewTitle("Audit / attributed events", "What changed, and who recorded it?", "Event summaries are closed codes. Free-text service records and credentials do not belong in audit."), role("manager") ? shopLinks() : null,
      B.panel("Find an event or record history", [filter, role("manager") ? B.link("All shop events", "#/audit") : null]), host]);
  }
  function backupCard(b) {
    B.backupSummary(b);
    return B.panel("Snapshot · " + B.date(b.created_at), [
      B.facts([["Backup ID", b.backup_id], ["Schema", b.schema_version], ["Integrity", "Verified at creation only"], ["Bytes", b.size_bytes], ["SHA-256", b.sha256]]),
      B.el("div", { class: "actions" }, [
        B.link("Verify & inspect snapshot", "#/backups/" + b.backup_id, "button primary")
      ])
    ]);
  }
  async function backupsPage() {
    var page = await B.request("/staff/backups"), createId = B.uuid();
    var create = B.button("Create consistent backup", async function () {
      create.disabled = true;
      try {
        var receipt = await B.request("/staff/backups", { command_id: createId }), b = B.backupReceipt(receipt, createId);
        B.notice(receipt.status === "replayed" ? "Previously created · same operation" : "Backup created", "Verified at creation: " + b.backup_id + " · " + B.date(b.created_at) + ". This is not a restore.", "success"); B.refresh();
      } catch (e) { B.notice(e.unknown ? "Backup outcome unknown" : "Backup unavailable", B.errorText(e) + " Retrying this button uses the same operation ID: " + createId, "error"); create.disabled = false; }
    }, "primary");
    var host = B.el("div", {}, list(page.items, backupCard, "No backups recorded. A download or backup alone never proves recovery."));
    pager(page, function (cursor) { return B.request(B.query("/staff/backups", { cursor: cursor })); }, backupCard, host);
    return B.el("div", {}, [
      viewTitle("Manager / continuity", "Backups & offline restore", "Private shop data. No browser upload, live-database replacement or web restore action.", create), shopLinks(),
      B.callout("Treat every backup as confidential", "A SHA-256 hash detects corruption; it does not provide encryption or authenticity. Keep backups outside web/publication folders. Real-customer backup policy needs owner approval.", "caution"), host]);
  }
  function verifiedBackupPanel(b) {
    var status = B.el("div", { role: "status" });
    var download = B.button("Download private database", async function () {
      download.disabled = true; B.clear(status); status.appendChild(B.text("p", "Rechecking snapshot and requesting private download…"));
      try {
        await B.downloadBackup(b.backup_id);
        B.clear(status); status.appendChild(B.callout("Download handed to browser", "This is the database file only, not a complete offline restore bundle. Keep it confidential. This is not restore verification.", "success"));
      } catch (e) { B.clear(status); status.appendChild(B.callout("Download unavailable", B.errorText(e), "error")); }
      finally { download.disabled = false; }
    });
    return B.panel("Currently verified manifest", [
      B.facts([["Backup", b.backup_id], ["Created", B.date(b.created_at)], ["Format", b.format_version], ["Database", b.database_file], ["Schema", b.schema_version], ["Bytes", b.size_bytes], ["SHA-256", b.sha256], ["Integrity", "Server verified now · download rechecks"]]),
      download, status
    ]);
  }
  async function backupDetailPage(id) {
    var b = B.backupDetail(await B.request("/staff/backups/" + encodeURIComponent(id)));
    if (b.backup_id !== id) throw new Error("The verified manifest does not identify this snapshot.");
    return B.el("div", {}, [B.link("← Backups", "#/backups"), viewTitle("Manager / verified snapshot", "Inspect private backup", "Hash/integrity checks detect corruption, not authenticity or encryption."),
      verifiedBackupPanel(b), B.link("Read-only offline restore guide", "#/backups/" + b.backup_id + "/restore", "button primary")]);
  }
  async function restorePage(id) {
    var b = B.backupDetail(await B.request("/staff/backups/" + encodeURIComponent(id))), guide = await B.request("/staff/backups/" + encodeURIComponent(id) + "/restore-guide");
    var phrase = guide.confirmation_phrase;
    if (b.backup_id !== id || phrase !== "RESTORE BENCHFLOW " + b.backup_id) throw new Error("The server guide does not identify this backup's exact confirmation phrase. Ask the host operator to review it; no restore can be run here.");
    var steps = [
      "Identify the snapshot below and read the host operator's docs/RUNBOOK.md. Preserve the current data and the latest verified/pre-restore backups.",
      "Stop the exact BenchFlow application process using the operator runbook. Do not kill processes by name or delete WAL/SHM files. The restore tool must refuse an active data-root lock.",
      "The host operator follows the backup-specific server steps and the fixed-root offline Restore.ps1 runbook. No backup path, database upload or password belongs in the browser.",
      "The tool must validate manifest/hash/schema, stage any explicitly supported forward migrations, check integrity/foreign keys, preserve a pre-restore snapshot and invalidate all sessions, passwords, codes and credential caches before replacement. Refusal must leave current data unchanged.",
      "After verified identity confirmation, recover an active manager using the runbook's host-local no-echo password prompt. The manager enters the password personally, twice. Do not print passwords or recovery codes. In isolated demo mode, restart rotates new credentials while preserving stories.",
      "Explicitly start BenchFlow using the runbook; restore must never auto-start. Verify readiness, sign in with recovered access and inspect the restored jobs/stock/settlement and ownership before resuming work. Issue fresh verified access codes to others."
    ];
    var serverContent = B.el("div", {});
    if (typeof guide.title === "string") serverContent.appendChild(B.text("h3", guide.title));
    ["consequences","confirmation_phrase","integrity","schema_status"].forEach(function (key) {
      var v = guide[key]; if (typeof v === "string") serverContent.appendChild(B.callout(B.human(key), v, "caution"));
    });
    ["stop_steps","restore_steps","start_steps","verify_steps"].forEach(function (key) {
      serverContent.appendChild(B.text("h3", B.human(key)));
      serverContent.appendChild(B.el("ol", {}, guide[key].map(function (s) { return B.text("li", s); })));
    });
    var check = B.el("input", { type: "checkbox" }), command = B.el("section", { hidden: true }, [
      B.text("h3", "Offline operator confirmation—not run by this page"),
      B.text("pre", phrase),
      B.text("p", "Use the server-provided restore steps and docs/RUNBOOK.md for the exact command, demo-mode flag and prerequisites. This page has not stopped or restored the host.")
    ]);
    check.addEventListener("change", function () { command.hidden = !check.checked; });
    var verify = B.button("Check running host readiness", async function () {
      verify.disabled = true;
      try { var health = await B.request("/health"); B.notice("Host reports " + health.status, "Schema " + health.schema_version + ". Readiness is not proof that restore ran. Verify recovered sign-in and restored story/data separately.", "success"); }
      catch (e) { B.notice("Host unavailable", B.errorText(e), "caution"); }
      finally { verify.disabled = false; }
    });
    return B.el("div", {}, [
      B.link("← Backups", "#/backups"), viewTitle("Read-only recovery guide", "Restore with the host operator", "The live application never replaces its own database."),
      B.callout("Consequences", "This replaces shop data with the selected snapshot. Newer work may be lost. Every old session, password, invitation and recovery code must become unusable. Offline operator recovery is required before normal use.", "caution"),
      verifiedBackupPanel(b), B.panel("Server-provided guide", [serverContent]),
      B.panel("Stop → restore → recover → start → verify", [
        B.el("ol", {}, steps.map(function (s) { return B.text("li", s); })),
        B.el("label", { class: "check" }, [check, B.text("span", 'I have read the consequences and the exact confirmation phrase: "' + phrase + '".')]),
        command, verify, B.link("Sign in after operator recovery", "#/login", "button")
      ])
    ]);
  }
  B.prepareContext = async function (name, c) {
    var a = B.session.account, options = c.options = {}, defaults = c.defaults = {};
    if (c.job && c.job.job_id) c.job = await B.request((customer() ? "/me/jobs/" : "/staff/jobs/") + encodeURIComponent(c.job.job_id));
    if (c.customer) c.customer = await B.request("/staff/customers/" + encodeURIComponent(c.customer.customer_id));
    if (c.linkedCustomerId) c.account = await B.request("/staff/customers/" + encodeURIComponent(c.linkedCustomerId) + "/account");
    else if (c.account) c.account = await B.request("/staff/accounts/" + encodeURIComponent(c.account.id));
    if (c.request) c.request = await B.request("/staff/contact-change-requests/" + encodeURIComponent(c.request.request_id));
    if (c.part) c.part = await B.request(B.query("/staff/parts/" + encodeURIComponent(c.part.part_id), { job_id: c.jobId }));
    if (c.profile) {
      var profile = await B.request("/staff/qc-profiles/" + encodeURIComponent(c.profile.profile_id));
      if (!profile.profile || !Array.isArray(profile.history)) throw new Error("QC profile/history wrapper unavailable. No profile revision is guessed.");
      c.profile = profile.profile;
    }
    if (c.policy) c.policy = await B.request("/staff/policy");
    if (customer()) { options.staff = []; return c; }
    if (c.jobId && !c.job) c.job = await B.request("/staff/jobs/" + encodeURIComponent(c.jobId));
    var job = c.job, parts = [], staff = [{ id: a.id, display_name: a.display_name, kind: "staff" }];
    if (job) {
      var directory = await B.request(B.query("/staff/assignable-staff", { job_id: job.job_id }));
      staff = directory.items.map(function (s) { return { id: s.staff_id, display_name: s.display_name, roles: s.roles }; });
    } else if (role("manager")) {
      // Paginate the authorized directory; never use manager enumeration for intake/technician accounts.
      var cursor = null;
      do {
        var page = await B.request(B.query("/staff/accounts", { state: "active", cursor: cursor }));
        page.items.forEach(function (s) { if (s.kind === "staff" && !staff.some(function (x) { return x.id === s.id; })) staff.push(s); });
        cursor = page.next_cursor;
      } while (cursor);
    }
    options.staff = staff.map(function (s) { return [s.id, s.display_name]; }); defaults.owner_staff_id = a.id; defaults.assigned_staff_id = a.id;
    options.technicians = staff.filter(function (s) { return s.roles && s.roles.indexOf("technician") !== -1 && (!job || job.assignment.assigned_staff_ids.indexOf(s.id) !== -1); }).map(function (s) { return [s.id, s.display_name]; });
    if (/^parts\./.test(name) || name === "estimate.draft_save" || /^work\.(start|consume)$/.test(name)) {
      if (desk() || job) {
        var pc = null;
        do {
          var partPage = await B.request(B.query("/staff/parts", { job_id: job && job.job_id, cursor: pc }));
          parts = parts.concat(partPage.items); pc = partPage.next_cursor;
        } while (pc);
      }
    }
    if (c.part && !parts.some(function (p) { return p.part_id === c.part.part_id; })) parts.push(c.part);
    options.parts = parts.map(function (p) { return [p.part_id, p.sku + " · " + p.description + " / " + p.available_qty + " available"]; });
    if (c.part) defaults.part_id = c.part.part_id;
    options.movements = (c.part && c.part.movements || []).map(function (m) { return [m.movement_id, B.human(m.kind) + " · " + B.date(m.occurred_at) + " · " + m.movement_id.slice(0, 8)]; });
    if (!job) return c;
    var est = B.currentEstimate(c), scopes = [], lines = [];
    if (job.diagnostic && job.diagnostic.current) scopes.push([job.diagnostic.current.permission_id, "Diagnostic · " + job.diagnostic.current.scope]);
    (est && est.lines || []).forEach(function (l) {
      lines.push([l.line_id, "v" + est.version + " · " + l.description]);
      if (l.scope_ref) scopes.push([l.scope_ref, "v" + est.version + " · " + l.description]);
    });
    var lineHistory = (job.estimates || []).concat(job.estimate_drafts || []);
    var unknownScopes = [];
    lineHistory.forEach(function (record) {
      (record.lines || []).forEach(function (line) {
        if (line.kind === "part" && !line.part_id && line.scope_ref && unknownScopes.indexOf(line.scope_ref) === -1) unknownScopes.push(line.scope_ref);
      });
    });
    if (name === "work.task_create" && unknownScopes.length) {
      scopes = scopes.filter(function (scope) { return unknownScopes.indexOf(scope[0]) === -1; });
    }
    if (name === "estimate.draft_save") (job.estimate_drafts || []).forEach(function (draft) {
      draft.lines.forEach(function (line) {
        if (line.scope_ref && !scopes.some(function (s) { return s[0] === line.scope_ref; })) scopes.push([line.scope_ref, "Draft scope · " + line.description]);
      });
    });
    options.scopes = scopes; options.lines = lines;
    options.permissions = job.diagnostic && job.diagnostic.current ? [[job.diagnostic.current.permission_id, job.diagnostic.current.scope + " · " + B.human(job.diagnostic.current.state)]] : [];
    options.drafts = (job.estimate_drafts || []).filter(function (d) {
      return name !== "estimate.issue" || !B.hasUnknownCatalogPart(d);
    }).map(function (d) { return [d.draft_id, "Draft · " + B.date(d.updated_at)]; });
    options.tasks = (job.work || []).filter(function (w) {
      if (name === "diagnostic.record") return w.scope_kind === "diagnostic";
      if (name === "work.task_reassign") return true;
      return w.assigned_staff_id === a.id && (name !== "qc.record" || !w.rework_of_task_id);
    }).map(function (w) { return [w.task_id, B.taskPurpose(w, job) + " · " + B.human(w.state)]; });
    options.checks = (job.qc || []).map(function (q) { return [q.check_id, q.label + (q.required ? " · required" : "") + " · profile v" + q.profile_version]; });
    options.reservations = []; options.orders = [];
    (job.parts || []).forEach(function (p) {
      p.reservations.forEach(function (r) { options.reservations.push([r.reservation_id, p.sku + " · " + B.human(r.state) + " · " + r.qty + " reserved / " + r.consumed_qty + " consumed"]); });
      p.orders.forEach(function (o) { options.orders.push([o.order_id, p.sku + " · " + o.supplier_label + " · " + B.human(o.state)]); });
    });
    options.cases = (job.return_cases || []).filter(function (r) { return r.return_job_id === job.job_id; }).map(function (r) { return [r.return_case_id, "Case · " + B.human(r.coverage || "undetermined") + " · return " + r.return_job_id.slice(0, 8)]; });
    options.disputes = (job.dispositions || []).filter(function (d) { return d.kind === "dispute" && d.state === "open"; }).map(function (d) { return [d.disposition_id, d.summary + " · revision " + d.disposition_rev]; });
    if (c.dispositionId) defaults.disposition_id = c.dispositionId;
    if (c.taskId) defaults.task_id = c.taskId;
    options.charges = []; options.payments = []; options.entries = [];
    if (job.settlement) ["charges","credits","payments","refunds"].forEach(function (kind) {
      var singular = { charges: "charge", credits: "credit", payments: "payment", refunds: "refund" }[kind];
      job.settlement[kind].forEach(function (entry) {
        var pair = [entry[singular + "_id"], B.human(singular) + " · " + B.money(entry.amount.amount_minor, entry.amount.currency) + " · " + B.date(entry.posted_at || entry.recorded_at)];
        options.entries.push(pair); if (kind === "charges") options.charges.push(pair); if (kind === "payments") options.payments.push(pair);
      });
    });
    return c;
  };
  async function route() {
    var epoch = ++routeEpoch, hash = location.hash || "#/", bits = hash.slice(1).split("?"), parts = bits[0].split("/").filter(Boolean), params = new URLSearchParams(bits[1] || "");
    var old = document.getElementById("action-dialog");
    if (old.open && old.dataset.pending) {
      history.replaceState(null, "", lastRoute || "#/"); B.notice("Recording in progress", "Wait for the server result before leaving this action.", "caution"); return;
    }
    if (old.open && hash === lastRoute) {
      B.notice("Draft still open", "Close or finish the current dialog before refreshing. Your entries have not been discarded.", "caution", "draft-open"); return;
    }
    var retained = hash === confirmedHash && B.session && B.session.authenticated && B.session.account.id === confirmedAccount ? confirmedView : null;
    var refreshFocus = document.activeElement && document.activeElement.id;
    var refreshY = window.scrollY;
    B.readOnlyDocket = !!retained;
    if (hash !== lastRoute && lastRoute) returnPoints[lastRoute] = { y: window.scrollY, id: document.activeElement && document.activeElement.closest("[id^='job-']") && document.activeElement.closest("[id^='job-']").id };
    if (old.open) old.close();
    shell(); main.className = customer() ? "customer" : ""; main.setAttribute("aria-busy", "true");
    B.clear(main);
    if (retained) {
      retained.inert = true; retained.setAttribute("aria-disabled", "true");
      var refreshing = B.el("section", { role: "status", tabindex: "-1", class: "callout caution" }, [
        B.text("h2", "Refreshing docket · previous records are read-only"),
        B.text("p", "Not current until the server responds. Actions are unavailable while refreshing.")]);
      main.appendChild(refreshing); main.appendChild(retained); refreshing.focus();
    } else {
      confirmedView = null; confirmedHash = ""; confirmedAccount = null;
      main.appendChild(B.text("h1", "Loading " + (customer() ? "your repair" : "the docket"))); main.appendChild(B.text("p", "Reading current server records…", "loading"));
    }
    try {
      if (!B.session) await B.getSession();
      if (epoch !== routeEpoch) return;
      var content, name = parts[0] || "", id = parts[1], publicRoute = ["login","activate","recover"].indexOf(name) !== -1;
      if (publicRoute) content = authPage(name);
      else if (!B.session.authenticated) content = authPage("login");
      else if (customer()) {
        if (!name) content = await myJobs();
        else if (name === "jobs" && id) content = await myJob(id);
        else if (name === "account") content = await accountPage();
        else content = B.el("div", {}, [viewTitle("Your account", "Page unavailable", "Use My jobs to view your linked service history."), B.link("My jobs", "#/", "button")]);
      } else {
        if (!name) { if (params.get("bucket") && role("manager")) queueState.bucket = params.get("bucket"); content = await actionsPage(); }
        else if (name === "search") content = await jobsSearch(params.get("query") || "");
        else if (name === "jobs" && id) content = await staffJob(id, parts[2] || "overview");
        else if ((name === "customers" || name === "intake") && desk()) content = id ? await customerDetail(id) : await customersPage(params.get("query") || "", name === "intake");
        else if (name === "parts") content = id ? await partDetail(id, params.get("job_id")) : await partsPage(params.get("query"), params.get("job_id"));
        else if (name === "contacts" && desk()) content = id ? await contactDetail(id) : await contactsPage(params.get("state"));
        else if (name === "shop" && role("manager")) content = shopPage();
        else if (name === "accounts" && role("manager")) content = id ? await accountDetail(id) : await accountsPage(params.get("state"));
        else if (name === "profiles" && role("manager")) content = await profilesPage(id);
        else if (name === "policy" && role("manager")) content = await policyPage();
        else if (name === "backups" && role("manager")) content = id ? (parts[2] === "restore" ? await restorePage(id) : await backupDetailPage(id)) : await backupsPage();
        else if (name === "audit") content = await auditPage(params);
        else content = B.el("div", {}, [viewTitle("Access / navigation", "Page unavailable", "Your assigned access does not include this page, or the route does not exist. Contact a manager if you need help."), B.link("Back to actions", "#/", "button")]);
      }
      if (epoch !== routeEpoch) return;
      B.clear(main); main.appendChild(content); main.className = customer() ? "customer" : "";
      B.readOnlyDocket = false;
      confirmedView = content; confirmedHash = hash; confirmedAccount = B.session.authenticated ? B.session.account.id : null;
      shell(); main.removeAttribute("aria-busy");
      if (hash !== lastRoute) {
        var point = returnPoints[hash], focus = point && point.id && document.getElementById(point.id);
        if (focus) { var link = focus.querySelector("a"); if (link) link.focus(); }
        else B.focusHeading(main);
        window.scrollTo(0, point ? point.y : 0);
      } else {
        var restored = refreshFocus && document.getElementById(refreshFocus);
        if (restored) restored.focus(); else B.focusHeading(main);
        window.scrollTo(0, refreshY);
      }
      document.title = (main.querySelector("h1") ? main.querySelector("h1").textContent : "Repair docket") + " · BenchFlow";
      lastRoute = hash;
      watchSession();
    } catch (e) {
      if (epoch !== routeEpoch) return;
      if (retained && ![401,403,404].includes(e.status)) {
        B.clear(main); main.removeAttribute("aria-busy"); main.className += " offline";
        var stale = B.el("section", { role: "alert", tabindex: "-1", class: "callout error" }, [
          B.text("h2", "Refresh failed · previous records are NOT current"),
          B.text("p", B.errorText(e) + " These records remain read-only; no action is available."),
          B.button("Retry refresh", B.refresh, "primary"), B.link("Back to start", "#/")]);
        main.appendChild(stale); main.appendChild(retained); stale.focus(); return;
      }
      confirmedView = null; confirmedHash = ""; confirmedAccount = null;
      B.clear(main); main.removeAttribute("aria-busy"); main.className += " offline";
      main.appendChild(viewTitle("No confirmed data", e.status === 404 ? "Record unavailable" : e.status === 403 ? "Access unavailable" : "Could not open the docket", B.errorText(e)));
      if (e.requestId) main.appendChild(B.text("p", "Request reference: " + e.requestId, "mono"));
      main.appendChild(B.el("div", { class: "actions" }, [B.button("Retry current page", function () { B.refresh(); }, "primary"), B.link("Sign in", "#/login"), B.link("Back to start", "#/")]));
      B.focusHeading(main);
    }
  }
  B.refresh = function () { return route(); };
  document.querySelector(".skip").addEventListener("click", function (event) { event.preventDefault(); main.focus(); });
  window.addEventListener("hashchange", function () { if (location.hash === "#main") { main.focus(); return; } route(); });
  window.addEventListener("pagehide", function () {
    var d = document.getElementById("action-dialog"); if (d.open) d.close();
    main.querySelectorAll('input[type="password"]').forEach(function (n) { n.value = ""; });
    B.clear(main); B.clear(document.getElementById("identity")); B.clear(document.getElementById("navigation"));
    B.session = null; B.safeDraft = null; B.captureDraft = null; confirmedView = null; confirmedHash = ""; confirmedAccount = null;
  });
  window.addEventListener("pageshow", function (e) { if (e.persisted) { B.session = null; B.clear(main); route(); } });
  document.addEventListener("visibilitychange", function () {
    if (document.hidden) document.querySelectorAll(".secret").forEach(function (node) { node.textContent = "Cleared — reissue explicitly if still needed."; });
  });
  route();
}());
