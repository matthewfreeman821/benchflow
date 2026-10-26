"""Intake, explicit diagnostic permission, immutable proposals and exact decisions."""
from __future__ import annotations

import json

from pydantic import ValidationError

from contracts.common import EstimateLineInput
from backend.core import (
    Rejected, eligible_staff, encode, insert, instant, later, one, require, rows, uid,
)
from backend.credentials import fingerprint


def current_estimate(db, job_id):
    return one(db, "SELECT * FROM estimates WHERE job_id=? ORDER BY version DESC LIMIT 1",
               (job_id,), required=False)


def permission(db, job_id):
    return one(db, "SELECT * FROM diagnostic_permissions WHERE job_id=? ORDER BY rowid DESC LIMIT 1",
               (job_id,), required=False)


def decision(db, estimate_id):
    return one(db, "SELECT * FROM estimate_decisions WHERE estimate_id=? ORDER BY rowid DESC LIMIT 1",
               (estimate_id,), required=False)


HISTORICAL_CATALOG_BLOCK = (
    "Historical catalog identity is not recorded. A new mapped estimate and "
    "approval are required before catalog-dependent work."
)

QC_REWORK_FOLLOW_UP = "Complete linked rework and retest"
QC_COLLECTION_FOLLOW_UP = "Arrange collection for completed repair"


def require_recorded_catalog_identity(line):
    if line["kind"] == "part" and line["part_id"] is None:
        raise Rejected("STOCK_SHORTAGE", HISTORICAL_CATALOG_BLOCK)
    return line


def scope_allowed(db, job, scope_ref, kind):
    require(job["lifecycle"] == "open", "JOB_CANCELLED")
    if kind == "diagnostic":
        p = permission(db, job["id"])
        require(p is not None and p["id"] == scope_ref and p["state"] == "granted", "SCOPE_NOT_APPROVED")
        return p
    require(not job["repair_hold"], "REVISION_HOLD")
    estimate = current_estimate(db, job["id"])
    require(estimate is not None, "SCOPE_NOT_APPROVED")
    approved = decision(db, estimate["id"])
    require(approved is not None and approved["decision"] == "approved", "SCOPE_NOT_APPROVED")
    line = one(db, "SELECT * FROM estimate_lines WHERE estimate_id=? AND scope_ref=? AND kind!='credit'",
               (estimate["id"], scope_ref), required=False)
    require(line is not None, "SCOPE_NOT_APPROVED")
    require_recorded_catalog_identity(line)
    # Expiry limits acceptance, not already granted unchanged work.
    return estimate


def planning_scope(db, job_id, scope_ref):
    p = one(db, "SELECT * FROM diagnostic_permissions WHERE id=? AND job_id=?", (scope_ref, job_id), required=False)
    if p:
        return "diagnostic"
    e = current_estimate(db, job_id)
    require(e and one(db, "SELECT 1 FROM estimate_lines WHERE estimate_id=? AND scope_ref=?",
                      (e["id"], scope_ref), required=False), "NOT_FOUND")
    return "estimate_line"


def hold(ctx, reason):
    """Stop future repair starts, require safe stop, release only unused reservations."""
    db = ctx.db
    active = one(db, "SELECT 1 FROM work_tasks WHERE job_id=? AND state='active'",
                 (ctx.target,), required=False)
    db.execute("UPDATE jobs SET repair_hold=?,safe_stop_required=? WHERE id=?",
               (reason, int(active is not None), ctx.target))
    from backend.stockwork import release_reservation
    for r in rows(db, "SELECT * FROM reservations WHERE job_id=? AND qty>released_qty+consumed_qty", (ctx.target,)):
        release_reservation(ctx, r, r["qty"] - r["released_qty"] - r["consumed_qty"],
                            "scope_change" if reason == "revision" else "customer_request")


def follow_up(ctx, action):
    job = one(ctx.db, "SELECT * FROM jobs WHERE id=?", (ctx.target,))
    due = later(ctx.now, hours=24)
    insert(ctx.db, "follow_ups", id=uid(), job_id=ctx.target, state="due",
           owner_staff_id=job["owner_staff_id"], owner_queue=job["owner_queue"],
           next_action=action, due_at=due, created_at=ctx.now)
    ctx.db.execute("UPDATE jobs SET next_action=?,next_action_due_at=? WHERE id=?", (action, due, ctx.target))


