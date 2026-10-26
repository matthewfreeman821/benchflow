"""Fictional fixture support, never a production HTTP entry point.

Only rotate_demo is invoked by demo lifespan, already holding the root lock.
The exported integration fixture owns its own lock and refuses existing data.
"""
from __future__ import annotations

from contextlib import closing

from contracts.actions import EXPECTED_KEYS, parse_command
from contracts.auth import DemoCredentialsResponse
from contracts.runtime import ApprovalStartScenario
from backend import commands
from backend.core import account_actor, insert, later, one, require, rows, uid
from backend.credentials import hash_password, new_secret, normalize_login
from backend.customers import issue_code
from backend.database import acquire_data_root_lock, connect, immediate_transaction, initialize_database, utc_now
from backend.identity import revoke
from backend.repairs import current_estimate

UNKNOWN = dict(value=None, known=False)


def fixture_account(db, kind, name, login, password, now, roles=()):
    id_ = insert(db, "accounts", id=uid(), kind=kind, state="active", display_name=name, login_id=login,
                 login_normalized=normalize_login(login), password_hash=hash_password(password),
                 credential_state="usable", created_at=now, updated_at=now)
    for role in roles:
        insert(db, "staff_roles", account_id=id_, role=role)
    return id_


def run(db, actor_id, action, data, now, target=None, expected=None, *, demo_mode=True):
    """Construct exact current revisions, then invoke the real transactional service."""
    # Legacy test callers use this fixture builder, never the HTTP parser.
    data = dict(data)
    if action == "diagnostic.permission":
        data.setdefault("fee_source", "configured_policy")
    if action == "estimate.draft_save":
        data.setdefault("tax_source", "not_configured" if data["tax_minor"] == 0 else "manual_shop_entry")
        data["lines"] = [dict(part_id=None, **line) if "part_id" not in line else dict(line) for line in data["lines"]]
    if action == "settlement.charge":
        data.setdefault("source", "estimate_line")
        data.setdefault("permission_id", None)
    exp = {}
    keys = EXPECTED_KEYS[action]
    if target and target[0] == "job":
        job = one(db, "SELECT * FROM jobs WHERE id=?", (target[1],))
        exp.update({key: job[key] for key in ("job_rev", "gate_rev") if key in keys})
        if "estimate_version" in keys:
            e = current_estimate(db, target[1])
            exp["estimate_version"] = e["version"] if e else None
            if (action == "work.task_create" and data["scope_kind"] == "diagnostic") or (
                action == "settlement.charge" and data["source"] == "diagnostic_fee"):
                exp["estimate_version"] = None
    for key, table, id_key in (("task_rev", "work_tasks", "task_id"),
                               ("disposition_rev", "job_dispositions", "disposition_id")):
        if key in keys:
            exp[key] = one(db, f"SELECT {key} FROM {table} WHERE id=?", (data[id_key],))[key]
    if "customer_rev" in keys and not (expected and "customer_rev" in expected):
        customer_id = target[1] if target and target[0] == "customer" else data["customer_id"]
        exp["customer_rev"] = one(db, "SELECT customer_rev FROM customers WHERE id=?", (customer_id,))["customer_rev"]
    if "stock_rev" in keys:
        part_id = data.get("part_id")
        if action == "parts.release":
            part_id = one(db, "SELECT part_id FROM reservations WHERE id=?", (data["reservation_id"],))["part_id"]
        exp["stock_rev"] = one(db, "SELECT stock_rev FROM parts WHERE id=?", (part_id,))["stock_rev"]
    if expected:
        exp.update(expected)
    payload = dict(command_id=uid(), action=action, expected=exp, data=data)
    if target:
        payload["target"] = dict(type=target[0], id=target[1])
    return commands.execute(db, parse_command(payload), account_actor(db, actor_id), now, demo_mode=demo_mode)


def new_customer(db, actor, name, now):
    return run(db, actor, "customer.create", dict(display_name=name, phone=UNKNOWN, email=UNKNOWN,
                                               notes="Fictional demonstration record."), now)["aggregate"]["id"]


