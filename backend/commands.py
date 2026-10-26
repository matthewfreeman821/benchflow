"""Closed typed command dispatch, ownership, revisions and durable idempotency.

The caller owns BEGIN IMMEDIATE and revalidates the cookie session in that
transaction. No command retries itself against a refreshed revision.
"""
from __future__ import annotations

from dataclasses import dataclass
import json

from contracts.actions import ACTION_NAMES, ParsedCommand
from contracts.projections import CommandSuccess
from backend import customers, repairs, settlement, stockwork, views
from backend.core import encode, event, insert, instant, one, require, revision, uid, visible_job
from backend.credentials import fingerprint


CUSTOMER_ACTIONS = frozenset({
    "diagnostic.permission", "estimate.decision", "estimate.revoke", "return.create", "contact.change_request",
})
MANAGER_ONLY = frozenset({
    "customer.merge", "account.invite_staff", "account.change_roles", "account.suspend", "account.reactivate",
    "account.close", "account.unlink_customer", "job.assign", "job.reopen", "qc.profile_create", "qc.profile_update",
    "parts.adjust", "parts.create", "settlement.correct", "policy.update_demo", "return.assess",
    "account.reissue_invite", "account.revoke_invite", "settlement.credit",
    "job.resolve_dispute", "work.task_reassign",
})
TECH_ACTIONS = frozenset({
    "diagnostic.record", "work.start", "work.log", "work.consume", "qc.record",
    "job.record_disposition",
})
SHARED_OPERATIONAL = frozenset({"estimate.draft_save", "parts.demand", "parts.reserve", "parts.release",
                               "job.update_follow_up", "work.task_create"})
DEMO_ONLY_ACTIONS = frozenset({"settlement.payment_simulate", "property.handover_simulate"})
REAL_SETTLEMENT_ACTIONS = frozenset({"settlement.payment_record", "settlement.refund_record",
                                     "property.authorize_collector", "property.handover"})
INTAKE_ACTIONS = ACTION_NAMES - MANAGER_ONLY - TECH_ACTIONS - {"contact.change_request"}


def authorize(actor, action):
    if actor["kind"] == "customer":
        require(action in CUSTOMER_ACTIONS, "FORBIDDEN")
    elif action in MANAGER_ONLY:
        require("manager" in actor["roles"], "FORBIDDEN")
    elif action in TECH_ACTIONS:
        require("technician" in actor["roles"], "FORBIDDEN")
    elif action in SHARED_OPERATIONAL:
        require(bool({"manager", "technician", "intake"} & set(actor["roles"])), "FORBIDDEN")
        if action == "work.task_create":
            require(bool({"manager", "technician"} & set(actor["roles"])), "FORBIDDEN")
    else:
        require(action != "contact.change_request" and bool({"manager", "intake"} & set(actor["roles"])), "FORBIDDEN")


@dataclass
class Context:
    db: object
    parsed: ParsedCommand
    actor: dict
    now: str
    target: str | None
    job: dict | None = None
    linked_job: str | None = None

    @property
    def action(self):
        return self.parsed.envelope.action

    @property
    def data(self):
        return self.parsed.typed_data.model_dump(mode="json")

    @property
    def expected(self):
        return self.parsed.envelope.expected

    def result(self, aggregate_type, aggregate_id, result_type, result_id, *, code=None):
        return dict(aggregate_type=aggregate_type, aggregate_id=aggregate_id,
                    result_type=result_type, result_id=result_id, code=code)