def complete_rework_follow_up(ctx):
    db = ctx.db
    due = rows(db, "SELECT id FROM follow_ups WHERE job_id=? AND state='due' AND next_action=?",
               (ctx.target, QC_REWORK_FOLLOW_UP))
    if not due:
        return
    db.execute("UPDATE follow_ups SET state='resolved',resolved_at=? "
               "WHERE job_id=? AND state='due' AND next_action=?",
               (ctx.now, ctx.target, QC_REWORK_FOLLOW_UP))
    if ctx.job["next_action"] == QC_REWORK_FOLLOW_UP:
        follow_up(ctx, QC_COLLECTION_FOLLOW_UP)


def create_job(db, actor, customer, data, now, *, queue=False, device_id=None):
    if device_id is None:
        device_id = insert(db, "devices", id=uid(), customer_id=customer["id"],
                           **{f"{key}_{suffix}": value for key, item in data["device"].items()
                              for suffix, value in (("value", item["value"]), ("known", int(item["known"])))},
                           search_normalized=" ".join((item["value"] or "").casefold() for item in data["device"].values()))
    id_ = uid()
    insert(db, "jobs", id=id_, job_code="BF-" + id_.replace("-", "")[:10].upper(),
           customer_id=customer["id"], device_id=device_id, owner_staff_id=None if queue else data["owner_staff_id"],
           owner_queue="manager_intake" if queue else None,
           next_action=data["next_action"], next_action_due_at=data["next_action_due_at"],
           reported_issue=data["reported_issue"], condition_value=data["condition"]["value"],
           condition_known=int(data["condition"]["known"]), accessories_known=int(data["accessories"]["known"]),
           testing_constraints_value=data["testing_constraints"]["value"],
           testing_constraints_known=int(data["testing_constraints"]["known"]),
           received_by_customer_name=data["received_by_customer_name"],
           received_by_staff_id=actor["id"], acknowledgement_version=data["acknowledgement"]["text_version"],
           acknowledgement_method=data["acknowledgement"]["method"], acknowledgement_actor_id=actor["id"],
           opened_at=now, updated_at=now)
    for ordinal, accessory in enumerate(data["accessories"]["items"]):
        insert(db, "job_accessories", job_id=id_, ordinal=ordinal, label=accessory)
    if not queue:
        insert(db, "job_assignments", job_id=id_, staff_id=data["owner_staff_id"])
    return id_


