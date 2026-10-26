"""Correction regressions: real SQLite transactions, no mocked readiness."""
from contextlib import closing
from dataclasses import replace
import json
import sqlite3
import shutil
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from config.settings import CANONICAL_ORIGIN, Settings
from contracts.actions import parse_command
from contracts.projections import BackupDetailView, BackupPage, QCProfileDetail
from backend import backups, commands, queries, views
from backend.app import create_app
from backend.core import Rejected, account_actor, insert, later, one, rows, uid
from backend.credentials import hash_password
from backend.database import acquire_data_root_lock, connect, immediate_transaction, inspect_database, utc_now
from backend.database import MIGRATIONS_ROOT, _initialize_database, initialize_database
from backend.tests.test_domains import shop as _domain_shop, act, approve_shop
from backend.tests.test_http_identity import headers, login
from backend.testing import approve, fixture_account, run, scenario


@pytest.fixture
def shop(tmp_path):
    result = _domain_shop.__wrapped__(tmp_path)
    return (replace(result[0], local_app_data_root=tmp_path / "local"), *result[1:])


def facts(settings):
    """All logical persistent facts, including logs/revisions/events/receipts."""
    with closing(connect(settings)) as db:
        return tuple(db.iterdump())


def fails_unchanged(shop, code, actor, action, data, target, **kwargs):
    before = facts(shop[0])
    with pytest.raises(Rejected, match=code):
        act(shop, actor, action, data, target, **kwargs)
    assert facts(shop[0]) == before


def part_scope(shop, part, *, job_id=None):
    job_id = job_id or shop[1].job_id
    target = ("job", job_id)
    scope = uid()
    draft = act(shop, shop[3], "estimate.draft_save", dict(draft_id=None, lines=[
        dict(kind="part", description="Externally sourced replacement", quantity=2,
             unit_amount_minor=500, scope_ref=scope, part_id=part)],
        currency="USD", tax_minor=0, tax_label="No tax", tax_source="not_configured"), target)["result"]["id"]
    act(shop, shop[2], "estimate.issue", dict(draft_id=draft, expires_at=later(utc_now(), days=7),
        change_reason="New part scope"), target)
    with closing(connect(shop[0])) as db, immediate_transaction(db):
        approve(db, shop[2], job_id, utc_now())
    task = act(shop, shop[3], "work.task_create", dict(job_id=job_id, scope_ref=scope,
        scope_kind="estimate_line", assigned_staff_id=shop[3], summary="Replace approved part"), target)["result"]["id"]
    return target, scope, task


