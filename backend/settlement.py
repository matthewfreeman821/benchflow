"""Append-only manual financial records, custody and distinct actionable return jobs."""
from __future__ import annotations

from datetime import timedelta
from zoneinfo import ZoneInfo

from backend.core import insert, instant, one, require, rows, stamp, uid
from backend.repairs import create_job, current_estimate, decision, permission


def effective(db, entry_id):
    entry = one(db, "SELECT amount_minor FROM settlement_entries WHERE id=?", (entry_id,))
    return entry["amount_minor"] + one(db, "SELECT coalesce(sum(delta_minor),0) n FROM settlement_corrections WHERE entry_id=?", (entry_id,))["n"]


def balance(db, job_id):
    recorded = sum((1 if e["bucket"] in {"charge", "refund"} else -1) * effective(db, e["id"])
                   for e in rows(db, "SELECT id,bucket FROM settlement_entries WHERE job_id=?", (job_id,)))
    simulated = sum(e["amount_minor"] for e in rows(db, "SELECT amount_minor FROM simulated_payments WHERE job_id=?", (job_id,)))
    return recorded - simulated


def validate_money(db, job_id):
    for e in rows(db, "SELECT * FROM settlement_entries WHERE job_id=?", (job_id,)):
        require(0 <= effective(db, e["id"]) <= 10_000_000_000)
        if e["bucket"] == "payment":
            refunded = sum(effective(db, r["id"]) for r in rows(db, "SELECT id FROM settlement_entries WHERE payment_id=?", (e["id"],)))
            require(refunded <= effective(db, e["id"]), "REFUND_LIMIT")
        if e["bucket"] == "charge" and e["line_id"]:
            line = one(db, "SELECT line_amount_minor FROM estimate_lines WHERE id=?", (e["line_id"],))
            total = sum(effective(db, r["id"]) for r in rows(db, "SELECT id FROM settlement_entries WHERE line_id=? AND bucket='charge'", (e["line_id"],)))
            require(total <= line["line_amount_minor"], "SCOPE_NOT_APPROVED")
        if e["bucket"] == "charge" and e["source"] in {"diagnostic_fee", "estimate_tax"}:
            diagnostic = e["source"] == "diagnostic_fee"
            column = "permission_id" if diagnostic else "estimate_id"
            ref = e[column]
            cap = one(db, "SELECT fee_minor n FROM diagnostic_permissions WHERE id=?" if diagnostic
                      else "SELECT tax_minor n FROM estimates WHERE id=?", (ref,))["n"]
            total = sum(effective(db, r["id"]) for r in rows(db,
                f"SELECT id FROM settlement_entries WHERE {column}=? AND source=?", (ref, e["source"])))
            require(total <= cap, "SCOPE_NOT_APPROVED")
        if e["bucket"] == "charge":
            credits = sum(effective(db, r["id"]) for r in rows(db, "SELECT id FROM settlement_entries WHERE charge_id=?", (e["id"],)))
            require(credits <= effective(db, e["id"]))


def quality(db, job_id):
    tasks = rows(db, "SELECT * FROM work_tasks WHERE job_id=? AND scope_kind='estimate_line'", (job_id,))
    estimate = current_estimate(db, job_id)
    current_scopes = {r["scope_ref"] for r in rows(db, "SELECT scope_ref FROM estimate_lines WHERE estimate_id=?",
                                                (estimate["id"],))} if estimate else set()
    tasks = [t for t in tasks if t["state"] == "completed" or t["scope_ref"] in current_scopes]
    if not tasks:
        return "not_required"
    if any(t["state"] != "completed" for t in tasks):
        return "in_progress"
    states = []
    for task in tasks:
        run = one(db, "SELECT overall FROM qc_runs WHERE task_id=? ORDER BY rowid DESC LIMIT 1", (task["id"],), required=False)
        # Retests are recorded on the original task; linked rework inherits its current QC status.
        if task["rework_of_task_id"]:
            run = one(db, "SELECT overall FROM qc_runs WHERE task_id=? ORDER BY rowid DESC LIMIT 1",
                      (task["rework_of_task_id"],), required=False)
        states.append(run["overall"] if run else "not_tested")
    return "failed" if "fail" in states else "not_tested" if "not_tested" in states else "passed"