def scenario(db, manager, technician, customer_id, now, *, label="Display cable", price=12000):
    created = run(db, manager, "job.create", dict(customer_id=customer_id,
        device=dict(make=dict(value="Aster", known=True), model=dict(value="N14", known=True), serial=UNKNOWN),
        reported_issue=label + " needs inspection.", condition=dict(value="Fictional corner scuff", known=True),
        accessories=dict(known=True, items=["charger"]), testing_constraints=dict(value="No device contents requested.", known=True),
        received_by_customer_name="Fictional customer", acknowledgement=dict(text_version="receipt-v1", method="in_person"),
        owner_staff_id=technician, next_action="Review findings and exact estimate", next_action_due_at=later(now, days=1)), now, ("customer", customer_id))
    job_id = created["aggregate"]["id"]
    target = ("job", job_id)
    run(db, manager, "diagnostic.permission", dict(decision="grant", scope="Non-invasive inspection", fee=dict(currency="USD", amount_minor=0),
        method="in_person", attribution="Fictional verified customer", expected_decision_id=None), now, target)
    run(db, technician, "diagnostic.record", dict(task_id=None, kind="finding", summary=label + " fault reproduced",
        customer_text=label + " can be replaced.", customer_visible=True, uncertainty="Intermittent fault may require retesting"), now, target)
    scope = uid()
    draft = run(db, technician, "estimate.draft_save", dict(draft_id=None,
        lines=[dict(kind="labor", description="Replace and test " + label.lower(), quantity=1, unit_amount_minor=price, scope_ref=scope)],
        currency="USD", tax_minor=0, tax_label="Tax not configured"), now, target)["result"]["id"]
    run(db, manager, "estimate.issue", dict(draft_id=draft, expires_at=later(now, days=7), change_reason="Initial proposal"), now, target)
    task_id = run(db, technician, "work.task_create", dict(job_id=job_id, scope_ref=scope, scope_kind="estimate_line",
        assigned_staff_id=technician, summary="Replace and test within approved scope"), now, target)["result"]["id"]
    return job_id, task_id, scope


def approve(db, manager, job_id, now):
    e = current_estimate(db, job_id)
    return run(db, manager, "estimate.decision", dict(decision="approve", version=e["version"], method="phone",
        attribution="Fictional customer confirmed exact version", confirmation=dict(version=e["version"],
            total_minor=e["total_minor"], currency=e["currency"], expires_at=e["expires_at"])), now, ("job", job_id))


def seed_approval_start_scenario(settings) -> ApprovalStartScenario:
    customer_password, technician_password = new_secret(), new_secret()
    with acquire_data_root_lock(settings):
        initialize_database(settings)
        with closing(connect(settings)) as db, immediate_transaction(db):
            require(not one(db, "SELECT 1 FROM accounts", required=False))
            now = utc_now()
            insert(db, "shop_policy", id=uid(), shop_timezone="America/New_York", estimate_window_days=7,
                   diagnostic_fee_minor=0, currency="USD", tax_enabled=0, tax_label="Tax not configured")
            manager = fixture_account(db, "staff", "Fictional manager", "fixture.manager", new_secret(), now, ["manager"])
            technician = fixture_account(db, "staff", "Fictional technician", "fixture.technician", technician_password, now, ["technician"])
            customer_id = new_customer(db, manager, "Fictional customer A", now)
            customer = fixture_account(db, "customer", "Fictional customer A", "fixture.customer", customer_password, now)
            insert(db, "customer_accounts", customer_id=customer_id, account_id=customer, verified_at=now,
                   verified_by_staff_id=manager, verification_method="in_person")
            run(db, manager, "qc.profile_create", dict(label="Fictional functional checks",
                checks=[dict(label="Reported issue retested", required=True), dict(label="Reassembly checked", required=True)],
                reason="manual_record"), now)
            job_id, task_id, scope = scenario(db, manager, technician, customer_id, now)
            e = current_estimate(db, job_id)
            j = one(db, "SELECT * FROM jobs WHERE id=?", (job_id,))
            return ApprovalStartScenario(
                customer_login_id="fixture.customer", customer_password=customer_password,
                technician_login_id="fixture.technician", technician_password=technician_password,
                job_id=job_id, task_id=task_id, scope_ref=scope, estimate_version=e["version"],
                total_minor=e["total_minor"], currency="USD", expires_at=e["expires_at"],
                job_rev=j["job_rev"], gate_rev=j["gate_rev"], stock_revisions={})


