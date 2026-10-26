"""Named explicit allowlist projections. No database record is returned to a browser."""
from __future__ import annotations

import json

from contracts import projections as p
from backend.core import Rejected, account_actor, instant, one, rows, visible_job
from backend.repairs import current_estimate, decision, permission
from backend.settlement import balance, quality, repaired_ready


def known(row, key):
    return dict(value=row[key + "_value"], known=bool(row[key + "_known"]))


def account(db, account_id, now):
    a = account_actor(db, account_id)
    invite = one(db, "SELECT * FROM invitations WHERE account_id=? ORDER BY rowid DESC LIMIT 1", (account_id,), required=False)
    return p.AccountView(
        id=a["id"], kind=a["kind"], state=a["state"], credential_state=a["credential_state"],
        login_id=a["login_id"], roles=a["roles"], customer_id=a["customer_id"], display_name=a["display_name"],
        account_rev=a["account_rev"], auth_rev=a["auth_rev"],
        invite_id=invite["id"] if invite else None,
        invite_state=("expired" if invite["state"] == "pending" and instant(now) >= instant(invite["expires_at"]) else invite["state"]) if invite else None,
        invitation_owner_staff_id=invite["owner_staff_id"] if invite else None,
        invitation_next_action_due_at=invite["next_action_due_at"] if invite else None,
    ).model_dump(mode="json")


def owner(db, row):
    if row["owner_staff_id"]:
        name = one(db, "SELECT display_name FROM accounts WHERE id=?", (row["owner_staff_id"],))
        return dict(kind="staff", staff_id=row["owner_staff_id"], label=name["display_name"])
    return dict(kind="queue", id="manager_intake", label="Manager intake")


def estimates(db, job_id, now):
    result = []
    versions = rows(db, "SELECT * FROM estimates WHERE job_id=? ORDER BY version", (job_id,))
    for e in versions:
        d = decision(db, e["id"])
        state = "superseded" if e["version"] != versions[-1]["version"] else (
            "expired_unapproved" if instant(now) >= instant(e["expires_at"]) and not (d and d["decision"] == "approved") else "issued")
        lines = [dict(line_id=l["id"], **{key: l[key] for key in (
            "kind", "description", "quantity", "unit_amount_minor", "line_amount_minor", "scope_ref", "part_id")})
                 for l in rows(db, "SELECT * FROM estimate_lines WHERE estimate_id=? ORDER BY ordinal", (e["id"],))]
        result.append(p.EstimateView(
            estimate_id=e["id"], **{key: e[key] for key in ("version", "issued_at", "expires_at", "currency",
                "subtotal_minor", "tax_minor", "total_minor", "tax_label")},
            state=state, shop_timezone="America/New_York", lines=lines,
            tax_source=e["tax_source"] or ("manual_shop_entry" if e["tax_enabled"] else "not_configured"),
            tax_policy_rev=e["tax_policy_rev"],
            change_from_previous=json.loads(e["change_json"]),
            decision=dict(decision_id=d["id"] if d else None, value=d["decision"] if d else "pending", **{
                key: d[key] if d else None for key in ("method", "actor_kind", "actor_id", "attribution", "decided_at")}),
        ).model_dump(mode="json"))
    return result


