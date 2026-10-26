"""Atomic inventory vectors, scoped work and explicit versioned QC."""
from __future__ import annotations

from backend.core import eligible_staff, insert, one, require, revision, rows, uid
from backend.repairs import planning_scope, require_recorded_catalog_identity, scope_allowed


def movement(ctx, part_id, kind, on_hand, reserved, *, reason="manual_record", **refs):
    part = one(ctx.db, "SELECT * FROM parts WHERE id=?", (part_id,))
    require(part["on_hand_qty"] + on_hand >= part["reserved_qty"] + reserved >= 0, "STOCK_SHORTAGE")
    ctx.db.execute("UPDATE parts SET on_hand_qty=on_hand_qty+?,reserved_qty=reserved_qty+?,stock_rev=stock_rev+1 WHERE id=?",
                   (on_hand, reserved, part_id))
    return insert(ctx.db, "stock_movements", id=uid(), part_id=part_id, kind=kind,
                  on_hand_delta=on_hand, reserved_delta=reserved, reason_code=reason,
                  actor_staff_id=ctx.actor["id"], occurred_at=ctx.now, **refs)


def release_reservation(ctx, reservation, qty, reason):
    require(qty <= reservation["qty"] - reservation["released_qty"] - reservation["consumed_qty"])
    ctx.db.execute("UPDATE reservations SET released_qty=released_qty+? WHERE id=?", (qty, reservation["id"]))
    return movement(ctx, reservation["part_id"], "release", 0, -qty, reason=reason,
                    job_id=reservation["job_id"], reservation_id=reservation["id"])


def consumptions(ctx, task, items):
    ids = [item["reservation_id"] for item in items]
    require(len(ids) == len(set(ids)))
    resolved = []
    for item in items:
        r = one(ctx.db, "SELECT * FROM reservations WHERE id=? AND job_id=?", (item["reservation_id"], ctx.target))
        require(r["scope_ref"] == task["scope_ref"], "NOT_FOUND")
        require(item["qty"] <= r["qty"] - r["released_qty"] - r["consumed_qty"], "STOCK_SHORTAGE")
        resolved.append((r, item["qty"]))
    return resolved


def vector(ctx, part_ids):
    keys = {f"stock_rev:{id_}" for id_ in part_ids}
    require({key for key in ctx.expected if key.startswith("stock_rev:")} == keys)
    for part_id in part_ids:
        p = one(ctx.db, "SELECT stock_rev FROM parts WHERE id=?", (part_id,))
        revision(ctx.expected, f"stock_rev:{part_id}", p["stock_rev"])


def task_start(ctx, task, items, *, resume=False):
    require(task["assigned_staff_id"] == ctx.actor["id"], "FORBIDDEN")
    require(task["state"] in ({"paused"} if resume else {"not_started", "rework"}))
    authorization = scope_allowed(ctx.db, ctx.job, task["scope_ref"], task["scope_kind"])
    require(not one(ctx.db, "SELECT 1 FROM work_tasks WHERE job_id=? AND scope_ref=? AND state='completed' "
                    "AND id!=? AND rework_of_task_id IS NULL",
                    (ctx.target, task["scope_ref"], task["id"]), required=False)
            or task["rework_of_task_id"], "POLICY_CONFLICT")
    require(not ctx.job["safe_stop_required"], "REVISION_HOLD")
    consumed = consumptions(ctx, task, items)
    active = rows(ctx.db, "SELECT * FROM reservations WHERE job_id=? AND scope_ref=? AND qty>released_qty+consumed_qty",
                  (ctx.target, task["scope_ref"]))
    if not resume:
        vector(ctx, {r["part_id"] for r in active} | {r["part_id"] for r, _ in consumed})
    demands = rows(ctx.db, "SELECT part_id,sum(qty) qty FROM part_requirements WHERE job_id=? AND scope_ref=? GROUP BY part_id",
                   (ctx.target, task["scope_ref"]))
    if task["scope_kind"] == "estimate_line":
        line = one(ctx.db, "SELECT * FROM estimate_lines WHERE estimate_id=? AND scope_ref=?",
                   (authorization["id"], task["scope_ref"]))
        if line["kind"] == "part":
            require_recorded_catalog_identity(line)
            require(len(demands) == 1 and demands[0]["part_id"] == line["part_id"]
                    and demands[0]["qty"] >= line["quantity"], "STOCK_SHORTAGE")
    for demand in demands:
        allocated = one(ctx.db, "SELECT coalesce(sum(qty-released_qty-consumed_qty),0) n FROM reservations "
                        "WHERE job_id=? AND scope_ref=? AND part_id=?",
                        (ctx.target, task["scope_ref"], demand["part_id"]))["n"]
        # Resume/rework credits only consumption of this exact scope, not released stock.
        used = one(ctx.db, "SELECT coalesce(sum(consumed_qty),0) n FROM reservations WHERE job_id=? AND scope_ref=? AND part_id=?",
                   (ctx.target, task["scope_ref"], demand["part_id"]))["n"] if resume or task["rework_of_task_id"] else 0
        require(allocated + used >= demand["qty"], "STOCK_SHORTAGE")
    for r, qty in consumed:
        consume(ctx, task, r, qty)
    ctx.db.execute("UPDATE work_tasks SET state='active',started_at=coalesce(started_at,?),"
                   "started_under_estimate_id=coalesce(started_under_estimate_id,?),"
                   "started_under_permission_id=coalesce(started_under_permission_id,?),task_rev=task_rev+1 WHERE id=?",
                   (ctx.now, authorization["id"] if task["scope_kind"] == "estimate_line" else None,
                    authorization["id"] if task["scope_kind"] == "diagnostic" else None, task["id"]))