def test_exact_part_readiness_no_demand_no_allocation_partial_external_receipt(shop):
    part = act(shop, shop[2], "parts.create", dict(sku="EXTERNAL", description="Catalog item", unit="each",
        initial_qty=0, reason="manual_record"))["aggregate"]["id"]
    target, scope, task = part_scope(shop, part)
    start = dict(task_id=task, scope_ref=scope, consume=[])
    fails_unchanged(shop, "STOCK_SHORTAGE", shop[3], "work.start", start, target)
    wrong = act(shop, shop[2], "parts.create", dict(sku="WRONG", description="Wrong item", unit="each",
        initial_qty=2, reason="manual_record"))["aggregate"]["id"]
    fails_unchanged(shop, "NOT_FOUND", shop[3], "parts.demand",
        dict(part_id=wrong, job_id=target[1], qty=2, scope_ref=scope, needed_by=None), target)
    requirement = act(shop, shop[3], "parts.demand",
        dict(part_id=part, job_id=target[1], qty=2, scope_ref=scope, needed_by=None), target)["result"]["id"]
    fails_unchanged(shop, "STOCK_SHORTAGE", shop[3], "work.start", start, target)
    act(shop, shop[2], "parts.receive", dict(order_id=None, part_id=part, qty=1,
        source_note="External supplier receipt A"), ("part", part))
    act(shop, shop[3], "parts.reserve", dict(part_id=part, job_id=target[1], qty=1, scope_ref=scope, reason=None), target)
    with closing(connect(shop[0])) as db:
        rev = one(db, "SELECT stock_rev FROM parts WHERE id=?", (part,))["stock_rev"]
    fails_unchanged(shop, "STOCK_SHORTAGE", shop[3], "work.start", start, target, expected={f"stock_rev:{part}":rev})
    act(shop, shop[2], "parts.receive", dict(order_id=None, part_id=part, qty=1,
        source_note="External supplier receipt B"), ("part", part))
    reservation = act(shop, shop[3], "parts.reserve", dict(part_id=part, job_id=target[1], qty=1,
        scope_ref=scope, reason=None), target)
    detail = reservation["job"]
    assert detail["parts"][0]["requirement_ids"] == [requirement]
    with closing(connect(shop[0])) as db:
        j = one(db, "SELECT * FROM jobs WHERE id=?", (target[1],))
        rev = one(db, "SELECT stock_rev FROM parts WHERE id=?", (part,))["stock_rev"]
    payload = dict(command_id=uid(), action="work.start", target=dict(type="job", id=target[1]), data=start,
        expected=dict(job_rev=j["job_rev"], gate_rev=j["gate_rev"], estimate_version=2, **{f"stock_rev:{part}":rev}))
    def execute():
        with closing(connect(shop[0])) as db, immediate_transaction(db):
            return commands.execute(db, parse_command(payload), account_actor(db, shop[3]), utc_now())
    first = execute()
    assert first["status"] == "applied"
    before = facts(shop[0])
    assert execute()["status"] == "replayed"
    assert facts(shop[0]) == before
    with closing(connect(shop[0])) as db:
        assert one(db, "SELECT count(*) n FROM work_logs WHERE task_id=? AND kind='start'", (task,))["n"] == 1
        assert one(db, "SELECT count(*) n FROM stock_movements WHERE part_id=? AND kind='receipt'", (part,))["n"] == 2


def test_dispositions_dispute_reassignment_permissions_and_revisions(shop):
    target = ("job", shop[1].job_id)
    follow = dict(owner_staff_id=shop[3], next_action="Arrange safe return", next_action_due_at=later(utc_now(), days=1),
                  reason="manual_record")
    fails_unchanged(shop, "FORBIDDEN", shop[2], "job.record_disposition",
        dict(disposition="no_fault_found", finding="No fault reproduced", **follow), target)
    recorded = act(shop, shop[3], "job.record_disposition",
        dict(disposition="no_fault_found", finding="No fault reproduced", **follow), target)
    assert recorded["job"]["dispositions"][0]["kind"] == "no_fault_found"
    assert not recorded["job"]["settlement"]["charges"] and recorded["job"]["lifecycle"] == "open"
    dispute = act(shop, shop[2], "job.open_dispute", dict(summary="Customer disputes finding", **follow), target)["result"]["id"]
    resolution = dict(disposition_id=dispute, resolution_summary="Agreed inspection review only", **follow)
    fails_unchanged(shop, "FORBIDDEN", shop[3], "job.resolve_dispute", resolution, target)
    fails_unchanged(shop, "STALE_REVISION", shop[2], "job.resolve_dispute", resolution, target,
                    expected=dict(disposition_rev=2))
    result = act(shop, shop[2], "job.resolve_dispute", resolution, target)
    assert result["job"]["dispositions"][-1]["disposition_rev"] == 2
    fails_unchanged(shop, "DISPOSITION_CONFLICT", shop[2], "job.resolve_dispute", resolution, target)
    reassignment = dict(task_id=shop[1].task_id, assigned_staff_id=shop[3], reason="manual_record")
    fails_unchanged(shop, "FORBIDDEN", shop[3], "work.task_reassign", reassignment, target)
    fails_unchanged(shop, "STALE_REVISION", shop[2], "work.task_reassign", reassignment, target, expected=dict(task_rev=9))
    reassigned = act(shop, shop[2], "work.task_reassign", reassignment, target)
    assert reassigned["job"]["work"][0]["task_rev"] == 2
    act(shop, shop[3], "job.record_disposition", dict(disposition="unrepairable", finding="Unsafe damage", **follow), target)