def repaired_ready(db, job):
    if job["lifecycle"] != "open" or job["repair_hold"] or job["safe_stop_required"]:
        return False
    estimate = current_estimate(db, job["id"])
    approved = decision(db, estimate["id"]) if estimate else None
    if not (approved and approved["decision"] == "approved" and quality(db, job["id"]) == "passed"):
        return False
    for line in rows(db, "SELECT * FROM estimate_lines WHERE estimate_id=? AND kind IN ('labor','part')", (estimate["id"],)):
        completed = one(db, "SELECT 1 FROM work_tasks WHERE job_id=? AND scope_ref=? AND state='completed'",
                        (job["id"], line["scope_ref"]), required=False)
        if not completed:
            return False
    return True


def next_weekday(now):
    local = instant(now).astimezone(ZoneInfo("America/New_York")) + timedelta(days=1)
    while local.weekday() >= 5:
        local += timedelta(days=1)
    return stamp(local.replace(hour=17, minute=0, second=0, microsecond=0))


def handle(ctx):
    db, a, d, now = ctx.db, ctx.action, ctx.data, ctx.now
    if a == "settlement.payment_simulate":
        require(ctx.settings_demo, "FORBIDDEN")
        owed = balance(db, ctx.target)
        require(d["amount_minor"] > 0, "INVALID_REQUEST")
        require(owed > 0 and d["amount_minor"] <= owed, "SCOPE_NOT_APPROVED")
        id_ = insert(db, "simulated_payments", id=uid(), job_id=ctx.target, amount_minor=d["amount_minor"],
                     currency=d["currency"], recorded_at=now, actor_staff_id=ctx.actor["id"],
                     reason_code="manual_record")
        return ctx.result("job", ctx.target, "simulated_payment", id_)
    if a.startswith("settlement."):
        if a in {"settlement.payment_record", "settlement.refund_record"}:
            require(not ctx.settings_demo, "FORBIDDEN")
        require(d.get("currency", "USD") == "USD")
        if a == "settlement.correct":
            e = one(db, "SELECT * FROM settlement_entries WHERE id=? AND job_id=?", (d["entry_id"], ctx.target))
            require(d["delta_minor"] != 0)
            if e["bucket"] in {"payment", "refund"}:
                require(bool(d["external_reference"]))
            id_ = insert(db, "settlement_corrections", id=uid(), entry_id=e["id"], job_id=ctx.target, bucket=e["bucket"],
                         delta_minor=d["delta_minor"], currency="USD", external_reference=d["external_reference"],
                         reason_code=d["reason"], posted_at=now, actor_staff_id=ctx.actor["id"])
            validate_money(db, ctx.target)
            return ctx.result("job", ctx.target, "compensation", id_)
        bucket = {"settlement.charge": "charge", "settlement.credit": "credit",
                  "settlement.payment_record": "payment", "settlement.refund_record": "refund"}[a]
        require(d["amount_minor"] > 0)
        extra = {}
        if bucket == "charge":
            if d["source"] == "diagnostic_fee":
                p = one(db, "SELECT * FROM diagnostic_permissions WHERE id=? AND job_id=?", (d["permission_id"], ctx.target))
                require(p["state"] == "granted" and p["fee_minor"] == d["amount_minor"] and p["currency"] == d["currency"],
                        "SCOPE_NOT_APPROVED")
                extra = dict(source="diagnostic_fee", permission_id=p["id"], authorized_at=p["decided_at"])
            else:
                estimate = one(db, "SELECT * FROM estimates WHERE job_id=? AND version=?", (ctx.target, d["estimate_version"]))
                approved = decision(db, estimate["id"])
                require(approved and approved["decision"] == "approved", "SCOPE_NOT_APPROVED")
                extra = dict(source=d["source"], estimate_id=estimate["id"], authorized_at=approved["decided_at"])
                if d["source"] == "estimate_tax":
                    require(d["amount_minor"] == estimate["tax_minor"] and estimate["tax_source"] == "manual_shop_entry",
                            "SCOPE_NOT_APPROVED")
                    extra["tax_label"] = estimate["tax_label"]
                else:
                    line = one(db, "SELECT * FROM estimate_lines WHERE estimate_id=? AND id=?", (estimate["id"], d["line_ref"]))
                    require(line["kind"] != "credit", "SCOPE_NOT_APPROVED")
                    work = one(db, "SELECT 1 FROM work_tasks WHERE job_id=? AND scope_ref=? AND state='completed' "
                               "AND started_under_estimate_id=?", (ctx.target, line["scope_ref"], estimate["id"]), required=False)
                    require(work is not None or line["kind"] == "fee", "SCOPE_NOT_APPROVED")
                    extra["line_id"] = line["id"]
        elif bucket == "credit":
            if d["charge_id"]:
                one(db, "SELECT 1 FROM settlement_entries WHERE id=? AND job_id=? AND bucket='charge'", (d["charge_id"], ctx.target))
            extra = dict(charge_id=d["charge_id"], credit_kind=d["kind"])
        elif bucket == "payment":
            extra = dict(method=d["method"], external_reference=d["reference"])
        elif bucket == "refund":
            one(db, "SELECT 1 FROM settlement_entries WHERE id=? AND job_id=? AND bucket='payment'", (d["payment_id"], ctx.target))
            extra = dict(payment_id=d["payment_id"], external_reference=d["reference"])
        id_ = insert(db, "settlement_entries", id=uid(), job_id=ctx.target, bucket=bucket, amount_minor=d["amount_minor"],
                     currency="USD", occurred_at=d.get("received_at") or d.get("refunded_at") or now,
                     recorded_at=now, actor_staff_id=ctx.actor["id"], reason_code=d.get("reason"), **extra)
        validate_money(db, ctx.target)
        return ctx.result("job", ctx.target, bucket, id_)
    if a == "property.authorize_collector":
        require(not ctx.settings_demo, "FORBIDDEN")
        id_ = insert(db, "collector_authorizations", id=uid(), job_id=ctx.target, collector_name=d["collector_name"],
                     method=d["method"], verified_by_staff_id=ctx.actor["id"], verified_at=now, reason_code=d["reason"])
        return ctx.result("job", ctx.target, "collector_authorization", id_)
    if a == "property.handover":
        require(not ctx.settings_demo, "FORBIDDEN")
        require(ctx.job["lifecycle"] != "closed")
        require(not ctx.job["safe_stop_required"] and not one(db, "SELECT 1 FROM work_tasks WHERE job_id=? AND state='active'",
                                                           (ctx.target,), required=False), "REVISION_HOLD")
        if d["handover_kind"] == "completed_repair":
            require(repaired_ready(db, ctx.job), "QC_INCOMPLETE")
        else:
            require(ctx.job["disposition"] in {"cancelled", "no_fault_found", "unrepairable"})
        collector = one(db, "SELECT * FROM collector_authorizations WHERE job_id=? AND collector_name=? ORDER BY rowid DESC LIMIT 1",
                        (ctx.target, d["collector_name"]))
        owed = balance(db, ctx.target)
        if owed > 0:
            require("manager" in ctx.actor["roles"] and d["manager_balance_override_reason"], "FORBIDDEN")
        id_ = insert(db, "handovers", id=uid(), job_id=ctx.target, kind=d["handover_kind"], collector_name=d["collector_name"],
                     collector_authorization_id=collector["id"], acknowledgement=d["acknowledgement"],
                     recorded_at=now, actor_staff_id=ctx.actor["id"], balance_override_reason=d["manager_balance_override_reason"],
                     balance_minor_at_handover=owed)
        db.execute("UPDATE jobs SET lifecycle='closed' WHERE id=?", (ctx.target,))
        db.execute("UPDATE follow_ups SET state='resolved',resolved_at=? WHERE job_id=? AND state='due'", (now, ctx.target))
        db.execute("UPDATE return_cases SET state='closed',return_rev=return_rev+1 WHERE return_job_id=?", (ctx.target,))
        return ctx.result("job", ctx.target, "handover", id_)
    if a == "property.handover_simulate":
        require(ctx.settings_demo, "FORBIDDEN")
        require(ctx.job["lifecycle"] != "closed")
        require(not ctx.job["safe_stop_required"] and not one(
            db, "SELECT 1 FROM work_tasks WHERE job_id=? AND state='active'", (ctx.target,), required=False
        ), "REVISION_HOLD")
        require(repaired_ready(db, ctx.job), "QC_INCOMPLETE")
        owed = balance(db, ctx.target)
        require(owed == 0, "SCOPE_NOT_APPROVED")
        require(not one(db, "SELECT 1 FROM handovers WHERE job_id=?", (ctx.target,), required=False)
                and not one(db, "SELECT 1 FROM simulated_handovers WHERE job_id=?", (ctx.target,), required=False),
                "POLICY_CONFLICT")
        id_ = insert(db, "simulated_handovers", id=uid(), job_id=ctx.target, kind="completed_repair",
                     collector_name=d["collector_name"],
                     acknowledgement="Fictional demo only; no property was physically transferred.",
                     recorded_at=now, actor_staff_id=ctx.actor["id"], balance_minor_at_handover=owed)
        db.execute("UPDATE jobs SET lifecycle='closed' WHERE id=?", (ctx.target,))
        db.execute("UPDATE follow_ups SET state='resolved',resolved_at=? WHERE job_id=? AND state='due'", (now, ctx.target))
        db.execute("UPDATE return_cases SET state='closed',return_rev=return_rev+1 WHERE return_job_id=?", (ctx.target,))
        return ctx.result("job", ctx.target, "simulated_handover", id_)
    if a == "return.create":
        require(d["original_job_id"] == ctx.target, "NOT_FOUND")
        require(ctx.job["lifecycle"] == "closed")
        customer = one(db, "SELECT * FROM customers WHERE id=?", (ctx.job["customer_id"],))
        unknown = {"value": None, "known": False}
        data = dict(owner_staff_id=None, next_action="Review recurrence and contact customer",
                    next_action_due_at=next_weekday(now), reported_issue=d["reported_recurrence"],
                    condition=unknown, accessories={"known": False, "items": []}, testing_constraints=unknown,
                    received_by_customer_name=d["customer_attribution"] or customer["display_name"],
                    acknowledgement={"text_version": "recurrence_request", "method": "in_person"})
        new_id = create_job(db, ctx.actor, customer, data, now, queue=True, device_id=ctx.job["device_id"])
        id_ = insert(db, "return_cases", id=uid(), original_job_id=ctx.target, return_job_id=new_id,
                     reported_recurrence=d["reported_recurrence"], customer_attribution=d["customer_attribution"],
                     state="open", created_at=now, created_by_account_id=ctx.actor["id"])
        ctx.linked_job = new_id
        return ctx.result("return_case", id_, "return_case", id_)
    if a == "return.assess":
        case = one(db, "SELECT * FROM return_cases WHERE id=? AND return_job_id=?", (d["return_case_id"], ctx.target))
        p = permission(db, ctx.target)
        require(p and p["id"] == d["inspection_permission_id"] and p["state"] == "granted", "SCOPE_NOT_APPROVED")
        insert(db, "return_assessments", id=uid(), return_case_id=case["id"], inspection_permission_id=p["id"],
               coverage=d["coverage"], reason_code=d["reason"], actor_staff_id=ctx.actor["id"], recorded_at=now)
        db.execute("UPDATE return_cases SET return_rev=return_rev+1 WHERE id=?", (case["id"],))
        return ctx.result("return_case", case["id"], "return_case", case["id"])
    raise RuntimeError("SETTLEMENT_HANDLER_MISSING")