def consume(ctx, task, r, qty):
    ctx.db.execute("UPDATE reservations SET consumed_qty=consumed_qty+? WHERE id=?", (qty, r["id"]))
    return movement(ctx, r["part_id"], "consumption", -qty, -qty, job_id=ctx.target,
                    reservation_id=r["id"], task_id=task["id"])


def initialize_checklist(ctx, task_id, profile_version_id):
    """Record actual checklist initialization, not a performed/passed QC test.

    The creator and time describe recording these untested results *now*.
    No historical actor/time or successful check is inferred.
    """
    run_id = insert(ctx.db, "qc_runs", id=uid(), job_id=ctx.target, task_id=task_id,
                    profile_version_id=profile_version_id, overall="not_tested",
                    actor_staff_id=ctx.actor["id"], recorded_at=ctx.now, reason_code=None)
    for check in rows(ctx.db, "SELECT id FROM qc_profile_checks WHERE profile_version_id=? ORDER BY ordinal",
                      (profile_version_id,)):
        insert(ctx.db, "qc_results", id=uid(), run_id=run_id, check_id=check["id"],
               result="not_tested", note=None)


def handle(ctx):
    db, a, d, now = ctx.db, ctx.action, ctx.data, ctx.now
    if a == "parts.create":
        require(one(db, "SELECT 1 FROM parts WHERE sku=?", (d["sku"],), required=False) is None)
        id_ = insert(db, "parts", id=uid(), sku=d["sku"], description=d["description"], unit=d["unit"],
                     on_hand_qty=0, reserved_qty=0)
        movement(ctx, id_, "opening", d["initial_qty"], 0, reason=d["reason"])
        return ctx.result("part", id_, "part", id_)
    if a == "parts.adjust":
        refs = {}
        kind = "adjustment"
        if d["source"] != "return_to_stock":
            require(d["source_movement_id"] is None and d["inspection_note"] is None)
        if d["source"] == "return_to_stock":
            require(d["delta_qty"] > 0 and d["source_movement_id"] and d["inspection_note"])
            source = one(db, "SELECT * FROM stock_movements WHERE id=? AND part_id=? AND kind='consumption'",
                         (d["source_movement_id"], d["part_id"]))
            returned = one(db, "SELECT coalesce(sum(on_hand_delta),0) n FROM stock_movements WHERE source_movement_id=? "
                           "AND kind='return_to_stock'", (source["id"],))["n"]
            require(returned + d["delta_qty"] <= -source["on_hand_delta"])
            refs = dict(source_movement_id=source["id"], source_note=d["inspection_note"], job_id=source["job_id"])
            kind = "return_to_stock"
        id_ = movement(ctx, d["part_id"], kind, d["delta_qty"], 0, reason=d["reason"], **refs)
        return ctx.result("part", d["part_id"], "stock_movement", id_)
    if a in {"parts.demand", "parts.reserve"}:
        require(d["job_id"] == ctx.target, "NOT_FOUND")
        kind = planning_scope(db, ctx.target, d["scope_ref"])
        if kind == "estimate_line":
            e = one(db, "SELECT id FROM estimates WHERE job_id=? ORDER BY version DESC LIMIT 1", (ctx.target,))
            line = one(db, "SELECT * FROM estimate_lines WHERE estimate_id=? AND scope_ref=?",
                       (e["id"], d["scope_ref"]))
            # Reservation additionally requires the current approved scope; do
            # that hold/approval gate before exposing the unknown-identity block.
            if a == "parts.reserve":
                scope_allowed(db, ctx.job, d["scope_ref"], kind)
            if line["kind"] == "part":
                require_recorded_catalog_identity(line)
                require(line["part_id"] == d["part_id"], "NOT_FOUND")
        if a == "parts.reserve":
            demand = one(db, "SELECT coalesce(sum(qty),0) n FROM part_requirements WHERE job_id=? AND part_id=? AND scope_ref=?",
                         (ctx.target, d["part_id"], d["scope_ref"]))["n"]
            allocated = one(db, "SELECT coalesce(sum(qty-released_qty),0) n FROM reservations WHERE job_id=? AND part_id=? AND scope_ref=?",
                            (ctx.target, d["part_id"], d["scope_ref"]))["n"]
            require(allocated + d["qty"] <= demand)
            id_ = insert(db, "reservations", id=uid(), job_id=ctx.target, part_id=d["part_id"], qty=d["qty"],
                         scope_ref=d["scope_ref"], created_at=now, created_by_staff_id=ctx.actor["id"])
            movement(ctx, d["part_id"], "reservation", 0, d["qty"], job_id=ctx.target, reservation_id=id_)
            return ctx.result("job", ctx.target, "reservation", id_)
        id_ = insert(db, "part_requirements", id=uid(), job_id=ctx.target, part_id=d["part_id"],
                     qty=d["qty"], scope_ref=d["scope_ref"], needed_by=d["needed_by"], created_at=now)
        return ctx.result("job", ctx.target, "job_part_requirement", id_)
    if a == "parts.release":
        r = one(db, "SELECT * FROM reservations WHERE id=? AND job_id=?", (d["reservation_id"], ctx.target))
        id_ = release_reservation(ctx, r, d["qty"], d["reason"])
        return ctx.result("job", ctx.target, "stock_movement", id_)
    if a == "parts.manual_order":
        require(d["eta_at"] is None or bool(d["eta_evidence"]))
        id_ = insert(db, "supplier_orders", id=uid(), job_id=ctx.target, part_id=d["part_id"], qty=d["qty"],
                     supplier_label=d["supplier_label"], eta_at=d["eta_at"], eta_evidence=d["eta_evidence"],
                     state=d["placement_state"], recorded_at=now, actor_staff_id=ctx.actor["id"])
        return ctx.result("job", ctx.target, "supplier_order", id_)
    if a == "parts.record_order_outcome":
        order = one(db, "SELECT * FROM supplier_orders WHERE id=? AND job_id=?", (d["order_id"], ctx.target))
        require(order["state"] != "received")
        insert(db, "supplier_order_outcomes", id=uid(), order_id=order["id"], outcome=d["outcome"], evidence=d["evidence"],
               reason_code=d["reason"], recorded_at=now, actor_staff_id=ctx.actor["id"])
        db.execute("UPDATE supplier_orders SET state=? WHERE id=?", (d["outcome"], order["id"]))
        return ctx.result("job", ctx.target, "supplier_order", order["id"])
    if a == "parts.receive":
        if d["order_id"]:
            order = one(db, "SELECT * FROM supplier_orders WHERE id=? AND job_id=?", (d["order_id"], ctx.target))
            require(order["part_id"] == d["part_id"], "NOT_FOUND")
            received = one(db, "SELECT coalesce(sum(qty),0) n FROM receipts WHERE order_id=?", (order["id"],))["n"]
            require(received + d["qty"] <= order["qty"])
            require(order["state"] not in {"cancellation_reported", "unavailable"})
            if received + d["qty"] == order["qty"]:
                db.execute("UPDATE supplier_orders SET state='received' WHERE id=?", (order["id"],))
        id_ = insert(db, "receipts", id=uid(), order_id=d["order_id"], part_id=d["part_id"], qty=d["qty"],
                     source_note=d["source_note"], recorded_at=now, actor_staff_id=ctx.actor["id"])
        mid = movement(ctx, d["part_id"], "receipt", d["qty"], 0, receipt_id=id_, source_note=d["source_note"],
                       job_id=ctx.target if d["order_id"] else None)
        db.execute("UPDATE jobs SET job_rev=job_rev+1,gate_rev=gate_rev+1,updated_at=? WHERE id IN "
                   "(SELECT job_id FROM part_requirements WHERE part_id=?)", (now, d["part_id"]))
        return ctx.result("job" if d["order_id"] else "part", ctx.target, "stock_movement", mid)
    if a in {"qc.profile_create", "qc.profile_update"}:
        if a.endswith("create"):
            profile_id = insert(db, "qc_profiles", id=uid(), label=d["label"])
            version = 1
        else:
            profile_id = d["profile_id"]
            profile = one(db, "SELECT * FROM qc_profiles WHERE id=?", (profile_id,))
            revision(ctx.expected, "profile_rev", profile["profile_rev"])
            version = profile["profile_rev"] + 1
            db.execute("UPDATE qc_profiles SET profile_rev=? WHERE id=?", (version, profile_id))
        id_ = insert(db, "qc_profile_versions", id=uid(), profile_id=profile_id, version=version,
                     created_at=now, created_by_staff_id=ctx.actor["id"], reason_code=d["reason"])
        require(len({item["label"] for item in d["checks"]}) == len(d["checks"]) and any(item["required"] for item in d["checks"]))
        for ordinal, check in enumerate(d["checks"]):
            insert(db, "qc_profile_checks", id=uid(), profile_version_id=id_, ordinal=ordinal,
                   label=check["label"], required=int(check["required"]))
        return ctx.result("qc_profile", profile_id, "qc_profile", profile_id)
    if a == "work.task_create":
        require(d["job_id"] == ctx.target, "NOT_FOUND")
        require(planning_scope(db, ctx.target, d["scope_ref"]) == d["scope_kind"], "NOT_FOUND")
        require(not one(db, "SELECT 1 FROM work_tasks WHERE job_id=? AND scope_ref=?",
                        (ctx.target, d["scope_ref"]), required=False), "POLICY_CONFLICT")
        eligible_staff(db, d["assigned_staff_id"], {"technician"})
        require(one(db, "SELECT 1 FROM job_assignments WHERE job_id=? AND staff_id=?",
                    (ctx.target, d["assigned_staff_id"]), required=False) is not None)
        profile = one(db, "SELECT v.id FROM qc_profile_versions v JOIN qc_profiles p ON p.id=v.profile_id "
                      "WHERE v.version=p.profile_rev ORDER BY v.created_at DESC,v.rowid DESC LIMIT 1", required=False)
        require(d["scope_kind"] == "diagnostic" or profile is not None, "QC_INCOMPLETE")
        id_ = insert(db, "work_tasks", id=uid(), job_id=ctx.target, scope_ref=d["scope_ref"], scope_kind=d["scope_kind"],
                     assigned_staff_id=d["assigned_staff_id"], summary=d["summary"], state="not_started",
                     profile_version_id=profile["id"] if profile and d["scope_kind"] == "estimate_line" else None, created_at=now)
        if profile and d["scope_kind"] == "estimate_line":
            initialize_checklist(ctx, id_, profile["id"])
        return ctx.result("job", ctx.target, "work_task", id_)
    task = one(db, "SELECT * FROM work_tasks WHERE id=? AND job_id=?", (d["task_id"], ctx.target))
    if a == "work.task_reassign":
        require(task["state"] in {"not_started", "paused", "rework"}, "TASK_NOT_REASSIGNABLE")
        eligible_staff(db, d["assigned_staff_id"], {"technician"})
        one(db, "SELECT 1 FROM job_assignments WHERE job_id=? AND staff_id=?", (ctx.target, d["assigned_staff_id"]))
        db.execute("UPDATE work_tasks SET assigned_staff_id=?,task_rev=task_rev+1 WHERE id=?",
                   (d["assigned_staff_id"], task["id"]))
        return ctx.result("job", ctx.target, "work_task", task["id"])
    require(task["assigned_staff_id"] == ctx.actor["id"], "FORBIDDEN")
    if a == "work.start":
        require(d["scope_ref"] == task["scope_ref"], "NOT_FOUND")
        task_start(ctx, task, d["consume"])
        id_ = insert(db, "work_logs", id=uid(), task_id=task["id"], kind="start", summary="Scoped work started",
                     actor_staff_id=ctx.actor["id"], recorded_at=now)
        return ctx.result("job", ctx.target, "work_log", id_)
    if a == "work.consume":
        scope_allowed(db, ctx.job, task["scope_ref"], task["scope_kind"])
        require(task["state"] == "active" and not ctx.job["safe_stop_required"], "REVISION_HOLD")
        consumed = consumptions(ctx, task, d["items"])
        vector(ctx, {r["part_id"] for r, _ in consumed})
        for r, qty in consumed:
            consume(ctx, task, r, qty)
        return ctx.result("job", ctx.target, "work_task", task["id"])
    if a == "work.log":
        if d["kind"] == "resume":
            # The frozen work.log contract has job/gate revisions, not a stock
            # vector. Recheck allocation and every scope gate under this writer
            # transaction; resume does not consume or reserve any inventory.
            task_start(ctx, task, [], resume=True)
        elif d["kind"] in {"pause", "complete"}:
            require(task["state"] == "active")
            if d["kind"] == "complete":
                require(not ctx.job["safe_stop_required"], "REVISION_HOLD")
                scope_allowed(db, ctx.job, task["scope_ref"], task["scope_kind"])
            else:
                require(d["owner_staff_id"] and d["next_action"] and d["next_action_due_at"])
                eligible_staff(db, d["owner_staff_id"])
            db.execute("UPDATE work_tasks SET state=?,completed_at=?,task_rev=task_rev+1 WHERE id=?",
                       ("paused" if d["kind"] == "pause" else "completed", now if d["kind"] == "complete" else None, task["id"]))
        else:
            require(task["state"] in {"active", "paused"})
        id_ = insert(db, "work_logs", id=uid(), task_id=task["id"], kind=d["kind"], summary=d["summary"],
                     owner_staff_id=d["owner_staff_id"], next_action=d["next_action"], next_action_due_at=d["next_action_due_at"],
                     actor_staff_id=ctx.actor["id"], recorded_at=now)
        if d["kind"] == "pause":
            db.execute("UPDATE jobs SET owner_staff_id=?,owner_queue=NULL,next_action=?,next_action_due_at=?,"
                       "safe_stop_required=CASE WHEN EXISTS(SELECT 1 FROM work_tasks WHERE job_id=? AND state='active') THEN 1 ELSE 0 END WHERE id=?",
                       (d["owner_staff_id"], d["next_action"], d["next_action_due_at"], ctx.target, ctx.target))
        return ctx.result("job", ctx.target, "work_log", id_)
    if a == "qc.record":
        require(task["state"] == "completed" and task["profile_version_id"], "QC_INCOMPLETE")
        require(not task["rework_of_task_id"], "QC_INCOMPLETE")
        version = one(db, "SELECT * FROM qc_profile_versions WHERE id=?", (task["profile_version_id"],))
        require(d["profile_version"] == version["version"], "STALE_REVISION")
        checks = rows(db, "SELECT * FROM qc_profile_checks WHERE profile_version_id=?", (version["id"],))
        require({c["id"] for c in checks} == {c["check_id"] for c in d["checks"]} and len(checks) == len(d["checks"]), "NOT_FOUND")
        required = {c["id"] for c in checks if c["required"]}
        result_map = {c["check_id"]: c["result"] for c in d["checks"]}
        computed = "fail" if any(c["result"] == "fail" for c in d["checks"]) else (
            "not_tested" if any(result_map[id_] != "pass" for id_ in required) else "pass")
        require(d["overall"] == computed)
        # Do not allow re-testing a failure into success without completing its linked rework.
        rework = rows(db, "SELECT * FROM work_tasks WHERE rework_of_task_id=?", (task["id"],))
        require(all(t["state"] == "completed" for t in rework), "QC_INCOMPLETE")
        id_ = insert(db, "qc_runs", id=uid(), job_id=ctx.target, task_id=task["id"],
                     profile_version_id=version["id"], overall=computed, actor_staff_id=ctx.actor["id"],
                     recorded_at=now, reason_code=d["reason"])
        for check in d["checks"]:
            insert(db, "qc_results", id=uid(), run_id=id_, check_id=check["check_id"], result=check["result"], note=check["note"])
        if computed == "fail":
            rework_id = insert(db, "work_tasks", id=uid(), job_id=ctx.target, scope_ref=task["scope_ref"], scope_kind=task["scope_kind"],
                   assigned_staff_id=task["assigned_staff_id"], summary="Correct failed quality checks",
                   state="rework", profile_version_id=task["profile_version_id"], rework_of_task_id=task["id"], created_at=now)
            initialize_checklist(ctx, rework_id, task["profile_version_id"])
            from backend.repairs import QC_REWORK_FOLLOW_UP, follow_up
            follow_up(ctx, QC_REWORK_FOLLOW_UP)
        elif computed == "pass" and rework:
            from backend.repairs import complete_rework_follow_up
            complete_rework_follow_up(ctx)
        return ctx.result("job", ctx.target, "qc_run", id_)
    raise RuntimeError("STOCK_WORK_HANDLER_MISSING")