def test_task_reassignment_rejects_active_and_completed_and_scope_reuse(shop):
    approve_shop(shop)
    target = ("job", shop[1].job_id)
    task = shop[1].task_id
    act(shop, shop[3], "work.start", dict(task_id=task, scope_ref=shop[1].scope_ref, consume=[]), target)
    fails_unchanged(shop, "TASK_NOT_REASSIGNABLE", shop[2], "work.task_reassign",
                    dict(task_id=task, assigned_staff_id=shop[3], reason="manual_record"), target)
    act(shop, shop[3], "work.log", dict(task_id=task, kind="complete", summary="Completed unchanged scope",
        owner_staff_id=None, next_action=None, next_action_due_at=None), target)
    fails_unchanged(shop, "TASK_NOT_REASSIGNABLE", shop[2], "work.task_reassign",
                    dict(task_id=task, assigned_staff_id=shop[3], reason="manual_record"), target)
    fails_unchanged(shop, "POLICY_CONFLICT", shop[3], "work.task_create",
        dict(job_id=target[1], scope_ref=shop[1].scope_ref, scope_kind="estimate_line",
             assigned_staff_id=shop[3], summary="Must not repeat completed scope"), target)
    draft = act(shop, shop[3], "estimate.draft_save", dict(draft_id=None, lines=[
        dict(kind="labor", part_id=None, description="Different work with reused scope", quantity=1,
             unit_amount_minor=15000, scope_ref=shop[1].scope_ref)], currency="USD", tax_minor=0,
        tax_label="No tax", tax_source="not_configured"), target)["result"]["id"]
    fails_unchanged(shop, "POLICY_CONFLICT", shop[2], "estimate.issue",
        dict(draft_id=draft, expires_at=later(utc_now(), days=7), change_reason="Invalid scope reuse"), target)


def test_fee_tax_provenance_and_caps_survive_policy_edit_and_restart(shop):
    target = ("job", shop[1].job_id)
    with closing(connect(shop[0])) as db, immediate_transaction(db):
        db.execute("UPDATE shop_policy SET diagnostic_fee_minor=700,policy_rev=2,tax_enabled=1")
    old = act(shop, shop[2], "diagnostic.permission", dict(decision="request", scope="Extra inspection",
        fee=dict(currency="USD", amount_minor=700), method="phone", attribution=None,
        expected_decision_id=one_permission(shop)), target)["result"]["id"]
    with closing(connect(shop[0])) as db, immediate_transaction(db):
        db.execute("UPDATE shop_policy SET diagnostic_fee_minor=900,policy_rev=3")
    permission = act(shop, shop[2], "diagnostic.permission", dict(decision="grant", scope="Extra inspection",
        fee=dict(currency="USD", amount_minor=700), method="phone", attribution="Fictional customer",
        expected_decision_id=old), target)["result"]["id"]
    fee = dict(job_id=target[1], source="diagnostic_fee", estimate_version=None, line_ref=None,
        permission_id=permission, amount_minor=700, currency="USD", reason=None)
    charged = act(shop, shop[2], "settlement.charge", fee, target)
    assert charged["job"]["diagnostic"]["current"]["fee_policy_rev"] == 2
    assert charged["job"]["settlement"]["charges"][0]["diagnostic_permission_id"] == permission
    fails_unchanged(shop, "SCOPE_NOT_APPROVED", shop[2], "settlement.charge", fee, target)
    draft = act(shop, shop[3], "estimate.draft_save", dict(draft_id=None, lines=[
        dict(kind="labor", part_id=None, description="New scope", quantity=1, unit_amount_minor=1000, scope_ref=uid())],
        currency="USD", tax_minor=100, tax_label="Manual shop tax entry", tax_source="manual_shop_entry"), target)["result"]["id"]
    act(shop, shop[2], "estimate.issue", dict(draft_id=draft, expires_at=later(utc_now(), days=7), change_reason="Manual tax"), target)
    approve_shop(shop)
    tax = dict(job_id=target[1], source="estimate_tax", estimate_version=2, line_ref=None,
        permission_id=None, amount_minor=100, currency="USD", reason=None)
    charged = act(shop, shop[2], "settlement.charge", tax, target)
    assert charged["job"]["settlement"]["charges"][-1]["tax_label"] == "Manual shop tax entry"
    assert charged["job"]["estimates"][-1]["tax_policy_rev"] == 3
    fails_unchanged(shop, "SCOPE_NOT_APPROVED", shop[2], "settlement.charge", tax, target)
    with closing(connect(shop[0])) as db:
        assert views.settlement_view(db, target[1])["balance_minor"] == 800


