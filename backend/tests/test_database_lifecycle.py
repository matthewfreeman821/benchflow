"""Actual lifecycle implementation tests; need the Infra venv/shared imports."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest

from config.settings import Settings
from backend.database import (
    MIGRATIONS_ROOT, StorageError, _initialize_database, acquire_data_root_lock,
    connect, immediate_transaction, initialize_database,
)


class DatabaseLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.settings = Settings.for_test(Path(self.temp.name) / "data")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def init(self):
        with acquire_data_root_lock(self.settings):
            return initialize_database(self.settings)

    def test_first_start_and_restart_validate_same_schema(self) -> None:
        status = self.init()
        self.assertTrue(status.ready)
        self.assertEqual(status.schema_version, len(list(MIGRATIONS_ROOT.glob("*.sql"))))
        self.assertEqual(self.init(), status)

    def test_connections_have_required_pragmas(self) -> None:
        self.init()
        db = connect(self.settings)
        try:
            self.assertEqual(db.execute("PRAGMA foreign_keys").fetchone()[0], 1)
            self.assertEqual(db.execute("PRAGMA synchronous").fetchone()[0], 2)
            self.assertEqual(db.execute("PRAGMA busy_timeout").fetchone()[0], 2000)
            self.assertEqual(db.execute("PRAGMA journal_mode").fetchone()[0], "wal")
        finally:
            db.close()

    def test_lock_excludes_second_owner_and_is_reusable(self) -> None:
        with acquire_data_root_lock(self.settings):
            with self.assertRaisesRegex(StorageError, "DATA_ROOT_LOCKED"):
                with acquire_data_root_lock(self.settings):
                    self.fail("a second owner acquired the data-root lock")
        with acquire_data_root_lock(self.settings):
            pass

    def test_caller_exception_releases_os_lock(self) -> None:
        with self.assertRaisesRegex(ValueError, "fictional"):
            with acquire_data_root_lock(self.settings):
                raise ValueError("fictional")
        with acquire_data_root_lock(self.settings):
            pass

    def test_changed_sql_checksum_fails_closed_without_ledger_change(self) -> None:
        self.init()
        copied = Path(self.temp.name) / "migrations"
        shutil.copytree(MIGRATIONS_ROOT, copied)
        path = next(copied.glob("M001_*.sql"))
        path.write_text(path.read_text(encoding="utf-8") + "\n-- fictional drift\n", encoding="utf-8")
        with acquire_data_root_lock(self.settings):
            with self.assertRaisesRegex(StorageError, "MIGRATION_CHECKSUM_MISMATCH"):
                _initialize_database(self.settings, copied)
        self.assertEqual(self.init().schema_version, len(list(MIGRATIONS_ROOT.glob("*.sql"))))

    def test_missing_migration_in_catalog_is_rejected(self) -> None:
        copied = Path(self.temp.name) / "migrations"
        shutil.copytree(MIGRATIONS_ROOT, copied)
        next(copied.glob("M003_*.sql")).unlink()
        with self.assertRaisesRegex(StorageError, "MIGRATION_CATALOG_INVALID"):
            _initialize_database(self.settings, copied)

    def test_ledger_gap_is_rejected(self) -> None:
        self.init()
        db = connect(self.settings)
        try:
            db.execute("DELETE FROM schema_migrations WHERE version=3")
        finally:
            db.close()
        with self.assertRaisesRegex(StorageError, "MIGRATION_HISTORY_GAP"):
            self.init()

    def test_future_schema_is_rejected(self) -> None:
        self.init()
        db = connect(self.settings)
        try:
            future = len(list(MIGRATIONS_ROOT.glob("*.sql"))) + 1
            db.execute("INSERT INTO schema_migrations VALUES (?,'future.sql',?,'fictional')", (future, "0" * 64))
        finally:
            db.close()
        with self.assertRaisesRegex(StorageError, "FUTURE_SCHEMA"):
            self.init()

    def test_unversioned_database_is_never_adopted(self) -> None:
        db = connect(self.settings)
        db.execute("CREATE TABLE unowned_data(id INTEGER)")
        db.close()
        with self.assertRaisesRegex(StorageError, "UNVERSIONED_DATABASE"):
            self.init()

    def test_user_version_drift_is_not_silently_corrected(self) -> None:
        self.init()
        db = connect(self.settings)
        db.execute("PRAGMA user_version=4")
        db.close()
        with self.assertRaisesRegex(StorageError, "SCHEMA_VERSION_MISMATCH"):
            self.init()

    def test_corrupt_storage_does_not_report_ready(self) -> None:
        self.settings.data_root.mkdir(parents=True)
        self.settings.database_path.write_bytes(b"fictional corrupt database, not SQLite")
        with self.assertRaises(StorageError):
            self.init()

    def test_failed_migration_rolls_back_ddl_and_ledger(self) -> None:
        copied = Path(self.temp.name) / "migrations"
        copied.mkdir()
        (copied / "M001_example.sql").write_text("CREATE TABLE example(id INTEGER);\n", encoding="utf-8")
        (copied / "M002_fail.sql").write_text(
            "CREATE TABLE must_rollback(id INTEGER);\nINSERT INTO nonexistent VALUES (1);\n", encoding="utf-8",
        )
        with acquire_data_root_lock(self.settings):
            with self.assertRaisesRegex(StorageError, "MIGRATION_UNAVAILABLE"):
                _initialize_database(self.settings, copied)
        db = connect(self.settings)
        try:
            self.assertEqual(db.execute("SELECT max(version) FROM schema_migrations").fetchone()[0], 1)
            self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='must_rollback'").fetchone())
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 1)
        finally:
            db.close()

    def test_transaction_rolls_back_after_exception(self) -> None:
        self.init()
        db = connect(self.settings)
        try:
            with self.assertRaisesRegex(ValueError, "fictional"):
                with immediate_transaction(db):
                    db.execute("CREATE TABLE rollback_example(id INTEGER)")
                    raise ValueError("fictional")
            self.assertFalse(db.in_transaction)
            self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='rollback_example'").fetchone())
        finally:
            db.close()

    def test_nested_transaction_is_rejected(self) -> None:
        self.init()
        db = connect(self.settings)
        try:
            with immediate_transaction(db):
                with self.assertRaisesRegex(StorageError, "NESTED_TRANSACTION"):
                    with immediate_transaction(db):
                        pass
        finally:
            db.close()

    def test_held_writer_times_out_without_partial_ddl(self) -> None:
        self.init()
        db = connect(self.settings)

        def competing_writer() -> str:
            other = connect(self.settings)
            try:
                with immediate_transaction(other):
                    other.execute("CREATE TABLE forbidden_partial(id INTEGER)")
            except sqlite3.OperationalError:
                return "busy"
            finally:
                other.close()
            return "committed"

        try:
            with immediate_transaction(db):
                with ThreadPoolExecutor(max_workers=1) as pool:
                    self.assertEqual(pool.submit(competing_writer).result(), "busy")
            self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='forbidden_partial'").fetchone())
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