def job_parts(db, job_id):
    result = []
    current = current_estimate(db, job_id)
    current_scopes = {r["scope_ref"] for r in rows(db, "SELECT scope_ref FROM estimate_lines WHERE estimate_id=?",
                                                (current["id"],))} if current else set()
    perm = permission(db, job_id)
    if perm:
        current_scopes.add(perm["id"])
    requirements = rows(db, "SELECT part_id,scope_ref,sum(qty) required_qty FROM part_requirements WHERE job_id=? GROUP BY part_id,scope_ref",
                        (job_id,))
    for item in requirements:
        part = one(db, "SELECT * FROM parts WHERE id=?", (item["part_id"],))
        reservations = []
        rs = rows(db, "SELECT * FROM reservations WHERE job_id=? AND part_id=? AND scope_ref=? ORDER BY rowid",
                  (job_id, part["id"], item["scope_ref"]))
        for r in rs:
            state = "consumed" if r["consumed_qty"] == r["qty"] else "released" if r["released_qty"] + r["consumed_qty"] == r["qty"] else (
                "partially_released" if r["released_qty"] else "active")
            reservations.append(dict(reservation_id=r["id"], state=state,
                                     **{k: r[k] for k in ("qty", "created_at", "released_qty", "consumed_qty")}))
        orders = []
        for order in rows(db, "SELECT * FROM supplier_orders WHERE job_id=? AND part_id=?", (job_id, part["id"])):
            outcome = one(db, "SELECT evidence FROM supplier_order_outcomes WHERE order_id=? ORDER BY rowid DESC LIMIT 1",
                          (order["id"],), required=False)
            orders.append(dict(order_id=order["id"], outcome_evidence=outcome["evidence"] if outcome else None,
                               **{k: order[k] for k in ("qty", "supplier_label", "eta_at", "eta_evidence", "state", "recorded_at")}))
        allocated = sum(r["qty"] - r["released_qty"] for r in rs)
        consumed = sum(r["consumed_qty"] for r in rs)
        state = "consumed" if consumed >= item["required_qty"] else "reserved" if allocated >= item["required_qty"] else (
            "short" if part["on_hand_qty"] - part["reserved_qty"] < item["required_qty"] - allocated else "needed")
        if item["scope_ref"] not in current_scopes:
            state = "released"
        result.append(p.PartView(requirement_ids=[r["id"] for r in rows(db,
            "SELECT id FROM part_requirements WHERE job_id=? AND part_id=? AND scope_ref=? ORDER BY rowid",
            (job_id, part["id"], item["scope_ref"]))],
            part_id=part["id"], required_qty=item["required_qty"], scope_ref=item["scope_ref"],
                                 **{k: part[k] for k in ("sku", "description", "on_hand_qty", "reserved_qty", "stock_rev")},
                                 available_qty=part["on_hand_qty"] - part["reserved_qty"], state=state,
                                 reservations=reservations, orders=orders).model_dump(mode="json"))
    return result


def blockers(db, job, now):
    codes = []
    perm = permission(db, job["id"])
    e = current_estimate(db, job["id"])
    d = decision(db, e["id"]) if e else None
    if not perm or perm["state"] != "granted":
        codes.append("DIAGNOSTIC_PERMISSION_PENDING" if perm and perm["state"] == "pending" else "DIAGNOSTIC_PERMISSION_REQUIRED")
    if e and not (d and d["decision"] == "approved"):
        codes.append("ESTIMATE_EXPIRED_UNAPPROVED" if instant(now) >= instant(e["expires_at"]) else "ESTIMATE_DECISION_REQUIRED")
    if job["repair_hold"]:
        codes.append({"revision": "ESTIMATE_REVISION_HOLD", "revoked": "CONSENT_REVOKED",
                      "cancelled": "CANCELLED", "safety": "SAFETY_STOP"}[job["repair_hold"]])
    if job["safe_stop_required"]:
        codes.append("SAFETY_STOP")
    parts = job_parts(db, job["id"])
    if e and one(db, "SELECT 1 FROM estimate_lines l WHERE l.estimate_id=? AND l.kind='part' AND "
                 "(l.part_id IS NULL OR NOT EXISTS(SELECT 1 FROM part_requirements r WHERE r.job_id=? "
                 "AND r.scope_ref=l.scope_ref AND r.part_id=l.part_id GROUP BY r.part_id HAVING sum(r.qty)>=l.quantity))",
                 (e["id"], job["id"]), required=False):
        codes.append("PARTS_NOT_RESERVED")
    if any(part["state"] == "short" for part in parts):
        codes.append("PARTS_SHORTAGE")
    if any(part["state"] == "needed" for part in parts):
        codes.append("PARTS_NOT_RESERVED")
    if one(db, "SELECT 1 FROM work_tasks WHERE job_id=? AND state='paused'", (job["id"],), required=False):
        codes.append("WORK_PAUSED")
    q = quality(db, job["id"])
    if q == "failed":
        codes.append("QC_FAILED")
    elif q in {"not_tested", "in_progress"}:
        codes.append("QC_INCOMPLETE")
    if instant(now) >= instant(job["next_action_due_at"]) and job["lifecycle"] != "closed":
        codes.append("FOLLOW_UP_DUE")
    if balance(db, job["id"]) > 0:
        codes.append("BALANCE_DUE")
    if job["lifecycle"] == "closed":
        return []
    return [dict(code=code, label=code.replace("_", " ").capitalize(),
                 detail=code.replace("_", " ").capitalize(), since=job["updated_at"]) for code in dict.fromkeys(codes)]