def handle(ctx):
    db, a, d, actor, now = ctx.db, ctx.action, ctx.data, ctx.actor, ctx.now
    job = ctx.job
    if a == "job.create":
        eligible_staff(db, d["owner_staff_id"])
        customer = one(db, "SELECT * FROM customers WHERE id=? AND merged_into_id IS NULL", (ctx.target,))
        id_ = create_job(db, actor, customer, d, now)
        ctx.target = id_
        return ctx.result("job", id_, "job", id_)
    if a == "job.assign":
        for staff_id in d["staff_ids"]:
            eligible_staff(db, staff_id)
        db.execute("DELETE FROM job_assignments WHERE job_id=?", (ctx.target,))
        for staff_id in d["staff_ids"]:
            insert(db, "job_assignments", job_id=ctx.target, staff_id=staff_id)
        db.execute("UPDATE jobs SET owner_staff_id=?,owner_queue=NULL WHERE id=?", (d["owner_staff_id"], ctx.target))
    elif a in {"job.record_disposition", "job.open_dispute", "job.resolve_dispute"}:
        require(job["lifecycle"] == "open", "JOB_CANCELLED")
        eligible_staff(db, d["owner_staff_id"])
        if a == "job.resolve_dispute":
            item = one(db, "SELECT * FROM job_dispositions WHERE id=? AND job_id=?", (d["disposition_id"], ctx.target))
            require(item["kind"] == "dispute" and item["state"] == "open", "DISPOSITION_CONFLICT")
            id_ = item["id"]
            db.execute("UPDATE job_dispositions SET state='resolved',disposition_rev=disposition_rev+1,"
                       "resolved_at=?,resolved_by_staff_id=?,resolution_summary=?,owner_staff_id=?,"
                       "next_action=?,next_action_due_at=? WHERE id=?",
                       (now, actor["id"], d["resolution_summary"], d["owner_staff_id"],
                        d["next_action"], d["next_action_due_at"], id_))
        else:
            dispute = a == "job.open_dispute"
            id_ = insert(db, "job_dispositions", id=uid(), job_id=ctx.target,
                         kind="dispute" if dispute else d["disposition"], state="open" if dispute else "recorded",
                         summary=d["summary"] if dispute else d["finding"], actor_staff_id=actor["id"],
                         recorded_at=now, owner_staff_id=d["owner_staff_id"],
                         next_action=d["next_action"], next_action_due_at=d["next_action_due_at"])
            if not dispute:
                hold(ctx, "safety")
                db.execute("UPDATE jobs SET disposition=? WHERE id=?", (d["disposition"], ctx.target))
        db.execute("UPDATE jobs SET owner_staff_id=?,owner_queue=NULL,next_action=?,next_action_due_at=? WHERE id=?",
                   (d["owner_staff_id"], d["next_action"], d["next_action_due_at"], ctx.target))
        return ctx.result("job", ctx.target, "disposition", id_)
    elif a == "job.update_follow_up":
        if d["state"] == "due":
            require(d["next_action"] and d["next_action_due_at"])
            if d["owner_staff_id"]:
                eligible_staff(db, d["owner_staff_id"])
            else:
                require("manager" in actor["roles"], "FORBIDDEN")
            id_ = insert(db, "follow_ups", id=uid(), job_id=ctx.target, state="due",
                         owner_staff_id=d["owner_staff_id"], owner_queue="manager_intake" if not d["owner_staff_id"] else None,
                         next_action=d["next_action"],
                         due_at=d["next_action_due_at"], created_at=now)
            db.execute("UPDATE jobs SET owner_staff_id=?,owner_queue=?,next_action=?,next_action_due_at=? WHERE id=?",
                       (d["owner_staff_id"], "manager_intake" if not d["owner_staff_id"] else None,
                        d["next_action"], d["next_action_due_at"], ctx.target))
        else:
            db.execute("UPDATE follow_ups SET state='resolved',resolved_at=? WHERE job_id=? AND state='due'", (now, ctx.target))
    elif a == "job.record_contact_attempt":
        id_ = insert(db, "contact_attempts", id=uid(), job_id=ctx.target, channel=d["channel"], outcome=d["outcome"],
                     summary=d["summary"], occurred_at=d["occurred_at"] or now, recorded_at=now, actor_staff_id=actor["id"])
        # No contact outcome is consent, closure or a money event.
    elif a == "job.cancel":
        require(job["lifecycle"] == "open", "JOB_CANCELLED")
        hold(ctx, "cancelled")
        db.execute("UPDATE jobs SET lifecycle='cancelled',disposition='cancelled',next_action='Arrange safe unrepaired collection' WHERE id=?",
                   (ctx.target,))
        follow_up(ctx, "Arrange return/collection for cancelled repair")
    elif a == "job.reopen":
        require(job["lifecycle"] == "cancelled")
        db.execute("UPDATE jobs SET lifecycle='open',disposition=NULL,repair_hold='revoked',next_action=?,next_action_due_at=? WHERE id=?",
                   (d["next_action"], d["next_action_due_at"], ctx.target))
    elif a == "diagnostic.permission":
        old = permission(db, ctx.target)
        require(d["expected_decision_id"] == (old["id"] if old else None), "STALE_REVISION")
        if actor["kind"] == "customer":
            require(d["method"] == "customer_account" and d["decision"] != "request", "FORBIDDEN")
            require(old and old["scope"] == d["scope"] and old["fee_minor"] == d["fee"]["amount_minor"]
                    and old["currency"] == d["fee"]["currency"], "STALE_REVISION")
        else:
            require(d["method"] != "customer_account", "FORBIDDEN")
        require(d["fee"]["currency"] == "USD")
        policy = one(db, "SELECT * FROM shop_policy")
        fee_rev = policy["policy_rev"]
        if old and d["decision"] != "request":
            require(old["scope"] == d["scope"] and old["fee_minor"] == d["fee"]["amount_minor"]
                    and old["currency"] == d["fee"]["currency"], "STALE_REVISION")
            fee_rev = old["fee_policy_rev"]
        else:
            require(d["fee"]["amount_minor"] == policy["diagnostic_fee_minor"], "POLICY_CONFLICT")
        state = {"request": "pending", "grant": "granted", "decline": "declined", "revoke": "revoked"}[d["decision"]]
        if state == "revoked":
            require(old and old["state"] == "granted")
            hold(ctx, "revoked")
        id_ = insert(db, "diagnostic_permissions", id=uid(), job_id=ctx.target,
                     previous_permission_id=old["id"] if old else None, state=state, scope=d["scope"],
                     fee_minor=d["fee"]["amount_minor"], currency=d["fee"]["currency"], method=d["method"],
                     fee_policy_rev=fee_rev,
                     attribution=d["attribution"], actor_kind=actor["kind"], actor_id=actor["id"], decided_at=now)
        return ctx.result("job", ctx.target, "diagnostic_permission", id_)
    elif a == "diagnostic.record":
        p = permission(db, ctx.target)
        require(p and p["state"] == "granted", "SCOPE_NOT_APPROVED")
        if d["task_id"]:
            task = one(db, "SELECT * FROM work_tasks WHERE id=? AND job_id=?", (d["task_id"], ctx.target))
            require(task["scope_kind"] == "diagnostic" and task["scope_ref"] == p["id"], "SCOPE_NOT_APPROVED")
        id_ = insert(db, "diagnostic_records", id=uid(), job_id=ctx.target, permission_id=p["id"],
                     actor_staff_id=actor["id"], recorded_at=now, **d)
        return ctx.result("job", ctx.target, "diagnostic_record", id_)
    elif a == "estimate.draft_save":
        require(d["currency"] == "USD")
        refs = [line["scope_ref"] for line in d["lines"] if line["scope_ref"]]
        require(len(refs) == len(set(refs)))
        for line in d["lines"]:
            if line["part_id"]:
                one(db, "SELECT 1 FROM parts WHERE id=?", (line["part_id"],))
        if d["draft_id"]:
            one(db, "SELECT 1 FROM estimate_drafts WHERE id=? AND job_id=?", (d["draft_id"], ctx.target))
            db.execute("UPDATE estimate_drafts SET lines_json=?,currency=?,tax_minor=?,tax_label=?,updated_at=?,"
                       "updated_by_staff_id=?,tax_source=? WHERE id=?", (encode(d["lines"]), d["currency"], d["tax_minor"],
                                                           d["tax_label"], now, actor["id"], d["tax_source"], d["draft_id"]))
            id_ = d["draft_id"]
        else:
            id_ = insert(db, "estimate_drafts", id=uid(), job_id=ctx.target, currency=d["currency"],
                         tax_minor=d["tax_minor"], tax_label=d["tax_label"], lines_json=encode(d["lines"]),
                         tax_source=d["tax_source"],
                         updated_at=now, updated_by_staff_id=actor["id"])
        return ctx.result("job", ctx.target, "estimate_draft", id_)
    elif a == "estimate.issue":
        draft = one(db, "SELECT * FROM estimate_drafts WHERE id=? AND job_id=?", (d["draft_id"], ctx.target))
        # Persisted draft JSON may predate the strict command parser. Revalidate
        # before scope generation, content calculation, or any write. Historical
        # unknown identity is readable only in issued history, never issuable.
        try:
            lines = [EstimateLineInput.model_validate(line).model_dump(mode="json")
                     for line in json.loads(draft["lines_json"])]
        except (TypeError, ValueError, ValidationError):
            raise Rejected("VALIDATION_FAILED") from None
        require(instant(d["expires_at"]) > instant(now), "ESTIMATE_WINDOW_CLOSED")
        for line in lines:
            line["scope_ref"] = line["scope_ref"] or uid()
            # A scope UUID cannot relabel changed work, even across older versions/jobs.
            previous = rows(db, "SELECT l.*,e.job_id FROM estimate_lines l JOIN estimates e ON e.id=l.estimate_id "
                            "WHERE l.scope_ref=?", (line["scope_ref"],))
            for prior in previous:
                require(prior["job_id"] == ctx.target and all(prior[k] == line[k] for k in
                        ("kind", "description", "quantity", "unit_amount_minor", "part_id")), "POLICY_CONFLICT")
        subtotal = sum(line["quantity"] * line["unit_amount_minor"] * (-1 if line["kind"] == "credit" else 1) for line in lines)
        require(0 <= subtotal <= 10_000_000_000 and subtotal + draft["tax_minor"] <= 10_000_000_000)
        old = current_estimate(db, ctx.target)
        policy = one(db, "SELECT * FROM shop_policy", required=False)
        tax_enabled = policy["tax_enabled"] if policy else 0
        require(draft["tax_source"] is not None and (tax_enabled or draft["tax_minor"] == 0), "POLICY_CONFLICT")
        content = dict(lines=lines, currency=draft["currency"], tax_minor=draft["tax_minor"],
                       tax_label=draft["tax_label"], tax_enabled=tax_enabled, tax_source=draft["tax_source"])
        changes = []
        if old:
            oldlines = rows(db, "SELECT kind,description,quantity,unit_amount_minor,scope_ref,part_id FROM estimate_lines "
                           "WHERE estimate_id=? ORDER BY ordinal", (old["id"],))
            before = dict(lines=oldlines, currency=old["currency"], tax_minor=old["tax_minor"],
                          tax_label=old["tax_label"], tax_enabled=old["tax_enabled"], tax_source=old["tax_source"])
            for field in content:
                if before[field] != content[field]:
                    changes.append(dict(field=field, before=encode(before[field]), after=encode(content[field])))
            approved = decision(db, old["id"])
            require(changes or not (approved and approved["decision"] == "approved"), "POLICY_CONFLICT")
        id_ = insert(db, "estimates", id=uid(), job_id=ctx.target, version=old["version"] + 1 if old else 1,
                     issued_at=now, expires_at=d["expires_at"], currency=draft["currency"], subtotal_minor=subtotal,
                     tax_minor=draft["tax_minor"], total_minor=subtotal + draft["tax_minor"], tax_label=draft["tax_label"],
                     tax_enabled=tax_enabled, content_hash=fingerprint(encode(content)),
                     tax_source=draft["tax_source"], tax_policy_rev=policy["policy_rev"],
                     change_reason=d["change_reason"], change_json=encode(changes), issued_by_staff_id=actor["id"])
        for ordinal, line in enumerate(lines):
            insert(db, "estimate_lines", id=uid(), estimate_id=id_, ordinal=ordinal, **line,
                   line_amount_minor=line["quantity"] * line["unit_amount_minor"] * (-1 if line["kind"] == "credit" else 1))
        # Every replacement has a new decision binding, even a window refresh.
        if old:
            hold(ctx, "revision")
            follow_up(ctx, "Review revised estimate and remaining blockers")
        return ctx.result("job", ctx.target, "estimate_version", id_)
    elif a in {"estimate.decision", "estimate.revoke"}:
        estimate = current_estimate(db, ctx.target)
        require(estimate and estimate["version"] == d["version"], "ESTIMATE_STALE")
        require((actor["kind"] == "customer") == (d["method"] == "customer_account"), "FORBIDDEN")
        old = decision(db, estimate["id"])
        if a == "estimate.decision":
            require(old is None, "DECISION_ALREADY_RECORDED")
            if d["decision"] == "approve":
                require(instant(now) < instant(estimate["expires_at"]), "ESTIMATE_WINDOW_CLOSED")
            confirm = d["confirmation"]
            require(confirm["version"] == estimate["version"] and confirm["total_minor"] == estimate["total_minor"]
                    and confirm["currency"] == estimate["currency"] and
                    instant(confirm["expires_at"]) == instant(estimate["expires_at"]), "ESTIMATE_STALE")
            value = "approved" if d["decision"] == "approve" else "declined"
            if value == "approved":
                db.execute("UPDATE jobs SET repair_hold=NULL WHERE id=?", (ctx.target,))
        else:
            require(old and old["decision"] == "approved", "SCOPE_NOT_APPROVED")
            value = "revoked"
            hold(ctx, "revoked")
        id_ = insert(db, "estimate_decisions", id=uid(), job_id=ctx.target, estimate_id=estimate["id"], decision=value,
                     method=d["method"], attribution=d["attribution"], actor_kind=actor["kind"],
                     actor_id=actor["id"], decided_at=now, reason_code=d.get("reason"))
        return ctx.result("job", ctx.target, "estimate_decision", id_)
    else:
        raise RuntimeError("REPAIR_HANDLER_MISSING")
    return ctx.result("job", ctx.target, "job", ctx.target)
