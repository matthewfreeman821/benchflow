"""Real schema-7 legacy catalog recovery: migration, reads, gates and remediation."""
from __future__ import annotations

from contextlib import closing
import json
import shutil
import sqlite3

import pytest
from fastapi.testclient import TestClient

from config.settings import Settings
from backend.app import create_app
from backend.core import Rejected, account_actor, insert, later, one, uid
from backend.database import (
    MIGRATIONS_ROOT, _initialize_database, acquire_data_root_lock, connect,
    immediate_transaction, initialize_database, inspect_database, utc_now,
)
from backend.identity import issue_session
from backend.testing import fixture_account, run
from backend.credentials import new_secret


def _facts(settings):
    with closing(connect(settings)) as db:
        return tuple(db.iterdump())


def _seed_schema7(settings, migration_root):
    """Create records under the actual pre-M008 schema before migration."""
    with acquire_data_root_lock(settings):
        assert _initialize_database(settings, migration_root).schema_version == 7

    now = utc_now()
    ids = {key: uid() for key in (
        "customer", "other_customer", "device", "job", "permission", "draft_missing",
        "draft_null", "estimate", "line_missing", "line_null", "scope_missing",
        "scope_null", "part", "requirement_missing", "requirement_null",
        "reservation_missing", "reservation_null", "active_task", "paused_task",
        "not_started_task",
    )}
    with closing(connect(settings)) as db, immediate_transaction(db):
        manager = fixture_account(
            db, "staff", "Fictional manager", "fixture.manager", new_secret(), now, ["manager"],
        )
        technician = fixture_account(
            db, "staff", "Fictional technician", "fixture.technician", new_secret(), now, ["technician"],
        )
        customer_account = fixture_account(
            db, "customer", "Fictional customer", "fixture.customer", new_secret(), now,
        )
        other_account = fixture_account(
            db, "customer", "Fictional other customer", "fixture.other", new_secret(), now,
        )
        insert(db, "shop_policy", id=uid(), shop_timezone="America/New_York",
               estimate_window_days=7, diagnostic_fee_minor=0, currency="USD",
               tax_enabled=0, tax_label="No tax")
        for key, name in (("customer", "Fictional customer"),
                          ("other_customer", "Fictional other customer")):
            insert(db, "customers", id=ids[key], display_name=name, name_normalized=name.casefold(),
                   phone_value=None, phone_known=0, phone_normalized=None, email_value=None,
                   email_known=0, email_normalized=None, notes="Fictional recovery fixture.",
                   created_at=now, updated_at=now)
        insert(db, "customer_accounts", customer_id=ids["customer"], account_id=customer_account,
               verified_at=now, verified_by_staff_id=manager, verification_method="in_person")
        insert(db, "customer_accounts", customer_id=ids["other_customer"], account_id=other_account,
               verified_at=now, verified_by_staff_id=manager, verification_method="in_person")
        insert(db, "devices", id=ids["device"], customer_id=ids["customer"], make_value="Aster",
               make_known=1, model_value="N14", model_known=1, serial_value=None,
               serial_known=0, search_normalized="aster n14")
        insert(db, "jobs", id=ids["job"], job_code="BF-LEGACYREC1",
               customer_id=ids["customer"], device_id=ids["device"], lifecycle="open",
               job_rev=3, gate_rev=4, owner_staff_id=technician, owner_queue=None,
               next_action="Review historical estimate", next_action_due_at=later(now, days=1),
               reported_issue="Fictional repair issue.", condition_value="Fictional scuff",
               condition_known=1, accessories_known=1,
               testing_constraints_value="No contents requested.", testing_constraints_known=1,
               received_by_customer_name="Fictional customer", received_by_staff_id=manager,
               acknowledgement_version="receipt-v1", acknowledgement_method="in_person",
               acknowledgement_actor_id=manager, opened_at=now, updated_at=now)
        insert(db, "job_assignments", job_id=ids["job"], staff_id=technician)
        insert(db, "diagnostic_permissions", id=ids["permission"], job_id=ids["job"],
               previous_permission_id=None, state="granted", scope="Fictional inspection consent",
               fee_minor=0, currency="USD", method="in_person", attribution="Fictional authorization",
               actor_kind="staff", actor_id=manager, decided_at=now)

        missing_line = dict(
            kind="part", description="Fictional legacy cable A", quantity=2,
            unit_amount_minor=1200, scope_ref=ids["scope_missing"],
        )
        null_line = dict(
            kind="part", description="Fictional legacy cable B", quantity=1,
            unit_amount_minor=700, scope_ref=ids["scope_null"], part_id=None,
        )
        insert(db, "estimate_drafts", id=ids["draft_missing"], job_id=ids["job"],
               currency="USD", tax_minor=0, tax_label="No tax",
               lines_json=json.dumps([missing_line], separators=(",", ":")),
               updated_at=now, updated_by_staff_id=technician)
        insert(db, "estimate_drafts", id=ids["draft_null"], job_id=ids["job"],
               currency="USD", tax_minor=0, tax_label="No tax",
               lines_json=json.dumps([null_line], separators=(",", ":")),
               updated_at=now, updated_by_staff_id=technician)
        insert(db, "estimates", id=ids["estimate"], job_id=ids["job"], version=1,
               issued_at=now, expires_at=later(now, days=7), currency="USD",
               subtotal_minor=3100, tax_minor=0, total_minor=3100, tax_label="No tax",
               tax_enabled=0, content_hash="a" * 64, change_reason="Original historical issue",
               change_json="[]", issued_by_staff_id=manager)
        for ordinal, (line_id, scope, description, quantity, price) in enumerate((
            (ids["line_missing"], ids["scope_missing"], "Fictional legacy cable A", 2, 1200),
            (ids["line_null"], ids["scope_null"], "Fictional legacy cable B", 1, 700),
        )):
            # M007 has no part_id column: this is a genuine pre-M008 issued row.
            insert(db, "estimate_lines", id=line_id, estimate_id=ids["estimate"], ordinal=ordinal,
                   kind="part", description=description, quantity=quantity,
                   unit_amount_minor=price, line_amount_minor=quantity * price, scope_ref=scope)
        insert(db, "estimate_decisions", id=uid(), job_id=ids["job"], estimate_id=ids["estimate"],
               decision="approved", method="in_person", attribution="Fictional exact estimate approval",
               actor_kind="staff", actor_id=manager, decided_at=now, reason_code=None)
        insert(db, "parts", id=ids["part"], sku="FICT-EXISTING",
               description="Fictional stock; not evidence of historical mapping", unit="each",
               on_hand_qty=10, reserved_qty=3, stock_rev=3)
        for req_id, reservation_id, scope, quantity in (
            (ids["requirement_missing"], ids["reservation_missing"], ids["scope_missing"], 2),
            (ids["requirement_null"], ids["reservation_null"], ids["scope_null"], 1),
        ):
            insert(db, "part_requirements", id=req_id, job_id=ids["job"], part_id=ids["part"],
                   scope_ref=scope, qty=quantity, needed_by=None, created_at=now)
            insert(db, "reservations", id=reservation_id, job_id=ids["job"], part_id=ids["part"],
                   scope_ref=scope, qty=quantity, released_qty=0, consumed_qty=0,
                   created_at=now, created_by_staff_id=technician)
            insert(db, "stock_movements", id=uid(), part_id=ids["part"], job_id=ids["job"],
                   reservation_id=reservation_id, receipt_id=None, task_id=None, source_movement_id=None,
                   kind="reservation", on_hand_delta=0, reserved_delta=quantity,
                   reason_code="reservation", source_note=None, actor_staff_id=technician, occurred_at=now)
        for task_id, scope, state in (
            (ids["active_task"], ids["scope_missing"], "active"),
            (ids["paused_task"], ids["scope_null"], "paused"),
            (ids["not_started_task"], ids["scope_missing"], "not_started"),
        ):
            insert(db, "work_tasks", id=task_id, job_id=ids["job"], scope_ref=scope,
                   scope_kind="estimate_line", assigned_staff_id=technician,
                   summary="Fictional " + state + " legacy task", state=state,
                   profile_version_id=None, rework_of_task_id=None,
                   started_under_estimate_id=None, started_under_permission_id=None,
                   created_at=now, started_at=now if state != "not_started" else None,
                   completed_at=None)
        for task_id, kind in (
            (ids["active_task"], "start"), (ids["paused_task"], "start"), (ids["paused_task"], "pause"),
        ):
            is_pause = kind == "pause"
            insert(db, "work_logs", id=uid(), task_id=task_id, kind=kind,
                   summary="Recorded before migration", actor_staff_id=technician, recorded_at=now,
                   ended_at=None, owner_staff_id=manager if is_pause else None,
                   next_action="Review safely" if is_pause else None,
                   next_action_due_at=later(now, days=1) if is_pause else None)
    return ids, now, manager, technician, customer_account, other_account, missing_line, null_line


