"""Dependency-free SQL foundation tests, NOT HTTP/domain acceptance.

These intentionally exercise persisted SQL independently of shared Python
imports, so migration constraints can be checked while Infra builds its venv.
The lifecycle suite separately imports the actual database implementation.
"""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3
import tempfile
import unittest
from uuid import uuid4

SQL_ROOT = Path(__file__).resolve().parents[1] / "migrations"
NOW = "2026-10-22T19:00:00.000000Z"
LATER = "2026-10-23T19:00:00.000000Z"


class MigrationSQLTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "fictional.sqlite3"
        self.db = self.open_db()
        for migration in sorted(SQL_ROOT.glob("*.sql")):
            self.db.executescript("BEGIN IMMEDIATE;\n" + migration.read_text(encoding="utf-8") + "\nCOMMIT;")
        self.staff = self.account()
        self.customer = str(uuid4())
        self.db.execute(
            "INSERT INTO customers(id,display_name,name_normalized,phone_known,email_known,created_at,updated_at)"
            " VALUES (?,'Fictional Morgan','fictional morgan',0,0,?,?)", (self.customer, NOW, NOW),
        )
        self.device = str(uuid4())
        self.db.execute(
            "INSERT INTO devices(id,customer_id,make_known,model_known,serial_known,search_normalized)"
            " VALUES (?,?,0,0,0,'fictional laptop')", (self.device, self.customer),
        )
        self.job = str(uuid4())
        self.db.execute(
            "INSERT INTO jobs(id,job_code,customer_id,device_id,owner_staff_id,next_action,next_action_due_at,"
            "reported_issue,condition_known,accessories_known,testing_constraints_known,received_by_customer_name,"
            "received_by_staff_id,acknowledgement_version,acknowledgement_method,acknowledgement_actor_id,opened_at,updated_at)"
            " VALUES (?,?,?,?,?,'Review fictional intake',?,'Fictional intermittent display',0,0,0,"
            "'Fictional Morgan',?,'fictional-v1','in_person',?,?,?)",
            (self.job, "FICTIONAL-01", self.customer, self.device, self.staff, LATER, self.staff, self.staff, NOW, NOW),
        )

    def tearDown(self) -> None:
        self.db.close()
        self.temp.cleanup()

    def open_db(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, isolation_level=None, timeout=2)
        db.execute("PRAGMA foreign_keys=ON")
        return db

    def account(self, *, kind: str = "staff", login: str | None = None) -> str:
        account_id = str(uuid4())
        login = login or "fictional." + account_id
        self.db.execute(
            "INSERT INTO accounts(id,kind,state,display_name,login_id,login_normalized,credential_state,created_at,updated_at)"
            " VALUES (?,?,'active','Fictional identity',?,?,'reset_required',?,?)",
            (account_id, kind, login, login, NOW, NOW),
        )
        return account_id

    def part(self, qty: int = 1) -> str:
        part_id = str(uuid4())
        self.db.execute(
            "INSERT INTO parts(id,sku,description,unit,on_hand_qty,reserved_qty) VALUES (?,?, 'Fictional battery','each',?,0)",
            (part_id, str(uuid4()), qty),
        )
        return part_id

    def event(self) -> str:
        event_id = str(uuid4())
        self.db.execute(
            "INSERT INTO audit_events(id,aggregate_type,aggregate_id,event_type,actor_kind,actor_id,occurred_at,summary_code,refs_json)"
            " VALUES (?,'job',?,'job.create','staff',?,?,'job_created','[]')", (event_id, self.job, self.staff, NOW),
        )
        return event_id

    def estimate(self) -> str:
        estimate_id = str(uuid4())
        self.db.execute(
            "INSERT INTO estimates(id,job_id,version,issued_at,expires_at,currency,subtotal_minor,tax_minor,total_minor,"
            "tax_label,tax_enabled,content_hash,change_reason,change_json,issued_by_staff_id)"
            " VALUES (?,?,1,?,?,'USD',12000,0,12000,'tax not configured',0,?,'Fictional issue','[]',?)",
            (estimate_id, self.job, NOW, LATER, "0" * 64, self.staff),
        )
        return estimate_id

    def test_all_sql_migrations_apply_and_have_valid_integrity(self) -> None:
        self.assertGreaterEqual(len(list(SQL_ROOT.glob("*.sql"))), 7)
        self.assertEqual(self.db.execute("PRAGMA integrity_check").fetchall(), [("ok",)])
        self.assertEqual(self.db.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_foreign_customer_is_rejected_at_storage_boundary(self) -> None:
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("UPDATE jobs SET customer_id=? WHERE id=?", (str(uuid4()), self.job))

    def test_customer_cannot_receive_staff_role(self) -> None:
        customer_account = self.account(kind="customer")
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("INSERT INTO staff_roles(account_id,role) VALUES (?,'manager')", (customer_account,))

    def test_staff_cannot_be_customer_link(self) -> None:
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute(
                "INSERT INTO customer_accounts VALUES (?,?,?,?,'in_person')",
                (self.customer, self.staff, NOW, self.staff),
            )

    def test_normalized_login_is_unique(self) -> None:
        self.account(login="fictional.duplicate")
        with self.assertRaises(sqlite3.IntegrityError):
            self.account(login="fictional.duplicate")

    def test_usable_credentials_require_hash(self) -> None:
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("UPDATE accounts SET credential_state='usable' WHERE id=?", (self.staff,))

    def test_unknown_contact_cannot_have_value(self) -> None:
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("UPDATE customers SET phone_value='fictional phone',phone_known=0 WHERE id=?", (self.customer,))

    def test_required_queue_owner_is_not_nullable(self) -> None:
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("UPDATE jobs SET owner_staff_id=NULL,owner_queue=NULL WHERE id=?", (self.job,))

    def test_queue_owner_is_exclusive(self) -> None:
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("UPDATE jobs SET owner_queue='manager_intake' WHERE id=?", (self.job,))

    def test_stock_cannot_be_negative_or_overreserved(self) -> None:
        part_id = self.part()
        for hand, reserved in ((-1, 0), (1, 2), (1, -1), (1.5, 0), (2, 0.5)):
            with self.subTest(hand=hand, reserved=reserved), self.assertRaises(sqlite3.IntegrityError):
                self.db.execute("UPDATE parts SET on_hand_qty=?,reserved_qty=? WHERE id=?", (hand, reserved, part_id))

    def test_estimate_is_immutable(self) -> None:
        estimate_id = self.estimate()
        for sql in ("UPDATE estimates SET total_minor=0 WHERE id=?", "DELETE FROM estimates WHERE id=?"):
            with self.subTest(sql=sql), self.assertRaises(sqlite3.IntegrityError):
                self.db.execute(sql, (estimate_id,))

    def test_estimate_total_must_match_snapshot_arithmetic(self) -> None:
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute(
                "INSERT INTO estimates(id,job_id,version,issued_at,expires_at,currency,subtotal_minor,tax_minor,total_minor,"
                "tax_label,tax_enabled,content_hash,change_reason,change_json,issued_by_staff_id)"
                " VALUES (?,?,1,?,?,'USD',100,0,101,'tax not configured',0,?,'fictional','[]',?)",
                (str(uuid4()), self.job, NOW, LATER, "0" * 64, self.staff),
            )

    def test_decision_is_unique_and_immutable(self) -> None:
        estimate_id, decision_id = self.estimate(), str(uuid4())
        values = (decision_id, self.job, estimate_id, self.staff, NOW)
        statement = (
            "INSERT INTO estimate_decisions(id,job_id,estimate_id,decision,method,attribution,actor_kind,actor_id,decided_at)"
            " VALUES (?,?,?,'approved','phone','Fictional confirmation','staff',?,?)"
        )
        self.db.execute(statement, values)
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute(statement, (str(uuid4()), *values[1:]))
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("UPDATE estimate_decisions SET decision='declined' WHERE id=?", (decision_id,))

    def test_audit_cannot_be_rewritten_or_deleted(self) -> None:
        event_id = self.event()
        for sql in ("UPDATE audit_events SET summary_code='changed' WHERE id=?", "DELETE FROM audit_events WHERE id=?"):
            with self.subTest(sql=sql), self.assertRaises(sqlite3.IntegrityError):
                self.db.execute(sql, (event_id,))

    def test_money_is_integer_and_append_only(self) -> None:
        entry_id = str(uuid4())
        insert = (
            "INSERT INTO settlement_entries(id,job_id,bucket,amount_minor,currency,method,occurred_at,recorded_at,actor_staff_id)"
            " VALUES (?,?,'payment',?,'USD','cash',?,?,?)"
        )
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute(insert, (entry_id, self.job, 12.34, NOW, NOW, self.staff))
        self.db.execute(insert, (entry_id, self.job, 1234, NOW, NOW, self.staff))
        for sql in ("UPDATE settlement_entries SET amount_minor=1 WHERE id=?", "DELETE FROM settlement_entries WHERE id=?"):
            with self.subTest(sql=sql), self.assertRaises(sqlite3.IntegrityError):
                self.db.execute(sql, (entry_id,))

    def test_reservation_releases_and_consumption_cannot_exceed_quantity(self) -> None:
        part_id, reservation_id = self.part(3), str(uuid4())
        self.db.execute(
            "INSERT INTO reservations(id,job_id,part_id,scope_ref,qty,created_at,created_by_staff_id)"
            " VALUES (?,?,?,?,3,?,?)", (reservation_id, self.job, part_id, str(uuid4()), NOW, self.staff),
        )
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("UPDATE reservations SET released_qty=2,consumed_qty=2 WHERE id=?", (reservation_id,))

    def test_return_must_have_distinct_original_and_new_job(self) -> None:
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute(
                "INSERT INTO return_cases(id,original_job_id,return_job_id,reported_recurrence,state,created_at,created_by_account_id)"
                " VALUES (?,?,?,'Fictional recurrence','open',?,?)",
                (str(uuid4()), self.job, self.job, NOW, self.staff),
            )

    def test_parameter_binding_keeps_sql_text_in_business_record(self) -> None:
        note = "'; DROP TABLE accounts; -- fictional input"
        self.db.execute("UPDATE customers SET notes=? WHERE id=?", (note, self.customer))
        self.assertEqual(self.db.execute("SELECT notes FROM customers WHERE id=?", (self.customer,)).fetchone()[0], note)
        self.assertEqual(self.db.execute("SELECT count(*) FROM accounts").fetchone()[0], 1)

    def test_rollback_does_not_leave_business_event_or_idempotency(self) -> None:
        self.db.execute("BEGIN IMMEDIATE")
        self.db.execute("UPDATE jobs SET job_rev=job_rev+1 WHERE id=?", (self.job,))
        event_id = self.event()
        self.db.execute(
            "INSERT INTO idempotency_results VALUES (?,'staff',?,?,?,200,'job',?,?,?)",
            (str(uuid4()), self.staff, "a" * 64, "{}", self.job, NOW, event_id),
        )
        self.db.rollback()
        self.assertEqual(self.db.execute("SELECT job_rev FROM jobs WHERE id=?", (self.job,)).fetchone()[0], 1)
        self.assertEqual(self.db.execute("SELECT count(*) FROM audit_events").fetchone()[0], 0)
        self.assertEqual(self.db.execute("SELECT count(*) FROM idempotency_results").fetchone()[0], 0)

    def test_committed_facts_survive_connection_restart(self) -> None:
        event_id = self.event()
        self.db.close()
        self.db = self.open_db()
        self.assertEqual(self.db.execute("SELECT id FROM audit_events").fetchone()[0], event_id)
        self.assertEqual(self.db.execute("SELECT reported_issue FROM jobs").fetchone()[0], "Fictional intermittent display")

    def test_database_last_unit_guard_has_one_winner(self) -> None:
        """Storage predicate only; this is NOT the unimplemented reserve API."""
        part_id = self.part()

        def allocate() -> int:
            db = self.open_db()
            try:
                db.execute("BEGIN IMMEDIATE")
                changed = db.execute(
                    "UPDATE parts SET reserved_qty=reserved_qty+1,stock_rev=stock_rev+1 "
                    "WHERE id=? AND on_hand_qty-reserved_qty>=1", (part_id,),
                ).rowcount
                db.commit()
                return changed
            finally:
                db.close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(lambda _: allocate(), range(2)))
        self.assertEqual(sorted(outcomes), [0, 1])
        self.assertEqual(self.db.execute("SELECT on_hand_qty-reserved_qty FROM parts WHERE id=?", (part_id,)).fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