def membership(ctx):
    """Resolve all submitted children before stale checks; no ID selects the job."""
    db, d, job_id = ctx.db, ctx.data, ctx.target
    if d.get("job_id") is not None:
        require(d["job_id"] == job_id, "NOT_FOUND")
    if d.get("original_job_id") is not None:
        require(d["original_job_id"] == job_id, "NOT_FOUND")
    for key, table in (
        ("task_id", "work_tasks"), ("draft_id", "estimate_drafts"),
        ("reservation_id", "reservations"), ("order_id", "supplier_orders"),
        ("entry_id", "settlement_entries"), ("payment_id", "settlement_entries"),
        ("charge_id", "settlement_entries"), ("inspection_permission_id", "diagnostic_permissions"),
        ("expected_decision_id", "diagnostic_permissions"),
        ("permission_id", "diagnostic_permissions"), ("disposition_id", "job_dispositions"),
    ):
        if d.get(key):
            one(db, f"SELECT 1 FROM {table} WHERE id=? AND job_id=?", (d[key], job_id))
    for item in d.get("consume", []) + d.get("items", []):
        one(db, "SELECT 1 FROM reservations WHERE id=? AND job_id=?", (item["reservation_id"], job_id))
    if d.get("return_case_id"):
        one(db, "SELECT 1 FROM return_cases WHERE id=? AND return_job_id=?", (d["return_case_id"], job_id))
    if d.get("line_ref"):
        one(db, "SELECT 1 FROM estimate_lines l JOIN estimates e ON e.id=l.estimate_id WHERE l.id=? AND e.job_id=?",
            (d["line_ref"], job_id))
    if d.get("part_id"):
        one(db, "SELECT 1 FROM parts WHERE id=?", (d["part_id"],))
    if d.get("scope_ref"):
        require(one(db, "SELECT 1 FROM diagnostic_permissions WHERE id=? AND job_id=?", (d["scope_ref"], job_id), required=False)
                or one(db, "SELECT 1 FROM estimate_lines l JOIN estimates e ON e.id=l.estimate_id WHERE e.job_id=? AND l.scope_ref=?",
                       (job_id, d["scope_ref"]), required=False), "NOT_FOUND")
    if ctx.action == "qc.record":
        task = one(db, "SELECT profile_version_id FROM work_tasks WHERE id=? AND job_id=?", (d["task_id"], job_id))
        for check in d["checks"]:
            one(db, "SELECT 1 FROM qc_profile_checks WHERE id=? AND profile_version_id=?", (check["check_id"], task["profile_version_id"]))
    technical = ctx.action in TECH_ACTIONS or (
        ctx.action in SHARED_OPERATIONAL and not ({"manager", "intake"} & set(ctx.actor["roles"])))
    if technical:
        require(one(db, "SELECT 1 FROM job_assignments WHERE job_id=? AND staff_id=?",
                    (job_id, ctx.actor["id"]), required=False), "NOT_FOUND")
    if ctx.action == "job.update_follow_up" and technical:
        require(d["owner_staff_id"] == ctx.actor["id"], "FORBIDDEN")


def check_revisions(ctx):
    db, d, expected = ctx.db, ctx.data, ctx.expected
    if ctx.job:
        for key in ("job_rev", "gate_rev"):
            if key in expected:
                revision(expected, key, ctx.job[key])
        if "estimate_version" in expected:
            e = repairs.current_estimate(db, ctx.target)
            diagnostic = False
            if ctx.action in {"work.start", "work.consume", "work.task_create"}:
                if ctx.action == "work.task_create":
                    diagnostic = d["scope_kind"] == "diagnostic"
                else:
                    task = one(db, "SELECT scope_kind FROM work_tasks WHERE id=? AND job_id=?", (d["task_id"], ctx.target))
                    diagnostic = task["scope_kind"] == "diagnostic"
            if ctx.action == "settlement.charge":
                diagnostic = d["source"] == "diagnostic_fee"
            if ctx.action == "parts.reserve":
                diagnostic = repairs.planning_scope(db, ctx.target, d["scope_ref"]) == "diagnostic"
            revision(expected, "estimate_version", None if diagnostic else e["version"] if e else None)
    elif ctx.action == "job.create":
        c = one(db, "SELECT customer_rev FROM customers WHERE id=? AND merged_into_id IS NULL", (ctx.target,))
        revision(expected, "customer_rev", c["customer_rev"])
    if "stock_rev" in expected:
        part_id = d.get("part_id")
        if ctx.action == "parts.release":
            part_id = one(db, "SELECT part_id FROM reservations WHERE id=? AND job_id=?", (d["reservation_id"], ctx.target))["part_id"]
        p = one(db, "SELECT stock_rev FROM parts WHERE id=?", (part_id,))
        revision(expected, "stock_rev", p["stock_rev"])
    for key, table, id_key in (("task_rev", "work_tasks", "task_id"),
                               ("disposition_rev", "job_dispositions", "disposition_id")):
        if key in expected:
            column = key
            child = one(db, f"SELECT {column} FROM {table} WHERE id=?", (d[id_key],))
            revision(expected, key, child[column])


def policy(ctx):
    require(ctx.data["shop_timezone"] == "America/New_York" and 1 <= ctx.data["estimate_window_days"] <= 30, "POLICY_CONFLICT")
    require(ctx.settings_demo, "FORBIDDEN")
    row = one(ctx.db, "SELECT * FROM shop_policy")
    revision(ctx.expected, "policy_rev", row["policy_rev"])
    d = ctx.data
    ctx.db.execute("UPDATE shop_policy SET policy_rev=policy_rev+1,estimate_window_days=?,diagnostic_fee_minor=?,tax_enabled=? WHERE id=?",
                   (d["estimate_window_days"], d["diagnostic_fee_minor"], int(d["tax_enabled"]), row["id"]))
    return ctx.result("policy", row["id"], "policy", row["id"])


HANDLERS = {
    **{a: customers.handle for a in ACTION_NAMES if a.startswith(("customer.", "account.", "contact."))},
    **{a: repairs.handle for a in ACTION_NAMES if a.startswith(("job.", "diagnostic.", "estimate."))},
    **{a: stockwork.handle for a in ACTION_NAMES if a.startswith(("parts.", "work.", "qc."))},
    **{a: settlement.handle for a in ACTION_NAMES if a.startswith(("settlement.", "property.", "return."))},
    "policy.update_demo": policy,
}
assert set(HANDLERS) == ACTION_NAMES