def test_schema7_unknown_part_migration_reads_gates_and_fresh_remediation(tmp_path):
    settings = Settings.for_test(tmp_path / "legacy-data")
    schema7_catalog = tmp_path / "schema7-migrations"
    schema7_catalog.mkdir()
    for migration in sorted(MIGRATIONS_ROOT.glob('*.sql'))[:7]:
        shutil.copyfile(migration, schema7_catalog / migration.name)
    ids, now, manager, technician, customer_account, other_account, missing_line, null_line = (
        _seed_schema7(settings, schema7_catalog)
    )

    with acquire_data_root_lock(settings):
        assert initialize_database(settings).schema_version == 9
    with acquire_data_root_lock(settings):
        inspection = inspect_database(settings)
        assert inspection.schema_version == inspection.latest_schema_version == 9
        assert inspection.pending_migration_versions == ()
        assert inspection.integrity_check == inspection.foreign_key_check == "ok"
    assert initialize_database(settings).schema_version == 9  # restart is stable

    with closing(connect(settings)) as db:
        issued_before = tuple(tuple(row) for row in db.execute(
            "SELECT id,estimate_id,ordinal,kind,description,quantity,unit_amount_minor,"
            "line_amount_minor,scope_ref,part_id FROM estimate_lines WHERE estimate_id=? ORDER BY ordinal",
            (ids["estimate"],),
        ))
        assert [row[-1] for row in issued_before] == [None, None]
        assert [row[7] for row in issued_before] == [2400, 700]
        stored_drafts_before = {
            row["id"]: row["lines_json"] for row in db.execute(
                "SELECT id,lines_json FROM estimate_drafts WHERE job_id=?", (ids["job"],),
            )
        }
        assert stored_drafts_before == {
            ids["draft_missing"]: json.dumps([missing_line], separators=(",", ":")),
            ids["draft_null"]: json.dumps([null_line], separators=(",", ":")),
        }
        assert one(db, "SELECT decision FROM estimate_decisions WHERE estimate_id=?",
                   (ids["estimate"],))["decision"] == "approved"
        assert one(db, "SELECT on_hand_qty,reserved_qty FROM parts WHERE id=?",
                   (ids["part"],)) == {"on_hand_qty": 10, "reserved_qty": 3}
        assert one(db, "SELECT count(*) n FROM reservations WHERE job_id=?",
                   (ids["job"],))["n"] == 2
        before_immutable_probe = tuple(db.iterdump())
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("UPDATE estimate_lines SET description='tampered' WHERE id=?", (ids["line_missing"],))
        db.rollback()
        assert tuple(db.iterdump()) == before_immutable_probe

        def rejects_without_mutation(action, data, expected=None):
            before = tuple(db.iterdump())
            with pytest.raises(Rejected, match="STOCK_SHORTAGE") as error:
                with immediate_transaction(db):
                    run(db, technician, action, data, now, ("job", ids["job"]), expected=expected)
            assert error.value.public_message == (
                "Historical catalog identity is not recorded. A new mapped estimate and "
                "approval are required before catalog-dependent work."
            )
            assert tuple(db.iterdump()) == before

        rejects_without_mutation("parts.demand", dict(
            part_id=ids["part"], job_id=ids["job"], qty=1,
            scope_ref=ids["scope_missing"], needed_by=None,
        ))
        rejects_without_mutation("parts.reserve", dict(
            part_id=ids["part"], job_id=ids["job"], qty=1,
            scope_ref=ids["scope_missing"], reason="manual_record",
        ))
        rejects_without_mutation("work.start", dict(
            task_id=ids["not_started_task"], scope_ref=ids["scope_missing"], consume=[],
        ), {f"stock_rev:{ids['part']}": 3})
        rejects_without_mutation("work.log", dict(
            task_id=ids["paused_task"], kind="resume", summary="Attempt resume",
            owner_staff_id=None, next_action=None, next_action_due_at=None,
        ))
        rejects_without_mutation("work.consume", dict(
            task_id=ids["active_task"], items=[dict(reservation_id=ids["reservation_missing"], qty=1)],
        ), {f"stock_rev:{ids['part']}": 3})
        rejects_without_mutation("work.log", dict(
            task_id=ids["active_task"], kind="complete", summary="Attempt completion",
            owner_staff_id=None, next_action=None, next_action_due_at=None,
        ))
        for draft_id in (ids["draft_missing"], ids["draft_null"]):
            before = tuple(db.iterdump())
            with pytest.raises(Rejected, match="VALIDATION_FAILED"):
                with immediate_transaction(db):
                    run(db, manager, "estimate.issue", dict(
                        draft_id=draft_id, expires_at=later(now, days=7),
                        change_reason="Do not issue retained unknown draft",
                    ), now, ("job", ids["job"]))
            assert tuple(db.iterdump()) == before

        # Preserve a safe state before issuing the replacement: the old
        # active task has an unknown scope, so explicitly pause it rather than
        # silently carry it onto the new estimate.
        with immediate_transaction(db):
            run(db, technician, "work.log", dict(
                task_id=ids["active_task"], kind="pause", summary="Pause unknown historical scope",
                owner_staff_id=manager, next_action="Review new mapped estimate",
                next_action_due_at=later(now, days=1),
            ), now, ("job", ids["job"]))
        staff_token, _ = issue_session(db, now, account_actor(db, manager))
        tech_token, tech_csrf = issue_session(db, now, account_actor(db, technician))
        customer_token, _ = issue_session(db, now, account_actor(db, customer_account))
        other_token, _ = issue_session(db, now, account_actor(db, other_account))

    app = create_app(settings)
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        client.cookies.set("bf_session", staff_token)
        staff_detail = client.get(f"/api/v1/staff/jobs/{ids['job']}")
        assert staff_detail.status_code == 200, staff_detail.text
        staff_body = staff_detail.json()
        assert len(staff_body["estimate_drafts"]) == 2
        for draft in staff_body["estimate_drafts"]:
            assert draft["lines"][0]["part_id"] is None
        assert staff_body["estimates"][0]["lines"][0]["part_id"] is None

        client.cookies.clear()
        client.cookies.set("bf_session", customer_token)
        customer_detail = client.get(f"/api/v1/me/jobs/{ids['job']}")
        assert customer_detail.status_code == 200, customer_detail.text
        customer_line = customer_detail.json()["current_estimate"]["lines"][0]
        assert customer_line["description"] == "Fictional legacy cable A"
        assert "part_id" not in customer_line
        customer_history = client.get("/api/v1/me/jobs")
        assert customer_history.status_code == 200
        assert any(item["job_id"] == ids["job"] for item in customer_history.json()["items"])

        client.cookies.clear()
        client.cookies.set("bf_session", other_token)
        assert client.get(f"/api/v1/me/jobs/{ids['job']}").status_code == 404

        # Fresh mapped remediation: a new draft/scope, issue, exact approval,
        # demand, reservation, and start. Every response must project the
        # retained old draft and immutable issued history successfully.
        client.cookies.clear()
        client.cookies.set("bf_session", tech_token)
        target = ("job", ids["job"])
        fresh_scope = uid()
        fresh_draft = dict(
            draft_id=None,
            lines=[dict(kind="part", description="Fictional mapped replacement",
                        quantity=2, unit_amount_minor=1500, part_id=ids["part"],
                        scope_ref=fresh_scope)],
            currency="USD", tax_minor=0, tax_label="No tax", tax_source="not_configured",
        )
        with closing(connect(settings)) as db, immediate_transaction(db):
            response = run(db, technician, "estimate.draft_save", fresh_draft, utc_now(), target)
        assert response["status"] == "applied"
        assert len(response["job"]["estimate_drafts"]) == 3
        assert all(line["part_id"] is None for draft in response["job"]["estimate_drafts"][:2]
                   for line in draft["lines"])

        with closing(connect(settings)) as db, immediate_transaction(db):
            response = run(db, manager, "estimate.issue", dict(
                draft_id=response["result"]["id"], expires_at=later(utc_now(), days=7),
                change_reason="Fresh mapped replacement scope",
            ), utc_now(), target)
        assert response["job"]["estimates"][-1]["lines"][0]["part_id"] == ids["part"]
        with closing(connect(settings)) as db, immediate_transaction(db):
            from backend.testing import approve
            response = approve(db, manager, ids["job"], utc_now())
        assert response["job"]["estimates"][-1]["decision"]["value"] == "approved"
        with closing(connect(settings)) as db, immediate_transaction(db):
            run(db, manager, "qc.profile_create", dict(
                label="Fictional mapped recovery checks",
                checks=[dict(label="Repair retested", required=True)],
                reason="manual_record",
            ), utc_now())
        with closing(connect(settings)) as db, immediate_transaction(db):
            task_id = run(db, technician, "work.task_create", dict(
                job_id=ids["job"], scope_ref=fresh_scope, scope_kind="estimate_line",
                assigned_staff_id=technician, summary="Fictional approved mapped repair",
            ), utc_now(), target)["result"]["id"]
        with closing(connect(settings)) as db, immediate_transaction(db):
            run(db, technician, "parts.demand", dict(
                part_id=ids["part"], job_id=ids["job"], qty=2,
                scope_ref=fresh_scope, needed_by=None,
            ), utc_now(), target)
        with closing(connect(settings)) as db, immediate_transaction(db):
            run(db, technician, "parts.reserve", dict(
                part_id=ids["part"], job_id=ids["job"], qty=2,
                scope_ref=fresh_scope, reason="manual_record",
            ), utc_now(), target)
        with closing(connect(settings)) as db:
            revision = one(db, "SELECT stock_rev FROM parts WHERE id=?", (ids["part"],))["stock_rev"]
        with closing(connect(settings)) as db, immediate_transaction(db):
            result = run(db, technician, "work.start", dict(
                task_id=task_id, scope_ref=fresh_scope, consume=[],
            ), utc_now(), target, expected={f"stock_rev:{ids['part']}": revision})
        assert result["job"]["work"][-1]["state"] == "active"
        assert result["job"]["estimates"][0]["lines"][0]["part_id"] is None
        assert [line["part_id"] for draft in result["job"]["estimate_drafts"][:2]
                for line in draft["lines"]] == [None, None]

    with closing(connect(settings)) as db:
        issued_after = tuple(tuple(row) for row in db.execute(
            "SELECT id,estimate_id,ordinal,kind,description,quantity,unit_amount_minor,"
            "line_amount_minor,scope_ref,part_id FROM estimate_lines WHERE estimate_id=? ORDER BY ordinal",
            (ids["estimate"],),
        ))
        stored_drafts_after = {
            row["id"]: row["lines_json"] for row in db.execute(
                "SELECT id,lines_json FROM estimate_drafts WHERE job_id=?", (ids["job"],),
            )
        }
        assert issued_after == issued_before
        assert all(stored_drafts_after[draft_id] == stored_drafts_before[draft_id]
                   for draft_id in (ids["draft_missing"], ids["draft_null"]))
        assert one(db, "SELECT count(*) n FROM work_logs WHERE task_id=? AND kind='start'",
                   (task_id,))["n"] == 1
