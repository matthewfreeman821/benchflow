"""Real Backend/Infra recovery, temporary storage only; never open a socket.

Secrets are random process-local fixture values. Assertions deliberately avoid
emitting credentials, cookies, response bodies or business records on failure.
"""
from contextlib import closing
from dataclasses import replace
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
import uuid

from fastapi.testclient import TestClient

from backend.app import create_app
from backend.credentials import new_secret
from backend.core import account_actor, later, one
from backend.customers import issue_code
from backend.database import (
    StorageError, acquire_data_root_lock, connect, immediate_transaction,
    initialize_database, inspect_database, utc_now,
)
from backend.recovery import recover_manager
from backend.testing import seed_approval_start_scenario
from config.settings import Settings
from contracts.projections import BackupManifest
from infra import operations as ops


def client_for(settings):
    return TestClient(create_app(settings), base_url=settings.origin,
                      client=("127.0.0.1", 41000))


def login(client, settings, login_id, password):
    response = client.get("/api/v1/session")
    if response.status_code != 200:
        raise AssertionError("session endpoint failed")
    return client.post("/api/v1/auth/login",
                       json={"login_id": login_id, "password": password},
                       headers={"Origin": settings.origin,
                                "X-CSRF-Token": response.json()["csrf_token"]})


def business_image(settings):
    # Compare every non-auth/non-operational table, not just a row count.
    excluded = {"accounts", "sessions", "auth_codes", "auth_throttles",
                "invitations", "audit_events", "runtime_metadata",
                "schema_migrations", "backup_operations", "sqlite_sequence"}
    with closing(connect(settings)) as db:
        names = sorted(r[0] for r in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"))
        return {name: sorted(tuple(row) for row in db.execute(f'SELECT * FROM "{name}"'))
                for name in names if name not in excluded}


class RecoveryIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="benchflow-infra-recovery-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        # Isolate BOTH roots: Settings.for_test alone does not isolate backups.
        self.settings = Settings(data_root=self.base / "data", local_app_data_root=self.base)
        ops.private_directory(self.settings.data_root)

    def initialized_snapshot(self):
        with ops.offline(self.settings):
            initialize_database(self.settings)
            return ops.snapshot(self.settings)

    def test_snapshot_restore_recover_restart_and_full_business_comparison(self):
        s = self.settings
        scenario = seed_approval_start_scenario(s)
        with closing(connect(s)) as db:
            manager = db.execute("SELECT id FROM accounts WHERE login_id='fixture.manager'").fetchone()[0]
        expected_business = business_image(s)

        # Real app lifespan owns the root lock. Actual login/session and online
        # manager backup HTTP handler produce the same exact shared bundle.
        manager_password = new_secret()
        recover_manager(s, account_id=manager, password=manager_password, verification_method="in_person")
        app_client = client_for(s)
        with app_client as client:
            self.assertEqual(client.get("/api/v1/health").status_code, 200)
            response = login(client, s, "fixture.manager", manager_password)
            self.assertEqual(response.status_code, 200)
            old_manager_cookie = client.cookies.get("bf_session")
            bid = str(uuid.uuid4())
            created = client.post("/api/v1/staff/backups", json={"command_id": bid},
                                  headers={"Origin": s.origin,
                                           "X-CSRF-Token": response.json()["csrf_token"]})
            self.assertEqual(created.status_code, 200)
            self.assertEqual(created.json()["backup"]["backup_id"], bid)
            with self.assertRaisesRegex(StorageError, "DATA_ROOT_LOCKED"):
                ops.restore(s, bid, f"RESTORE BENCHFLOW {bid}")
            client.cookies.clear()  # a second browser, not login over an authenticated session
            response = login(client, s, scenario.customer_login_id, scenario.customer_password)
            self.assertEqual(response.status_code, 200)
            old_customer_cookie = client.cookies.get("bf_session")
        self.assertFalse(app_client.app.state.ready)  # actual shutdown

        # Issue real Backend codes in the isolated fixture, never raw fake auth.
        with acquire_data_root_lock(s), closing(connect(s)) as db, immediate_transaction(db):
            actor = account_actor(db, manager)
            customer = one(db, "SELECT * FROM accounts WHERE login_id='fixture.customer'")
            technician = one(db, "SELECT * FROM accounts WHERE login_id='fixture.technician'")
            now = utc_now()
            _, recovery_code = issue_code(db, actor, customer, now, purpose="recovery", method="in_person")
            _, invitation_code = issue_code(db, actor, technician, now, purpose="invite",
                                            owner=manager, due=later(now, hours=24), method="in_person")
        # An OFFLINE snapshot contains real sessions and the issued code/invitation.
        with ops.offline(s):
            bid = ops.snapshot(s)
            checked = inspect_database(s)
            self.assertTrue(ops.current_schema(checked))
        source, manifest = ops.validate_bundle(s, bid)
        self.assertIsInstance(manifest, BackupManifest)
        self.assertEqual(source.name, "benchflow.sqlite3")
        self.assertEqual(manifest.sha256, ops.sha256(source))
        with closing(connect(s)) as db:
            self.assertGreater(db.execute("SELECT count(*) FROM sessions WHERE account_id IS NOT NULL").fetchone()[0], 0)
            self.assertGreater(db.execute("SELECT count(*) FROM auth_codes").fetchone()[0], 0)
            self.assertGreater(db.execute("SELECT count(*) FROM invitations WHERE state='pending'").fetchone()[0], 0)
            db.execute("INSERT INTO runtime_metadata VALUES ('infra-after-snapshot','fictional')")
        before_bundles = set(s.backup_root.iterdir())
        ops.restore(s, bid, f"RESTORE BENCHFLOW {bid}")
        self.assertEqual(business_image(s), expected_business)
        preserved = set(s.backup_root.iterdir()) - before_bundles
        self.assertEqual(len(preserved), 1)
        prior, _ = ops.validate_bundle(s, preserved.pop().name)
        with closing(sqlite3.connect(prior)) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM runtime_metadata WHERE key='infra-after-snapshot'").fetchone()[0], 1)
        with closing(connect(s)) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM runtime_metadata WHERE key='infra-after-snapshot'").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT count(*) FROM sessions").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT count(*) FROM auth_codes").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT count(*) FROM invitations WHERE state='pending'").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT count(*) FROM accounts WHERE password_hash IS NOT NULL OR credential_state != 'reset_required'").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT count(*) FROM audit_events WHERE event_type='restore.completed'").fetchone()[0], 1)
        # Recovery is HOST-only and self-locking; no outer DB lock here.
        recovered_password = new_secret()
        recover_manager(s, account_id=manager, password=recovered_password, verification_method="in_person")
        with client_for(s) as client:
            health = client.get("/api/v1/health")
            self.assertEqual(health.json(), {"status": "ready", "schema_version": manifest.schema_version})
            for cookie in (old_manager_cookie, old_customer_cookie):
                client.cookies.clear()
                client.cookies.set("bf_session", cookie)
                self.assertEqual(client.get("/api/v1/me").status_code, 401)
            client.cookies.clear()
            self.assertEqual(login(client, s, "fixture.manager", manager_password).status_code, 401)
            self.assertEqual(login(client, s, scenario.customer_login_id, scenario.customer_password).status_code, 401)
            for operation, payload in (
                ("recover", {"code": recovery_code["value"], "new_password": new_secret()}),
                ("activate", {"code": invitation_code["value"], "login_id": "unused.fictional",
                              "password": new_secret()}),
            ):
                csrf = client.get("/api/v1/session").json()["csrf_token"]
                denied = client.post("/api/v1/auth/" + operation, json=payload,
                                     headers={"Origin": s.origin, "X-CSRF-Token": csrf})
                self.assertEqual(denied.status_code, 400)
                self.assertEqual(denied.json()["error"]["code"], "AUTH_CODE_INVALID")
            self.assertEqual(login(client, s, "fixture.manager", recovered_password).status_code, 200)
            self.assertEqual(client.get("/api/v1/me").status_code, 200)
        self.assertEqual(business_image(s), expected_business)
        with acquire_data_root_lock(s):
            self.assertTrue(ops.current_schema(inspect_database(s)))

    def test_wrong_hash_and_manifest_schema_do_not_replace_live(self):
        bid = self.initialized_snapshot()
        manifest_path = self.settings.backup_root / bid / "manifest.json"
        original = manifest_path.read_text()
        live_hash = ops.sha256(self.settings.database_path)
        for field, value, error in (("sha256", "0" * 64, "BACKUP_MANIFEST_MISMATCH"),
                                    ("schema_version", 1, "BACKUP_SCHEMA_MISMATCH")):
            with self.subTest(field=field):
                body = json.loads(original)
                body[field] = value
                manifest_path.write_text(json.dumps(body))
                with self.assertRaisesRegex(ops.Refused, error):
                    ops.restore(self.settings, bid, f"RESTORE BENCHFLOW {bid}")
                self.assertEqual(ops.sha256(self.settings.database_path), live_hash)
                self.assertFalse(list(self.settings.data_root.glob(".restore-*")))
        manifest_path.write_text(original)

    def test_future_and_drifted_schema_even_with_matching_hash_are_refused(self):
        for fault, code in (("future", "FUTURE_SCHEMA"), ("drift", "MIGRATION_CHECKSUM_MISMATCH")):
            with self.subTest(fault=fault):
                bid = self.initialized_snapshot()
                source = self.settings.backup_root / bid / "benchflow.sqlite3"
                with closing(sqlite3.connect(source)) as db:
                    if fault == "future":
                        version = db.execute("PRAGMA user_version").fetchone()[0] + 1
                        db.execute("INSERT INTO schema_migrations VALUES (?, 'future.sql', ?, 'fictional')",
                                   (version, "0" * 64))
                    else:
                        db.execute("UPDATE schema_migrations SET checksum=? WHERE version=1", ("0" * 64,))
                    db.commit()
                path = source.parent / "manifest.json"
                body = json.loads(path.read_text())
                body.update(size_bytes=source.stat().st_size, sha256=ops.sha256(source))
                path.write_text(json.dumps(body))
                live_hash = ops.sha256(self.settings.database_path)
                with self.assertRaisesRegex(StorageError, code):
                    ops.restore(self.settings, bid, f"RESTORE BENCHFLOW {bid}")
                self.assertEqual(ops.sha256(self.settings.database_path), live_hash)

    def test_stage_auth_invalidator_requires_released_lock(self):
        from backend.recovery import invalidate_restored_authentication
        staged = replace(self.settings, data_root=self.base / ".restore-fixture")
        with acquire_data_root_lock(staged):
            initialize_database(staged)
            before = ops.sha256(staged.database_path)
            with self.assertRaisesRegex(StorageError, "DATA_ROOT_LOCKED"):
                invalidate_restored_authentication(
                    staged, source_backup_id=str(uuid.uuid4()), operator_provenance=ops.provenance())
            self.assertEqual(ops.sha256(staged.database_path), before)


if __name__ == "__main__":
    unittest.main()