def one_permission(shop):
    with closing(connect(shop[0])) as db:
        return one(db, "SELECT id FROM diagnostic_permissions WHERE job_id=? ORDER BY rowid DESC LIMIT 1", (shop[1].job_id,))["id"]


def test_configured_qc_checks_failure_rework_retest_and_history_reachable(shop):
    approve_shop(shop)
    target = ("job", shop[1].job_id)
    task = shop[1].task_id
    with closing(connect(shop[0])) as db:
        actor = account_actor(db, shop[3])
        detail = views.job_detail(db, actor, target[1], utc_now())
        initial = [q for q in detail["qc"] if q["task_id"] == task]
        assert len(initial) == 2 and all(q["state"] == "not_tested" for q in initial)
        assert len({q["run_id"] for q in initial}) == 1
        profile = one(db, "SELECT id FROM qc_profiles")
        manager = account_actor(db, shop[2])
        QCProfileDetail.model_validate(queries.read(db, manager, "staff/qc-profiles/" + profile["id"], {}, utc_now(), b"test"))
    act(shop, shop[2], "qc.profile_update", dict(profile_id=profile["id"],
        checks=[dict(label="Later profile check", required=True)], reason="manual_record"), expected=dict(profile_rev=1))
    with closing(connect(shop[0])) as db:
        history = queries.read(db, account_actor(db,shop[2]), "staff/qc-profiles/" + profile["id"], {}, utc_now(), b"test")
        assert [r["version"] for r in history["history"]] == [1,2]
        assert [r["active_for_new_tasks"] for r in history["history"]] == [False,True]
        assert all(r["profile_rev"] == 2 for r in history["history"])
        assert history["profile"] == history["history"][-1]
    act(shop, shop[3], "work.start", dict(task_id=task, scope_ref=shop[1].scope_ref, consume=[]), target)
    act(shop, shop[3], "work.log", dict(task_id=task, kind="complete", summary="Repair completed",
        owner_staff_id=None, next_action=None, next_action_due_at=None), target)
    checks = [dict(check_id=q["check_id"], result="fail" if i == 0 else "pass", note=None) for i,q in enumerate(initial)]
    fail = act(shop, shop[3], "qc.record", dict(task_id=task, profile_version=1, checks=checks,
        overall="fail", reason=None), target)
    failure_id = fail["result"]["id"]
    history = fail["job"]["qc_history"]
    assert any(r["run_id"] == failure_id and r["overall"] == "fail" for r in history)
    with closing(connect(shop[0])) as db:
        qc_follow_up = one(db, "SELECT * FROM follow_ups WHERE job_id=? AND next_action=?",
                           (target[1], "Complete linked rework and retest"))
        assert qc_follow_up["state"] == "due"
    with closing(connect(shop[0])) as db:
        rework = one(db, "SELECT id FROM work_tasks WHERE rework_of_task_id=?", (task,))["id"]
    checks = [dict(check_id=q["check_id"], result="pass", note="Retested after rework") for q in initial]
    fails_unchanged(shop, "QC_INCOMPLETE", shop[3], "qc.record",
        dict(task_id=task, profile_version=1, checks=checks, overall="pass", reason=None), target)
    with closing(connect(shop[0])) as db:
        assert one(db, "SELECT state FROM follow_ups WHERE id=?", (qc_follow_up["id"],))["state"] == "due"
    act(shop, shop[3], "work.start", dict(task_id=rework, scope_ref=shop[1].scope_ref, consume=[]), target)
    act(shop, shop[3], "work.log", dict(task_id=rework, kind="complete", summary="Corrected failed check",
        owner_staff_id=None, next_action=None, next_action_due_at=None), target)
    passed = act(shop, shop[3], "qc.record", dict(task_id=task, profile_version=1, checks=checks,
        overall="pass", reason=None), target)
    assert passed["job"]["summary"]["qc_state"] == "passed"
    assert any(r["run_id"] == failure_id and r["overall"] == "fail" for r in passed["job"]["qc_history"])
    with closing(connect(shop[0])) as db:
        resolved = one(db, "SELECT * FROM follow_ups WHERE id=?", (qc_follow_up["id"],))
        assert resolved["state"] == "resolved" and resolved["resolved_at"]
        detail = views.job_detail(db, account_actor(db, shop[3]), target[1], utc_now())
        collection_follow_up = [item for item in detail["follow_ups"]
                                if item["state"] == "due" and item["next_action"] == "Arrange collection for completed repair"]
        assert len(collection_follow_up) == 1
        queue_item = views.queue_item(db, one(db, "SELECT * FROM jobs WHERE id=?", (target[1],)), utc_now())
        assert queue_item["next_action"] == "Arrange collection for completed repair"
        assert "FOLLOW_UP_DUE" not in {blocker["code"] for blocker in queue_item["blockers"]}
        assert views.job_detail(db, account_actor(db, shop[3]), target[1], utc_now())["qc_history"] == passed["job"]["qc_history"]


