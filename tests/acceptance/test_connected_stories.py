"""Connected acceptance probes through the real ASGI app and isolated SQLite.

Fixtures use the project's real account/scenario builders; all story mutations
under test cross the HTTP command boundary. No external service or listener is
started. Recovery/restore operations remain Infra-owned.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import uuid

import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.core import Rejected, later, one, rows, uid
from backend.database import (
    acquire_data_root_lock,
    connect,
    immediate_transaction,
    initialize_database,
    utc_now,
)
from backend.repairs import current_estimate
from backend.testing import fixture_account, new_customer, run, scenario
from config.settings import CANONICAL_ORIGIN, Settings
from contracts.actions import EXPECTED_KEYS


MANAGER_LOGIN = "accept.manager"
MANAGER_PASSWORD = "fictional-manager-password-2026"
TECH_LOGIN = "accept.technician"
TECH_PASSWORD = "fictional-technician-password-2026"
CUSTOMER_A_LOGIN = "accept.customer-a"
CUSTOMER_A_PASSWORD = "fictional-customer-a-password-2026"
CUSTOMER_B_LOGIN = "accept.customer-b"
CUSTOMER_B_PASSWORD = "fictional-customer-b-password-2026"


def _headers(csrf: str) -> dict[str, str]:
    return {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Origin": CANONICAL_ORIGIN,
        "X-CSRF-Token": csrf,
        "X-Request-ID": str(uuid.uuid4()),
    }


def _csrf(client: TestClient) -> str:
    response = client.get("/api/v1/session", headers={"Accept": "application/json"})
    assert response.status_code == 200, response.text
    return response.json()["csrf_token"]


def _login(client: TestClient, login_id: str, password: str) -> str:
    # The application intentionally permits one account session per browser
    # profile; switching personas requires an explicit logout.
    logout_csrf = _csrf(client)
    logged_out = client.post(
        "/api/v1/auth/logout", json={}, headers=_headers(logout_csrf)
    )
    assert logged_out.status_code == 204, logged_out.text
    response = client.post(
        "/api/v1/auth/login",
        json={"login_id": login_id, "password": password},
        headers=_headers(_csrf(client)),
    )
    assert response.status_code == 200, response.text
    assert response.json()["demo_mode"] is False
    return response.json()["csrf_token"]


@pytest.fixture
def shop(tmp_path):
    """Create distinct fictional identities/jobs using existing real fixtures."""
    settings = Settings.for_test(tmp_path / "connected-shop")
    with acquire_data_root_lock(settings):
        initialize_database(settings)
        with closing(connect(settings)) as db, immediate_transaction(db):
            now = utc_now()
            db.execute(
                "INSERT INTO shop_policy "
                "(id,shop_timezone,estimate_window_days,diagnostic_fee_minor,currency,tax_enabled,tax_label) "
                "VALUES (?,?,?,?,?,?,?)",
                (uid(), "America/New_York", 7, 0, "USD", 0, "Tax not configured"),
            )
            manager = fixture_account(
                db, "staff", "Fictional acceptance manager", MANAGER_LOGIN,
                MANAGER_PASSWORD, now, ["manager"],
            )
            technician = fixture_account(
                db, "staff", "Fictional acceptance technician", TECH_LOGIN,
                TECH_PASSWORD, now, ["technician"],
            )
            customer_a = new_customer(db, manager, "Fictional acceptance customer A", now)
            customer_a_account = fixture_account(
                db, "customer", "Fictional acceptance customer A", CUSTOMER_A_LOGIN,
                CUSTOMER_A_PASSWORD, now,
            )
            db.execute(
                "INSERT INTO customer_accounts "
                "(customer_id,account_id,verified_at,verified_by_staff_id,verification_method) "
                "VALUES (?,?,?,?,?)",
                (customer_a, customer_a_account, now, manager, "in_person"),
            )
            customer_b = new_customer(db, manager, "Fictional acceptance customer B", now)
            customer_b_account = fixture_account(
                db, "customer", "Fictional acceptance customer B", CUSTOMER_B_LOGIN,
                CUSTOMER_B_PASSWORD, now,
            )
            db.execute(
                "INSERT INTO customer_accounts "
                "(customer_id,account_id,verified_at,verified_by_staff_id,verification_method) "
                "VALUES (?,?,?,?,?)",
                (customer_b, customer_b_account, now, manager, "in_person"),
            )
            run(
                db, manager, "qc.profile_create",
                {
                    "label": "Fictional acceptance QC profile",
                    "checks": [
                        {"label": "Reported symptom retested", "required": True},
                        {"label": "Reassembly checked", "required": True},
                    ],
                    "reason": "manual_record",
                },
                now,
            )
            part_id = run(
                db, manager, "parts.create",
                {"sku": "ACCEPT-MAPPED-PART", "description": "Fictional mapped repair part",
                 "unit": "each", "initial_qty": 0, "reason": "manual_record"},
                now,
            )["aggregate"]["id"]
            job_a, task_a, scope_a = _mapped_scenario(
                db, manager, technician, customer_a, part_id, now,
                label="Acceptance display repair", price=12000,
            )
            job_b, task_b, scope_b = _mapped_scenario(
                db, manager, technician, customer_b, part_id, now,
                label="Acceptance keyboard repair", price=9000,
            )
    app = create_app(settings)
    client = TestClient(
        app, base_url=CANONICAL_ORIGIN, client=("127.0.0.1", 42173)
    )
    client.__enter__()
    record = {
        "settings": settings,
        "app": app,
        "client": client,
        "client_open": True,
        "manager": manager,
        "technician": technician,
        "customer_a": customer_a,
        "customer_b": customer_b,
        "job_a": job_a,
        "task_a": task_a,
        "scope_a": scope_a,
        "job_b": job_b,
        "task_b": task_b,
        "scope_b": scope_b,
        "part_id": part_id,
    }
    try:
        yield record
    finally:
        if record["client_open"]:
            client.__exit__(None, None, None)


def _mapped_scenario(db, manager, technician, customer_id, part_id, now, *, label, price):
    """Use real command handlers for a new, correctly mapped part estimate."""
    job_id, _, _ = scenario(
        db, manager, technician, customer_id, now, label=label, price=price
    )
    scope_ref = uid()
    draft_id = run(
        db, technician, "estimate.draft_save",
        {
            "draft_id": None,
            "lines": [
                {"kind": "part", "description": "Fictional mapped replacement part", "quantity": 1,
                 "unit_amount_minor": price, "part_id": part_id, "scope_ref": scope_ref},
            ],
            "currency": "USD", "tax_minor": 0, "tax_label": "Tax not configured",
            "tax_source": "not_configured",
        },
        now, ("job", job_id),
    )["result"]["id"]
    run(
        db, manager, "estimate.issue",
        {"draft_id": draft_id, "expires_at": later(now, days=7),
         "change_reason": "Initial mapped fictional repair scope"},
        now, ("job", job_id),
    )
    task_id = run(
        db, technician, "work.task_create",
        {"job_id": job_id, "scope_ref": scope_ref, "scope_kind": "estimate_line",
         "assigned_staff_id": technician, "summary": f"Fictional mapped repair: {label}"},
        now, ("job", job_id),
    )["result"]["id"]
    run(
        db, technician, "parts.demand",
        {"part_id": part_id, "job_id": job_id, "qty": 1,
         "scope_ref": scope_ref, "needed_by": None},
        now, ("job", job_id),
    )
    return job_id, task_id, scope_ref


def _close_client(shop):
    if shop["client_open"]:
        shop["client"].__exit__(None, None, None)
        shop["client_open"] = False


def _reservation(shop, part_id: str, job_id: str) -> str:
    with closing(connect(shop["settings"])) as db:
        return one(
            db, "SELECT id FROM reservations WHERE part_id=? AND job_id=? "
            "AND released_qty+consumed_qty<qty",
            (part_id, job_id),
        )["id"]


def _expected(shop, action: str, data: dict, target: tuple[str, str] | None):
    keys = EXPECTED_KEYS[action]
    expected: dict[str, object] = {}
    with closing(connect(shop["settings"])) as db:
        if target and target[0] == "job":
            job = one(db, "SELECT * FROM jobs WHERE id=?", (target[1],))
            for key in ("job_rev", "gate_rev"):
                if key in keys:
                    expected[key] = job[key]
            if "estimate_version" in keys:
                estimate = current_estimate(db, target[1])
                expected["estimate_version"] = estimate["version"] if estimate else None
                if action == "settlement.charge" and data["source"] == "diagnostic_fee":
                    expected["estimate_version"] = None
        if "customer_rev" in keys:
            if action == "contact.change_resolve":
                request_row = one(
                    db, "SELECT r.request_rev,c.customer_rev FROM contact_change_requests r "
                    "JOIN customers c ON c.id=r.customer_id WHERE r.id=?",
                    (data["request_id"],),
                )
                expected["customer_rev"] = request_row["customer_rev"]
            else:
                customer_id = (
                    target[1] if target and target[0] == "customer"
                    else data.get("customer_id")
                )
                expected["customer_rev"] = one(
                    db, "SELECT customer_rev FROM customers WHERE id=?", (customer_id,)
                )["customer_rev"]
        if "request_rev" in keys:
            expected["request_rev"] = one(
                db, "SELECT request_rev FROM contact_change_requests WHERE id=?",
                (data["request_id"],),
            )["request_rev"]
        if action in {"work.start", "work.consume"}:
            consumed_items = data.get("consume", data.get("items", []))
            for item in consumed_items:
                reservation = one(
                    db, "SELECT part_id FROM reservations WHERE id=?",
                    (item["reservation_id"],),
                )
                part_id = reservation["part_id"]
                expected[f"stock_rev:{part_id}"] = one(
                    db, "SELECT stock_rev FROM parts WHERE id=?", (part_id,)
                )["stock_rev"]
        if "task_rev" in keys:
            expected["task_rev"] = one(
                db, "SELECT task_rev FROM work_tasks WHERE id=?", (data["task_id"],)
            )["task_rev"]
        if "disposition_rev" in keys:
            expected["disposition_rev"] = one(
                db, "SELECT disposition_rev FROM job_dispositions WHERE id=?",
                (data["disposition_id"],),
            )["disposition_rev"]
        if "stock_rev" in keys:
            part_id = data.get("part_id")
            if action == "parts.release":
                part_id = one(
                    db, "SELECT part_id FROM reservations WHERE id=?", (data["reservation_id"],)
                )["part_id"]
            expected["stock_rev"] = one(
                db, "SELECT stock_rev FROM parts WHERE id=?", (part_id,)
            )["stock_rev"]
        for key in keys:
            if key.startswith("stock_rev:"):
                part_id = key.removeprefix("stock_rev:")
                expected[key] = one(
                    db, "SELECT stock_rev FROM parts WHERE id=?", (part_id,)
                )["stock_rev"]
    return expected


def _send(
    shop, csrf: str, action: str, data: dict,
    target: tuple[str, str] | None = None, *, expected: dict | None = None,
):
    envelope = {
        "command_id": str(uuid.uuid4()),
        "action": action,
        "expected": _expected(shop, action, data, target) if expected is None else expected,
        "data": data,
    }
    if target is not None:
        envelope["target"] = {"type": target[0], "id": target[1]}
    response = shop["client"].post(
        "/api/v1/commands", json=envelope, headers=_headers(csrf)
    )
    return response


def _applied(response):
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["status"] == "applied", result
    return result


def _approve_customer(shop, job_id: str, customer_login=CUSTOMER_A_LOGIN,
                      password=CUSTOMER_A_PASSWORD):
    client = shop["client"]
    csrf = _login(client, customer_login, password)
    with closing(connect(shop["settings"])) as db:
        estimate = current_estimate(db, job_id)
    response = _send(
        shop, csrf, "estimate.decision",
        {
            "decision": "approve",
            "version": estimate["version"],
            "method": "customer_account",
            "attribution": None,
            "confirmation": {
                "version": estimate["version"],
                "total_minor": estimate["total_minor"],
                "currency": estimate["currency"],
                "expires_at": estimate["expires_at"],
            },
        },
        ("job", job_id),
    )
    return _applied(response), csrf


def _staff_detail(shop, csrf: str, job_id: str):
    response = shop["client"].get(
        f"/api/v1/staff/jobs/{job_id}", headers={"Accept": "application/json"}
    )
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.integration
def test_story_01_intake_consent_stock_repair_qc_settlement_handover_restart(shop):
    """API intake through authorized repair, fictional settlement and app restart."""
    client = shop["client"]
    manager_csrf = _login(client, MANAGER_LOGIN, MANAGER_PASSWORD)
    created = _applied(_send(
        shop, manager_csrf, "job.create",
        {
            "customer_id": shop["customer_a"],
            "device": {
                "make": {"value": "Fictional Aster", "known": True},
                "model": {"value": "N14", "known": True},
                "serial": {"value": None, "known": False},
            },
            "reported_issue": "Fictional display intermittently dark",
            "condition": {"value": "Fictional corner scuff", "known": True},
            "accessories": {"known": True, "items": ["charger"]},
            "testing_constraints": {"value": "No device contents requested", "known": True},
            "received_by_customer_name": "Fictional customer A",
            "acknowledgement": {"text_version": "acceptance-receipt-v1", "method": "in_person"},
            "owner_staff_id": shop["technician"],
            "next_action": "Inspect reported display symptom",
            "next_action_due_at": later(utc_now(), days=1),
        },
        ("customer", shop["customer_a"]),
    ))
    story_job = created["aggregate"]["id"]
    _applied(_send(
        shop, manager_csrf, "diagnostic.permission",
        {"decision": "grant", "scope": "Non-invasive fictional inspection",
         "fee": {"currency": "USD", "amount_minor": 0},
         "fee_source": "configured_policy", "method": "in_person",
         "attribution": "Fictional customer A", "expected_decision_id": None},
        ("job", story_job),
    ))
    technician_csrf = _login(client, TECH_LOGIN, TECH_PASSWORD)
    _applied(_send(
        shop, technician_csrf, "diagnostic.record",
        {"task_id": None, "kind": "finding",
         "summary": "Fictional display connection fault reproduced",
         "customer_text": "A replacement part is proposed",
         "customer_visible": True, "uncertainty": "Intermittent fault may recur"},
        ("job", story_job),
    ))
    story_scope = uid()
    draft_id = _applied(_send(
        shop, technician_csrf, "estimate.draft_save",
        {"draft_id": None,
         "lines": [{"kind": "part", "description": "Fictional mapped display cable",
                    "quantity": 1, "unit_amount_minor": 12000,
                    "part_id": shop["part_id"], "scope_ref": story_scope}],
         "currency": "USD", "tax_minor": 0, "tax_label": "Tax not configured",
         "tax_source": "not_configured"},
        ("job", story_job),
    ))["result"]["id"]
    manager_csrf = _login(client, MANAGER_LOGIN, MANAGER_PASSWORD)
    _applied(_send(
        shop, manager_csrf, "estimate.issue",
        {"draft_id": draft_id, "expires_at": later(utc_now(), days=7),
         "change_reason": "Initial fictional repair proposal"},
        ("job", story_job),
    ))
    technician_csrf = _login(client, TECH_LOGIN, TECH_PASSWORD)
    story_task = _applied(_send(
        shop, technician_csrf, "work.task_create",
        {"job_id": story_job, "scope_ref": story_scope, "scope_kind": "estimate_line",
         "assigned_staff_id": shop["technician"],
         "summary": "Replace and verify fictional display cable"},
        ("job", story_job),
    ))["result"]["id"]
    _applied(_send(
        shop, technician_csrf, "parts.demand",
        {"part_id": shop["part_id"], "job_id": story_job, "qty": 1,
         "scope_ref": story_scope, "needed_by": None},
        ("job", story_job),
    ))
    _approve_customer(shop, story_job)

    manager_csrf = _login(client, MANAGER_LOGIN, MANAGER_PASSWORD)
    part_id = shop["part_id"]
    order_id = _applied(_send(
        shop, manager_csrf, "parts.manual_order",
        {"part_id": part_id, "job_id": story_job, "qty": 1,
         "supplier_label": "Fictional local supplier", "eta_at": None,
         "eta_evidence": None, "placement_state": "planned", "reason": "manual_record"},
        ("job", story_job),
    ))["result"]["id"]
    _applied(_send(
        shop, manager_csrf, "parts.receive",
        {"order_id": order_id, "part_id": part_id, "qty": 1,
         "source_note": "Fictional manual receipt; no supplier confirmation claimed"},
        ("job", story_job),
    ))
    _applied(_send(
        shop, _login(client, MANAGER_LOGIN, MANAGER_PASSWORD), "parts.reserve",
        {"part_id": part_id, "job_id": story_job, "qty": 1,
         "scope_ref": story_scope, "reason": None},
        ("job", story_job),
    ))
    technician_csrf = _login(client, TECH_LOGIN, TECH_PASSWORD)
    _applied(_send(
        shop, technician_csrf, "work.start",
        {"task_id": story_task, "scope_ref": story_scope,
         "consume": [{"reservation_id": _reservation(shop, part_id, story_job), "qty": 1}]},
        ("job", story_job),
    ))
    _applied(_send(
        shop, technician_csrf, "work.log",
        {"task_id": story_task, "kind": "complete",
         "summary": "Fictional authorized repair completed",
         "owner_staff_id": None, "next_action": None, "next_action_due_at": None},
        ("job", story_job),
    ))
    with closing(connect(shop["settings"])) as db:
        checks = rows(
            db, "SELECT c.id FROM qc_profile_checks c JOIN work_tasks t "
            "ON t.profile_version_id=c.profile_version_id WHERE t.id=? ORDER BY c.id",
            (story_task,),
        )
    _applied(_send(
        shop, technician_csrf, "qc.record",
        {"task_id": story_task, "profile_version": 1,
         "checks": [{"check_id": c["id"], "result": "pass", "note": "Fictional observed test"}
                    for c in checks],
         "overall": "pass", "reason": "manual_record"},
        ("job", story_job),
    ))
    manager_csrf = _login(client, MANAGER_LOGIN, MANAGER_PASSWORD)
    with closing(connect(shop["settings"])) as db:
        estimate = current_estimate(db, story_job)
        line = one(db, "SELECT id FROM estimate_lines WHERE estimate_id=?", (estimate["id"],))
    _applied(_send(
        shop, manager_csrf, "settlement.charge",
        {"job_id": story_job, "source": "estimate_line",
         "estimate_version": estimate["version"], "line_ref": line["id"],
         "permission_id": None, "amount_minor": estimate["total_minor"],
         "currency": estimate["currency"], "reason": "manual_record"},
        ("job", story_job),
    ))
    with closing(connect(shop["settings"])) as db, immediate_transaction(db):
        with pytest.raises(Rejected):
           run(db, shop["manager"], "settlement.payment_record",
               {"job_id": story_job, "amount_minor": estimate["total_minor"],
                "currency": estimate["currency"], "method": "cash", "reference": None, "received_at": None},
               utc_now(), ("job", story_job))
        assert run(db, shop["manager"], "settlement.payment_simulate",
                  {"job_id": story_job, "amount_minor": estimate["total_minor"], "currency": estimate["currency"]},
                  utc_now(), ("job", story_job))["status"] == "applied"
        with pytest.raises(Rejected):
           run(db, shop["manager"], "property.authorize_collector",
               {"job_id": story_job, "collector_name": "Fictional customer A", "method": "in_person", "reason": None},
               utc_now(), ("job", story_job))
        with pytest.raises(Rejected):
           run(db, shop["manager"], "property.handover",
               {"job_id": story_job, "handover_kind": "completed_repair",
                "collector_name": "Fictional customer A", "acknowledgement": "Fictional acknowledgement",
                "manager_balance_override_reason": None},
               utc_now(), ("job", story_job))
        assert run(db, shop["manager"], "property.handover_simulate",
                  {"job_id": story_job, "collector_name": "Fictional customer A"},
                  utc_now(), ("job", story_job))["status"] == "applied"
    with closing(connect(shop["settings"])) as db:
        assert one(db, "SELECT on_hand_qty,reserved_qty FROM parts WHERE id=?", (part_id,)) == {
           "on_hand_qty": 0, "reserved_qty": 0
        }
        assert one(db, "SELECT count(*) n FROM settlement_entries WHERE job_id=? AND bucket='payment'",
                  (story_job,))["n"] == 0
        assert one(db, "SELECT count(*) n FROM simulated_payments WHERE job_id=?", (story_job,))["n"] == 1
        assert one(db, "SELECT count(*) n FROM handovers WHERE job_id=?", (story_job,))["n"] == 0
        assert one(db, "SELECT count(*) n FROM simulated_handovers WHERE job_id=?", (story_job,))["n"] == 1
        assert one(db, "SELECT count(*) n FROM audit_events WHERE aggregate_id=?", (story_job,))["n"] >= 8

    _close_client(shop)
    with TestClient(
        create_app(shop["settings"]), base_url=CANONICAL_ORIGIN,
        client=("127.0.0.1", 42174),
    ) as restarted:
        csrf = _login(restarted, MANAGER_LOGIN, MANAGER_PASSWORD)
        detail_response = restarted.get(
            f"/api/v1/staff/jobs/{story_job}", headers={"Accept": "application/json"}
        )
        assert detail_response.status_code == 200
        detail = detail_response.json()
        assert detail["property"]["state"] == "simulated_handover"
        assert detail["property"]["handover"]["simulated"] is True
        assert "no property was physically transferred" in detail["property"]["handover"]["acknowledgement"].lower()
        with closing(connect(shop["settings"])) as db:
            assert one(db, "SELECT lifecycle FROM jobs WHERE id=?", (story_job,))[
                "lifecycle"
            ] == "closed"
            handover = one(db, "SELECT collector_name FROM simulated_handovers WHERE job_id=?",
                           (story_job,))
            assert handover["collector_name"] == "Fictional customer A"
        customer_csrf = _login(restarted, CUSTOMER_A_LOGIN, CUSTOMER_A_PASSWORD)
        customer_detail = restarted.get(
            f"/api/v1/me/jobs/{story_job}", headers={"Accept": "application/json"}
        )
        assert customer_detail.status_code == 200, customer_detail.text
        assert customer_detail.json()["collection"]["handover"]["simulated"] is True
        assert "no property was physically transferred" in customer_detail.json()["collection"]["what_to_bring"].lower()
        assert customer_detail.json()["status"]["code"] == "demo_complete"
        assert "fictional demo only" in customer_detail.json()["status"]["explanation"].lower()
        history = restarted.get("/api/v1/me/jobs", headers={"Accept": "application/json"})
        assert history.status_code == 200
        assert story_job in {item["job_id"] for item in history.json()["items"]}
        assert csrf and customer_csrf


def test_simulated_settlement_and_handover_are_rejected_outside_demo_mode(shop):
    manager = _login(shop["client"], MANAGER_LOGIN, MANAGER_PASSWORD)
    payment = _send(
        shop, manager, "settlement.payment_simulate",
        {"job_id": shop["job_a"], "amount_minor": 1, "currency": "USD"},
        ("job", shop["job_a"]),
    )
    handover = _send(
        shop, manager, "property.handover_simulate",
        {"job_id": shop["job_a"], "collector_name": "Fictional customer A"},
        ("job", shop["job_a"]),
    )
    assert payment.status_code == handover.status_code == 403
    with closing(connect(shop["settings"])) as db:
        assert one(db, "SELECT count(*) n FROM simulated_payments WHERE job_id=?", (shop["job_a"],))["n"] == 0
        assert one(db, "SELECT count(*) n FROM simulated_handovers WHERE job_id=?", (shop["job_a"],))["n"] == 0


@pytest.mark.integration
def test_story_02_material_revision_stale_consent_and_independent_stock_hold(shop):
    client = shop["client"]
    _approve_customer(shop, shop["job_a"])
    tech = _login(client, TECH_LOGIN, TECH_PASSWORD)
    manager = _login(client, MANAGER_LOGIN, MANAGER_PASSWORD)
    _applied(_send(
        shop, manager, "parts.adjust",
        {"part_id": shop["part_id"], "delta_qty": 1, "reason": "stock_count",
         "source": "count", "source_movement_id": None, "inspection_note": None},
    ))
    reservation_id = _applied(_send(
        shop, manager, "parts.reserve",
        {"part_id": shop["part_id"], "job_id": shop["job_a"], "qty": 1,
         "scope_ref": shop["scope_a"], "reason": None},
        ("job", shop["job_a"]),
    ))["result"]["id"]
    tech = _login(client, TECH_LOGIN, TECH_PASSWORD)
    _applied(_send(
        shop, tech, "work.start",
        {"task_id": shop["task_a"], "scope_ref": shop["scope_a"],
         "consume": [{"reservation_id": reservation_id, "qty": 1}]},
        ("job", shop["job_a"]),
    ))
    stale_version = 1
    revised_scope = uid()
    draft = _applied(_send(
        shop, tech, "estimate.draft_save",
        {"draft_id": None,
         "lines": [{"kind": "part", "description": "Fictional mapped revised part",
                    "quantity": 1, "unit_amount_minor": 15000,
                    "part_id": shop["part_id"], "scope_ref": revised_scope}],
         "currency": "USD", "tax_minor": 0, "tax_label": "Tax not configured",
         "tax_source": "not_configured"},
        ("job", shop["job_a"]),
    ))["result"]["id"]
    expires = later(utc_now(), days=2)
    manager = _login(client, MANAGER_LOGIN, MANAGER_PASSWORD)
    _applied(_send(
        shop, manager, "estimate.issue",
        {"draft_id": draft, "expires_at": expires, "change_reason": "Material revised scope"},
        ("job", shop["job_a"]),
    ))
    tech = _login(client, TECH_LOGIN, TECH_PASSWORD)
    _applied(_send(
        shop, tech, "work.log",
        {"task_id": shop["task_a"], "kind": "pause",
         "summary": "Safe stop after revised scope issued",
         "owner_staff_id": shop["technician"], "next_action": "Obtain current consent",
         "next_action_due_at": later(utc_now(), days=1)},
        ("job", shop["job_a"]),
    ))
    new_task = _applied(_send(
        shop, tech, "work.task_create",
        {"job_id": shop["job_a"], "scope_ref": revised_scope, "scope_kind": "estimate_line",
         "assigned_staff_id": shop["technician"], "summary": "Fictional revised repair task"},
        ("job", shop["job_a"]),
    ))["result"]["id"]
    _applied(_send(
        shop, tech, "parts.demand",
        {"part_id": shop["part_id"], "job_id": shop["job_a"], "qty": 1,
         "scope_ref": revised_scope, "needed_by": None},
        ("job", shop["job_a"]),
    ))
    customer = _login(client, CUSTOMER_A_LOGIN, CUSTOMER_A_PASSWORD)
    with closing(connect(shop["settings"])) as db:
        current = current_estimate(db, shop["job_a"])
    stale = _send(
        shop, customer, "estimate.decision",
        {"decision": "approve", "version": stale_version, "method": "customer_account",
         "attribution": None,
         "confirmation": {"version": stale_version, "total_minor": 12000,
                          "currency": "USD", "expires_at": current["expires_at"]}},
        ("job", shop["job_a"]),
    )
    assert stale.status_code == 409, stale.text
    assert stale.json()["error"]["code"] == "ESTIMATE_STALE"
    _applied(_send(
        shop, customer, "estimate.decision",
        {"decision": "approve", "version": current["version"], "method": "customer_account",
         "attribution": None,
         "confirmation": {"version": current["version"], "total_minor": current["total_minor"],
                          "currency": current["currency"], "expires_at": current["expires_at"]}},
        ("job", shop["job_a"]),
    ))
    staff = _login(client, TECH_LOGIN, TECH_PASSWORD)
    before = _staff_detail(shop, staff, shop["job_a"])
    denied = _send(
        shop, staff, "work.start",
        {"task_id": new_task, "scope_ref": revised_scope, "consume": []},
        ("job", shop["job_a"]),
    )
    assert denied.status_code == 409, denied.text
    with closing(connect(shop["settings"])) as db:
        assert one(db, "SELECT state FROM work_tasks WHERE id=?", (shop["task_a"],))["state"] == "paused"
        assert one(db, "SELECT count(*) n FROM estimate_decisions WHERE job_id=? AND decision='approved'",
                   (shop["job_a"],))["n"] == 2
    assert before["summary"]["blockers"]


@pytest.mark.integration
def test_story_03_two_jobs_contend_for_last_stock_unit_over_http(shop):
    client = shop["client"]
    manager = _login(client, MANAGER_LOGIN, MANAGER_PASSWORD)
    tech = _login(client, TECH_LOGIN, TECH_PASSWORD)
    _approve_customer(shop, shop["job_a"])
    _approve_customer(shop, shop["job_b"], CUSTOMER_B_LOGIN, CUSTOMER_B_PASSWORD)
    manager = _login(client, MANAGER_LOGIN, MANAGER_PASSWORD)
    part = shop["part_id"]
    _applied(_send(
        shop, manager, "parts.adjust",
        {"part_id": part, "delta_qty": 1, "reason": "stock_count",
         "source": "count", "source_movement_id": None, "inspection_note": None},
    ))
    tech = _login(client, TECH_LOGIN, TECH_PASSWORD)

    # Freeze the identical observed stock revision before simultaneous requests.
    expected_stock = _expected(
        shop, "parts.reserve",
        {"part_id": part, "job_id": shop["job_a"], "qty": 1,
         "scope_ref": shop["scope_a"], "reason": None},
        ("job", shop["job_a"]),
    )["stock_rev"]

    def reserve(job_id, scope_ref):
        payload = {
            "command_id": str(uuid.uuid4()), "action": "parts.reserve",
            "expected": {
                **_expected(
                    shop, "parts.reserve",
                    {"part_id": part, "job_id": job_id, "qty": 1,
                     "scope_ref": scope_ref, "reason": None},
                    ("job", job_id),
                ),
                "stock_rev": expected_stock,
            },
            "target": {"type": "job", "id": job_id},
            "data": {"part_id": part, "job_id": job_id, "qty": 1,
                     "scope_ref": scope_ref, "reason": None},
        }
        return client.post("/api/v1/commands", json=payload, headers=_headers(tech))

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(
            lambda args: reserve(*args),
            ((shop["job_a"], shop["scope_a"]), (shop["job_b"], shop["scope_b"])),
        ))
    assert sum(response.status_code == 200 for response in responses) == 1, [
        response.text for response in responses
    ]
    assert all(response.status_code in (200, 409) for response in responses)
    with closing(connect(shop["settings"])) as db:
        stock = one(db, "SELECT on_hand_qty,reserved_qty,stock_rev FROM parts WHERE id=?", (part,))
        assert stock["on_hand_qty"] == 1 and stock["reserved_qty"] == 1
        assert one(db, "SELECT count(*) n FROM reservations WHERE part_id=? "
                       "AND released_qty+consumed_qty<qty", (part,))["n"] == 1
        loser = next(
            job for job in (shop["job_a"], shop["job_b"])
            if not one(db, "SELECT 1 FROM reservations WHERE job_id=? AND part_id=? "
                           "AND released_qty+consumed_qty<qty",
                       (job, part), required=False)
        )
    losing_scope = shop["scope_a"] if loser == shop["job_a"] else shop["scope_b"]
    retry = _send(
        shop, _login(client, TECH_LOGIN, TECH_PASSWORD), "parts.reserve",
        {"part_id": part, "job_id": loser, "qty": 1, "scope_ref": losing_scope, "reason": None},
        ("job", loser),
    )
    assert retry.status_code == 409 and retry.json()["error"]["code"] == "STOCK_SHORTAGE"
    with closing(connect(shop["settings"])) as db:
        assert one(db, "SELECT on_hand_qty,reserved_qty FROM parts WHERE id=?", (part,)) == {
            "on_hand_qty": 1, "reserved_qty": 1
        }


@pytest.mark.integration
def test_story_04_failed_qc_blocks_readiness_and_rework_preserves_history(shop):
    client = shop["client"]
    _approve_customer(shop, shop["job_a"])
    manager = _login(client, MANAGER_LOGIN, MANAGER_PASSWORD)
    _applied(_send(
        shop, manager, "parts.adjust",
        {"part_id": shop["part_id"], "delta_qty": 1, "reason": "stock_count",
         "source": "count", "source_movement_id": None, "inspection_note": None},
    ))
    reservation_id = _applied(_send(
        shop, manager, "parts.reserve",
        {"part_id": shop["part_id"], "job_id": shop["job_a"], "qty": 1,
         "scope_ref": shop["scope_a"], "reason": None},
        ("job", shop["job_a"]),
    ))["result"]["id"]
    tech = _login(client, TECH_LOGIN, TECH_PASSWORD)
    tech = _login(client, TECH_LOGIN, TECH_PASSWORD)
    for action, data in (
        ("work.start", {"task_id": shop["task_a"], "scope_ref": shop["scope_a"],
                        "consume": [{"reservation_id": reservation_id, "qty": 1}]}),
        ("work.log", {"task_id": shop["task_a"], "kind": "complete",
                      "summary": "Authorized repair complete",
                      "owner_staff_id": None, "next_action": None, "next_action_due_at": None}),
    ):
        _applied(_send(shop, tech, action, data, ("job", shop["job_a"])))
    with closing(connect(shop["settings"])) as db:
        checks = rows(
            db, "SELECT c.id FROM qc_profile_checks c JOIN work_tasks t "
            "ON t.profile_version_id=c.profile_version_id WHERE t.id=? ORDER BY c.id",
            (shop["task_a"],),
        )
    failed = _send(
        shop, tech, "qc.record",
        {"task_id": shop["task_a"], "profile_version": 1,
         "checks": [{"check_id": c["id"], "result": "fail", "note": "Fictional test failed"}
                    for c in checks],
         "overall": "fail", "reason": "manual_record"},
        ("job", shop["job_a"]),
    )
    _applied(failed)
    detail = _staff_detail(shop, tech, shop["job_a"])
    assert any(run["overall"] == "fail" for run in detail["qc_history"])
    with closing(connect(shop["settings"])) as db:
        rework = one(db, "SELECT * FROM work_tasks WHERE rework_of_task_id=?", (shop["task_a"],))
    detail = _staff_detail(shop, tech, shop["job_a"])
    assert next(task for task in detail["work"] if task["task_id"] == rework["id"])[
        "rework_of_task_id"
    ] == shop["task_a"]
    _applied(_send(
        shop, tech, "work.start",
        {"task_id": rework["id"], "scope_ref": shop["scope_a"], "consume": []},
        ("job", shop["job_a"]),
    ))
    _applied(_send(
        shop, tech, "work.log",
        {"task_id": rework["id"], "kind": "complete", "summary": "Fictional correction complete",
         "owner_staff_id": None, "next_action": None, "next_action_due_at": None},
        ("job", shop["job_a"]),
    ))
    correction_qc = _send(
        shop, tech, "qc.record",
        {"task_id": rework["id"], "profile_version": 1,
         "checks": [{"check_id": c["id"], "result": "pass", "note": "Fictional retest"}
                    for c in checks],
         "overall": "pass", "reason": "manual_record"},
        ("job", shop["job_a"]),
    )
    assert correction_qc.status_code == 409
    assert correction_qc.json()["error"]["code"] == "QC_INCOMPLETE"
    passed = _send(
        shop, tech, "qc.record",
        {"task_id": shop["task_a"], "profile_version": 1,
         "checks": [{"check_id": c["id"], "result": "pass", "note": "Fictional retest after linked rework"}
                    for c in checks],
         "overall": "pass", "reason": "manual_record"},
        ("job", shop["job_a"]),
    )
    _applied(passed)
    assert passed.json()["job"]["summary"]["qc_state"] == "passed"
    assert not any(b["code"] in {"QC_FAILED", "QC_INCOMPLETE"}
                   for b in passed.json()["job"]["summary"]["blockers"])
    with closing(connect(shop["settings"])) as db:
        runs = rows(db, "SELECT overall FROM qc_runs WHERE task_id=? ORDER BY rowid",
                    (shop["task_a"],))
        assert len(runs) >= 2 and "fail" in {run["overall"] for run in runs} and runs[-1]["overall"] == "pass"
        rework_runs = rows(db, "SELECT overall FROM qc_runs WHERE task_id=?", (rework["id"],))
        assert rework_runs and all(run["overall"] == "not_tested" for run in rework_runs)
        assert one(db, "SELECT rework_of_task_id FROM work_tasks WHERE id=?", (rework["id"],))[
            "rework_of_task_id"
        ] == shop["task_a"]


@pytest.mark.integration
def test_story_05_no_response_unrepairable_return_has_no_inferred_consent_or_fee(shop):
    client = shop["client"]
    manager = _login(client, MANAGER_LOGIN, MANAGER_PASSWORD)
    target = ("job", shop["job_a"])
    _applied(_send(
        shop, manager, "job.record_contact_attempt",
        {"channel": "phone", "outcome": "no_answer",
         "summary": "Fictional manual contact attempt; no delivery claimed",
         "occurred_at": None},
        target,
    ))
    tech = _login(client, TECH_LOGIN, TECH_PASSWORD)
    _applied(_send(
        shop, tech, "job.record_disposition",
        {"disposition": "unrepairable", "finding": "Fictional board fault not repairable",
         "owner_staff_id": shop["manager"], "next_action": "Arrange unrepaired return",
         "next_action_due_at": later(utc_now(), days=1), "reason": "manual_record"},
        target,
    ))
    manager = _login(client, MANAGER_LOGIN, MANAGER_PASSWORD)
    _applied(_send(
        shop, manager, "property.authorize_collector",
        {"job_id": shop["job_a"], "collector_name": "Fictional customer A",
         "method": "phone", "reason": None},
        target,
    ))
    _applied(_send(
        shop, manager, "property.handover",
        {"job_id": shop["job_a"], "handover_kind": "return_unrepaired",
         "collector_name": "Fictional customer A",
         "acknowledgement": "Fictional customer acknowledged unrepaired return",
         "manager_balance_override_reason": None},
        target,
    ))
    with closing(connect(shop["settings"])) as db:
        assert one(db, "SELECT count(*) n FROM estimate_decisions WHERE job_id=?",
                   (shop["job_a"],))["n"] == 0
        assert one(db, "SELECT count(*) n FROM settlement_entries WHERE job_id=?",
                   (shop["job_a"],))["n"] == 0
        assert one(db, "SELECT count(*) n FROM handovers WHERE job_id=? AND kind='return_unrepaired'",
                   (shop["job_a"],))["n"] == 1
        assert one(db, "SELECT count(*) n FROM contact_attempts WHERE job_id=?",
                   (shop["job_a"],))["n"] == 1
    tech = _login(client, TECH_LOGIN, TECH_PASSWORD)
    assert _staff_detail(shop, tech, shop["job_a"])["dispositions"]


@pytest.mark.integration
def test_story_06_account_money_contact_change_recurrence_and_restart(shop):
    client = shop["client"]
    a = _login(client, CUSTOMER_A_LOGIN, CUSTOMER_A_PASSWORD)
    own = client.get("/api/v1/me/jobs", headers={"Accept": "application/json"})
    assert own.status_code == 200
    own_ids = {item["job_id"] for item in own.json()["items"]}
    assert shop["job_a"] in own_ids and shop["job_b"] not in own_ids
    _logout = client.post(
        "/api/v1/auth/logout", json={}, headers=_headers(a)
    )
    assert _logout.status_code == 204
    b = _login(client, CUSTOMER_B_LOGIN, CUSTOMER_B_PASSWORD)
    foreign_id = shop["job_a"]
    same_request_id = str(uuid.uuid4())
    foreign = client.get(
        f"/api/v1/me/jobs/{foreign_id}",
        headers={"Accept": "application/json", "X-Request-ID": same_request_id},
    )
    missing = client.get(
        f"/api/v1/me/jobs/{uid()}",
        headers={"Accept": "application/json", "X-Request-ID": same_request_id},
    )
    assert foreign.status_code == missing.status_code == 404
    assert foreign.content == missing.content
    assert client.post(
        "/api/v1/auth/logout", json={}, headers=_headers(b)
    ).status_code == 204

    customer_a = _login(client, CUSTOMER_A_LOGIN, CUSTOMER_A_PASSWORD)
    _applied(_send(
        shop, customer_a, "contact.change_request",
        {"requested_fields": ["phone"], "display_name": {"value": None, "known": False},
         "phone": {"value": "555-0102", "known": True},
         "email": {"value": None, "known": False}},
    ))
    with closing(connect(shop["settings"])) as db:
        request = one(db, "SELECT * FROM contact_change_requests WHERE customer_id=? ORDER BY requested_at DESC",
                      (shop["customer_a"],))
        request_id = request["id"]
    manager = _login(client, MANAGER_LOGIN, MANAGER_PASSWORD)
    _applied(_send(
        shop, manager, "contact.change_resolve",
        {"request_id": request_id, "decision": "accept", "confirmation_method": "phone",
         "reason_code": "contact_resolution"},
    ))

    _approve_customer(shop, shop["job_a"])
    tech = _login(client, TECH_LOGIN, TECH_PASSWORD)
    detail = _staff_detail(shop, tech, shop["job_a"])
    manager = _login(client, MANAGER_LOGIN, MANAGER_PASSWORD)
    _applied(_send(
        shop, manager, "parts.adjust",
        {"part_id": shop["part_id"], "delta_qty": 1, "reason": "stock_count",
         "source": "count", "source_movement_id": None, "inspection_note": None},
    ))
    reservation_id = _applied(_send(
        shop, manager, "parts.reserve",
        {"part_id": shop["part_id"], "job_id": shop["job_a"], "qty": 1,
         "scope_ref": shop["scope_a"], "reason": None},
        ("job", shop["job_a"]),
    ))["result"]["id"]
    tech = _login(client, TECH_LOGIN, TECH_PASSWORD)
    _applied(_send(
        shop, tech, "work.start",
        {"task_id": shop["task_a"], "scope_ref": shop["scope_a"],
         "consume": [{"reservation_id": reservation_id, "qty": 1}]},
        ("job", shop["job_a"]),
    ))
    _applied(_send(
        shop, tech, "work.log",
        {"task_id": shop["task_a"], "kind": "complete", "summary": "Fictional completed repair",
         "owner_staff_id": None, "next_action": None, "next_action_due_at": None},
        ("job", shop["job_a"]),
    ))
    with closing(connect(shop["settings"])) as db:
        check_rows = rows(
            db, "SELECT c.id FROM qc_profile_checks c JOIN work_tasks t "
            "ON t.profile_version_id=c.profile_version_id WHERE t.id=? ORDER BY c.id",
            (shop["task_a"],),
        )
    _applied(_send(
        shop, tech, "qc.record",
        {"task_id": shop["task_a"], "profile_version": 1,
         "checks": [{"check_id": c["id"], "result": "pass", "note": "Fictional QC"}
                    for c in check_rows],
         "overall": "pass", "reason": "manual_record"},
        ("job", shop["job_a"]),
    ))
    manager = _login(client, MANAGER_LOGIN, MANAGER_PASSWORD)
    with closing(connect(shop["settings"])) as db:
        estimate = current_estimate(db, shop["job_a"])
        line = one(db, "SELECT id FROM estimate_lines WHERE estimate_id=?", (estimate["id"],))
    _applied(_send(
        shop, manager, "settlement.charge",
        {"job_id": shop["job_a"], "source": "estimate_line",
         "estimate_version": estimate["version"], "line_ref": line["id"],
         "permission_id": None, "amount_minor": estimate["total_minor"],
         "currency": estimate["currency"], "reason": "manual_record"},
        ("job", shop["job_a"]),
    ))
    payment = _applied(_send(
        shop, manager, "settlement.payment_record",
        {"job_id": shop["job_a"], "amount_minor": estimate["total_minor"] + 500,
         "currency": estimate["currency"], "method": "cash", "reference": None,
         "received_at": None},
        ("job", shop["job_a"]),
    ))
    payment_id = payment["result"]["id"]
    _applied(_send(
        shop, manager, "settlement.refund_record",
        {"job_id": shop["job_a"], "payment_id": payment_id, "amount_minor": 500,
         "currency": estimate["currency"], "reference": None, "refunded_at": None,
         "reason": "customer_request"},
        ("job", shop["job_a"]),
    ))
    too_much = _send(
        shop, manager, "settlement.refund_record",
        {"job_id": shop["job_a"], "payment_id": payment_id,
         "amount_minor": estimate["total_minor"] + 1,
         "currency": estimate["currency"], "reference": None, "refunded_at": None,
         "reason": "customer_request"},
        ("job", shop["job_a"]),
    )
    assert too_much.status_code == 409, too_much.text
    _applied(_send(
        shop, manager, "property.authorize_collector",
        {"job_id": shop["job_a"], "collector_name": "Fictional alternate collector",
         "method": "phone", "reason": None},
        ("job", shop["job_a"]),
    ))
    _applied(_send(
        shop, manager, "property.handover",
        {"job_id": shop["job_a"], "handover_kind": "completed_repair",
         "collector_name": "Fictional alternate collector",
         "acknowledgement": "Fictional alternate collector acknowledged handover",
         "manager_balance_override_reason": None},
        ("job", shop["job_a"]),
    ))
    recurrence = _applied(_send(
        shop, manager, "return.create",
        {"original_job_id": shop["job_a"], "reported_recurrence": "Fictional intermittent recurrence",
         "customer_attribution": "Fictional customer A"},
        ("job", shop["job_a"]),
    ))
    with closing(connect(shop["settings"])) as db:
        case = one(db, "SELECT * FROM return_cases WHERE id=?", (recurrence["result"]["id"],))
        assert case["original_job_id"] == shop["job_a"]
        assert one(db, "SELECT count(*) n FROM settlement_entries WHERE job_id=?",
                   (shop["job_a"],))["n"] == 3
        assert one(db, "SELECT phone_value FROM customers WHERE id=?", (shop["customer_a"],))[
            "phone_value"
        ] == "555-0102"
    # Reopen the same data root in a fresh app lifespan, preserving work and finance.
    _close_client(shop)
    with TestClient(
        create_app(shop["settings"]), base_url=CANONICAL_ORIGIN,
        client=("127.0.0.1", 42175),
    ) as restarted:
        _login(restarted, MANAGER_LOGIN, MANAGER_PASSWORD)
        persisted = restarted.get(
            f"/api/v1/staff/jobs/{shop['job_a']}", headers={"Accept": "application/json"}
        )
        assert persisted.status_code == 200
        with closing(connect(shop["settings"])) as db:
            case = one(db, "SELECT original_job_id,return_job_id FROM return_cases "
                           "WHERE original_job_id=?", (shop["job_a"],))
            assert case["original_job_id"] == shop["job_a"]
            assert one(db, "SELECT customer_id FROM jobs WHERE id=?", (case["return_job_id"],))[
                "customer_id"
            ] == shop["customer_a"]
            assert one(db, "PRAGMA integrity_check")["integrity_check"] == "ok"


@pytest.mark.integration
def test_api_expiry_boundary_rejects_equality_and_late_but_keeps_timely_scope(shop):
    """Server-clock API boundary: exact/late rejection and timely approval after expiry."""
    client = shop["client"]
    with closing(connect(shop["settings"])) as db:
        estimate_a = current_estimate(db, shop["job_a"])
        estimate_b = current_estimate(db, shop["job_b"])

    shop["app"].state.clock = lambda: estimate_a["expires_at"]
    customer_a = _login(client, CUSTOMER_A_LOGIN, CUSTOMER_A_PASSWORD)
    at_deadline = _send(
        shop, customer_a, "estimate.decision",
        {"decision": "approve", "version": estimate_a["version"],
         "method": "customer_account", "attribution": None,
         "confirmation": {"version": estimate_a["version"],
                          "total_minor": estimate_a["total_minor"],
                          "currency": estimate_a["currency"],
                          "expires_at": estimate_a["expires_at"]}},
        ("job", shop["job_a"]),
    )
    assert at_deadline.status_code == 409, at_deadline.text
    assert at_deadline.json()["error"]["code"] == "ESTIMATE_WINDOW_CLOSED"
    shop["app"].state.clock = lambda: later(estimate_a["expires_at"], seconds=1)
    after_deadline = _send(
        shop, customer_a, "estimate.decision",
        {"decision": "approve", "version": estimate_a["version"],
         "method": "customer_account", "attribution": None,
         "confirmation": {"version": estimate_a["version"],
                          "total_minor": estimate_a["total_minor"],
                          "currency": estimate_a["currency"],
                          "expires_at": estimate_a["expires_at"]}},
        ("job", shop["job_a"]),
    )
    assert after_deadline.status_code == 409, after_deadline.text
    assert after_deadline.json()["error"]["code"] == "ESTIMATE_WINDOW_CLOSED"
    with closing(connect(shop["settings"])) as db:
        assert one(db, "SELECT count(*) n FROM estimate_decisions WHERE job_id=?",
                   (shop["job_a"],))["n"] == 0

    shop["app"].state.clock = lambda: later(estimate_b["expires_at"], seconds=-60)
    customer_b = _login(client, CUSTOMER_B_LOGIN, CUSTOMER_B_PASSWORD)
    shop["app"].state.clock = lambda: later(estimate_b["expires_at"], seconds=-1)
    _applied(_send(
        shop, customer_b, "estimate.decision",
        {"decision": "approve", "version": estimate_b["version"],
         "method": "customer_account", "attribution": None,
         "confirmation": {"version": estimate_b["version"],
                          "total_minor": estimate_b["total_minor"],
                          "currency": estimate_b["currency"],
                          "expires_at": estimate_b["expires_at"]}},
        ("job", shop["job_b"]),
    ))
    shop["app"].state.clock = lambda: later(estimate_b["expires_at"], days=1)
    manager = _login(client, MANAGER_LOGIN, MANAGER_PASSWORD)
    _applied(_send(
        shop, manager, "parts.adjust",
        {"part_id": shop["part_id"], "delta_qty": 1, "reason": "stock_count",
         "source": "count", "source_movement_id": None, "inspection_note": None},
    ))
    reservation_id = _applied(_send(
        shop, manager, "parts.reserve",
        {"part_id": shop["part_id"], "job_id": shop["job_b"], "qty": 1,
         "scope_ref": shop["scope_b"], "reason": None},
        ("job", shop["job_b"]),
    ))["result"]["id"]
    tech = _login(client, TECH_LOGIN, TECH_PASSWORD)
    _applied(_send(
        shop, tech, "work.start",
        {"task_id": shop["task_b"], "scope_ref": shop["scope_b"],
         "consume": [{"reservation_id": reservation_id, "qty": 1}]},
        ("job", shop["job_b"]),
    ))
    with closing(connect(shop["settings"])) as db:
        assert one(db, "SELECT state FROM work_tasks WHERE id=?", (shop["task_b"],))["state"] == "active"
        assert one(db, "SELECT count(*) n FROM estimate_decisions WHERE job_id=? AND decision='approved'",
                   (shop["job_b"],))["n"] == 1


@pytest.mark.integration
def test_customer_collector_requires_explicit_authorization(shop):
    client=shop["client"]
    _login(client,CUSTOMER_A_LOGIN,CUSTOMER_A_PASSWORD)
    path=f"/api/v1/me/jobs/{shop['job_a']}"
    before=client.get(path,headers={"Accept":"application/json"})
    assert before.status_code==200
    assert before.json()["collection"]["authorized_collector"]=="No authorized collector recorded."
    manager=_login(client,MANAGER_LOGIN,MANAGER_PASSWORD)
    _applied(_send(shop,manager,"property.authorize_collector",{
        "job_id":shop["job_a"],"collector_name":"Explicit fictional alternate collector",
        "method":"phone","reason":None},("job",shop["job_a"])))
    _login(client,CUSTOMER_A_LOGIN,CUSTOMER_A_PASSWORD)
    after=client.get(path,headers={"Accept":"application/json"})
    assert after.status_code==200
    assert after.json()["collection"]["authorized_collector"]=="Explicit fictional alternate collector"