def phase(db, job):
    if job["lifecycle"] == "closed":
        return "complete"
    if job["lifecycle"] == "cancelled":
        return "return"
    if repaired_ready(db, job):
        return "collection"
    tasks = rows(db, "SELECT state FROM work_tasks WHERE job_id=?", (job["id"],))
    if tasks:
        return "quality" if all(t["state"] == "completed" for t in tasks) else "repair"
    e = current_estimate(db, job["id"])
    if e:
        d = decision(db, e["id"])
        return "parts" if d and d["decision"] == "approved" else "awaiting_decision"
    perm = permission(db, job["id"])
    return "diagnosis" if perm and perm["state"] == "granted" else "intake"


def device_label(db, job):
    device = one(db, "SELECT * FROM devices WHERE id=?", (job["device_id"],))
    return " ".join(value for value in (device["make_value"], device["model_value"]) if value) or "Laptop (details unknown)"


def queue_item(db, job, now):
    customer = one(db, "SELECT display_name FROM customers WHERE id=?", (job["customer_id"],))
    return p.QueueItem(job_id=job["id"], job_code=job["job_code"], customer_label=customer["display_name"],
                       device_label=device_label(db, job), phase=phase(db, job), lifecycle=job["lifecycle"],
                       blockers=blockers(db, job, now), owner=owner(db, job),
                       **{k: job[k] for k in ("next_action", "next_action_due_at", "updated_at", "job_rev", "gate_rev")}).model_dump(mode="json")


def contact_status(r):
    return dict(request_id=r["id"], requested_fields=json.loads(r["requested_fields_json"]), state=r["state"],
                created_at=r["requested_at"], resolved_at=r["resolved_at"])


def contact_detail(db, request_id):
    r = one(db, "SELECT * FROM contact_change_requests WHERE id=?", (request_id,))
    c = one(db, "SELECT * FROM customers WHERE id=?", (r["customer_id"],))
    proposed = json.loads(r["proposed_values_json"])
    return p.ContactChangeRequestDetail(
        **contact_status(r), customer_id=c["id"], customer_label=c["display_name"],
        request_rev=r["request_rev"], customer_rev=c["customer_rev"],
        requested_values={key: proposed.get(key) for key in ("display_name", "phone", "email")},
        confirmation_method=r["confirmation_method"], reason_code=r["reason_code"]).model_dump(mode="json")


def profile(db, profile_id, version=None):
    r = one(db, "SELECT * FROM qc_profiles WHERE id=?", (profile_id,))
    v = one(db, "SELECT * FROM qc_profile_versions WHERE profile_id=? AND version=?", (profile_id, version or r["profile_rev"]))
    checks = rows(db, "SELECT * FROM qc_profile_checks WHERE profile_version_id=? ORDER BY ordinal", (v["id"],))
    return p.QCProfileView(profile_id=profile_id, version=v["version"], profile_rev=r["profile_rev"],
                           label=r["label"], active_for_new_tasks=v["version"] == r["profile_rev"], created_at=v["created_at"],
                           created_by_staff_id=v["created_by_staff_id"], reason=v["reason_code"],
                           checks=[dict(check_id=c["id"], label=c["label"], required=bool(c["required"])) for c in checks]).model_dump(mode="json")


def part_detail(db, part_id):
    r = one(db, "SELECT * FROM parts WHERE id=?", (part_id,))
    movements = [dict(movement_id=m["id"], reason=m["reason_code"], **{key: m[key] for key in (
        "part_id", "kind", "on_hand_delta", "reserved_delta", "job_id", "reservation_id", "source_movement_id",
        "actor_staff_id", "occurred_at", "source_note")})
        for m in rows(db, "SELECT * FROM stock_movements WHERE part_id=? ORDER BY rowid", (part_id,))]
    return p.PartDetail(part_id=part_id, **{key: r[key] for key in (
        "sku", "description", "unit", "on_hand_qty", "reserved_qty", "stock_rev")},
        available_qty=r["on_hand_qty"] - r["reserved_qty"], movements=movements).model_dump(mode="json")