def test_exact_part_two_jobs_last_unit_reservation_race(shop):
    part = act(shop, shop[2], "parts.create", dict(sku="EXACT-RACE", description="Catalog part", unit="each",
        initial_qty=2, reason="manual_record"))["aggregate"]["id"]
    target1, scope1, _ = part_scope(shop, part)
    with closing(connect(shop[0])) as db, immediate_transaction(db):
        job2, _, _ = scenario(db, shop[2], shop[3], shop[4], utc_now(), label="Another job")
    target2, scope2, _ = part_scope(shop, part, job_id=job2)
    for target, scope in [(target1,scope1),(target2,scope2)]:
        act(shop, shop[3], "parts.demand", dict(part_id=part,job_id=target[1],scope_ref=scope,qty=2,needed_by=None), target)
    with closing(connect(shop[0])) as db:
        stock_rev = one(db, "SELECT stock_rev FROM parts WHERE id=?", (part,))["stock_rev"]
    def reserve(item):
        target, scope = item
        try:
            act(shop, shop[3], "parts.reserve", dict(part_id=part,job_id=target[1],scope_ref=scope,qty=2,reason=None),
                target, expected=dict(stock_rev=stock_rev))
            return "applied"
        except Rejected as error:
            return str(error)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(reserve, [(target1,scope1),(target2,scope2)]))
    assert sorted(results) == ["STALE_REVISION","applied"]
    with closing(connect(shop[0])) as db:
        assert one(db, "SELECT reserved_qty FROM parts WHERE id=?", (part,))["reserved_qty"] == 2
        assert one(db, "SELECT count(*) n FROM reservations WHERE part_id=?", (part,))["n"] == 1