def execute(db, parsed, actor, now, *, demo_mode=False):
    require(db.in_transaction)
    envelope = parsed.envelope
    authorize(actor, envelope.action)
    if envelope.action in DEMO_ONLY_ACTIONS:
        require(demo_mode, "FORBIDDEN")
    if demo_mode and envelope.action in REAL_SETTLEMENT_ACTIONS:
        require(False, "FORBIDDEN")
    ctx = Context(db, parsed, actor, now, envelope.target.id if envelope.target else None)
    ctx.settings_demo = demo_mode
    if envelope.target and envelope.target.type == "job":
        ctx.job = visible_job(db, actor, ctx.target)
        membership(ctx)
    request_hash = fingerprint(encode(envelope.model_dump(mode="json")))
    stored = one(db, "SELECT * FROM idempotency_results WHERE command_id=?", (envelope.command_id,), required=False)
    require(not one(db, "SELECT 1 FROM backup_operations WHERE command_id=?", (envelope.command_id,), required=False),
            "IDEMPOTENCY_KEY_REUSED")
    if stored:
        require(stored["actor_id"] == actor["id"] and stored["request_hash"] == request_hash, "IDEMPOTENCY_KEY_REUSED")
        # Reauthorize current ownership/permission, not historic authority.
        if stored["aggregate_type"] == "job":
            visible_job(db, actor, stored["aggregate_id"])
        if stored["aggregate_type"] == "contact_change_request" and actor["kind"] == "customer":
            one(db, "SELECT 1 FROM contact_change_requests WHERE id=? AND customer_id=? AND account_id=?",
                (stored["aggregate_id"], actor["customer_id"], actor["id"]))
        response = json.loads(stored["response_json"])
        response["status"] = "replayed"
        return CommandSuccess.model_validate(response).model_dump(mode="json")
    check_revisions(ctx)
    for key in ("occurred_at", "received_at", "refunded_at"):
        if ctx.data.get(key):
            require(instant(ctx.data[key]) <= instant(now))
    if ctx.job and ctx.job["lifecycle"] == "closed":
        require(envelope.action in {"return.create", "settlement.payment_record", "settlement.refund_record",
                                   "settlement.credit", "settlement.correct"}, "POLICY_CONFLICT")
    result = HANDLERS[envelope.action](ctx)
    if ctx.job:
        db.execute("UPDATE jobs SET job_rev=job_rev+1,gate_rev=gate_rev+1,updated_at=? WHERE id=?", (now, ctx.target))
    agg_type, agg_id = result["aggregate_type"], result["aggregate_id"]
    table, key = {
        "job": ("jobs", "job_rev"), "customer": ("customers", "customer_rev"), "account": ("accounts", "account_rev"),
        "part": ("parts", "stock_rev"), "qc_profile": ("qc_profiles", "profile_rev"),
        "return_case": ("return_cases", "return_rev"), "contact_change_request": ("contact_change_requests", "request_rev"),
        "policy": ("shop_policy", "policy_rev"),
    }[agg_type]
    rev = one(db, f"SELECT {key} FROM {table} WHERE id=?", (agg_id,))[key]
    reason = ctx.data.get("reason_code") or ctx.data.get("reason")
    eid = event(db, actor, "shop" if agg_type == "policy" else agg_type, agg_id, envelope.action, now,
                command_id=envelope.command_id, reason=reason,
                verification=next((ctx.data[k] for k in ("verification_method", "confirmation_method")
                                   if ctx.data.get(k) in {"in_person", "phone"}), None),
                refs=[(result["result_type"], result["result_id"])] if result["result_id"] else [])
    job_id = ctx.linked_job or (ctx.target if ctx.job or envelope.action == "job.create" else None)
    response = CommandSuccess(
        command_id=envelope.command_id, status="applied", applied_at=now, event_id=eid,
        aggregate=dict(type=agg_type, id=agg_id, rev=rev),
        result=dict(type=result["result_type"], id=result["result_id"], linked_job_id=ctx.linked_job,
                    linked_job_rev=1 if ctx.linked_job else None, one_time_code=result["code"],
                    code_retrieval="available" if result["code"] else "not_applicable"),
        job=views.job_detail(db, actor, job_id, now) if job_id else None,
    ).model_dump(mode="json")
    replay = json.loads(encode(response))
    if result["code"]:
        replay["result"]["one_time_code"] = None
        replay["result"]["code_retrieval"] = "not_available"
    insert(db, "idempotency_results", command_id=envelope.command_id, actor_kind=actor["kind"], actor_id=actor["id"],
           request_hash=request_hash, response_json=encode(replay), original_http_status=200,
           aggregate_type=agg_type, aggregate_id=agg_id, created_at=now, event_id=eid)
    return response