def return_view(db, case, actor=None):
    if actor is not None:
        visible_job(db, actor, case["original_job_id"])
        visible_job(db, actor, case["return_job_id"])
    assessment = one(db, "SELECT * FROM return_assessments WHERE return_case_id=? ORDER BY rowid DESC LIMIT 1", (case["id"],), required=False)
    job = one(db, "SELECT * FROM jobs WHERE id=?", (case["return_job_id"],))
    original = one(db, "SELECT job_rev FROM jobs WHERE id=?", (case["original_job_id"],))
    return p.ReturnView(
        return_case_id=case["id"], return_job_id=case["return_job_id"], original_job_id=case["original_job_id"],
        return_rev=case["return_rev"], return_job_rev=job["job_rev"], original_job_rev=original["job_rev"],
        reported_recurrence=case["reported_recurrence"], state=case["state"],
        coverage=assessment["coverage"] if assessment else None, coverage_reason_code=assessment["reason_code"] if assessment else None,
        owner=owner(db, job), next_action=job["next_action"], next_action_due_at=job["next_action_due_at"]).model_dump(mode="json")


def timeline(db, job_id):
    result = []
    for e in rows(db, "SELECT * FROM audit_events WHERE aggregate_id=? ORDER BY rowid", (job_id,)):
        name = one(db, "SELECT display_name FROM accounts WHERE id=?", (e["actor_id"],), required=False)
        result.append(dict(event_id=e["id"], event_type=e["event_type"], occurred_at=e["occurred_at"],
                           actor=dict(kind=e["actor_kind"], display=name["display_name"] if name else e["actor_kind"]),
                           reason_code=e["reason_code"], summary_code=e["summary_code"], refs=json.loads(e["refs_json"])))
    return result


def settlement_view(db, job_id):
    output = dict(currency="USD", charges=[], credits=[], payments=[], refunds=[], corrections=[], balance_minor=balance(db, job_id))
    for e in rows(db, "SELECT * FROM settlement_entries WHERE job_id=? ORDER BY rowid", (job_id,)):
        amount = dict(currency=e["currency"], amount_minor=e["amount_minor"])
        if e["bucket"] == "charge":
            estimate = one(db, "SELECT version FROM estimates WHERE id=?", (e["estimate_id"],), required=False)
            line = one(db, "SELECT scope_ref FROM estimate_lines WHERE id=?", (e["line_id"],), required=False)
            output["charges"].append(dict(charge_id=e["id"], source=e["source"] or (
                "estimate_line" if line else "diagnostic_fee" if e["permission_id"] else None),
                diagnostic_permission_id=e["permission_id"], tax_label=e["tax_label"],
                estimate_version=estimate["version"] if estimate else None, estimate_line_id=e["line_id"],
                scope_ref=line["scope_ref"] if line else None, amount=amount, authorized_at=e["authorized_at"],
                posted_at=e["recorded_at"], actor_staff_id=e["actor_staff_id"], correction_of=None))
        elif e["bucket"] == "credit":
            output["credits"].append(dict(credit_id=e["id"], charge_id=e["charge_id"], kind=e["credit_kind"], amount=amount,
                reason=e["reason_code"], posted_at=e["recorded_at"], actor_staff_id=e["actor_staff_id"], correction_of=None))
        else:
            record = dict(amount=amount, external_reference=e["external_reference"], occurred_at=e["occurred_at"],
                          recorded_at=e["recorded_at"], actor_staff_id=e["actor_staff_id"])
            if e["bucket"] == "payment":
                output["payments"].append(dict(payment_id=e["id"], method=e["method"], simulated=False, **record))
            else:
                output["refunds"].append(dict(refund_id=e["id"], payment_id=e["payment_id"], reason=e["reason_code"], **record))
    for e in rows(db, "SELECT * FROM simulated_payments WHERE job_id=? ORDER BY rowid", (job_id,)):
        output["payments"].append(dict(payment_id=e["id"], method="fictional_simulation", simulated=True,
            amount=dict(currency=e["currency"], amount_minor=e["amount_minor"]), external_reference=None,
            occurred_at=None, recorded_at=e["recorded_at"], actor_staff_id=e["actor_staff_id"]))
    for c in rows(db, "SELECT * FROM settlement_corrections WHERE job_id=? ORDER BY rowid", (job_id,)):
        output["corrections"].append(dict(correction_id=c["id"], target_entry_id=c["entry_id"], reason=c["reason_code"],
                                         **{k: c[k] for k in ("bucket", "delta_minor", "currency", "external_reference", "posted_at", "actor_staff_id")}))
    return p.SettlementView(**output).model_dump(mode="json")