def test_backup_http_snapshot_replay_corruption_permissions_and_restart(shop):
    settings = shop[0]
    with closing(connect(settings)) as db, immediate_transaction(db):
        db.execute("UPDATE accounts SET password_hash=? WHERE id=?", (hash_password("Fictional-manager-password-123"), shop[2]))
    backup_id = uid()
    app = create_app(settings)
    with TestClient(app, base_url=CANONICAL_ORIGIN, client=("127.0.0.1", 43123)) as client:
        token = login(client, "fixture.manager", "Fictional-manager-password-123")
        response = client.post("/api/v1/staff/backups", json=dict(command_id=backup_id), headers=headers(token))
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "applied"
        assert client.post("/api/v1/staff/backups", json=dict(command_id=backup_id), headers=headers(token)).json()["status"] == "replayed"
        assert client.post("/api/v1/staff/backups", json=dict(command_id=uid(), path="unsafe"), headers=headers(token)).status_code == 422
        assert client.post("/api/v1/staff/backups", json=dict(command_id=uid()), headers=headers("wrong")).status_code == 403
        listing = BackupPage.model_validate(client.get("/api/v1/staff/backups").json())
        assert len(listing.items) == 1
        path = f"/api/v1/staff/backups/{backup_id}"
        detail = client.get(path)
        assert detail.status_code == 200, detail.text
        manifest = BackupDetailView.model_validate(detail.json()).manifest
        assert manifest.schema_version == 9
        download = client.get(path + "/download")
        assert download.status_code == 200 and len(download.content) == manifest.size_bytes
        assert download.headers["cache-control"] == "no-store"
        assert download.headers["content-type"] == "application/octet-stream"
        guide = client.get(path + "/restore-guide")
        assert guide.status_code == 200 and guide.json()["confirmation_phrase"] == f"RESTORE BENCHFLOW {backup_id}"
        assert client.post("/api/v1/auth/logout", json={}, headers=headers(token)).status_code == 204
        token = login(client, shop[1].technician_login_id, shop[1].technician_password)
        assert client.get(path).status_code == 403
        assert client.post("/api/v1/staff/backups", json=dict(command_id=uid()), headers=headers(token)).status_code == 403
    # Actual snapshot schema inspection and records after app root-lock release.
    bundle = settings.backup_root / backup_id
    assert inspect_database(settings.for_staged_root(bundle)).schema_version == 9
    with closing(sqlite3.connect(bundle / "benchflow.sqlite3")) as db:
        assert db.execute("SELECT count(*) FROM jobs").fetchone()[0] == 1
    with TestClient(create_app(settings), base_url=CANONICAL_ORIGIN, client=("127.0.0.1", 43123)) as client:
        token = login(client, "fixture.manager", "Fictional-manager-password-123")
        assert client.post("/api/v1/staff/backups", json=dict(command_id=backup_id), headers=headers(token)).json()["status"] == "replayed"
    with (bundle / "benchflow.sqlite3").open("ab") as handle:
        handle.write(b"corrupt")
    with pytest.raises(Rejected, match="BACKUP_INVALID"):
        backups.verify_bundle(settings, backup_id)
    with closing(connect(settings)) as db:
        assert one(db, "SELECT count(*) n FROM audit_events WHERE event_type='backup.created'")["n"] == 1


