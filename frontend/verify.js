/* Node-only source/contract checks. Never loaded by the application.
   All inputs below are isolated fictional test fixtures, not a demo API.
   Run: node frontend/verify.js (requires the project's existing Python venv).
   This does NOT prove browser usability, backend authorization or integration. */
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");
const assert = require("assert").strict;
const crypto = require("crypto");
const { spawnSync } = require("child_process");
function uuid() {
  const bytes = crypto.randomBytes(16); bytes[6] = (bytes[6] & 15) | 64; bytes[8] = (bytes[8] & 63) | 128;
  const hex = bytes.toString("hex");
  return [hex.slice(0,8),hex.slice(8,12),hex.slice(12,16),hex.slice(16,20),hex.slice(20)].join("-");
}
const root = path.resolve(__dirname, "..");
const python = process.env.BENCHFLOW_CHECK_PYTHON || path.join(root, ".venv", process.platform === "win32" ? "Scripts/python.exe" : "bin/python");
function py(code, input) {
  const run = spawnSync(python, ["-B", "-c", code], { cwd: root, input, encoding: "utf8", env: { ...process.env, PYTHONDONTWRITEBYTECODE: "1" } });
  if (run.status !== 0) throw new Error((run.stdout || "") + (run.stderr || "Python contract check failed"));
  return run.stdout.trim();
}
const shared = JSON.parse(py("import json\nfrom contracts.actions import command_json_schema,EXPECTED_KEYS,TARGET_SHAPE,JOB_TARGET_ACTIONS\nprint(json.dumps({'schemas':command_json_schema(),'expected':{a:sorted(k) for a,k in EXPECTED_KEYS.items()},'targets':TARGET_SHAPE,'jobs':sorted(JOB_TARGET_ACTIONS)}))"));
const id = n => "00000000-0000-4000-8000-" + String(n).padStart(12, "0");
const staffId = id(1), jobId = id(2), customerId = id(3), partId = id(4), secondPart = id(5), reservationId = id(6), secondReservation = id(7), scopeId = id(8), taskId = id(9);
const instant = "2026-10-23T12:00:00.000Z";
const B = {
  field: (key, label, type, extra) => ({ key, label, type, ...extra }),
  uuid,
  session: { authenticated: true, account: { id: staffId, kind: "staff", state: "active", roles: ["manager", "intake", "technician"] } },
  query: (url, args) => url + "?" + new URLSearchParams(Object.entries(args).filter(([,v]) => v !== undefined)),
  request: async url => url.includes("/parts/") ? { stock_rev: 9 } : { customer_rev: 7, display_name: "Fixture customer", account_id: null }
};
const context = vm.createContext({ window: { BF: B }, console });
vm.runInContext(fs.readFileSync(path.join(__dirname, "actions.js"), "utf8"), context);
// Small unit DOM with real registered listener dispatch/bubbling and a queued
// dialog close event. NOT layout, native validation, focus-trap or browser proof.
const domEvents = [];
let activeElement;
class TestNode {
  constructor(tag, text) { this.nodeType = 1; this.tagName = tag; this.children = []; this.attributes = {}; this.listeners = {}; this.dataset = {}; this.textContent = text || ""; this.value = ""; this.checked = false; this.disabled = false; }
  set textContent(value) { this._text = String(value); if (this.children) { this.children.forEach(n => { n.parentNode = null; }); this.children = []; } }
  get textContent() { return this._text + this.children.map(n => n.textContent).join(""); }
  set id(value) { this.attributes.id = value; }
  get id() { return this.attributes.id || ""; }
  get isConnected() { return this === domRoot || !!(this.parentNode && this.parentNode.isConnected); }
  set value(value) { this._value = String(value); }
  get value() { return this._value; }
  appendChild(node) { if (node.parentNode) node.parentNode.removeChild(node); this.children.push(node); node.parentNode = this; return node; }
  removeChild(node) { this.children.splice(this.children.indexOf(node), 1); node.parentNode = null; }
  remove() { if (this.parentNode) this.parentNode.removeChild(this); }
  get childNodes() { return this.children; }
  get lastChild() { return this.children[this.children.length - 1]; }
  get firstChild() { return this.children[0] || null; }
  setAttribute(key, value) { this.attributes[key] = value; if (key === "value") this.value = value; }
  getAttribute(key) { return this.attributes[key] || null; }
  removeAttribute(key) { delete this.attributes[key]; }
  addEventListener(type, fn, options) { (this.listeners[type] || (this.listeners[type] = [])).push({ fn, once: options && options.once }); }
  dispatchEvent(event) {
    event.target = event.target || this; event.currentTarget = this;
    event.preventDefault = event.preventDefault || function () { this.defaultPrevented = true; };
    event.stopPropagation = event.stopPropagation || function () { this.stopped = true; };
    for (const entry of (this.listeners[event.type] || []).slice()) {
      if (entry.once) this.listeners[event.type].splice(this.listeners[event.type].indexOf(entry), 1);
      entry.fn(event);
    }
    if (this["on" + event.type]) this["on" + event.type](event);
    if (event.bubbles && !event.stopped && this.parentNode) this.parentNode.dispatchEvent(event);
    return !event.defaultPrevented;
  }
  matches(selector) {
    return selector.split(",").some(s => {
      s = s.trim();
      if (s[0] === ".") return (this.className || "").split(" ").includes(s.slice(1));
      if (s[0] === "#") return this.id === s.slice(1);
      const m = s.match(/^([a-z0-9]+)?(?:\[([\w-]+)(\^?=)["']?([^"'\]]+)["']?\])?$/i);
      return !!m && (!m[1] || this.tagName === m[1]) && (!m[2] || (m[3] === "^=" ? (this.getAttribute(m[2]) || "").startsWith(m[4]) : this.getAttribute(m[2]) === m[4]));
    });
  }
  querySelectorAll(selector) { return this.children.reduce((a, n) => a.concat(n.matches(selector) ? [n] : [], n.querySelectorAll(selector)), []); }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  closest(selector) { return this.matches(selector) ? this : this.parentNode && this.parentNode.closest(selector); }
  checkValidity() { return true; }
  focus() { activeElement = this; }
  showModal() { assert.equal(!!this.open, false); this.open = true; }
  close() { if (!this.open) return; this.open = false; domEvents.push(() => this.dispatchEvent({ type: "close" })); }
}
const domRoot = new TestNode("body");
context.document = {
  createElement: tag => new TestNode(tag), createTextNode: text => new TestNode("#text", text),
  getElementById: id => domRoot.querySelector("#" + id),
  querySelector: selector => domRoot.querySelector(selector),
  querySelectorAll: selector => domRoot.querySelectorAll(selector),
  get activeElement() { return activeElement; }, addEventListener() {}
};
for (const name of ["main", "identity", "navigation", "notice", "action-dialog"]) domRoot.appendChild(new TestNode(name === "action-dialog" ? "dialog" : "div")).id = name;
domRoot.appendChild(new TestNode("a")).className = "skip";
activeElement = domRoot;
context.window.crypto = { getRandomValues: array => crypto.randomFillSync(array) };
vm.runInContext(fs.readFileSync(path.join(__dirname, "ui.js"), "utf8"), context);
vm.runInContext(fs.readFileSync(path.join(__dirname, "forms.js"), "utf8"), context);
const c = {
  customer: { customer_id: customerId, customer_rev: 7 },
  account: { id: id(10), account_rev: 6, customer_id: customerId, kind: "customer" },
  profile: { profile_id: id(11), profile_rev: 8 },
  policy: { policy_rev: 4 },
  request: { request_id: id(12), request_rev: 3, customer_rev: 7 },
  part: { part_id: partId, stock_rev: 9 },
  job: {
    job_id: jobId, job_rev: 12, gate_rev: 5,
    assignment: { assigned_staff_ids: [staffId] },
    diagnostic: { current: { permission_id: id(13), scope: "Fixture inspection", fee: { currency: "USD", amount_minor: 0 } } },
    estimates: [{ version: 2, total_minor: 1000, currency: "USD", expires_at: instant }],
    work: [{ task_id: taskId, task_rev: 6, assigned_staff_id: staffId, scope_kind: "estimate_line", scope_ref: scopeId }],
    dispositions: [{ disposition_id: id(31), disposition_rev: 3, kind: "dispute", state: "open" }],
    parts: [
      { part_id: partId, stock_rev: 9, scope_ref: scopeId, reservations: [{ reservation_id: reservationId, state: "active" }] },
      { part_id: secondPart, stock_rev: 11, scope_ref: scopeId, reservations: [{ reservation_id: secondReservation, state: "partially_released" }] }
    ]
  },
  options: {},
  defaults: {}
};
const options = { staff: staffId, technicians: staffId, parts: partId, scopes: scopeId, tasks: taskId, reservations: reservationId, drafts: id(14), orders: id(15), checks: id(16), movements: id(17), cases: id(18), permissions: id(13), lines: scopeId, charges: id(19), payments: id(20), entries: id(19), disputes: id(31) };
Object.entries(options).forEach(([key, value]) => { c.options[key] = [[value, "Fixture " + key]]; });
function dataFrom(fields) {
  const data = {};
  for (const f of fields) {
    let value = typeof f.value === "function" ? f.value(c) : f.value;
    if (f.type === "hidden") { data[f.key] = value === undefined ? null : value; continue; }
    if (f.type === "object") value = dataFrom(f.fields);
    else if (f.type === "repeat") value = f.empty ? [] : [dataFrom(f.fields)];
    else if (f.type === "known") value = { known: false, value: null };
    else if (f.type === "lines") value = [];
    else if (f.type === "scope") value = scopeId;
    else if (f.type === "multi") value = (typeof f.options === "function" ? f.options(c) : f.options).map(o => Array.isArray(o) ? o[0] : o);
    else if (value === undefined) {
      if (f.optional) value = null;
      else if (f.type === "select" || f.type === "ref") { const first = (typeof f.options === "function" ? f.options(c) : f.options)[0]; value = Array.isArray(first) ? first[0] : first; }
      else if (f.type === "integer" || f.type === "money") value = 1;
      else if (f.type === "bool") value = false;
      else if (f.type === "date") value = instant;
      else value = "Fixture record";
    }
    if (f.key === "source_customer_id") value = id(21);
    if (f.key === "target_customer_id") value = customerId;
    data[f.key] = value;
  }
  return data;
}
async function main() {
  assert.deepEqual(Object.keys(B.actions).sort(), Object.keys(shared.schemas).sort(), "Closed action catalog must match shared types exactly");
  const app = fs.readFileSync(path.join(__dirname, "app.js"), "utf8");
  const envelopes = [];
  for (const name of Object.keys(B.actions)) {
    assert.ok(app.includes('"' + name + '"'), name + " needs an explicit route action surface");
    assert.deepEqual(Array.from(B.actions[name].expected).sort(), shared.expected[name], name + " fixed revision keys");
    const fields = B.actionFields(name, c);
    let data = dataFrom(fields);
    if (name === "settlement.charge") Object.assign(data, { estimate_version: 2, line_ref: scopeId, permission_id: null });
    const form = B.form(fields, c, data), read = form.read();
    assert.equal(JSON.stringify(read), JSON.stringify(data), name + " actual form reader round-trip: " + JSON.stringify(read));
    const draft = form.safeSnapshot();
    for (const field of fields.filter(f => ["hidden","text","textarea","known","password","code","object","repeat"].includes(f.type))) assert.ok(!(field.key in draft), name + " safe draft excludes " + field.key);
    if (B.actions[name].normalize) data = B.actions[name].normalize(data);
    if (name === "diagnostic.permission") data.attribution = null;
    if (name === "job.update_follow_up") Object.assign(data, { owner_staff_id: staffId, next_action: "Fixture follow-up", next_action_due_at: instant });
    envelopes.push(await B.buildCommand(name, c, data));
  }
  function variant(name, data, ctx = c) { return B.buildCommand(name, ctx, data).then(e => { envelopes.push(e); return e; }); }
  const start = await variant("work.start", { task_id: taskId, scope_ref: scopeId, consume: [] });
  assert.equal(start.expected["stock_rev:" + partId], 9);
  assert.equal(start.expected["stock_rev:" + secondPart], 11);
  assert.ok(!("stock_rev" in start.expected));
  const consume = await variant("work.consume", { task_id: taskId, items: [{ reservation_id: reservationId, qty: 1 }, { reservation_id: secondReservation, qty: 2 }, { reservation_id: reservationId, qty: 1 }] });
  assert.equal(Object.keys(consume.expected).filter(k => k.startsWith("stock_rev:")).length, 2, "Distinct parts, not distinct rows");
  const emptyContext = { ...c, job: { ...c.job, parts: [] } };
  const noParts = await variant("work.start", { task_id: taskId, scope_ref: scopeId, consume: [] }, emptyContext);
  assert.equal(Object.keys(noParts.expected).length, 3);
  const diagnosticContext = { ...emptyContext, job: { ...emptyContext.job, work: [{ ...c.job.work[0], scope_kind: "diagnostic" }] } };
  assert.equal((await variant("work.start", { task_id: taskId, scope_ref: scopeId, consume: [] }, diagnosticContext)).expected.estimate_version, null);
  await variant("parts.receive", { order_id: id(15), part_id: partId, qty: 1, source_note: "Fixture physical receipt" });
  await assert.rejects(B.buildCommand("work.consume", c, { task_id: taskId, items: [{ reservation_id: id(99), qty: 1 }] }), /not uniquely present/);
  await assert.rejects(B.buildCommand("qc.profile_update", { ...c, profile: { profile_id: id(11), version: 8 } }, { profile_id: id(11), checks: [{ label: "Fixture", required: true }], reason: "record_correction" }), /profile_rev/);
  const reassigned = await variant("work.task_reassign", { task_id: taskId, assigned_staff_id: staffId, reason: "task_reassignment" });
  assert.equal(reassigned.expected.task_rev, 6);
  const resolved = await variant("job.resolve_dispute", { disposition_id: id(31), resolution_summary: "Fixture resolution", owner_staff_id: staffId, next_action: "Review explicit settlement separately", next_action_due_at: instant, reason: "dispute_resolution" });
  assert.equal(resolved.expected.disposition_rev, 3);
  await assert.rejects(B.buildCommand("work.task_reassign", { ...c, job: { ...c.job, work: [{ task_id: taskId }] } }, { task_id: taskId }), /task_rev/);
  await assert.rejects(B.buildCommand("job.resolve_dispute", { ...c, job: { ...c.job, dispositions: [] } }, { disposition_id: id(31) }), /disposition_rev/);
  const planned = await variant("work.task_create", { job_id: jobId, scope_ref: id(13), scope_kind: "diagnostic", assigned_staff_id: staffId, summary: "Fixture permitted inspection" });
  assert.equal(planned.expected.estimate_version, null);
  const chargeBase = { job_id: jobId, amount_minor: 100, currency: "USD", reason: "manual_record" };
  const oldCharge = await variant("settlement.charge", { ...chargeBase, source: "estimate_line", estimate_version: 1, line_ref: scopeId, permission_id: null });
  assert.equal(oldCharge.expected.estimate_version, 1, "Historical charges use the selected version, not the latest proposal");
  await variant("settlement.charge", { ...chargeBase, source: "estimate_tax", estimate_version: 2, line_ref: null, permission_id: null });
  const feeCharge = await variant("settlement.charge", { ...chargeBase, source: "diagnostic_fee", estimate_version: null, line_ref: null, permission_id: id(13) });
  assert.equal(feeCharge.expected.estimate_version, null);
  const partDraftData = { draft_id: null, lines: [{ kind: "part", description: "Fixture mapped part", quantity: 2, unit_amount_minor: 100, part_id: partId, scope_ref: scopeId }], currency: "USD", tax_minor: 0, tax_label: "Not configured", tax_source: "not_configured" };
  const mapped = await variant("estimate.draft_save", partDraftData);
  await variant("estimate.draft_save", { ...partDraftData, tax_minor: 25, tax_label: "Shop manual entry", tax_source: "manual_shop_entry" });
  const unknownLine = { line_id: id(41), kind: "part", description: "Retained historical part", quantity: 1, unit_amount_minor: 100, line_amount_minor: 100, part_id: null, scope_ref: scopeId };
  assert.equal(B.hasUnknownCatalogPart({ lines: [{ kind: "part" }] }), true, "Absent historical part identity is never treated as mapped");
  assert.equal(B.hasUnknownCatalogPart({ lines: [{ kind: "labor", part_id: null }] }), false, "Non-part lines remain unaffected");
  const unknownEstimate = { version: 2, total_minor: 100, currency: "USD", expires_at: instant, lines: [unknownLine] };
  const unknownContext = { ...c, job: { ...c.job, current_estimate: unknownEstimate, estimates: [unknownEstimate],
    estimate_drafts: [{ draft_id: id(42), updated_at: instant, lines: [unknownLine] }],
    work: [{ ...c.job.work[0], scope_kind: "estimate_line", scope_ref: scopeId }] } };
  assert.match(B.catalogMappingBlockReason("estimate.decision", unknownContext), /historical catalog identity/);
  assert.match(B.catalogMappingBlockReason("parts.demand", unknownContext), /unknown historical catalog identity/);
  const unknownTaskContext = { ...unknownContext, taskId };
  unknownTaskContext.job.work[0].scope_kind = "estimate_line";
  assert.match(B.catalogMappingBlockReason("work.start", unknownTaskContext), /unknown/);
  assert.match(B.catalogMappingBlockReason("work.consume", unknownTaskContext), /consumption/);
  assert.match(B.catalogMappingBlockReason("work.log", unknownTaskContext, { kind: "resume" }), /resume/);
  assert.match(B.catalogMappingBlockReason("work.log", unknownTaskContext, { kind: "complete" }), /completion/);
  assert.equal(B.catalogMappingBlockReason("work.log", unknownTaskContext, { kind: "pause" }), "", "Safe pause history remains available");
  await assert.rejects(B.buildCommand("estimate.decision", unknownContext, { decision: "approve" }), /historical catalog identity/);
  await assert.rejects(B.buildCommand("parts.reserve", unknownContext, { part_id: partId, scope_ref: scopeId }), /unknown historical catalog identity/);
  await assert.rejects(B.buildCommand("work.task_create", unknownContext, { scope_kind: "estimate_line", scope_ref: scopeId }), /unknown historical catalog identity/);
  await assert.rejects(B.buildCommand("estimate.issue", unknownContext, { draft_id: id(42) }), /historical draft/);
  const unavailable = B.actionButtons(["estimate.decision", "parts.reserve"], unknownContext);
  assert.equal(unavailable.children.length, 2);
  assert.equal(unavailable.children.every(button => button.disabled), true, "Catalog-dependent controls remain visible but unavailable");
  const mappedCurrent = { ...unknownEstimate, version: 3, lines: [{ ...unknownLine, kind: "labor", part_id: null }] };
  const olderUnknownContext = { ...unknownContext, job: { ...unknownContext.job, current_estimate: mappedCurrent, estimates: [unknownEstimate, mappedCurrent] } };
  assert.match(B.catalogMappingBlockReason("work.start", { ...olderUnknownContext, taskId }), /historical catalog identity/);
  assert.match(B.catalogMappingBlockReason("work.task_create", olderUnknownContext, { scope_kind: "estimate_line", scope_ref: scopeId }), /historical catalog identity/);
  assert.equal(B.catalogMappingBlockReason("work.start", { ...unknownContext, job: { ...unknownContext.job, work: [{ ...unknownContext.job.work[0], scope_kind: "diagnostic" }] }, taskId }), "");
  const badCases = [];
  function bad(base, update) { const value = JSON.parse(JSON.stringify(base)); update(value); badCases.push(value); }
  bad(mapped, v => { delete v.data.lines[0].part_id; });
  bad(mapped, v => { v.data.lines[0].part_id = null; });
  bad(mapped, v => { v.data.lines[0].scope_ref = null; });
  bad(mapped, v => { v.data.lines[0].kind = "labor"; });
  bad(mapped, v => { v.data.tax_minor = 1; });
  bad(oldCharge, v => { v.data.permission_id = id(13); });
  bad(oldCharge, v => { v.data.line_ref = null; });
  bad(feeCharge, v => { v.data.estimate_version = 2; });
  bad(feeCharge, v => { v.expected.estimate_version = 2; });
  const diagnosticEnvelope = envelopes.find(e => e.action === "diagnostic.permission");
  bad(diagnosticEnvelope, v => { delete v.data.fee_source; });
  bad(diagnosticEnvelope, v => { v.data.fee_source = "manual"; });
  const scopeField = B.actions["estimate.draft_save"].fields.find(f => f.key === "lines").fields.find(f => f.key === "scope_ref");
  const scopeForm = B.form([scopeField], c), fresh = scopeForm.read().scope_ref;
  assert.match(fresh, /^[0-9a-f-]{36}$/);
  assert.equal(scopeForm.read().scope_ref, fresh, "A fresh scope stays stable through review");
  assert.notEqual(B.form([scopeField], c).read().scope_ref, fresh, "Additional work gets a distinct scope");
  assert.equal(B.form([scopeField], c, { scope_ref: scopeId }).read().scope_ref, scopeId, "Unchanged current scope can be retained explicitly");
  // Owner-safe projection, never staff detail, supplies customer revisions/decision ID.
  const owner = { ...c, job: { job_id: jobId, job_rev: 12, gate_rev: 5, status: {}, diagnostic_permission: c.job.diagnostic.current, current_estimate: c.job.estimates[0] } };
  B.session.account = { id: id(30), state: "active", kind: "customer", roles: [] };
  for (const name of ["diagnostic.permission", "estimate.decision", "estimate.revoke"]) {
    const data = dataFrom(B.actionFields(name, owner));
    if (name === "diagnostic.permission") { data.scope = owner.job.diagnostic_permission.scope; data.fee = owner.job.diagnostic_permission.fee; }
    const envelope = await variant(name, data, owner);
    assert.equal(envelope.target.id, jobId);
    assert.equal(envelope.expected.job_rev, 12);
    assert.equal(data.method, "customer_account");
    assert.equal(data.attribution, null);
    if (name === "diagnostic.permission") assert.equal(data.expected_decision_id, id(13));
  }
  assert.equal(B.can("job.create"), false);
  assert.equal(B.can("estimate.decision", owner), true);
  B.session.account = { id: staffId, state: "active", kind: "staff", roles: ["manager"] };
  assert.equal(B.can("work.start", c), false, "Manager is not a technician override");
  assert.equal(B.can("job.record_disposition", c), false);
  assert.equal(B.can("work.task_reassign", c), true);
  B.session.demo_mode = false;
  assert.equal(B.can("settlement.payment_record", c), true);
  assert.equal(B.can("settlement.payment_simulate", c), false);
  assert.equal(B.can("property.handover", c), true);
  assert.equal(B.can("property.handover_simulate", c), false);
  B.session.demo_mode = true;
  assert.equal(B.can("settlement.payment_record", c), false);
  assert.equal(B.can("settlement.refund_record", c), false);
  assert.equal(B.can("settlement.payment_simulate", c), true);
  assert.equal(B.can("property.authorize_collector", c), false);
  assert.equal(B.can("property.handover", c), false);
  assert.equal(B.can("property.handover_simulate", c), true);
  B.session.demo_mode = false;
  B.session.account.roles = ["technician"];
  assert.equal(B.can("work.start", c), true);
  assert.equal(B.can("job.record_disposition", c), true);
  assert.equal(B.can("job.open_dispute", c), false);
  assert.equal(B.can("job.resolve_dispute", c), false);
  assert.equal(B.can("work.task_reassign", c), false);
  assert.equal(B.can("work.start", { job: { ...c.job, assignment: { assigned_staff_ids: [] } } }), false);
  assert.equal(B.can("work.start", { ...c, taskId: id(99) }), false);
  B.session.account.roles = ["intake"];
  assert.equal(B.can("account.change_roles"), false);
  assert.equal(B.can("job.open_dispute", c), true);
  assert.equal(B.can("job.resolve_dispute", c), false);
  assert.equal(B.can("account.issue_recovery", { account: c.account }), true);
  for (const name of ["parts.create","return.assess","settlement.credit","settlement.correct"]) assert.equal(B.can(name, c), false, name + " is manager-only");
  assert.equal(B.can("account.issue_recovery", { account: { id: id(10), kind: "staff" } }), false);
  B.session.account.state = "suspended";
  assert.equal(B.can("job.create"), false);
  const staleChoice = B.form([B.field("task_id", "Task", "ref", { options: [[taskId, "Current task"]] })], c, { task_id: id(99) }).read();
  assert.equal(staleChoice.task_id, "", "A restored draft cannot create a stale reference option");
  const validated = py(
    "import json,sys\nfrom contracts.actions import parse_command\nitems=json.load(sys.stdin)\nfor item in items:\n try: parse_command(item)\n except Exception as e: raise ValueError(item['action']+': '+str(e)) from e\nprint(f'{len(items)} frontend-built envelopes accepted by real shared parse_command')",
    JSON.stringify(envelopes)
  );
  console.log(Object.keys(B.actions).length + " exact actions / route literals / fixed revision sets checked");
  console.log(Object.keys(B.actions).length + " actual form-reader round-trips and safe-draft field exclusions (unit DOM, not a browser)");
  console.log(validated);
  console.log(py(
    "import json,sys\nfrom contracts.actions import parse_command\nitems=json.load(sys.stdin)\nfor item in items:\n try: parse_command(item)\n except Exception: pass\n else: raise AssertionError('Invalid provenance accepted: '+item['action'])\nprint(f'{len(items)} explicit part mapping / tax / charge / fee-source negatives rejected by shared parser')",
    JSON.stringify(badCases)
  ));
  console.log(py(
    "import json,sys,copy\nfrom contracts.actions import parse_command,EXPECTED_KEYS\nitems=json.load(sys.stdin)\ncount=0\nfor original in items:\n for kind in ('data','revision','target'):\n  item=copy.deepcopy(original)\n  if kind=='data': item['data']['unexpected_field']=True\n  elif kind=='revision':\n   if EXPECTED_KEYS[item['action']]: del item['expected'][next(iter(EXPECTED_KEYS[item['action']]))]\n   else: item['expected']['unexpected_revision']=1\n  elif 'target' in item: del item['target']\n  else: item['target']={'type':'job','id':'00000000-0000-4000-8000-000000000002'}\n  try: parse_command(item)\n  except Exception: count+=1\n  else: raise ValueError('Invalid '+kind+' accepted for '+item['action'])\nprint(f'{count} unknown-data / missing-or-extra-revision / missing-or-extra-target negatives rejected by shared parser')",
    JSON.stringify(envelopes)
  ));
  console.log("Stock vectors, zero-part diagnostic start, customer revisions/permission IDs, missing-revision failure and role/assignment negatives checked");
  console.log("Historical catalog-unknown entries remain explicit; decision, issue, matching repair task, demand, reservation, start, resume, completion and consumption controls fail closed; safe pause/notes and diagnostic work remain available");
  await transportChecks();
  taskPurposeChecks();
  await renderedDefectChecks();
  console.log("PASS — source checks only; browser and real-API integration NOT exercised");
}
function taskPurposeChecks() {
  const job = { estimates: [
    { version: 1, lines: [{ scope_ref: scopeId, description: "Replace and test display cable" }] },
    { version: 2, lines: [{ scope_ref: id(90), description: "Different replacement scope" }] }
  ], diagnostic: { current: { permission_id: id(13), scope: "Inspect battery and test safety" } } };
  const task = { task_id: taskId, scope_kind: "estimate_line", scope_ref: scopeId, state: "not_started", logs: [] };
  const purpose = "Replace and test display cable · estimate v1";
  const before = JSON.stringify(job);
  for (const state of ["not_started", "active", "paused", "completed", "rework"]) {
    for (const logs of [[], [{ kind: "start", summary: "Scoped work started" }],
      [{ kind: "note", summary: "Latest activity, not the planned purpose" }, { kind: "start", summary: "Scoped work started" }],
      [{ kind: "complete", summary: "Correction completed" }]]) {
      const current = { ...task, state, logs };
      const snapshot = JSON.stringify(current);
      assert.equal(B.taskPurpose(current, job), purpose, "Repair purpose must remain primary before/after start, work logs and rework");
      assert.equal(JSON.stringify(current), snapshot, "Purpose derivation must not change state or drop logs");
      assert.ok(!B.taskPurpose(current, job).includes(taskId));
      if (state === "rework") assert.equal(B.taskPurpose(current, job) + " · " + B.human(state), purpose + " · Rework");
    }
  }
  for (const state of ["not_started", "active", "rework"]) {
    const diagnostic = { ...task, state, scope_kind: "diagnostic", scope_ref: id(13), logs: [{ kind: "start", summary: "Scoped work started" }] };
    assert.equal(B.taskPurpose(diagnostic, job), "Diagnostic inspection · Inspect battery and test safety");
  }
  assert.equal(B.taskPurpose({ ...task, scope_ref: id(99), logs: [{ kind: "note", summary: "Unrelated activity" }] }, job), "Repair within recorded scope");
  assert.equal(B.taskPurpose({ ...task, scope_kind: "diagnostic", scope_ref: id(99) }, job), "Diagnostic inspection", "Do not mislabel an old permission with current diagnostic scope");
  assert.equal(JSON.stringify(job), before);
  console.log("Task-purpose regression: matching immutable scope stays primary before/after start, note, pause, completion and rework; diagnostic/rework distinctions, no UUID primary, no log/state mutation");
}
async function renderedDefectChecks() {
  const settle = async () => { await new Promise(resolve => setImmediate(resolve)); while (domEvents.length) domEvents.shift()(); await new Promise(resolve => setImmediate(resolve)); };
  const dialog = context.document.getElementById("action-dialog");
  const invoker = domRoot.appendChild(B.button("Start authorized task", () => B.openAction("work.start", start)));
  const start = { ...c, job: { ...c.job, job_code: "BF-UNIT", device: { label: "Unit device" }, parts: [],
    diagnostic: { current: { ...c.job.diagnostic.current, scope: "Inspect battery and test safety" } },
    work: [{ ...c.job.work[0], scope_kind: "diagnostic", scope_ref: id(13), state: "not_started", logs: [{ kind: "note", summary: "Inspect battery and test safety" }] }] } };
  B.session = { authenticated: true, account: { id: staffId, display_name: "Ren", kind: "staff", state: "active", roles: ["technician"] } };
  B.prepareContext = async (name, base) => ({ ...base, options: c.options, defaults: {} });
  invoker.focus(); invoker.dispatchEvent({ type: "click", bubbles: true }); await settle();
  const choice = dialog.querySelectorAll("button").find(n => n.textContent.includes("Inspect battery"));
  assert.ok(choice, "Actual global picker must offer a human-labeled assigned task");
  choice.focus(); choice.dispatchEvent({ type: "click", bubbles: true }); await settle();
  assert.ok(dialog.open && dialog.querySelector("h2") && dialog.querySelector(".dialog-close"), "Task choice listener must not erase replacement dialog");
  const form = dialog.querySelector("form");
  assert.ok(form, "Task choice must reach the real structured form, not a substitute");
  assert.equal(domEvents.length, 0, "Same-dialog transition must not queue a native close");
  let submitted;
  B.command = async envelope => {
    submitted = envelope;
    return { status: "applied", applied_at: instant, command_id: envelope.command_id, event_id: id(70), result: { id: taskId }, aggregate: { type: "job", id: jobId, rev: 13 }, job: { summary: { blockers: [{ label: "QC incomplete" }] } } };
  };
  form.dispatchEvent({ type: "submit", bubbles: true }); await settle();
  assert.ok(dialog.textContent.includes("Review · nothing recorded yet"), "Submit listener remains attached after task-picker replacement");
  const confirm = dialog.querySelectorAll("button").find(n => n.textContent.includes("Start authorized task · confirm"));
  assert.ok(confirm);
  confirm.dispatchEvent({ type: "click", bubbles: true }); await settle();
  assert.equal(submitted, undefined, "Explicit checkbox must still gate the write");
  dialog.querySelector('input[type="checkbox"]').checked = true;
  confirm.dispatchEvent({ type: "click", bubbles: true }); await settle();
  assert.equal(submitted.action, "work.start"); assert.equal(submitted.data.task_id, taskId);
  assert.ok(dialog.textContent.includes("Recorded by the server"));
  dialog.dataset.pending = "true";
  assert.equal(dialog.dispatchEvent({ type: "cancel" }), false, "Pending Escape remains refused");
  delete dialog.dataset.pending;
  dialog.close(); await settle(); assert.equal(activeElement, invoker, "Final close restores original page invoker, not removed picker");
  B.dialog("Old", B.text("p", "Old")); dialog.close();
  B.dialog("New", B.text("p", "Replacement")); await settle();
  assert.ok(dialog.textContent.includes("Replacement"), "Queued old close must not clear newly reopened content");
  dialog.close(); await settle();

  // Other recursive replacement listeners share the same lifecycle.
  B.session.account.roles = ["manager", "intake"];
  await B.openAction("settlement.charge", { ...c, job: { ...start.job, estimates: [{ version: 2, total_minor: 1000, currency: "USD", decision: { value: "approved" } }] } });
  dialog.querySelectorAll("button").find(n => n.textContent === "Estimate line").dispatchEvent({ type: "click", bubbles: true }); await settle();
  assert.ok(dialog.textContent.includes("Choose the immutable version"), "Charge source transition retains next choice");
  dialog.close(); await settle();
  const linked = { ...start, options: { ...c.options, cases: [] }, job: { ...start.job, return_cases: [{ return_job_id: id(88), reported_recurrence: "Battery recurrence" }] } };
  B.prepareContext = async (name, base) => base;
  await B.openAction("return.assess", linked);
  assert.equal(dialog.querySelector("form"), null, "Original job cannot offer a wrong-target assessment form");
  assert.equal(dialog.querySelector("a").getAttribute("href"), "#/jobs/" + id(88) + "/returns");
  dialog.close(); await settle();

  const change = { currency: "USD", change_from_previous: [
    { field: "lines", before: JSON.stringify([{ description: "Battery replacement", quantity: 1, unit_amount_minor: 11000, scope_ref: scopeId, part_id: null, internal_cost_minor: 700 }]), after: JSON.stringify([{ description: "Battery replacement and safety retest", quantity: 1, unit_amount_minor: 12500, scope_ref: id(89), part_id: partId }]) },
    { field: "tax_minor", before: "0", after: "500" }, { field: "unknown_field", before: '{"private":"not for display"}', after: "null" }
  ] };
  const comparison = B.estimateChanges(change).textContent;
  for (const text of ["Battery replacement", "safety retest", "$110.00", "$125.00", "$5.00", "Previous", "This version", "Removed or replaced scope", "New or replacement scope"]) assert.ok(comparison.includes(text), text);
  for (const text of [scopeId, partId, "null", "unit_amount_minor", "internal_cost", "not for display", "{"]) assert.ok(!comparison.includes(text), "Commercial comparison excludes " + text);
  assert.ok(B.estimateChanges({ currency: "USD", change_from_previous: [{ field: "lines", before: "{broken", after: "[]" }] }).textContent.includes("unavailable"));
  B.session.account = { id: id(30), kind: "customer", roles: [], state: "active" };
  for (const decision of ["approved", "declined", "revoked"]) {
    B.dialog("Decision receipt", B.text("p", "Pending"));
    B.showReceipt({ status: "applied", command_id: id(71), event_id: id(72), applied_at: instant, aggregate: { type: "job", id: jobId, rev: 13 }, result: { id: id(73) },
      job: { current_estimate: { version: 3, total_minor: 12500, currency: "USD", decision },
        decision_history: [{ decision_id: id(73), estimate_version: 3, decision, method: "customer_account", recorded_by: "you" }],
        status: { blockers: [{ label: "Work paused" }], explanation: "The shop must resolve the pause before continuing." } } }, decision === "revoked" ? "estimate.revoke" : "estimate.decision", {}, dialog);
    for (const text of ["Estimate v3 " + decision, "$125.00", "Work paused", "Customer Account", "You"]) assert.ok(dialog.textContent.includes(text), text);
    assert.ok(!dialog.textContent.includes("Aggregate")); assert.ok(!dialog.textContent.includes(jobId));
    dialog.close(); await settle();
  }

  // Exercise the application's actual route/refresh listener, not a copied
  // renderer. Transport fixtures remain unit-only; real API smoke is separate.
  B.session = { authenticated: true, account: { id: staffId, display_name: "Ren", kind: "staff", state: "active", roles: ["technician"] } };
  const job = { ...start.job, assignment: { ...c.job.assignment, owner: { label: "Ren" } }, customer: { display_name: "Tao" }, lifecycle: "open",
    summary: { phase: "repair", blockers: [], repair_gate: "open", diagnostic_permission: "granted", balance: { amount_minor: 0, currency: "USD" } },
    follow_ups: [], qc: [], qc_history: [], estimate_drafts: [], return_cases: [] };
  let readMode = "ok", release;
  const requests = [];
  B.request = async url => {
    requests.push(url);
    if (url.startsWith("/staff/jobs/")) {
      if (readMode === "pending") return new Promise(resolve => { release = () => resolve(job); });
      if (readMode === "error") throw new Error("Unit offline");
      if (readMode === "forbidden") { const error = new Error("Access changed"); error.status = 403; throw error; }
      return job;
    }
    if (url.startsWith("/staff/jobs?")) return { items: [{ job_id: jobId, next_action: "Review findings and exact estimate", next_action_due_at: "2026-10-24T12:00:00Z", owner: { label: "Ren" } }], next_cursor: null };
    if (url.startsWith("/staff/assignable-staff?")) return { items: [{ staff_id: staffId, display_name: "Ren", roles: ["technician"] }] };
    throw new Error("Unit refuses unexpected URL " + url);
  };
  const location = { hash: "#/jobs/" + jobId + "/work" };
  Object.assign(context, { location, history: { replaceState() {} }, URLSearchParams, setTimeout, clearTimeout });
  Object.assign(context.window, { addEventListener() {}, scrollY: 15, scrollTo() {} });
  vm.runInContext(fs.readFileSync(path.join(__dirname, "app.js"), "utf8"), context); await settle();
  const mainNode = context.document.getElementById("main");
  assert.ok(mainNode.textContent.includes("Review findings and exact estimate"), mainNode.textContent);
  assert.ok(mainNode.textContent.includes("Oct 24"), "Future action must not disappear as not-due");
  assert.ok(mainNode.textContent.includes("Inspect battery and test safety"));
  assert.ok(mainNode.textContent.includes("Ren"));
  const refresh = context.document.getElementById("refresh-docket");
  await B.openAction("work.start", start); await B.refresh();
  const notice = context.document.getElementById("notice");
  assert.ok(notice.textContent.includes("Draft still open"));
  dialog.close(); await settle();
  assert.equal(notice.textContent, "", "Closing the canceled draft clears only its stale refresh notice");
  refresh.focus(); readMode = "pending"; refresh.dispatchEvent({ type: "click", bubbles: true }); await settle();
  assert.ok(mainNode.textContent.includes("previous records are read-only"));
  assert.ok(mainNode.textContent.includes("Review findings and exact estimate"));
  assert.equal(B.readOnlyDocket, true);
  assert.ok(mainNode.lastChild.inert, "Retained docket is inert, not stale actionable data");
  await B.openAction("work.start", start); assert.equal(dialog.open, false);
  release(); await settle();
  assert.equal(B.readOnlyDocket, false); assert.equal(activeElement.id, "refresh-docket");
  readMode = "error"; await B.refresh(); await settle();
  assert.ok(mainNode.textContent.includes("NOT current")); assert.ok(mainNode.textContent.includes("Review findings and exact estimate"));
  assert.equal(B.readOnlyDocket, true); assert.equal(mainNode.lastChild.inert, true); assert.equal(activeElement.getAttribute("role"), "alert");
  readMode = "ok"; mainNode.querySelectorAll("button").find(n => n.textContent === "Retry refresh").dispatchEvent({ type: "click", bubbles: true }); await settle();
  assert.equal(B.readOnlyDocket, false);
  B.session.account.roles = ["manager"];
  job.return_cases = [{ return_case_id: id(90), return_rev: 1, original_job_id: jobId, original_job_rev: 12, return_job_id: id(88), return_job_rev: 1,
    reported_recurrence: "Battery recurrence", owner: { label: "Ren" }, next_action: "Obtain fresh inspection permission", next_action_due_at: instant }];
  location.hash = "#/jobs/" + jobId + "/returns"; await B.refresh(); await settle();
  assert.ok(!mainNode.querySelectorAll("button").some(n => n.textContent === "Record return coverage assessment"), "Actual original docket exposes linked return navigation, not assessment");
  assert.ok(mainNode.querySelectorAll("a").some(n => n.getAttribute("href") === "#/jobs/" + id(88) + "/returns"), "Actual original docket names linked NEW return path");
  readMode = "forbidden"; await B.refresh(); await settle();
  assert.ok(!mainNode.textContent.includes("Review findings and exact estimate"), "Access refusal clears prior records rather than retaining them");
  console.log("RD-01–07 unit DOM: actual picker click → replacement form submit → checkbox/confirmation receipt; queued close/reopen, source transition, return targeting, commercial comparison, decision/blocker receipts, future next action and refresh pending/error/focus/access refusal checked (NOT Chrome acceptance)");
}
async function transportChecks() {
  let sent, expired = 0, reply = { ok: true, status: 200, json: async () => ({ status: "applied" }) };
  const api = { uuid, expired: () => expired++ };
  class TestAbortController { constructor() { this.signal = {}; } abort() { this.signal.aborted = true; } }
  const sandbox = vm.createContext({ window: { BF: api }, AbortController: TestAbortController, setTimeout, clearTimeout, fetch: async (url, init) => { sent = { url, init }; if (reply instanceof Error) throw reply; return reply; } });
  vm.runInContext(fs.readFileSync(path.join(__dirname, "api.js"), "utf8"), sandbox);
  api.session = { csrf_token: "test-only-token" };
  const envelope = { command_id: id(77), action: "fixture" };
  await api.command(envelope);
  assert.equal(sent.url, "/api/v1/commands");
  assert.equal(sent.init.headers["X-CSRF-Token"], "test-only-token");
  assert.match(sent.init.headers["X-Request-ID"], /^[0-9a-f-]{36}$/);
  assert.equal(sent.init.credentials, "same-origin");
  assert.equal(sent.init.cache, "no-store");
  assert.equal(sent.init.body, JSON.stringify(envelope));
  reply = { ok: false, status: 409, json: async () => ({ error: { code: "STALE_REVISION", message: "Fixture stale", request_id: id(70), retryable: false, field_errors: [{ field: "data.qty", code: "invalid", message: "Fixture field error" }] } }) };
  await assert.rejects(api.command(envelope), e => e.code === "STALE_REVISION" && e.requestId === id(70) && e.fields[0].field === "data.qty" && !e.retryable);
  reply = { ok: false, status: 401, json: async () => ({ error: { code: "AUTH_REQUIRED", message: "Fixture expired", field_errors: [] } }) };
  await assert.rejects(api.command(envelope));
  assert.equal(expired, 1); assert.equal(api.session, null);
  reply = new Error("Fixture network failure");
  await assert.rejects(api.command(envelope), e => e.unknown === true);
  await assert.rejects(api.request("/me/jobs"), e => e.unknown === false);
  const summary = { backup_id: id(80), created_at: instant, schema_version: 8, sha256: "a".repeat(64), size_bytes: 4096, integrity: "verified_at_creation" };
  assert.equal(api.backupSummary(summary).backup_id, id(80));
  const manifest = { ...summary, integrity: "ok", format_version: 1, database_file: "benchflow.sqlite3" };
  assert.equal(api.backupDetail({ manifest, integrity: "verified_now" }).backup_id, id(80));
  assert.equal(api.backupReceipt({ command_id: id(80), status: "applied", backup: summary }, id(80)).backup_id, id(80));
  assert.equal(api.backupReceipt({ command_id: id(80), status: "replayed", backup: summary }, id(80)).backup_id, id(80));
  assert.throws(() => api.backupDetail(summary), /manifest/);
  assert.throws(() => api.backupDetail({ manifest, integrity: "verified_at_creation" }), /manifest/);
  assert.throws(() => api.backupDetail({ manifest: { ...manifest, database_file: "database.sqlite3" }, integrity: "verified_now" }), /manifest/);
  assert.throws(() => api.backupReceipt(summary, id(80)), /not confirmed/);
  assert.throws(() => api.backupReceipt({ command_id: id(81), status: "applied", backup: summary }, id(80)), /not confirmed/);
  assert.throws(() => api.backupReceipt({ command_id: id(80), status: "applied", backup: { ...summary, backup_id: id(81) } }, id(80)), /not confirmed/);
  const binary = { isolatedFixture: true };
  reply = { ok: true, status: 200, headers: { get: () => "application/octet-stream" }, blob: async () => binary };
  assert.equal(await api.request("/staff/backups/" + id(80) + "/download", undefined, true), binary);
  reply = { ok: true, status: 200, headers: { get: () => "application/json" } };
  await assert.rejects(api.request("/staff/backups/" + id(80) + "/download", undefined, true), /verified database/);
  reply = { ok: false, status: 503, json: async () => ({ error: { code: "BACKUP_UNAVAILABLE", message: "Fixture unavailable", retryable: true } }) };
  await assert.rejects(api.request("/staff/backups", { command_id: id(80) }), e => e.code === "BACKUP_UNAVAILABLE" && e.retryable);
  reply = { ok: false, status: 409, json: async () => ({ error: { code: "BACKUP_INVALID", message: "Fixture corrupt" } }) };
  await assert.rejects(api.request("/staff/backups/" + id(80) + "/download", undefined, true), e => e.code === "BACKUP_INVALID");
  console.log("Transport unit checks: same-origin/CSRF/request-ID, exact error envelope, expiry and unknown write outcome");
  console.log("Typed backup summary/detail/create+replay positives; legacy-flat/stale-verification/wrong-file/wrong-operation and unavailable/corrupt-download negatives");
}
if (process.argv.includes("--task-purposes")) {
  // Actual API projection -> production purpose helper; no invented fields.
  const job = JSON.parse(fs.readFileSync(0, "utf8"));
  const before = JSON.stringify(job);
  const purposes = job.work.map(task => ({ task_id: task.task_id, purpose: B.taskPurpose(task, job) }));
  assert.equal(JSON.stringify(job), before);
  process.stdout.write(JSON.stringify(purposes));
} else if (process.argv.includes("--present")) {
  // Render an actual API receipt in the unit DOM, without transport substitutes.
  const result = JSON.parse(fs.readFileSync(0, "utf8"));
  B.session.account = { kind: "customer", roles: [] };
  const dialog = B.dialog("Decision receipt", B.text("p", "Pending"));
  B.showReceipt(result, "estimate.decision", {}, dialog);
  process.stdout.write(dialog.textContent);
} else if (process.argv.includes("--build")) {
  // Real in-process API checks pass only actual authorized query projections.
  // Never fall back to this file's unit-test reference responses.
  B.request = async () => { throw new Error("Projection-only runtime bridge cannot synthesize a reference query"); };
  const input = JSON.parse(fs.readFileSync(0, "utf8"));
  B.buildCommand(input.name, input.context, input.data).then(envelope => process.stdout.write(JSON.stringify(envelope))).catch(error => { console.error(error.message); process.exitCode = 1; });
} else if (process.argv.includes("--integration")) {
  try { console.log(py(String.raw`
import json,secrets,subprocess,tempfile,uuid
from pathlib import Path
from fastapi.testclient import TestClient
from config.settings import Settings,CANONICAL_ORIGIN
from backend.app import create_app
from backend.testing import seed_approval_start_scenario
from contracts.projections import CustomerJobDetail,JobStaffDetail,AssignableStaffDirectory,CustomerContactChangePage

def check(response,status=200):
 assert response.status_code==status, f"{response.request.url.path}: expected {status}, got {response.status_code}"
 return None if status==204 else response.json()
def headers(csrf):
 return {'Origin':CANONICAL_ORIGIN,'Content-Type':'application/json','Accept':'application/json','X-CSRF-Token':csrf,'X-Request-ID':str(uuid.uuid4())}
def login(client,login,password):
 token=check(client.get('/api/v1/session'))['csrf_token']
 return check(client.post('/api/v1/auth/login',json={'login_id':login,'password':password},headers=headers(token)))['csrf_token']
def build(name,job,data):
 result=subprocess.run(['node','frontend/verify.js','--build'],input=json.dumps({'name':name,'context':{'job':job} if job else {},'data':data}),text=True,capture_output=True,check=True)
 return json.loads(result.stdout)
def post(client,csrf,envelope,status=200):
 return check(client.post('/api/v1/commands',json=envelope,headers=headers(csrf)),status)
def purpose(job,task_id):
 result=subprocess.run(['node','frontend/verify.js','--task-purposes'],input=json.dumps(job),encoding='utf-8',capture_output=True,check=True)
 return next(item['purpose'] for item in json.loads(result.stdout) if item['task_id']==task_id)

with tempfile.TemporaryDirectory(prefix='benchflow-frontend-check-') as folder:
 settings=Settings(data_root=Path(folder)/'data',local_app_data_root=Path(folder)/'local')
 fixture=seed_approval_start_scenario(settings)
 app=create_app(settings)
 with TestClient(app,base_url=CANONICAL_ORIGIN,client=("127.0.0.1",43123)) as owner:
  tech=TestClient(app,base_url=CANONICAL_ORIGIN,client=("127.0.0.1",43124))
  try:
   check(owner.get('/api/v1/health'))
   for asset in ('/','/styles.css','/ui.js','/api.js','/forms.js','/actions.js','/app.js','/demo.js'):
    response=owner.get(asset)
    assert response.status_code==200, 'Static asset unavailable: '+asset
    assert response.headers['cache-control']=='no-store'
   assert owner.get('/demo.html').status_code==404
   customer_csrf=login(owner,fixture.customer_login_id,fixture.customer_password)
   tech_csrf=login(tech,fixture.technician_login_id,fixture.technician_password)
   own=check(owner.get('/api/v1/me/jobs/'+fixture.job_id)); CustomerJobDetail.model_validate(own)
   old=check(tech.get('/api/v1/staff/jobs/'+fixture.job_id)); JobStaffDetail.model_validate(old)
   assert all(key in old for key in ('qc_history','dispositions'))
   assert all('task_rev' in task for task in old['work'])
   print('OBSERVED real API: isolated schema startup; static assets/no-store; normal-mode demo refusal; owner/technician login; current owner/staff projection validation including qc_history/dispositions/task_rev.',flush=True)
   AssignableStaffDirectory.model_validate(check(tech.get('/api/v1/staff/assignable-staff',params={'job_id':fixture.job_id})))
   queue=check(tech.get('/api/v1/staff/jobs',params={'query':old['job_code']}))
   queue_job=next(item for item in queue['items'] if item['job_id']==fixture.job_id)
   assert queue_job['next_action'] and queue_job['next_action_due_at'] and queue_job['owner']['label']
   assert own['diagnostic_permission']['permission_id']
   e=own['current_estimate']
   approval=build('estimate.decision',own,dict(decision='approve',version=e['version'],method='customer_account',attribution=None,confirmation={k:e[k] for k in ('version','total_minor','currency','expires_at')}))
   receipt=post(owner,customer_csrf,approval)
   assert receipt['status']=='applied'
   rendered=subprocess.run(['node','frontend/verify.js','--present'],input=json.dumps(receipt),text=True,capture_output=True,check=True).stdout
   assert 'Estimate v'+str(e['version'])+' approved' in rendered
   assert e['currency']=='USD' and ('$'+format(e['total_minor']/100,',.2f')) in rendered
   assert 'Remaining blockers' in rendered and 'Customer Account' in rendered
   assert all(blocker['label'] in rendered for blocker in receipt['job']['status']['blockers'])
   assert 'Aggregate' not in rendered
   print('OBSERVED real API + unit DOM: exact job-code queue search preserves next action/owner/due; actual customer approval response renders version/approved/method and every remaining blocker.',flush=True)
   assert post(owner,customer_csrf,approval)['status']=='replayed'
   stale=build('work.start',old,dict(task_id=fixture.task_id,scope_ref=fixture.scope_ref,consume=[]))
   assert post(tech,tech_csrf,stale,409)['error']['code']=='STALE_REVISION'
   current=check(tech.get('/api/v1/staff/jobs/'+fixture.job_id))
   repair_purpose=purpose(current,fixture.task_id)
   scope_line=next(line for version in current['estimates'] for line in version['lines'] if line['scope_ref']==fixture.scope_ref)
   assert repair_purpose.startswith(scope_line['description']+' \u00b7 estimate v')
   assert post(tech,tech_csrf,build('work.start',current,dict(task_id=fixture.task_id,scope_ref=fixture.scope_ref,consume=[])))['status']=='applied'
   current=check(tech.get('/api/v1/staff/jobs/'+fixture.job_id))
   assert any(log['summary']=='Scoped work started' for task in current['work'] if task['task_id']==fixture.task_id for log in task['logs'])
   assert purpose(current,fixture.task_id)==repair_purpose
   post(tech,tech_csrf,build('work.log',current,dict(task_id=fixture.task_id,kind='complete',summary='Isolated frontend integration check',owner_staff_id=None,next_action=None,next_action_due_at=None)))
   print('OBSERVED real API: authorized directory; JS-built exact-version approval and replay; stale start 409; fresh zero-part start and completion applied.',flush=True)
   current=check(tech.get('/api/v1/staff/jobs/'+fixture.job_id)); checks=[q for q in current['qc'] if q['task_id']==fixture.task_id]
   assert purpose(current,fixture.task_id)==repair_purpose
   assert checks, 'BACKEND_FIXTURE_DEPENDENCY: seed_approval_start_scenario supplied no configured QC checks for its task; QC/retest and later smoke steps not exercised. No substitute checks or profiles synthesized.'
   failed=post(tech,tech_csrf,build('qc.record',current,dict(task_id=fixture.task_id,profile_version=checks[0]['profile_version'],checks=[dict(check_id=q['check_id'],result='fail' if index==0 else 'pass',note='Isolated observed failure') for index,q in enumerate(checks)],overall='fail',reason='manual_record')))
   current=check(tech.get('/api/v1/staff/jobs/'+fixture.job_id))
   assert any(w['state']=='rework' for w in current['work'])
   assert any(run['run_id']==failed['result']['id'] and run['overall']=='fail' for run in current['qc_history'])
   rework=[w for w in current['work'] if w['task_id']!=fixture.task_id and w['scope_ref']==fixture.scope_ref]
   assert len(rework)==1, 'Failed QC must preserve one linked rework task.'
   checks=[q for q in current['qc'] if q['task_id']==fixture.task_id]
   retest=[dict(check_id=q['check_id'],result='pass',note='Fictional retest after completed rework') for q in checks]
   early=post(tech,tech_csrf,build('qc.record',current,dict(task_id=fixture.task_id,profile_version=checks[0]['profile_version'],checks=retest,overall='pass',reason=None)),409)
   assert early['error']['code']=='QC_INCOMPLETE'
   current=check(tech.get('/api/v1/staff/jobs/'+fixture.job_id))
   child=rework[0]
   assert child['state']=='rework' and purpose(current,child['task_id'])==repair_purpose
   assert post(tech,tech_csrf,build('work.start',current,dict(task_id=child['task_id'],scope_ref=child['scope_ref'],consume=[])))['status']=='applied'
   current=check(tech.get('/api/v1/staff/jobs/'+fixture.job_id))
   assert purpose(current,child['task_id'])==repair_purpose
   assert post(tech,tech_csrf,build('work.log',current,dict(task_id=child['task_id'],kind='complete',summary='Fictional correction for failed functional check',owner_staff_id=None,next_action=None,next_action_due_at=None)))['status']=='applied'
   current=check(tech.get('/api/v1/staff/jobs/'+fixture.job_id))
   assert purpose(current,child['task_id'])==repair_purpose
   print('OBSERVED real API + production helper: repair purpose stable before/after start, completion, linked rework creation/start/completion; generic start log retained.',flush=True)
   retest=post(tech,tech_csrf,build('qc.record',current,dict(task_id=fixture.task_id,profile_version=checks[0]['profile_version'],checks=retest,overall='pass',reason=None)))
   assert retest['job']['summary']['qc_state']=='passed'
   assert any(run['run_id']==failed['result']['id'] and run['overall']=='fail' for run in retest['job']['qc_history'])
   assert any(run['run_id']==retest['result']['id'] and run['overall']=='pass' for run in retest['job']['qc_history'])
   print('OBSERVED real API: failed QC retained; early retest refused; linked rework completed; original task retested and passed; immutable failed/pass QC runs both remain.',flush=True)
   unknown={'known':False,'value':None}
   contact=build('contact.change_request',None,dict(requested_fields=['phone'],display_name=unknown,email=unknown,phone={'known':True,'value':'555-0100'}))
   post(owner,customer_csrf,contact)
   requests=check(owner.get('/api/v1/customer/contact-change-requests')); CustomerContactChangePage.model_validate(requests)
   assert len(requests['items'])==1 and requests['items'][0]['state']=='pending'
   foreign=str(uuid.uuid4())
   assert check(owner.get('/api/v1/me/jobs/'+foreign),404)['error']['code']=='NOT_FOUND'
   check(owner.post('/api/v1/auth/logout',json={},headers=headers(customer_csrf)),204)
   assert check(owner.get('/api/v1/me/jobs'),401)['error']['code']=='AUTH_REQUIRED'
  finally:
   tech.close()
print('PASS actual in-process FastAPI: 8 static assets + demo-page refusal outside demo mode; customer/technician login; owner/staff projections; minimal directory; JS-built approval+idempotent replay; stale 409; zero-part start; completion; failed QC+retained history+early retest refusal+linked rework completion+successful original-task retest; contact request+typed status; missing-job 404; logout+401')
print('Isolated temporary SQLite root removed. No network listener or browser. Manager recovery, return creation, money writes, backup, multipart inventory, restore and demo credential retrieval not exercised by this fixture.')
`)); } catch (error) { console.error(error.message); process.exitCode = 1; }
} else main().catch(error => { console.error(error.message); process.exitCode = 1; });
