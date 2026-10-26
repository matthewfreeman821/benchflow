(function () {
  "use strict";
  var B = window.BF, f = B.field, A = B.actions = {};
  var reasons = ["customer_request","customer_declined","safety","scope_change","stock_count","damage","inspected_return","record_correction","verified_duplicate_merge","mistaken_link","role_change","suspension","reactivation","invite_revocation","contact_resolution","coverage_review","balance_exception","unrepaired_return","manual_record","other_allowed_business_reason","dispute_resolution","no_fault_found","unrepairable","task_reassignment","diagnostic_fee","tax_recording"];
  function t(key, label, optional) { return f(key, label, "text", { optional: !!optional }); }
  function note(key, label, optional) { return f(key, label, "textarea", { optional: !!optional }); }
  function sel(key, label, options, extra) { return f(key, label, "select", Object.assign({ options: options }, extra || {})); }
  function int(key, label, extra) { return f(key, label, "integer", extra); }
  function amount(key, label, extra) { return f(key, label, "money", Object.assign({ min: 0, hint: "Enter dollars and cents. Sent as integer minor units; no payment is processed." }, extra || {})); }
  function date(key, label, optional) { return f(key, label, "date", { optional: !!optional, hint: "Enter your device's local time. The docket displays shop time (America/New_York)." }); }
  function hidden(key, value) { return f(key, key, "hidden", { value: value }); }
  function known(key, label) { return f(key, label, "known"); }
  function reason(key) { return sel(key || "reason", "Reason", reasons); }
  function ref(key, label, source, extra) {
    return f(key, label, "ref", Object.assign({ options: function (c) { return c.options && c.options[source] || []; }, value: function (c) { return c.defaults && c.defaults[key]; } }, extra || {}));
  }
  function id(key, label, extra) { return t(key, label + " · record ID", extra && extra.optional); }
  function moneyObject(key, label) { return f(key, label, "object", { fields: [sel("currency", "Currency", ["USD"], { value: "USD" }), amount("amount_minor", "Amount")] }); }
  function roles() { return f("roles", "Assigned staff roles", "multi", { options: ["intake", "technician", "manager"] }); }
  function method(customer) { return customer ? hidden("method", "customer_account") : sel("method", "How the customer decided", ["phone", "in_person"]); }
  function attribution(customer) { return customer ? hidden("attribution", null) : t("attribution", "Customer's name and explicit decision"); }
  function consume(key, empty) {
    return f(key, "Reserved parts to consume", "repeat", { empty: empty, itemLabel: "reserved part", hint: "Select an existing job reservation. Consumption is checked atomically by the server.", fields: [
      ref("reservation_id", "Reservation", "reservations"), int("qty", "Quantity", { min: 1, max: 10000, value: 1 })
    ] });
  }
  var lineFields = [sel("kind", "Line type", ["labor", "part", "fee", "credit"]), t("description", "Scope / description"), int("quantity", "Quantity", { min: 1, max: 10000, value: 1 }), amount("unit_amount_minor", "Unit amount"),
    ref("part_id", "Exact catalog part (part lines only)", "parts", { optional: true, hint: "Required for a part line; leave blank for labor, fee and credit. External parts still need a catalog receipt, demand and reservation." }),
    f("scope_ref", "Authorization scope", "scope", { options: function (c) { return c.options.scopes || []; }, hint: "Use a NEW scope for changed or additional work, including a changed part or price. Retain an existing scope only for unchanged work. Completed work cannot be repeated by reusing its scope." })];
  var profileFields = [t("label", "Check label"), f("required", "Required before readiness", "bool", { value: true })];
  function define(name, label, group, access, expected, fields, help, normalize) {
    A[name] = { name: name, label: label, group: group, access: access, expected: expected ? expected.split(" ") : [], fields: fields, help: help, normalize: normalize };
  }
  define("customer.create", "Create customer record", "Customer", "desk", "", [
    t("display_name", "Customer name"), known("phone", "Phone"), known("email", "Email"), note("notes", "Internal service note", true)
  ], "Search existing records first. Similar contacts are a warning, not proof that records belong to the same person.");
  define("customer.update_contact", "Correct contact details", "Customer", "desk", "customer_rev", [
    t("display_name", "Customer name"), known("phone", "Phone"), known("email", "Email"), reason()
  ], "Record an attributable staff correction. This does not verify an electronic channel.");
  define("customer.merge", "Merge verified duplicate", "Customer", "manager", "source_customer_rev target_customer_rev", [
    id("source_customer_id", "Source customer"), id("target_customer_id", "Keep this customer"),
    sel("verification_method", "Same-person verification", ["in_person", "phone"]), reason()
  ], "Verify both service histories with the same person. Phone verification must use an existing number on file, not a newly supplied number. Source aliases remain. With two accounts the source account closes; with one source account its link moves; target-only account stays; neither creates no account. Affected sessions/codes are revoked.");
  define("account.invite_customer", "Invite this customer", "Customer", "desk", "customer_rev", [
    hidden("customer_id", function (c) { return c.customer.customer_id; }), sel("verification_method", "Person verified", ["in_person", "phone"]),
    note("contact_handoff_note", "Manual handoff note", true), ref("owner_staff_id", "Invitation follow-up owner", "staff"),
    date("next_action_due_at", "Next contact due")
  ], "Only a verified invitation links history. The code is shown once and is NOT SENT. Give it in person or read it out by phone.");
  define("account.invite_staff", "Invite staff member", "People", "manager", "", [
    t("display_name", "Staff name"), roles(), reason(), ref("owner_staff_id", "Invitation owner", "staff"), date("next_action_due_at", "Invitation follow-up due")
  ], "The person chooses their own login ID and password during activation. No password is set here.");
  [
    ["account.reissue_invite", "Reissue invitation", "reason", "A new code supersedes the previous invitation. Shown once; no message is sent."],
    ["account.revoke_invite", "Revoke pending invitation", "reason_code", "Invalidates pending codes. The invited account and its follow-up remain visible."],
    ["account.suspend", "Suspend account", "reason", "Revokes sessions and pending codes immediately. Owned staff actions need manager reassignment; recorded consent is not erased."],
    ["account.reactivate", "Reactivate account", "reason", "Old sessions are not restored. This does not restore an unlinked/closed account."],
    ["account.close", "Close account", "reason", "Closes access and revokes sessions/codes. Repair history remains." ]
  ].forEach(function (r) { define(r[0], r[1], "People", "manager", "account_rev", [hidden("account_id", function (c) { return c.account.id; }), reason(r[2])], r[3]); });
  define("account.change_roles", "Change assigned roles", "People", "manager", "account_rev", [
    hidden("account_id", function (c) { return c.account.id; }), roles(), reason()
  ], "Changes take effect immediately and revoke the person's sessions. Manager access does not bypass repair permission, stock or QC.");
  define("account.unlink_customer", "Close mistaken customer link", "People", "manager", "account_rev customer_rev", [
    hidden("account_id", function (c) { return c.account.id; }), hidden("customer_id", function (c) { return c.account.customer_id; }), reason()
  ], "This closes the account atomically and removes its active link. Correct the association using a new verified invitation, not by silently relinking this account.");
  define("account.issue_recovery", "Issue one-time recovery code", "People", "recovery", "account_rev", [
    hidden("account_id", function (c) { return c.account.id; }), sel("verification_method", "Identity verification", ["in_person", "phone", "manager_verified"]), reason()
  ], "Only after verifying the person. Shown once, NOT SENT. Staff never see or set someone else's password. Last-manager recovery is a host-operator procedure.");
  define("contact.change_request", "Request contact change", "Account", "customer", "", [
    f("requested_fields", "Which details should staff confirm?", "multi", { options: ["display_name", "phone", "email"] }),
    known("display_name", "Requested name"), known("phone", "Requested phone"), known("email", "Requested email")
  ], "This creates a persistent request, not a self-edit. Select only the fields that need changing. Staff must confirm in person or by phone. No email or SMS is sent.");
  define("contact.change_resolve", "Resolve contact change", "Contact requests", "desk", "request_rev customer_rev", [
    hidden("request_id", function (c) { return c.request.request_id; }), sel("decision", "Resolution", ["accept", "reject"]),
    sel("confirmation_method", "Manual confirmation", ["in_person", "phone"]), reason("reason_code")
  ], "Accept changes only the requested fields after checking the person's existing record. This is not electronic channel verification.");
  define("job.create", "Receive device & open docket", "Intake", "desk", "customer_rev", [
    hidden("customer_id", function (c) { return c.customer.customer_id; }),
    f("device", "Device identity", "object", { fields: [known("make", "Make"), known("model", "Model"), known("serial", "Serial number")] }),
    note("reported_issue", "Issue in the customer's words"), known("condition", "Condition on receipt"),
    f("accessories", "Accessories received", "object", { fields: [f("known", "Accessory list is known", "bool"), f("items", "Items, one per line", "lines", { optional: true })] }),
    known("testing_constraints", "Testing / data constraints"), t("received_by_customer_name", "Person handing over the device"),
    f("acknowledgement", "Intake acknowledgement", "object", { fields: [t("text_version", "Receipt / acknowledgement text version"), sel("method", "Acknowledged", ["in_person", "phone"])] }),
    ref("owner_staff_id", "Accountable staff owner", "staff"), t("next_action", "Next action"), date("next_action_due_at", "Next action due")
  ], "Receipt is custody, not diagnostic or repair permission. Record diagnostic permission separately after opening the docket.");
  define("job.assign", "Assign & reassign staff", "Overview", "manager", "job_rev", [
    f("staff_ids", "Assigned staff", "multi", { options: function (c) { return c.options.staff; } }),
    ref("owner_staff_id", "Accountable owner (must be assigned)", "staff"), reason()
  ], "Reassignment preserves history. A suspended person's actions are not authorized by leaving their name assigned.");
  define("job.update_follow_up", "Set next action & owner", "Overview", "desk", "job_rev", [
    ref("owner_staff_id", "Accountable owner", "staff", { optional: true }), t("next_action", "Next action", true),
    date("next_action_due_at", "Due", true), sel("state", "Follow-up state", ["due", "resolved"]), sel("reason", "Reason", reasons, { optional: true })
  ], "A due follow-up needs an owner, explicit action and due time. No response never means consent, fees or disposal.");
  define("job.record_contact_attempt", "Record manual contact attempt", "Overview", "desk", "job_rev", [
    sel("channel", "Channel", ["phone", "in_person", "other_manual"]),
    sel("outcome", "Outcome", ["no_answer", "message_left", "spoke", "customer_declined", "callback_requested"]),
    note("summary", "What happened"), date("occurred_at", "Occurred at (blank uses server time)", true)
  ], "This records contact only; it does not send a message or grant permission.");
  define("job.cancel", "Record explicit cancellation", "Overview", "desk", "job_rev gate_rev", [
    sel("requested_by", "Requested by", ["customer", "staff"]), t("requester_attribution", "Person making the request"), reason()
  ], "Stops new work and requires safe stopping and return planning. The device stays in custody until a separate verified unrepaired handover.");
  define("job.reopen", "Correct cancellation", "Overview", "manager", "job_rev gate_rev", [
    reason(), t("next_action", "Next action"), date("next_action_due_at", "Next action due")
  ], "Manager correction before handover only. A closed repair is not reopened; recurrence creates a new linked job.");
  define("job.record_disposition", "Record no-fault / unrepairable finding", "Overview", "technician", "job_rev gate_rev", [
    sel("disposition", "Finding disposition", ["no_fault_found", "unrepairable"]), note("finding", "Finding and limitations"),
    ref("owner_staff_id", "Responsible next-action owner", "staff"), t("next_action", "Next action"), date("next_action_due_at", "Next action due"), reason()
  ], "A finding is not cancellation, a fee, QC success or handover. Keep an owner and explicit next action; return-unrepaired handover is a separate gated record.");
  define("job.open_dispute", "Open service dispute", "Overview", "desk", "job_rev gate_rev", [
    note("summary", "What is disputed"), ref("owner_staff_id", "Dispute follow-up owner", "staff"),
    t("next_action", "Next action"), date("next_action_due_at", "Next action due"), reason()
  ], "Records the dispute only. It does not grant consent, cancel work, waive money or authorize release of property.");
  define("job.resolve_dispute", "Record dispute resolution", "Overview", "manager", "job_rev gate_rev disposition_rev", [
    ref("disposition_id", "Open dispute", "disputes"), note("resolution_summary", "Resolution and reasoning"),
    ref("owner_staff_id", "Next-action owner", "staff"), t("next_action", "Next action"), date("next_action_due_at", "Next action due"), reason()
  ], "Resolve this exact dispute revision. Consent, coverage, settlement, QC and property handover remain separate decisions.");
  define("diagnostic.permission", "Diagnostic permission", "Diagnosis", "consent", "job_rev gate_rev", [], "Diagnostic permission is separate from repair approval. Review the exact scope and any diagnostic fee.");
  define("diagnostic.record", "Record diagnosis / finding", "Diagnosis", "technician", "job_rev", [
    ref("task_id", "Diagnostic task", "tasks", { optional: true }), sel("kind", "Record type", ["test", "finding", "limitation"]),
    note("summary", "Internal finding"), f("customer_visible", "Include a customer-safe finding", "bool"),
    note("customer_text", "Customer-safe explanation", true), note("uncertainty", "Uncertainty / limits", true)
  ], "Only the explicitly customer-safe explanation appears to the customer. Do not place passwords or access codes in any field.",
  function (d) { if (!d.customer_visible) d.customer_text = null; return d; });
  define("estimate.draft_save", "Save estimate draft", "Estimates", "staff", "job_rev", [
    ref("draft_id", "Existing draft (or new)", "drafts", { optional: true }),
    f("lines", "Proposed scope & charges", "repeat", { itemLabel: "estimate line", fields: lineFields }),
    sel("currency", "Currency", ["USD"], { value: "USD" }),
    sel("tax_source", "Tax provenance", [["not_configured","Not configured · zero tax"],["manual_shop_entry","Manually entered by shop · no automatic calculation"]]),
    amount("tax_minor", "Tax amount", { value: 0 }), t("tax_label", "Tax description")
  ], "Drafts are not issued estimates and cannot authorize work. Issuing creates a new immutable version.");
  define("estimate.issue", "Issue immutable estimate version", "Estimates", "desk", "job_rev gate_rev estimate_version", [
    ref("draft_id", "Saved draft to issue", "drafts"), date("expires_at", "Acceptance deadline"), note("change_reason", "What changed and why")
  ], "Material revision holds ALL new repair starts across the job and safely stops active work. Earlier approvals, completed work and charges remain history. Every future start needs approved current scope.");
  define("estimate.decision", "Record exact-version decision", "Estimates", "consent", "job_rev gate_rev estimate_version", [], "Review the full scope, amount, version and deadline. Approval does not clear stock, safety, QC or other blockers.");
  define("estimate.revoke", "Revoke repair approval", "Estimates", "consent", "job_rev gate_rev estimate_version", [], "Revocation is a new decision event. It holds new starts and requires active work to safe-stop; it does not erase earlier authorized work or charges.");
  define("parts.create", "Add part to catalog", "Parts", "manager", "", [
    t("sku", "Stock code / SKU"), t("description", "Part description"), t("unit", "Stock unit"),
    int("initial_qty", "Opening on-hand quantity", { min: 0, max: 10000, value: 0 }), reason()
  ], "Opening stock is a recorded movement, not a supplier integration.");
  define("parts.adjust", "Record stock correction / inspected return", "Parts", "manager", "stock_rev", [
    hidden("part_id", function (c) { return c.part.part_id; }), int("delta_qty", "Quantity adjustment (+ / −)"),
    reason(), sel("source", "Source", ["count", "damage", "return_to_stock", "correction"]),
    ref("source_movement_id", "Original consumption / movement", "movements", { optional: true }), note("inspection_note", "Inspected return note", true)
  ], "Never silently edit stock totals. An inspected return to stock must identify its original consumption and inspection.");
  define("parts.demand", "Record part demand", "Parts", "staff", "job_rev gate_rev", [
    ref("part_id", "Catalog part", "parts"), hidden("job_id", function (c) { return c.job.job_id; }),
    int("qty", "Required quantity", { min: 1, max: 10000, value: 1 }), ref("scope_ref", "Authorized scope", "scopes"), date("needed_by", "Needed by", true)
  ], "Demand is not a reservation, order or implied substitution.");
  define("parts.reserve", "Reserve available stock", "Parts", "staff", "job_rev gate_rev stock_rev estimate_version", [
    ref("part_id", "Catalog part", "parts"), hidden("job_id", function (c) { return c.job.job_id; }),
    int("qty", "Quantity to reserve", { min: 1, max: 10000, value: 1 }), ref("scope_ref", "Scope", "scopes"), sel("reason", "Reason", reasons, { optional: true })
  ], "Availability is checked atomically. Another docket may reserve the last unit; no automatic substitution.");
  define("parts.release", "Release unused reservation", "Parts", "staff", "job_rev gate_rev stock_rev", [
    ref("reservation_id", "Job reservation", "reservations"), int("qty", "Quantity to release", { min: 1, max: 10000, value: 1 }), reason()
  ], "Release affects reserved quantity, not on-hand stock. Consumed parts require an inspected return movement.");
  define("parts.manual_order", "Record manual supplier order", "Parts", "desk", "job_rev", [
    ref("part_id", "Catalog part", "parts"), hidden("job_id", function (c) { return c.job.job_id; }),
    int("qty", "Quantity", { min: 1, max: 10000, value: 1 }), t("supplier_label", "Supplier"),
    date("eta_at", "Reported ETA", true), note("eta_evidence", "ETA evidence / uncertainty", true),
    sel("placement_state", "Placement state", ["planned", "placed_unconfirmed"]), reason()
  ], "This does not place an order. Record what staff did externally; unconfirmed stays unconfirmed.");
  define("parts.record_order_outcome", "Record supplier response", "Parts", "desk", "job_rev", [
    ref("order_id", "Job supplier order", "orders"), sel("outcome", "Supplier response", ["supplier_confirmed", "backordered", "cancellation_reported", "unavailable"]),
    note("evidence", "Evidence received"), reason()
  ], "A supplier response is not physical receipt. Record received stock separately.");
  define("parts.receive", "Receive stock", "Parts", "desk", "stock_rev", [
    ref("order_id", "Linked job order (blank for unlinked receipt)", "orders", { optional: true }),
    ref("part_id", "Received catalog part", "parts"), int("qty", "Quantity received", { min: 1, max: 10000, value: 1 }), note("source_note", "Receipt source / evidence")
  ], "Confirm physical receipt. Linked orders target this job; unlinked receipts target the selected part.");
  define("work.task_create", "Plan task / rework", "Work & QC", "planner", "job_rev estimate_version", [
    hidden("job_id", function (c) { return c.job.job_id; }), ref("scope_ref", "Permission / estimate scope", "scopes"),
    sel("scope_kind", "Scope type", ["diagnostic", "estimate_line"]), ref("assigned_staff_id", "Assigned technician", "technicians"), note("summary", "Planned work / rework")
  ], "Planning a task never authorizes its start. Use linked rework after failed QC and retain the original result.");
  define("work.task_reassign", "Reassign task technician", "Work & QC", "manager", "job_rev task_rev", [
    ref("task_id", "Task to reassign", "tasks"), ref("assigned_staff_id", "Active technician assigned to this job", "technicians"), reason()
  ], "The server permits safe not-started, paused or rework tasks only, never active/completed work. This does not authorize a start or relabel completed work.");
  define("work.start", "Start authorized task", "Work & QC", "technician", "job_rev gate_rev estimate_version", [
    ref("task_id", "Task", "tasks"), ref("scope_ref", "Task's current authorized scope", "scopes"), consume("consume", true)
  ], "Server rechecks diagnostic or current exact-version repair permission, job-wide holds, scope and atomic stock. No manager override.");
  define("work.log", "Log work / pause / complete", "Work & QC", "technician", "job_rev gate_rev", [
    ref("task_id", "Task", "tasks"), sel("kind", "Work event", ["note", "pause", "resume", "complete"]), note("summary", "Work performed / safe-stop details"),
    ref("owner_staff_id", "Pause follow-up owner", "staff", { optional: true }), t("next_action", "Pause next action", true), date("next_action_due_at", "Pause follow-up due", true)
  ], "Pause requires owner, next action and due date. Completion is not a QC pass. Resume is rechecked against current authorization.",
  function (d) { if (d.kind !== "pause") { d.owner_staff_id = null; d.next_action = null; d.next_action_due_at = null; } return d; });
  define("work.consume", "Consume reserved parts", "Work & QC", "technician", "job_rev gate_rev estimate_version", [
    ref("task_id", "Task", "tasks"), consume("items", false)
  ], "Use existing reservations only. Consumption rechecks current scope, permission and stock; it is not a free stock edit.");
  define("qc.record", "Record configured QC / retest", "Work & QC", "technician", "job_rev gate_rev", [
    ref("task_id", "Original repair task for QC", "tasks"), int("profile_version", "Task's immutable QC profile version", { min: 1 }),
    f("checks", "Explicit check results", "repeat", { itemLabel: "check result", fields: [
      ref("check_id", "Configured check", "checks"), sel("result", "Result", ["pass", "fail", "not_tested"]), note("note", "Result / limitation", true)
    ] }), sel("overall", "Overall result", ["pass", "fail", "not_tested"]), sel("reason", "Reason", reasons, { optional: true })
  ], "Failed and not-tested checks block repaired readiness. Record every configured required check. A fail retains this result and creates linked rework; after completing rework, record the fresh retest on the original task.");
  define("qc.profile_create", "Create QC profile", "Quality profiles", "manager", "", [
    t("label", "Profile name"), f("checks", "Configured checks", "repeat", { itemLabel: "quality check", fields: profileFields }), reason()
  ], "Profiles are immutable versions. Configuration is not permission to write technician results.");
  define("qc.profile_update", "Issue new QC profile version", "Quality profiles", "manager", "profile_rev", [
    hidden("profile_id", function (c) { return c.profile.profile_id; }), f("checks", "Checks for future tasks", "repeat", { itemLabel: "quality check", fields: profileFields }), reason()
  ], "New versions apply to future tasks. Existing tasks retain their configured checks and history.");
  define("return.create", "Report recurrence · open linked return", "Returns", "return", "job_rev gate_rev", [
    hidden("original_job_id", function (c) { return c.job.job_id; }), note("reported_recurrence", "What has returned / changed"),
    t("customer_attribution", "Person reporting recurrence", true)
  ], "Creates a distinct actionable linked job, not a change to this repair's history. Manager intake owns review and contact; inspection and repair need fresh permission.");
  define("return.assess", "Record return coverage assessment", "Returns", "manager", "job_rev gate_rev", [
    ref("return_case_id", "Linked return case", "cases"), sel("coverage", "Coverage", ["covered", "not_covered", "undetermined"]),
    reason(), ref("inspection_permission_id", "This return job's inspection permission", "permissions")
  ], "Target the NEW return job after separately permitted inspection. Coverage does not grant repair permission or close the case.");
  define("settlement.charge", "Post authorized charge", "Settlement", "desk", "job_rev gate_rev estimate_version", [
    hidden("job_id", function (c) { return c.job.job_id; }), sel("source", "Authorization source", ["estimate_line","estimate_tax","diagnostic_fee"]),
    int("estimate_version", "Authorized estimate version", { min: 1, optional: true }),
    ref("line_ref", "Authorized estimate line", "lines", { optional: true }), ref("permission_id", "Diagnostic permission", "permissions", { optional: true }),
    amount("amount_minor", "Charge amount"), sel("currency", "Currency", ["USD"], { value: "USD" }), sel("reason", "Reason", reasons, { optional: true })
  ], "Charges retain exact authorization provenance. This is recordkeeping, not charging a card.");
  define("settlement.credit", "Record credit / write-off", "Settlement", "manager", "job_rev", [
    hidden("job_id", function (c) { return c.job.job_id; }), ref("charge_id", "Related charge", "charges", { optional: true }),
    amount("amount_minor", "Credit amount"), sel("currency", "Currency", ["USD"], { value: "USD" }), sel("kind", "Credit type", ["correction", "write_off", "customer_credit"]), reason()
  ], "Append a credit with reason. Original charges remain visible.");
  define("settlement.payment_record", "Record external payment", "Settlement", "desk", "job_rev", [
    hidden("job_id", function (c) { return c.job.job_id; }), amount("amount_minor", "Payment received"), sel("currency", "Currency", ["USD"], { value: "USD" }),
    sel("method", "External method", ["cash", "card_external", "bank_external", "other_external"]), t("reference", "External reference", true), date("received_at", "Received at", true)
  ], "Record only a payment already received externally. BenchFlow does not process or verify funds.");
  define("settlement.payment_simulate", "Simulate fictional payment", "Settlement", "desk", "job_rev", [
    hidden("job_id", function (c) { return c.job.job_id; }), amount("amount_minor", "Simulated amount"), sel("currency", "Currency", ["USD"], { value: "USD" })
  ], "Demo-only fictional record. It changes the demo balance for workflow testing; it does not mean funds were received, and no payment is processed.");
  define("settlement.refund_record", "Record external refund", "Settlement", "desk", "job_rev", [
    hidden("job_id", function (c) { return c.job.job_id; }), ref("payment_id", "Original payment", "payments"),
    amount("amount_minor", "Refund paid externally"), sel("currency", "Currency", ["USD"], { value: "USD" }),
    t("reference", "External reference", true), date("refunded_at", "Refunded at", true), reason()
  ], "Refunds are linked to a payment and checked against its unrefunded amount. This records an external refund; it does not send money.");
  define("settlement.correct", "Append recordkeeping correction", "Settlement", "manager", "job_rev", [
    ref("entry_id", "Original entry", "entries"), amount("delta_minor", "Signed correction (+ / −)", { min: undefined }),
    sel("currency", "Currency", ["USD"], { value: "USD" }), t("external_reference", "External evidence", true), reason()
  ], "Compensation preserves the original entry. A real refund must be recorded as a linked refund, not hidden in a correction.");
  define("property.authorize_collector", "Verify authorized collector", "Handover", "desk", "job_rev", [
    hidden("job_id", function (c) { return c.job.job_id; }), t("collector_name", "Authorized collector's name"),
    sel("method", "Customer authorization verified", ["in_person", "phone"]), sel("reason", "Reason", reasons, { optional: true })
  ], "Verify authorization with the customer before recording the named collector.");
  define("property.handover", "Record verified property handover", "Handover", "desk", "job_rev gate_rev", [
    hidden("job_id", function (c) { return c.job.job_id; }), sel("handover_kind", "Property disposition", ["completed_repair", "return_unrepaired"]),
    t("collector_name", "Verified collector present"), note("acknowledgement", "Collector acknowledgement"),
    sel("manager_balance_override_reason", "Manager-only balance exception", reasons, { optional: true })
  ], "Completed repair requires current authorization, completed work and all required QC passed. Unrepaired return requires explicit disposition, safe stop and acknowledgement that repair is incomplete; no QC pass is implied. Balance exception never erases debt or overrides consent, stock, safety or QC.");
  define("property.handover_simulate", "Simulate fictional collection", "Handover", "desk", "job_rev gate_rev", [
    hidden("job_id", function (c) { return c.job.job_id; }), t("collector_name", "Fictional collector in demo")
  ], "Demo-only fictional workflow record. No collector authorization or identity is verified, and no device is physically transferred.");
  define("policy.update_demo", "Update demo shop policy", "Policy", "manager", "policy_rev", [
    t("shop_timezone", "Shop IANA timezone"), int("estimate_window_days", "Default estimate window (days)"),
    amount("diagnostic_fee_minor", "Default diagnostic fee"), f("tax_enabled", "Tax enabled", "bool"), reason()
  ], "Demo policy changes are attributable. The server controls allowed configuration; this does not rewrite issued estimates.");

  B.can = function (name, c) {
    var s = B.session, a = s && s.account, def = A[name]; if (!a || !def) return false;
    if (name === "settlement.payment_simulate" || name === "property.handover_simulate") { if (!s.demo_mode) return false; }
    if (s.demo_mode && ["settlement.payment_record", "settlement.refund_record", "property.authorize_collector", "property.handover"].indexOf(name) !== -1) return false;
    c = c || {};
    var r = a.roles || [], manager = r.indexOf("manager") !== -1, desk = manager || r.indexOf("intake") !== -1, tech = r.indexOf("technician") !== -1;
    if (a.state !== "active") return false;
    if (a.kind === "customer") return ["consent", "customer", "return"].indexOf(def.access) !== -1 && (!c.job || !!c.job.status);
    var assigned = c.job && c.job.assignment && c.job.assignment.assigned_staff_ids.indexOf(a.id) !== -1;
    if (c.job && (def.access === "technician" || def.access === "staff" || def.access === "planner") && !manager && !desk && !assigned) return false;
    if (c.job && def.access === "technician" && !assigned) return false;
    if (c.job && def.access === "planner" && !manager && !assigned) return false;
    if (c.taskId && def.access === "technician" && !(c.job.work || []).some(function (w) { return w.task_id === c.taskId && w.assigned_staff_id === a.id; })) return false;
    if (name === "account.issue_recovery" && c.account && (c.account.id === a.id || (c.account.kind === "staff" && !manager))) return false;
    if (name === "account.change_roles" && c.account && c.account.id === a.id) return false;
    return { manager: manager, desk: desk, technician: tech, staff: desk || tech, planner: manager || tech, consent: desk, return: desk, recovery: desk, customer: false }[def.access] || false;
  };
  B.actionButtons = function (names, context) {
    return B.el("div", { class: "actions" }, names.filter(function (name) { return B.can(name, context); }).map(function (name, i) {
      var button = B.button(A[name].label, function () { B.openAction(name, context); }, i === 0 ? "primary" : "");
      var reason = mappingBlockReason(name, context || {});
      if (reason) { button.disabled = true; button.title = reason; button.setAttribute("aria-label", A[name].label + " unavailable: " + reason); }
      return button;
    }));
  };
  B.actionSection = function (title, names, context) {
    var allowed = names.filter(function (name) { return B.can(name, context); });
    if (!allowed.length) return null;
    return B.el("details", { class: "disclosure" }, [B.text("summary", title), B.actionButtons(allowed, context)]);
  };
  function currentEstimate(c) {
    if (!c.job) return null;
    return c.job.current_estimate || (c.job.estimates || []).slice().sort(function (a, b) { return b.version - a.version; })[0] || null;
  }
  B.currentEstimate = currentEstimate;
  function unknownPartLines(estimate) {
    return (estimate && estimate.lines || []).filter(function (line) { return line.kind === "part" && !line.part_id; });
  }
  B.hasUnknownCatalogPart = function (record) { return unknownPartLines(record).length > 0; };
  function unknownDraft(job, draftId) {
    var draft = (job && job.estimate_drafts || []).find(function (item) { return item.draft_id === draftId; });
    return draft && unknownPartLines(draft).length ? draft : null;
  }
  function hasUnknownHistoricalScope(job, scopeRef) {
    if (!job || !scopeRef) return false;
    return (job.estimates || []).concat(job.estimate_drafts || []).some(function (record) {
      return unknownPartLines(record).some(function (line) { return line.scope_ref === scopeRef; });
    });
  }
  B.hasUnknownCatalogScope = hasUnknownHistoricalScope;
  function mappingBlockReason(name, c, data) {
    var estimate = currentEstimate(c), unknown = unknownPartLines(estimate), task;
    if (name === "estimate.issue" && data && unknownDraft(c.job, data.draft_id))
      return "This historical draft contains a part with no recorded catalog identity. It remains visible but cannot be issued. Create a new draft with a verified catalog selection.";
    if (name === "work.task_create" && data && data.scope_kind === "estimate_line" &&
        hasUnknownHistoricalScope(c.job, data.scope_ref))
      return "This repair scope contains a part with unknown historical catalog identity. Plan fresh mapped work only after issuing a new mapped estimate and recording its explicit approval.";
    if ((name === "work.start" || name === "work.consume" ||
        (name === "work.log" && data && ["resume","complete"].indexOf(data.kind) !== -1)) && c.taskId) {
      task = (c.job && c.job.work || []).find(function (item) { return item.task_id === c.taskId; });
      if (task && task.scope_kind === "estimate_line" && hasUnknownHistoricalScope(c.job, task.scope_ref))
        return "This repair task depends on a part whose historical catalog identity is unknown. Start, resume, completion and part consumption are unavailable; create and approve a fresh mapped estimate before catalog-dependent work.";
    }
    if (!unknown.length) return "";
    if (name === "estimate.decision") return "This issued estimate contains a part whose historical catalog identity was not recorded. Do not approve an inferred match. Create a new mapped draft, issue a fresh immutable version, and record a new explicit decision.";
    if (name === "parts.demand" || name === "parts.reserve") return "Part demand and reservation are unavailable while this current estimate contains a part with unknown historical catalog identity. Verify the exact catalog item in a new mapped draft first.";
    return "";
  }
  B.catalogMappingBlockReason = mappingBlockReason;
  function consentFields(name, c) {
    var customer = B.session.account.kind === "customer", e = currentEstimate(c), p = c.job.diagnostic ? c.job.diagnostic.current : c.job.diagnostic_permission;
    if (name === "diagnostic.permission") return [
      sel("decision", "Diagnostic decision", customer ? ["grant", "decline", "revoke"] : ["request", "grant", "decline", "revoke"]),
      customer ? hidden("scope", p && p.scope) : note("scope", "Exact diagnostic scope"),
      customer ? hidden("fee", p && p.fee) : moneyObject("fee", "Configured diagnostic fee · must match current shop policy"), method(customer),
      customer ? hidden("attribution", null) : t("attribution", "Customer attribution (blank for request)", true),
      hidden("fee_source", "configured_policy"), hidden("expected_decision_id", p && p.permission_id || null)
    ];
    if (name === "estimate.revoke") return [hidden("version", e ? e.version : null), reason(), method(customer), attribution(customer)];
    return [sel("decision", "Your explicit decision", ["approve", "decline"]), hidden("version", e ? e.version : null), method(customer), attribution(customer),
      hidden("confirmation", e ? { version: e.version, total_minor: e.total_minor, currency: e.currency, expires_at: e.expires_at } : null)];
  }
  B.actionFields = function (name, c) {
    if (name === "return.create" && B.session.account.kind === "customer") return A[name].fields.map(function (field) { return field.key === "customer_attribution" ? hidden(field.key, null) : field; });
    return ["diagnostic.permission","estimate.decision","estimate.revoke"].indexOf(name) !== -1 ? consentFields(name, c) : A[name].fields;
  };
  function initialValues(name, c) {
    var p = c.job && (c.job.diagnostic ? c.job.diagnostic.current : c.job.diagnostic_permission), e = currentEstimate(c);
    if (name === "customer.update_contact") return c.customer;
    if (name === "account.change_roles") return c.account;
    if (name === "policy.update_demo") return c.policy;
    if (name === "diagnostic.permission" && p) return { scope: p.scope, fee: p.fee };
    if (name === "estimate.draft_save" && c.draftId) {
      var d = c.job.estimate_drafts.find(function (draft) { return draft.draft_id === c.draftId; }); return Object.assign({}, d || {});
    }
    if (name === "settlement.charge") return { estimate_version: c.chargeSource === "diagnostic_fee" ? null : c.chargeVersion || (e ? e.version : null) };
    if (name === "qc.profile_update") return { checks: c.profile.checks };
    return {};
  }
  async function expectedFor(def, c, data) {
    var expected = {}, job = c.job || {}, e = currentEstimate(c), missing = [];
    var sources = { job_rev: job.job_rev, gate_rev: job.gate_rev, customer_rev: c.customer && c.customer.customer_rev,
      account_rev: c.account && c.account.account_rev, profile_rev: c.profile && c.profile.profile_rev,
      request_rev: c.request && c.request.request_rev, policy_rev: c.policy && c.policy.policy_rev,
      estimate_version: e ? e.version : null };
    if (def.name === "work.task_create" && data.scope_kind === "diagnostic") sources.estimate_version = null;
    if (def.name === "work.task_reassign") {
      var selectedTask = (job.work || []).find(function (w) { return w.task_id === data.task_id; });
      sources.task_rev = selectedTask && selectedTask.task_rev;
    }
    if (def.name === "job.resolve_dispute") {
      var dispute = (job.dispositions || []).find(function (d) { return d.disposition_id === data.disposition_id; });
      sources.disposition_rev = dispute && dispute.disposition_rev;
    }
    if (def.name === "settlement.charge") sources.estimate_version = data.source === "diagnostic_fee" ? null : data.estimate_version;
    if (c.request) sources.customer_rev = c.request.customer_rev;
    if (def.expected.indexOf("stock_rev") !== -1) {
      var partIds = [], partId = data.part_id;
      if (data.reservation_id) partId = (job.parts || []).filter(function (p) { return p.reservations.some(function (r) { return r.reservation_id === data.reservation_id; }); }).map(function (p) { return p.part_id; })[0];
      if (partId) partIds.push(partId);
      (data.consume || data.items || []).forEach(function (item) {
        (job.parts || []).forEach(function (p) { if (p.reservations.some(function (r) { return r.reservation_id === item.reservation_id; }) && partIds.indexOf(p.part_id) === -1) partIds.push(p.part_id); });
      });
      if (partIds.length !== 1) missing.push("Select exactly one part from this docket's current references.");
      else {
        var part = await B.request(B.query("/staff/parts/" + encodeURIComponent(partIds[0]), { job_id: job.job_id }));
        sources.stock_rev = part.stock_rev;
      }
      if (def.name === "parts.reserve" && job.diagnostic && job.diagnostic.current && data.scope_ref === job.diagnostic.current.permission_id) sources.estimate_version = null;
    }
    if (def.name === "work.start" || def.name === "work.consume") {
      var task = (job.work || []).filter(function (t) { return t.task_id === data.task_id; })[0], affected = {};
      if (!task) missing.push("The task is absent from this authorized docket.");
      if (task && task.scope_kind === "diagnostic") sources.estimate_version = null;
      (job.parts || []).forEach(function (p) {
        if (def.name === "work.start" && task && p.scope_ref === task.scope_ref && p.reservations.some(function (r) { return r.state === "active" || r.state === "partially_released"; })) affected[p.part_id] = p;
      });
      (data.consume || data.items || []).forEach(function (item) {
        var parents = (job.parts || []).filter(function (p) { return p.reservations.some(function (r) { return r.reservation_id === item.reservation_id; }); });
        if (parents.length !== 1) missing.push("A selected reservation is not uniquely present in this docket.");
        else affected[parents[0].part_id] = parents[0];
      });
      Object.keys(affected).forEach(function (id) {
        var rev = affected[id].stock_rev;
        if (!Number.isInteger(rev) || rev < 1) missing.push("The selected part's stock revision is unavailable.");
        else expected["stock_rev:" + id] = rev;
      });
    }
    if (def.name === "customer.merge") {
      var source = await B.request("/staff/customers/" + encodeURIComponent(data.source_customer_id));
      var target = await B.request("/staff/customers/" + encodeURIComponent(data.target_customer_id));
      sources.source_customer_rev = source.customer_rev; sources.target_customer_rev = target.customer_rev;
      c.mergeReview = [source, target];
    }
    if (def.name === "account.unlink_customer" && !sources.customer_rev && c.account.customer_id) {
      var cust = await B.request("/staff/customers/" + encodeURIComponent(c.account.customer_id)); sources.customer_rev = cust.customer_rev;
    }
    def.expected.forEach(function (key) {
      var value = sources[key], nullable = key === "estimate_version" && ["estimate.issue", "parts.reserve", "work.start", "work.consume", "work.task_create", "settlement.charge"].indexOf(def.name) !== -1;
      if (!(Number.isInteger(value) && value > 0) && !(value === null && nullable)) missing.push(key + " is missing from the authorized server projection.");
      else expected[key] = value;
    });
    if (missing.length) { var error = new Error("This action cannot be sent safely: " + missing.join(" ") + " Nothing was recorded."); error.code = "CONTRACT_INCOMPLETE"; throw error; }
    return expected;
  }
  function targetFor(name, c, data) {
    if (name === "customer.update_contact") return { type: "customer", id: c.customer.customer_id };
    if (name === "job.create") return { type: "customer", id: data.customer_id };
    if (name === "parts.receive") return { type: data.order_id ? "job" : "part", id: data.order_id ? c.job.job_id : data.part_id };
    if (["job.","diagnostic.","estimate.","work.","settlement.","property.","return."].some(function (prefix) { return name.indexOf(prefix) === 0; }) || ["parts.demand","parts.reserve","parts.release","parts.manual_order","parts.record_order_outcome","qc.record"].indexOf(name) !== -1) return { type: "job", id: c.job.job_id };
    return null;
  }
  // One construction path for the review UI and contract-boundary checks.
  B.buildCommand = async function (name, c, data) {
    var mappingBlock = mappingBlockReason(name, c || {}, data || {});
    if (mappingBlock) { var blocked = new Error(mappingBlock + " Nothing was recorded."); blocked.code = "HISTORICAL_CATALOG_UNKNOWN"; throw blocked; }
    var envelope = { command_id: B.uuid(), action: name, expected: await expectedFor(A[name], c, data), data: data };
    var target = targetFor(name, c, data); if (target) envelope.target = target;
    return envelope;
  };
  B.openAction = async function (name, base) {
    if (B.readOnlyDocket) { B.notice("Docket is read-only", "Refresh successfully before opening a record action.", "caution"); return; }
    var def = A[name]; if (!B.can(name)) { B.notice("Action unavailable", "Your role does not allow this action. Ask a manager for assistance.", "error"); return; }
    var c = Object.assign({}, base || {}), body = B.el("div", {}, B.text("p", "Loading current references…", "loading"));
    var d = B.dialog(def.label, body);
    var transition = d._transition;
    try { if (B.prepareContext) c = await B.prepareContext(name, c); }
    catch (e) { if (!d.open || d._transition !== transition) return; B.clear(body); body.appendChild(B.callout("References unavailable", B.errorText(e), "error")); body.appendChild(B.button("Retry references", function () { B.openAction(name, base); })); B.focusHeading(d); return; }
    if (!d.open || d._transition !== transition) return;
    B.clear(body);
    if (!B.can(name, c)) { body.appendChild(B.callout("Assignment or access changed", "No action was sent. Ask a manager to review the current assignment.", "error")); return; }
    var mappingBlock = mappingBlockReason(name, c);
    if (mappingBlock) {
      body.appendChild(B.callout("Catalog mapping unavailable", mappingBlock, "caution"));
      if (name === "estimate.decision") body.appendChild(B.callout("Required remediation", "Start a new draft and select the verified catalog item; issue it as a fresh immutable version, then obtain a new explicit customer decision. The historical estimate and any old draft remain unchanged."));
      return;
    }
    if (name === "customer.merge" && !c.mergeTarget) {
      body.appendChild(B.callout("Keep the current record; choose the duplicate to merge into it", "Search, compare both histories and verify the same person before continuing. Nothing has been changed."));
      body.appendChild(B.facts([["Keep", c.customer.display_name], ["Phone on file", B.known(c.customer.phone)]]));
      var search = B.form([t("query", "Find the source duplicate by name or known contact")], c), results = B.el("div", { "aria-live": "polite" });
      search.appendChild(B.el("button", { type: "submit", text: "Search customer records" })); body.appendChild(search); body.appendChild(results);
      search.addEventListener("submit", async function (event) {
        event.preventDefault(); var input = search.read(); if (!input) return;
        var button = search.querySelector("button"); button.disabled = true; B.clear(results); results.appendChild(B.text("p", "Searching existing customer records…", "loading"));
        try {
          var page = await B.request(B.query("/staff/customers", { query: input.query })); B.clear(results);
          function append(page) {
            page.items.filter(function (item) { return item.customer_id !== c.customer.customer_id; }).forEach(function (item) {
              results.appendChild(B.el("div", { class: "queue-card" }, [B.text("strong", item.display_name), B.text("p", (item.phone_masked || "No phone") + " · " + (item.email_masked || "No email")),
                B.link("Review service history", "#/customers/" + item.customer_id), B.button("Choose as duplicate source", function () { B.openAction(name, Object.assign({}, c, { mergeTarget: item.customer_id })); })]));
            });
            if (!results.childNodes.length) results.appendChild(B.empty("No other matching records. Try a different name or known contact."));
            if (page.next_cursor) results.appendChild(B.button("More matching customers", async function (e) {
              var more = e.currentTarget; more.disabled = true;
              try { var next = await B.request(B.query("/staff/customers", { query: input.query, cursor: page.next_cursor })); more.remove(); append(next); }
              catch (err) { more.disabled = false; B.notice("Search unavailable", B.errorText(err), "error"); }
            }));
          }
          append(page);
        } catch (err) { B.clear(results); results.appendChild(B.callout("Search unavailable", B.errorText(err), "error")); }
        finally { button.disabled = false; }
      });
      return;
    }
    if (name === "contact.change_request" && !c.requestFields) {
      body.appendChild(B.text("p", "Which details should the shop confirm? Existing contact details stay unchanged until staff verify this request."));
      var choose = B.form([def.fields[0]], c); choose.appendChild(B.el("button", { type: "submit", text: "Continue to requested values", class: "primary" })); body.appendChild(choose);
      choose.addEventListener("submit", function (event) { event.preventDefault(); var chosen = choose.read(); if (chosen) B.openAction(name, Object.assign({}, c, { requestFields: chosen.requested_fields })); });
      return;
    }
    if (name === "settlement.charge" && !c.chargeSource) {
      body.appendChild(B.callout("Choose charge provenance", "A record must refer to an exact immutable estimate line, its manually entered tax, or the disclosed diagnostic permission fee. No funds are processed."));
      [["estimate_line","Estimate line"],["estimate_tax","Estimate tax"],["diagnostic_fee","Diagnostic permission fee"]].forEach(function (pair) {
        body.appendChild(B.button(pair[1], function () { B.openAction(name, Object.assign({}, c, { chargeSource: pair[0] })); }));
      });
      return;
    }
    if (name === "settlement.charge" && c.chargeSource !== "diagnostic_fee" && !c.chargeVersion) {
      body.appendChild(B.text("p", "Choose the immutable version that authorized the charge. Earlier authorization remains attributable; this does not approve new work."));
      var versions = c.job.estimates || [];
      body.appendChild(versions.length ? B.el("div", { class: "stack" }, versions.map(function (version) {
        return B.button("v" + version.version + " · " + B.money(version.total_minor, version.currency) + " · " + B.human(version.decision.value), function () { B.openAction(name, Object.assign({}, c, { chargeVersion: version.version })); });
      })) : B.empty("No issued estimate is available for a provenance-linked charge."));
      return;
    }
    if (name === "estimate.draft_save" && c.draftId === undefined && c.job.estimate_drafts.length) {
      body.appendChild(B.text("p", "Choose an unissued draft to edit, or begin a separate proposal. Issued versions cannot be edited."));
      body.appendChild(B.button("Begin a new draft", function () { B.openAction(name, Object.assign({}, c, { draftId: null })); }, "primary"));
      c.job.estimate_drafts.forEach(function (draft) {
        var historicalUnknown = unknownPartLines(draft).length > 0;
        var edit = B.button(historicalUnknown ? "Read-only · unknown catalog identity · " + B.date(draft.updated_at) : "Edit draft · " + B.date(draft.updated_at),
          function () { B.openAction(name, Object.assign({}, c, { draftId: draft.draft_id })); });
        if (historicalUnknown) { edit.disabled = true; edit.title = "This retained draft is read-only in the editor so no historical line or identity is silently replaced."; }
        body.appendChild(edit);
      });
      return;
    }
    if (name === "estimate.draft_save" && c.draftId && unknownDraft(c.job, c.draftId)) {
      body.appendChild(B.callout("Historical draft retained as read-only", "At least one part line has no recorded catalog identity. Its description and other recorded facts remain visible on the docket; no catalog item is guessed and no line is removed."));
      body.appendChild(B.button("Start a new mapped draft", function () { B.openAction(name, Object.assign({}, c, { draftId: null })); }, "primary"));
      return;
    }
    if (name === "estimate.issue" && !(c.options.drafts || []).length) {
      body.appendChild(B.callout("No issueable mapped draft", "Drafts with unknown historical part identities remain in history but cannot be issued from here. Create a new draft and select the exact verified catalog item."));
      body.appendChild(B.button("Create a new mapped draft", function () { B.openAction("estimate.draft_save", Object.assign({}, c, { draftId: null })); }, "primary"));
      return;
    }
    if (["work.start","work.log","work.consume","qc.record"].indexOf(name) !== -1 && !c.taskId) {
      body.appendChild(B.text("p", "Choose your assigned task first. Its scope and configured checks will be loaded from this docket."));
      var tasks = (c.job.work || []).filter(function (w) { return w.assigned_staff_id === B.session.account.id; });
      body.appendChild(tasks.length ? B.el("div", { class: "stack" }, tasks.map(function (w) {
        var taskContext = Object.assign({}, c, { taskId: w.task_id }), reason = mappingBlockReason(name, taskContext);
        var choice = B.button(B.taskPurpose(w, c.job) + " · " + B.human(w.state), function () { B.openAction(name, taskContext); });
        if (reason) { choice.disabled = true; choice.title = reason; choice.setAttribute("aria-label", choice.textContent + " unavailable: " + reason); }
        return choice;
      })) : B.empty("No tasks are assigned to you. A manager or assigned technician must plan the task; planning does not authorize a start."));
      return;
    }
    if (name === "return.assess" && !(c.options.cases || []).length) {
      body.appendChild(B.callout("Open the linked return first", "Coverage is recorded on the NEW return docket after fresh inspection permission, never against the original repair."));
      (c.job.return_cases || []).filter(function (r) { return r.return_job_id !== c.job.job_id; }).forEach(function (r) {
        body.appendChild(B.link("Open return for inspection / coverage · " + r.reported_recurrence, "#/jobs/" + encodeURIComponent(r.return_job_id) + "/returns", "button"));
      });
      return;
    }
    var fields = B.actionFields(name, c);
    if (name === "estimate.draft_save") fields = fields.map(function (field) { return field.key === "draft_id" ? hidden("draft_id", c.draftId || null) : field; });
    if (name === "property.handover" && B.session.account.roles.indexOf("manager") === -1) fields = fields.map(function (field) { return field.key === "manager_balance_override_reason" ? hidden(field.key, null) : field; });
    if (name === "customer.merge") fields = [hidden("source_customer_id", c.mergeTarget), hidden("target_customer_id", c.customer.customer_id)].concat(def.fields.slice(2));
    if (name === "contact.change_request") fields = [hidden("requested_fields", c.requestFields)].concat(def.fields.slice(1).map(function (field) { return c.requestFields.indexOf(field.key) !== -1 ? field : hidden(field.key, { known: false, value: null }); }));
    if (name === "settlement.charge" && c.chargeSource !== "diagnostic_fee") {
      var version = c.job.estimates.find(function (estimate) { return estimate.version === c.chargeVersion; });
      if (!version) { body.appendChild(B.empty("This estimate version is unavailable. Close and refresh the docket.")); return; }
      c.options.lines = version.lines.map(function (line) { return [line.line_id, "v" + version.version + " · " + line.description + " · " + B.money(line.line_amount_minor, version.currency)]; });
      fields = fields.map(function (field) {
        if (field.key === "source") return hidden("source", c.chargeSource);
        if (field.key === "permission_id") return hidden("permission_id", null);
        if (field.key === "estimate_version") return hidden("estimate_version", version.version);
        if (field.key === "line_ref") return c.chargeSource === "estimate_tax" ? hidden("line_ref", null) : Object.assign({}, field, { optional: false });
        return field;
      });
      body.appendChild(B.estimateCard(version, false));
    }
    if (name === "settlement.charge" && c.chargeSource === "diagnostic_fee") {
      var permission = c.job.diagnostic.current;
      if (!permission) { body.appendChild(B.empty("No diagnostic permission was supplied for fee provenance. No charge can be submitted.")); return; }
      fields = fields.map(function (field) {
        if (field.key === "source") return hidden("source", "diagnostic_fee");
        if (field.key === "estimate_version" || field.key === "line_ref") return hidden(field.key, null);
        if (field.key === "permission_id") return hidden(field.key, permission.permission_id);
        if (field.key === "amount_minor") return hidden(field.key, permission.fee.amount_minor);
        if (field.key === "currency") return hidden(field.key, permission.fee.currency);
        return field;
      });
      body.appendChild(B.callout("Exact disclosed diagnostic fee", permission.scope + " · " + B.human(permission.state) + " · " + B.money(permission.fee.amount_minor, permission.fee.currency) + " · policy revision " + (permission.fee_policy_rev || "unknown historical provenance")));
    }
    var task = c.taskId && c.job.work.filter(function (w) { return w.task_id === c.taskId; })[0];
    if (task) {
      fields = fields.map(function (field) {
        if (field.key === "task_id") return hidden("task_id", task.task_id);
        if (field.key === "scope_ref") return hidden("scope_ref", task.scope_ref);
        return field;
      });
      body.appendChild(B.callout("Selected task · " + B.taskPurpose(task, c.job), B.taskScope(task, c.job) + " · " + B.human(task.state)));
      if (name === "work.log" && task.scope_kind === "estimate_line" && B.hasUnknownCatalogScope(c.job, task.scope_ref)) {
        fields = fields.map(function (field) {
          return field.key === "kind" ? Object.assign({}, field, { options: field.options.filter(function (option) { return option !== "resume" && option !== "complete"; }) }) : field;
        });
        body.appendChild(B.callout("Resume and completion unavailable", "This task's part line has no recorded catalog identity. Safe notes and pauses remain available; no resume or completion can be recorded until a fresh mapped estimate is approved.", "caution"));
      }
      if (name === "qc.record") {
        var checks = c.job.qc.filter(function (q) { return q.task_id === task.task_id; });
        if (!checks.length) { body.appendChild(B.empty("No configured checks are present for this task. Ask a manager to review quality configuration.")); return; }
        if (checks.some(function (q) { return q.profile_version !== checks[0].profile_version; })) { body.appendChild(B.callout("Quality configuration unavailable", "This task reports several profile versions. Ask a manager to review it; no version is guessed.", "error")); return; }
        fields = [hidden("task_id", task.task_id), hidden("profile_version", checks[0].profile_version)].concat(checks.map(function (q) {
          return f(q.check_id, q.label + (q.required ? " · required" : " · optional"), "object", { fields: [hidden("check_id", q.check_id), sel("result", "Result", ["pass","fail","not_tested"]), note("note", "Result / limitation", true)] });
        }), [sel("overall", "Overall result", ["pass","fail","not_tested"]), sel("reason", "Reason", reasons, { optional: true })]);
      }
    }
    var help = B.callout(def.label, def.help);
    body.appendChild(help);
    if (name === "work.task_create" && B.hasUnknownCatalogPart(e)) {
      body.appendChild(B.callout("Unmapped historical repair scopes are unavailable", "Scopes tied to a part line with no recorded catalog identity are excluded from this task form. Do not guess a part ID. Create a fresh mapped estimate and record its explicit approval before planning that repair scope.", "caution"));
    }
    if (c.job) body.appendChild(B.facts([["Docket", c.job.job_code], ["Device", B.device(c.job.device)]]));
    var e = currentEstimate(c);
    if ((name === "estimate.decision" || name === "estimate.revoke") && !e) { body.appendChild(B.callout("No issued estimate", "Ask the shop to issue an estimate before recording a versioned decision.", "caution")); return; }
    if ((name === "estimate.decision" || name === "estimate.revoke") && e) body.appendChild(B.estimateCard(e, false));
    if (name === "diagnostic.permission") {
      var permission = c.job.diagnostic_permission || c.job.diagnostic && c.job.diagnostic.current;
      body.appendChild(B.callout("Configured-policy fee only", "The server snapshots the configured fee and its policy revision. No arbitrary fee is authorized by this form. A later policy edit does not change an already disclosed customer decision."));
      if (permission) body.appendChild(B.facts([["Scope", permission.scope], ["Fee", B.money(permission.fee.amount_minor, permission.fee.currency)], ["Fee source", B.human(permission.fee_source)], ["Fee policy revision", permission.fee_policy_rev || "Unknown historical provenance"], ["Current decision", B.human(permission.state)]]));
    }
    var values = Object.assign({}, initialValues(name, c), c.safeValues || {});
    if (name === "qc.profile_update") values.checks = c.profile.checks.map(function (q) { return { label: q.label, required: q.required }; });
    var form = B.form(fields, c, values), review = B.el("section", { hidden: true }), envelope = null, pending = false, retryButton;
    var issuer = B.session.account.id;
    B.captureDraft = function () {
      B.safeDraft = { issuer: issuer, name: name, base: {
        job: c.job ? { job_id: c.job.job_id } : undefined, customer: c.customer ? { customer_id: c.customer.customer_id } : undefined,
        account: c.account ? { id: c.account.id } : undefined, part: c.part ? { part_id: c.part.part_id } : undefined,
        profile: c.profile ? { profile_id: c.profile.profile_id } : undefined, request: c.request ? { request_id: c.request.request_id } : undefined,
        policy: c.policy ? {} : undefined, linkedCustomerId: c.linkedCustomerId, jobId: c.jobId, taskId: c.taskId, dispositionId: c.dispositionId, safeValues: form.safeSnapshot()
      } };
    };
    var foot = B.el("div", { class: "actions form-foot" }, [B.el("button", { type: "submit", text: "Review before recording", class: "primary" }), B.button("Cancel", function () { d.close(); })]);
    form.appendChild(foot); body.appendChild(form); body.appendChild(review);
    form.addEventListener("submit", async function (event) {
      event.preventDefault(); if (pending) return;
      var data = form.read(); if (!data) return;
      if (def.normalize) data = def.normalize(data);
      if (name === "qc.record" && task) {
        var configured = c.job.qc.filter(function (q) { return q.task_id === task.task_id; });
        data.checks = configured.map(function (q) { var value = data[q.check_id]; delete data[q.check_id]; return value; });
      }
      if (name === "diagnostic.permission" && data.decision === "request") data.attribution = null;
      if (name === "property.handover" && B.session.account.roles.indexOf("manager") === -1) data.manager_balance_override_reason = null;
      pending = true; foot.querySelector("button").disabled = true;
      try {
        envelope = await B.buildCommand(name, c, data);
        B.clear(review); review.hidden = false; form.hidden = true;
        var heading = B.text("h3", "Review · nothing recorded yet"); heading.tabIndex = -1; review.appendChild(heading);
        if (e && (name === "estimate.decision" || name === "estimate.revoke")) review.appendChild(B.callout("Estimate v" + e.version + " · " + B.money(e.total_minor, e.currency), "Acceptance deadline: " + B.date(e.expires_at) + ". Decision applies only to this exact version.", "caution"));
        if (c.mergeReview) review.appendChild(B.facts([["Source", c.mergeReview[0].display_name + " · " + (c.mergeReview[0].account_id ? "has account" : "no account")], ["Keep", c.mergeReview[1].display_name + " · " + (c.mergeReview[1].account_id ? "has account" : "no account")]]));
        review.appendChild(B.reviewValues(fields, name === "qc.record" && task ? Object.assign({}, data, Object.fromEntries(data.checks.map(function (q) { return [q.check_id, q]; }))) : data, c));
        var confirmation = B.el("input", { type: "checkbox" });
        review.appendChild(B.el("label", { class: "check" }, [confirmation, B.text("span", "I have reviewed these details and explicitly confirm this record.")]));
        var status = B.el("div", { role: "status" }); review.appendChild(status);
        var verb = name === "estimate.decision" ? B.human(data.decision) + " v" + e.version + " · " + B.money(e.total_minor, e.currency) : def.label + " · confirm";
        retryButton = B.button(verb, async function () {
          if (!confirmation.checked) { B.clear(status); status.appendChild(B.callout("Confirmation required", "Read the details and select the confirmation checkbox.", "error")); confirmation.focus(); return; }
          if (pending) return;
          pending = true; d.dataset.pending = "true"; retryButton.disabled = true; edit.disabled = true; confirmation.disabled = true;
          d.querySelector(".dialog-close").disabled = true;
          B.clear(status); status.appendChild(B.text("p", "Recording… wait for server confirmation."));
          try {
            var result = await B.command(envelope);
            form.clearSecrets(); B.safeDraft = null; if (d.open) B.showReceipt(result, name, c, d);
          } catch (err) {
            if (!d.open) return;
            B.clear(status); status.tabIndex = -1;
            var title = err.unknown ? "Outcome not confirmed" : "Not recorded";
            status.appendChild(B.callout(title, B.errorText(err), "error"));
            if (err.requestId) status.appendChild(B.text("p", "Request reference: " + err.requestId, "mono"));
            status.appendChild(B.text("p", "Operation: " + envelope.command_id, "mono"));
            if (err.fields && err.fields.length) status.appendChild(B.el("ul", {}, err.fields.map(function (item) { return B.text("li", item.message); })));
            if (err.unknown || err.retryable) {
              status.appendChild(B.text("p", "Check the docket/history first. A retry here reuses this exact operation ID, payload and revisions; it never creates a second operation."));
              retryButton.textContent = "Retry this same operation"; retryButton.disabled = false;
              status.appendChild(B.button("Open current record / history", function () { d.close(); if (B.refresh) B.refresh(); }));
            } else {
              status.appendChild(B.text("p", err.status === 409 ? "The record or its gates changed. Refresh and review the latest version; this decision will not be retargeted automatically." : "Close and correct the record or sign in again. No action will be auto-submitted."));
              if (err.status === 422 || err.status === 400) {
                status.appendChild(B.button("Correct these fields", function () { review.hidden = true; form.hidden = false; envelope = null; form.showErrors(err.fields || [], B.errorText(err)); }));
              }
              status.appendChild(B.button("Close & refresh", function () { d.close(); if (B.refresh) B.refresh(); }));
            }
            status.focus();
          } finally { pending = false; delete d.dataset.pending; if (d.open) d.querySelector(".dialog-close").disabled = false; }
        }, "primary");
        var edit = B.button("Back to edit", function () { review.hidden = true; form.hidden = false; envelope = null; B.focusHeading(d); });
        review.appendChild(B.el("div", { class: "actions form-foot decision-actions" }, [retryButton, edit]));
        heading.focus();
      } catch (err) { form.showErrors(err.fields || [], B.errorText(err)); }
      finally { pending = false; foot.querySelector("button").disabled = false; }
    });
  };
  B.showReceipt = function (result, name, c, dialog) {
    var contents = [], job = result.job, event, estimate;
    if (job && (name === "estimate.decision" || name === "estimate.revoke")) {
      event = (job.decision_history || []).find(function (entry) { return entry.decision_id === result.result.id; });
      estimate = job.current_estimate;
      if (!estimate || event && estimate.version !== event.estimate_version) estimate = (job.estimates || []).find(function (entry) {
        return event ? entry.version === event.estimate_version : entry.decision && entry.decision.decision_id === result.result.id;
      });
      if (!event && estimate && estimate.decision && estimate.decision.decision_id === result.result.id) event = estimate.decision;
      // Never infer a decision from the submitted form or a later current version.
      if (event && estimate) {
        contents.push(B.callout("Estimate v" + estimate.version + " " + (event.decision || event.value) + " — " + B.money(estimate.total_minor, estimate.currency),
          "Server-confirmed decision · " + B.date(result.applied_at), "success"));
        contents.push(B.facts([["Recorded by", event.recorded_by ? B.human(event.recorded_by) : event.attribution || "Shop staff"],
          ["Method", B.human(event.method)], ["Decision reference", result.result.id]]));
      } else if (event) {
        contents.push(B.callout("Estimate v" + event.estimate_version + " " + event.decision,
          "This historical receipt has no commercial amount in the current projection. Ask the shop for that version; no current-version amount is substituted.", "caution"));
      }
    }
    contents.push(B.callout(result.status === "replayed" ? "Previously recorded · same operation" : "Recorded by the server", B.actions[name].label + " · " + B.date(result.applied_at), "success"));
    if (job) {
      var status = job.status || job.summary;
      if (status) {
        contents.push(B.text("h3", "Remaining blockers"));
        contents.push(B.blockers(status.blockers));
        if (status.explanation) contents.push(B.text("p", status.explanation));
      }
    } else contents.push(B.callout("Current blockers not supplied", "Refresh the docket before taking another action. This receipt alone does not establish readiness.", "caution"));
    contents.push(B.text("p", "Receipt reference: " + result.command_id, "small"));
    if (B.session.account.kind !== "customer") contents.push(B.el("details", { class: "disclosure" }, [
      B.text("summary", "Technical record references"),
      B.facts([["Operation", result.command_id], ["Event", result.event_id], ["Result record", result.result.id], ["Aggregate", result.aggregate.type + " · " + result.aggregate.id], ["Record revision", result.aggregate.rev]])]));
    if (result.result.one_time_code) {
      var code = result.result.one_time_code;
      contents.push(B.callout(code.label, "Shown once. Expires " + B.date(code.expires_at) + ". A replay cannot recover this code.", "caution"));
      contents.push(B.text("p", code.value, "secret"));
      contents.push(B.button("Clear one-time code", function (event) { var p = event.currentTarget.previousSibling; p.textContent = "Cleared"; event.currentTarget.disabled = true; }));
      result.result.one_time_code = null;
    }
    if (result.result.code_retrieval === "not_available") contents.push(B.callout("Code no longer available", "An earlier response consumed the one-time code. Reissue explicitly after checking the account.", "caution"));
    if (result.result.linked_job_id) contents.push(B.link("Open the new linked return docket", "#/jobs/" + encodeURIComponent(result.result.linked_job_id), "button primary"));
    contents.push(B.button("Done · refresh current record", function () {
      dialog.close();
      if (name === "job.create") location.hash = "#/jobs/" + result.aggregate.id;
      else if (name === "customer.create") location.hash = "#/customers/" + result.aggregate.id;
      else if (name === "parts.create") location.hash = "#/parts/" + result.aggregate.id;
      else if (B.refresh) B.refresh();
    }, "primary"));
    B.clear(dialog.querySelector(".dialog-body")); dialog.querySelector(".dialog-body").appendChild(B.el("div", { class: "receipt" }, contents)); B.focusHeading(dialog);
    // Raw response secrets are deliberately not retained in route state, history or storage.
  };
}());