def test_backup_crash_after_publish_reconciles_same_actor_without_duplicate(shop):
    actor = None
    with closing(connect(shop[0])) as db:
        actor = account_actor(db, shop[2])
    with acquire_data_root_lock(shop[0]):
        backup_id = uid()
        now = utc_now()
        with closing(connect(shop[0])) as db, immediate_transaction(db):
            insert(db, "backup_operations", command_id=backup_id, actor_id=actor["id"], created_at=now, state="pending")
        backups.create_consistent_snapshot(shop[0], backup_id=backup_id, created_at=now)
        assert backups.create_for_actor(shop[0], actor, backup_id, now)["status"] == "applied"
        assert backups.create_for_actor(shop[0], actor, backup_id, now)["status"] == "replayed"
        bundle = shop[0].backup_root / backup_id
        removed = shop[0].backup_root / (backup_id + ".operator-preserved")
        bundle.rename(removed)
        with pytest.raises(Rejected, match="BACKUP_NOT_FOUND"):
            backups.create_for_actor(shop[0], actor, backup_id, now)
        assert not bundle.exists()  # no silent replacement of a completed snapshot
        removed.rename(bundle)
        with closing(connect(shop[0])) as db, immediate_transaction(db):
            other = fixture_account(db, "staff", "Other fictional manager", "other.manager", "Fictional-password-123",
                                    now, ["manager"])
            other_actor = account_actor(db, other)
            old_command = one(db,"SELECT command_id FROM idempotency_results LIMIT 1")["command_id"]
        with pytest.raises(Rejected, match="IDEMPOTENCY_KEY_REUSED"):
            backups.create_for_actor(shop[0], other_actor, backup_id, now)
        with pytest.raises(Rejected, match="IDEMPOTENCY_KEY_REUSED"):
            backups.create_for_actor(shop[0], actor, old_command, now)
        path = shop[0].backup_root / backup_id / "manifest.json"
        manifest = json.loads(path.read_text(encoding="utf-8"))
        manifest["extra"] = "invalid"
        path.write_text(json.dumps(manifest), encoding="utf-8")
        with pytest.raises(Rejected, match="BACKUP_INVALID"):
            backups.verify_bundle(shop[0], backup_id)
        with pytest.raises(Rejected, match="BACKUP_NOT_FOUND"):
            backups.verify_bundle(shop[0], uid())
        partial_id = uid()
        (shop[0].backup_root / (partial_id + ".partial")).mkdir()
        with pytest.raises(Rejected, match="BACKUP_INVALID"):
            backups.create_consistent_snapshot(shop[0], backup_id=partial_id, created_at=now)


def test_schema7_upgrade_preserves_old_facts_and_unknown_provenance(shop, tmp_path):
    target = ("job", shop[1].job_id)
    payment = act(shop, shop[2], "settlement.payment_record", dict(job_id=target[1], amount_minor=500,
        currency="USD", method="cash", reference=None, received_at=None), target, demo_mode=False)["result"]["id"]
    act(shop, shop[2], "settlement.refund_record", dict(job_id=target[1], payment_id=payment, amount_minor=100,
        currency="USD", reference="Fictional refund receipt", refunded_at=None, reason="record_correction"), target, demo_mode=False)
    old_root = tmp_path / "catalog7"
    old_root.mkdir()
    for file in sorted(MIGRATIONS_ROOT.glob("M00[1-7]_*.sql")):
        shutil.copyfile(file, old_root / file.name)
    legacy = replace(shop[0], data_root=tmp_path / "legacy")
    with acquire_data_root_lock(legacy):
        _initialize_database(legacy, old_root)
        with closing(connect(legacy)) as dst, closing(connect(shop[0])) as src:
            dst.execute("PRAGMA foreign_keys=OFF")
            tables = [r[0] for r in dst.execute("SELECT name FROM sqlite_master WHERE type='table' AND name!='schema_migrations'")]
            before = {}
            for table in tables:
                columns = [r[1] for r in dst.execute(f"PRAGMA table_info({table})")]
                data = [tuple(r) for r in src.execute(f"SELECT {','.join(columns)} FROM {table} ORDER BY rowid")]
                before[table] = (columns, data)
                for row in data:
                    dst.execute(f"INSERT INTO {table}({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})", row)
            assert not dst.execute("PRAGMA foreign_key_check").fetchall()
        initialize_database(legacy)
        assert inspect_database(legacy).schema_version == 9
        with closing(connect(legacy)) as db:
            for table, (columns, data) in before.items():
                assert [tuple(r) for r in db.execute(f"SELECT {','.join(columns)} FROM {table} ORDER BY rowid")] == data
            assert one(db, "SELECT fee_policy_rev FROM diagnostic_permissions")["fee_policy_rev"] is None
            assert one(db, "SELECT tax_policy_rev,tax_source FROM estimates") == dict(tax_policy_rev=None,tax_source=None)
            assert all(r["source"] is None for r in rows(db, "SELECT source FROM settlement_entries"))
            with pytest.raises(sqlite3.IntegrityError, match="immutable_money"):
                db.execute("UPDATE settlement_entries SET amount_minor=0")