def rotate_demo(settings):
    """Caller holds root lock; refuses to convert a non-demo identity database."""
    require(settings.demo_mode)
    now = utc_now()
    output = []
    with closing(connect(settings)) as db, immediate_transaction(db):
        marker = one(db, "SELECT value FROM runtime_metadata WHERE key='fictional_demo'", required=False)
        if not marker:
            require(not one(db, "SELECT 1 FROM accounts", required=False))
            insert(db, "runtime_metadata", key="fictional_demo", value="1")
            specs = [
                ("staff.manager", "Ava (fictional)", "manager.ava", ["manager"]),
                ("staff.intake", "Jules (fictional)", "intake.jules", ["intake"]),
                ("staff.technician", "Ren (fictional)", "tech.ren", ["technician"]),
                ("staff.dual", "Casey (fictional)", "dual.casey", ["intake", "technician"]),
            ]
            for key, name, login, roles in specs:
                id_ = fixture_account(db, "staff", name, login, new_secret(), now, roles)
                insert(db, "demo_accounts", account_key=key, account_id=id_)
            manager = one(db, "SELECT account_id FROM demo_accounts WHERE account_key='staff.manager'")["account_id"]
            for key, name in (("customer.a", "Mira (fictional)"), ("customer.b", "Tao (fictional)")):
                c = new_customer(db, manager, name, now)
                id_ = fixture_account(db, "customer", name, key, new_secret(), now)
                insert(db, "customer_accounts", customer_id=c, account_id=id_, verified_at=now,
                       verified_by_staff_id=manager, verification_method="in_person")
                insert(db, "demo_accounts", account_key=key, account_id=id_)
            invited_customer = new_customer(db, manager, "Inez (fictional invited)", now)
            invitation = run(db, manager, "account.invite_customer", dict(customer_id=invited_customer, verification_method="in_person",
                contact_handoff_note=None, owner_staff_id=manager, next_action_due_at=later(now, hours=48)), now)
            insert(db, "demo_accounts", account_key="customer.invited", account_id=invitation["aggregate"]["id"])
            _demo_stories(db, manager, now)
        else:
            require(marker["value"] == "1")
            manager = one(db, "SELECT account_id FROM demo_accounts WHERE account_key='staff.manager'")["account_id"]
        order = ["staff.manager", "staff.intake", "staff.technician", "staff.dual", "customer.a", "customer.b"]
        for key in order:
            id_ = one(db, "SELECT account_id FROM demo_accounts WHERE account_key=?", (key,))["account_id"]
            account = account_actor(db, id_)
            require(account["state"] == "active")
            password = new_secret()
            revoke(db, id_, "recovered")
            db.execute("UPDATE accounts SET password_hash=?,credential_state='usable' WHERE id=?", (hash_password(password), id_))
            output.append(dict(account_key=key, status="active", login_id=account["login_id"], password=password))
        invited_id = one(db, "SELECT account_id FROM demo_accounts WHERE account_key='customer.invited'")["account_id"]
        invited = account_actor(db, invited_id)
        # Preserve activated/closed/suspended personas and their history. The
        # fictional invitation showcase moves to a new customer, never resets
        # an existing activated identity or relinks its jobs.
        if invited["state"] != "invited":
            customer_id = new_customer(db, manager, "Inez (new fictional invitation)", now)
            response = run(db, manager, "account.invite_customer", dict(customer_id=customer_id,
                verification_method="in_person", contact_handoff_note=None, owner_staff_id=manager,
                next_action_due_at=later(now, hours=48)), now)
            invited_id = response["aggregate"]["id"]
            db.execute("UPDATE demo_accounts SET account_id=? WHERE account_key='customer.invited'", (invited_id,))
            invited = account_actor(db, invited_id)
        _, code = issue_code(db, account_actor(db, manager), invited, now, purpose="invite", owner=manager, due=later(now, hours=48))
        result = dict(accounts=output, activation=dict(account_key="customer.invited", status="invited", login_id=None,
            code=code["value"], label="NOT SENT — activate locally; no message was sent"))
        return DemoCredentialsResponse.model_validate(result).model_dump(mode="json")