def job_detail(db, actor, job_id, now):
    j = visible_job(db, actor, job_id)
    customer = one(db, "SELECT * FROM customers WHERE id=?", (j["customer_id"],))
    perm = permission(db, job_id)
    es = estimates(db, job_id, now)
    parts = job_parts(db, job_id)
    missing_part_demand = any(b["code"] == "PARTS_NOT_RESERVED" for b in blockers(db, j, now))
    q = quality(db, job_id)
    ready = repaired_ready(db, j)
    handover = one(db, "SELECT * FROM handovers WHERE job_id=?", (job_id,), required=False)
    simulated_handover = one(db, "SELECT * FROM simulated_handovers WHERE job_id=?", (job_id,), required=False)
    collectors = rows(db, "SELECT * FROM collector_authorizations WHERE job_id=? ORDER BY rowid", (job_id,))
    cases = rows(db, "SELECT * FROM return_cases WHERE original_job_id=? OR return_job_id=?", (job_id, job_id))
    findings = rows(db, "SELECT * FROM diagnostic_records WHERE job_id=? ORDER BY rowid", (job_id,))
    phase_ = phase(db, j)
    status_code = dict(intake="received", diagnosis="diagnosing", estimate="waiting_for_approval",
                       awaiting_decision="waiting_for_approval", parts="waiting_for_parts", repair="repairing",
                       quality="quality_check", collection="ready_for_collection", complete="returned", return_="follow_up").get(phase_, "follow_up")
    if simulated_handover:
        status_code = "demo_complete"
    status = dict(code=status_code, label="Fictional demo complete" if simulated_handover else status_code.replace("_", " ").capitalize(),
                  explanation="Fictional demo only; no collector was verified and no property was physically transferred."
                    if simulated_handover else "Recorded shop progress; contact the shop with questions.",
                  blockers=[dict(code=b["code"], label=b["label"]) for b in blockers(db, j, now)])
    if actor["kind"] == "customer":
        estimate = None
        if es:
            e = es[-1]
            estimate = dict(**{key: e[key] for key in ("version", "state", "issued_at", "expires_at", "shop_timezone",
                "currency", "subtotal_minor", "tax_minor", "total_minor", "tax_label", "tax_source", "tax_policy_rev", "change_from_previous")},
                lines=[{key: line[key] for key in ("line_id", "description", "quantity", "unit_amount_minor", "line_amount_minor")}
                       for line in e["lines"]], decision=e["decision"]["value"])
        decisions = rows(db, "SELECT d.*,e.version FROM estimate_decisions d JOIN estimates e ON e.id=d.estimate_id WHERE d.job_id=? ORDER BY d.rowid", (job_id,))
        return p.CustomerJobDetail(
            job_id=job_id, job_code=j["job_code"], job_rev=j["job_rev"], gate_rev=j["gate_rev"], opened_at=j["opened_at"],
            device=dict(label=device_label(db, j)), status=status,
            findings=[dict(text=r["customer_text"], recorded_at=r["recorded_at"]) for r in findings if r["customer_visible"]],
            diagnostic_permission=dict(permission_id=perm["id"], state=perm["state"], scope=perm["scope"],
                fee=dict(currency=perm["currency"], amount_minor=perm["fee_minor"]),
                fee_source="configured_policy", fee_policy_rev=perm["fee_policy_rev"],
                method=perm["method"], decided_at=perm["decided_at"]) if perm else None,
            current_estimate=estimate,
            decision_history=[dict(decision_id=d["id"], estimate_version=d["version"], decision=d["decision"], method=d["method"],
                recorded_by="you" if d["actor_id"] == actor["id"] else "shop_staff", at=d["decided_at"]) for d in decisions],
            quality_check=dict(state=q, method="same_technician" if q in {"passed", "failed"} else "not_applicable",
                disclosure="Quality checks may be performed by the repair technician." if q in {"passed", "failed"} else None),
            status_history=[dict(code="received", label="Received", explanation="The shop recorded this job.", recorded_at=j["opened_at"])],
            collection=dict(ready=ready and not simulated_handover, balance=dict(currency="USD", amount_minor=balance(db, job_id)),
                what_to_bring="Fictional demo only; no property was physically transferred." if simulated_handover
                   else "Bring your job reference and confirm the authorized collector with the shop.",
                authorized_collector="No collector was verified; fictional demo only." if simulated_handover
                   else collectors[-1]["collector_name"] if collectors else "No authorized collector recorded.",
                handover=({key: simulated_handover[key] for key in ("recorded_at", "acknowledgement", "collector_name")}
                   | {"simulated": True} if simulated_handover else
                   {**{key: handover[key] if handover else None for key in ("recorded_at", "acknowledgement", "collector_name")},
                    "simulated": False})),
            return_request=dict(allowed=j["lifecycle"] == "closed", existing_case_id=cases[-1]["id"] if cases else None,
                linked_return_job_ids=[c["return_job_id"] for c in cases if c["original_job_id"] == job_id]),
        ).model_dump(mode="json")
    device = one(db, "SELECT * FROM devices WHERE id=?", (j["device_id"],))
    followups = [dict(follow_up_id=f["id"], state=f["state"], owner=owner(db, f),
                      **{key: f[key] for key in ("next_action", "due_at", "created_at", "resolved_at")})
                 for f in rows(db, "SELECT * FROM follow_ups WHERE job_id=? ORDER BY rowid", (job_id,))]
    work = []
    qc = []
    for t in rows(db, "SELECT * FROM work_tasks WHERE job_id=? ORDER BY rowid", (job_id,)):
        logs = [dict(log_id=l["id"], **{key: l[key] for key in ("kind", "actor_staff_id", "recorded_at", "ended_at",
            "summary", "owner_staff_id", "next_action", "next_action_due_at")})
                for l in rows(db, "SELECT * FROM work_logs WHERE task_id=? ORDER BY rowid", (t["id"],))]
        work.append(dict(task_id=t["id"], task_rev=t["task_rev"], rework_of_task_id=t["rework_of_task_id"],
                         scope_ref=t["scope_ref"], scope_kind=t["scope_kind"], state=t["state"],
                         assigned_staff_id=t["assigned_staff_id"], logs=logs))
        if t["profile_version_id"]:
            v = one(db, "SELECT version FROM qc_profile_versions WHERE id=?", (t["profile_version_id"],))
            run = one(db, "SELECT * FROM qc_runs WHERE task_id=? ORDER BY rowid DESC LIMIT 1", (t["id"],), required=False)
            if run is None:
                continue  # QCView represents a recorded result; never invent a run UUID.
            for check in rows(db, "SELECT * FROM qc_profile_checks WHERE profile_version_id=? ORDER BY ordinal", (t["profile_version_id"],)):
                r = one(db, "SELECT * FROM qc_results WHERE run_id=? AND check_id=?", (run["id"], check["id"]), required=False) if run else None
                qc.append(dict(run_id=run["id"], check_id=check["id"], profile_version=v["version"], label=check["label"], required=bool(check["required"]),
                    state={"pass": "passed", "fail": "failed", "not_tested": "not_tested"}[r["result"]] if r else "not_tested",
                    result_note=r["note"] if r else None, actor_staff_id=run["actor_staff_id"] if run else None,
                    recorded_at=run["recorded_at"] if run else None, task_id=t["id"]))
    property_state = "simulated_handover" if simulated_handover else "handed_over" if handover else "ready_for_handover" if ready else "return_planned" if j["disposition"] else "shop"
    draft_views = [dict(draft_id=d["id"], lines=json.loads(d["lines_json"]), **{key: d[key] for key in (
        "currency", "tax_minor", "tax_label", "updated_at", "updated_by_staff_id")})
        for d in rows(db, "SELECT * FROM estimate_drafts WHERE job_id=?", (job_id,))]
    visible_returns = []
    for case in cases:
        try:
            visible_returns.append(return_view(db, case, actor))
        except Rejected as error:
            if str(error) != "NOT_FOUND":
                raise
    return p.JobStaffDetail(
        job_id=job_id, job_code=j["job_code"], job_rev=j["job_rev"], gate_rev=j["gate_rev"], lifecycle=j["lifecycle"],
        customer=dict(customer_id=customer["id"], display_name=customer["display_name"], phone=known(customer, "phone"), email=known(customer, "email")),
        device=dict(device_id=device["id"], **{key: known(device, key) for key in ("make", "model", "serial")}),
        intake=dict(reported_issue=j["reported_issue"], condition=known(j, "condition"), testing_constraints=known(j, "testing_constraints"),
            accessories=dict(known=bool(j["accessories_known"]), items=[r["label"] for r in rows(db, "SELECT label FROM job_accessories WHERE job_id=? ORDER BY ordinal", (job_id,))]),
            received_by_staff_id=j["received_by_staff_id"], received_at=j["opened_at"],
            acknowledgement=dict(text_version=j["acknowledgement_version"], method=j["acknowledgement_method"], actor_id=j["acknowledgement_actor_id"], at=j["opened_at"])),
        assignment=dict(owner=owner(db, j), assigned_staff_ids=[r["staff_id"] for r in rows(db, "SELECT staff_id FROM job_assignments WHERE job_id=?", (job_id,))]),
        summary=dict(phase=phase_, blockers=blockers(db, j, now), diagnostic_permission=perm["state"] if perm else "not_requested",
            repair_gate="open" if es and es[-1]["decision"]["value"] == "approved" and not j["repair_hold"] and j["lifecycle"] == "open" else "held",
            parts_state="needed" if missing_part_demand else "not_required" if not parts else "short" if any(r["state"] == "short" for r in parts) else "reserved" if all(r["state"] in {"reserved", "consumed"} for r in parts) else "needed",
            work_state="active" if any(t["state"] == "active" for t in work) else "paused" if any(t["state"] == "paused" for t in work) else "completed" if work and all(t["state"] == "completed" for t in work) else "not_started",
            qc_state=q, property_state=property_state, balance=dict(currency="USD", amount_minor=balance(db, job_id)), follow_ups=followups),
        diagnostic=dict(current=dict(permission_id=perm["id"], fee=dict(currency=perm["currency"], amount_minor=perm["fee_minor"]),
            fee_source="configured_policy", fee_policy_rev=perm["fee_policy_rev"],
            revoked_at=perm["decided_at"] if perm["state"] == "revoked" else None,
            **{key: perm[key] for key in ("state", "scope", "method", "attribution", "actor_kind", "actor_id", "decided_at")}) if perm else None,
            records=[dict(record_id=r["id"], **{key: bool(r[key]) if key == "customer_visible" else r[key] for key in (
                "kind", "summary", "customer_text", "customer_visible", "uncertainty", "actor_staff_id", "recorded_at")}) for r in findings]),
        estimate_drafts=draft_views, estimates=es, parts=parts, work=work, qc=qc,
        qc_history=[dict(run_id=r["id"], task_id=r["task_id"],
            profile_version=one(db, "SELECT version FROM qc_profile_versions WHERE id=?", (r["profile_version_id"],))["version"],
            overall=r["overall"], actor_staff_id=r["actor_staff_id"], recorded_at=r["recorded_at"], reason=r["reason_code"],
            checks=[dict(check_id=c["check_id"], result={"pass":"passed","fail":"failed","not_tested":"not_tested"}[c["result"]],
                         note=c["note"]) for c in rows(db, "SELECT * FROM qc_results WHERE run_id=? ORDER BY rowid", (r["id"],))])
            for r in rows(db, "SELECT * FROM qc_runs WHERE job_id=? ORDER BY rowid", (job_id,))],
        dispositions=[dict(disposition_id=r["id"], **{k:r[k] for k in ("disposition_rev","kind","state","summary",
            "actor_staff_id","recorded_at","owner_staff_id","next_action","next_action_due_at",
            "resolved_at","resolved_by_staff_id","resolution_summary")})
            for r in rows(db, "SELECT * FROM job_dispositions WHERE job_id=? ORDER BY rowid", (job_id,))],
        settlement=settlement_view(db, job_id),
        property=dict(state=property_state, authorized_collectors=[dict(authorization_id=c["id"], reason=c["reason_code"],
            **{key: c[key] for key in ("collector_name", "method", "verified_by_staff_id", "verified_at")}) for c in collectors],
            handover=({key: simulated_handover[key] for key in ("collector_name", "acknowledgement", "recorded_at",
                "actor_staff_id", "balance_minor_at_handover")} | {"balance_override_reason": None, "simulated": True}
                if simulated_handover else
                {**{key: handover[key] for key in ("collector_name", "acknowledgement", "recorded_at", "actor_staff_id",
                    "balance_override_reason", "balance_minor_at_handover")}, "simulated": False} if handover else None)),
        follow_ups=followups, return_cases=visible_returns, timeline=timeline(db, job_id),
    ).model_dump(mode="json")