def _demo_stories(db, manager, now):
    technician = one(db, "SELECT account_id FROM demo_accounts WHERE account_key='staff.technician'")["account_id"]
    customers = [r["customer_id"] for r in rows(db, "SELECT ca.customer_id FROM customer_accounts ca JOIN demo_accounts d "
        "ON d.account_id=ca.account_id WHERE d.account_key IN ('customer.a','customer.b') ORDER BY d.account_key")]
    run(db, manager, "qc.profile_create", dict(label="Demo functional profile", checks=[
        dict(label="Reported symptom retested", required=True), dict(label="Reassembly inspected", required=True)],
        reason="manual_record"), now)
    for index, label in enumerate(["Display cable", "Battery revision", "Keyboard shortage", "Cooling QC rework", "No-response cancellation", "Collected repair recurrence"]):
        job_id, task, scope = scenario(db, manager, technician, customers[index % 2], now, label=label, price=8000 + index * 1000)
        target = ("job", job_id)
        if index != 4:
            approve(db, manager, job_id, now)
        if index == 1:
            run(db, technician, "work.start", dict(task_id=task, scope_ref=scope, consume=[]), now, target)
            draft = run(db, technician, "estimate.draft_save", dict(draft_id=None, lines=[dict(kind="labor",
                description="Revised battery replacement", quantity=1, unit_amount_minor=11000, scope_ref=uid())],
                currency="USD", tax_minor=0, tax_label="Tax not configured"), now, target)["result"]["id"]
            run(db, manager, "estimate.issue", dict(draft_id=draft, expires_at=later(now, days=7), change_reason="Material revised scope"), now, target)
            run(db, technician, "work.log", dict(task_id=task, kind="pause", summary="Documented safe stop",
                owner_staff_id=technician, next_action="Obtain fresh approval and order replacement", next_action_due_at=later(now, days=1)), now, target)
        if index == 2:
            part = run(db, manager, "parts.create", dict(sku="DEMO-KEYBOARD", description="Fictional keyboard", unit="each",
                initial_qty=0, reason="manual_record"), now)["aggregate"]["id"]
            run(db, technician, "parts.demand", dict(part_id=part, job_id=job_id, qty=1, scope_ref=scope, needed_by=None), now, target)
            run(db, manager, "parts.manual_order", dict(part_id=part, job_id=job_id, qty=1, supplier_label="Fictional parts desk",
                eta_at=None, eta_evidence=None, placement_state="planned", reason="manual_record"), now, target)
        if index in {3, 5}:
            run(db, technician, "work.start", dict(task_id=task, scope_ref=scope, consume=[]), now, target)
            run(db, technician, "work.log", dict(task_id=task, kind="complete", summary="Authorized repair completed",
                owner_staff_id=None, next_action=None, next_action_due_at=None), now, target)
            profile = one(db, "SELECT profile_version_id FROM work_tasks WHERE id=?", (task,))["profile_version_id"]
            checks = rows(db, "SELECT id FROM qc_profile_checks WHERE profile_version_id=?", (profile,))
            run(db, technician, "qc.record", dict(task_id=task, profile_version=1,
                checks=[dict(check_id=c["id"], result="fail" if index == 3 else "pass", note="Fictional observed test") for c in checks],
                overall="fail" if index == 3 else "pass", reason="manual_record"), now, target)
        if index == 4:
            run(db, manager, "job.record_contact_attempt", dict(channel="phone", outcome="no_answer",
                summary="Manual fictional call; no message delivery claimed", occurred_at=None), now, target)
            run(db, manager, "job.cancel", dict(requested_by="customer", requester_attribution="Fictional phone request",
                reason="customer_request"), now, target)
        if index == 5:
            e = current_estimate(db, job_id)
            line = one(db, "SELECT id FROM estimate_lines WHERE estimate_id=?", (e["id"],))["id"]
            run(db, manager, "settlement.charge", dict(job_id=job_id, estimate_version=1, line_ref=line,
                amount_minor=e["total_minor"], currency="USD", reason="manual_record"), now, target)
            run(db, manager, "settlement.payment_simulate", dict(job_id=job_id, amount_minor=e["total_minor"],
                currency="USD"), now, target)
            run(db, manager, "property.handover_simulate", dict(job_id=job_id, collector_name="Fictional Tao"),
                now, target)
            run(db, manager, "return.create", dict(original_job_id=job_id, reported_recurrence="Intermittent symptom recurred",
                customer_attribution="Fictional Tao"), now, target)
    no_account = new_customer(db, manager, "Nori (fictional, no online account)", now)
    scenario(db, manager, technician, no_account, now, label="No-account service desk path")
    suspended_customer = new_customer(db, manager, "Sol (fictional suspended account)", now)
    suspended = fixture_account(db, "customer", "Sol (fictional suspended)", "customer.suspended", new_secret(), now)
    insert(db, "customer_accounts", customer_id=suspended_customer, account_id=suspended, verified_at=now,
           verified_by_staff_id=manager, verification_method="in_person")
    scenario(db, manager, technician, suspended_customer, now, label="Suspended account staff-recorded path")
    run(db, manager, "account.suspend", dict(account_id=suspended, reason="suspension"), now, expected=dict(account_rev=1))
