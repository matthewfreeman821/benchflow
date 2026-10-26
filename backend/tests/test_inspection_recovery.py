from contextlib import closing
import hashlib
from pathlib import Path
import shutil
import sqlite3

import pytest

from config.settings import Settings
from contracts.runtime import SchemaInspection
from backend.bootstrap import bootstrap_manager
from backend.core import one, rows, uid
from backend.credentials import verify_password
from backend.database import (
    MIGRATIONS_ROOT, StorageError, _initialize_database, acquire_data_root_lock, connect,
    initialize_database, inspect_database,
)
from backend.operator import current_operator_provenance
from backend.recovery import invalidate_restored_authentication, recover_manager
from backend.testing import seed_approval_start_scenario


def image(path):
    return {p.name: (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns)
            for p in path.iterdir() if p.is_file() and p.name != ".benchflow.lock"}


def test_inspector_missing_and_empty_never_creates_storage(tmp_path):
    settings = Settings.for_test(tmp_path / "missing")
    with pytest.raises(StorageError, match="DATABASE_MISSING"):
        inspect_database(settings)
    assert not settings.data_root.exists()
    with acquire_data_root_lock(settings):
        settings.database_path.touch()
        before = image(settings.data_root)
        with pytest.raises(StorageError, match="UNVERSIONED_DATABASE"):
            inspect_database(settings)
        assert image(settings.data_root) == before


def test_inspection_current_read_only_and_supported_prefix(tmp_path):
    settings = Settings.for_test(tmp_path / "old")
    catalog = tmp_path / "catalog"
    catalog.mkdir()
    for path in sorted(MIGRATIONS_ROOT.glob("*.sql"))[:3]:
        shutil.copyfile(path, catalog / path.name)
    with acquire_data_root_lock(settings):
        _initialize_database(settings, catalog)
        before = image(settings.data_root)
        result = inspect_database(settings)
        assert isinstance(result, SchemaInspection)
        assert result.schema_version == 3 and result.pending_migration_versions == tuple(range(4, len(list(MIGRATIONS_ROOT.glob('*.sql')))+1))
        assert image(settings.data_root) == before
        initialize_database(settings)
        before = image(settings.data_root)
        assert inspect_database(settings).pending_migration_versions == ()
        assert image(settings.data_root) == before


@pytest.mark.parametrize("fault,code", [
    ("version", "SCHEMA_VERSION_MISMATCH"), ("gap", "MIGRATION_HISTORY_GAP"),
    ("checksum", "MIGRATION_CHECKSUM_MISMATCH"), ("future", "FUTURE_SCHEMA"), ("fk", "FOREIGN_KEY_CHECK_FAILED"),
])
def test_inspector_rejects_drift_gap_future_and_fk_without_modification(tmp_path, fault, code):
    settings = Settings.for_test(tmp_path / fault)
    with acquire_data_root_lock(settings):
        initialize_database(settings)
        with closing(connect(settings)) as db:
            if fault == "version":
                db.execute("PRAGMA user_version=2")
            elif fault == "gap":
                db.execute("DELETE FROM schema_migrations WHERE version=2")
            elif fault == "checksum":
                db.execute("UPDATE schema_migrations SET checksum=? WHERE version=1", ("0" * 64,))
            elif fault == "future":
                db.execute("INSERT INTO schema_migrations VALUES (?,'future.sql',?,'fictional')", (len(list(MIGRATIONS_ROOT.glob('*.sql')))+1, "0" * 64))
            else:
                db.execute("PRAGMA foreign_keys=OFF")
                db.execute("INSERT INTO customer_accounts VALUES (?,?,?,?,'phone')", (uid(), uid(), "fictional", uid()))
        before = image(settings.data_root)
        with pytest.raises(StorageError, match=code):
            inspect_database(settings)
        assert image(settings.data_root) == before


def test_bootstrap_real_operator_and_recovery_no_nested_lock(tmp_path):
    settings = Settings.for_test(tmp_path / "bootstrap")
    result = bootstrap_manager(settings, login_id="manager.fictional", display_name="Fictional manager", password="first fictional password")
    assert result.state == "created"
    assert bootstrap_manager(settings, login_id="ignored.manager", display_name="Other", password="another fictional password").state == "already_initialized"
    with acquire_data_root_lock(settings):
        with pytest.raises(StorageError, match="DATA_ROOT_LOCKED"):
            recover_manager(settings, account_id=result.account_id, password="updated fictional password", verification_method="phone")
    recover_manager(settings, account_id=result.account_id, password="updated fictional password", verification_method="phone")
    with closing(connect(settings)) as db:
        account = one(db, "SELECT * FROM accounts WHERE id=?", (result.account_id,))
        assert verify_password(account["password_hash"], "updated fictional password")
        assert not verify_password(account["password_hash"], "first fictional password")
        assert "password" not in str(rows(db, "SELECT summary_code FROM audit_events"))


def test_inspection_nonempty_wal_is_refused_without_sidecar_writes(tmp_path):
    settings = Settings.for_test(tmp_path / "wal")
    with acquire_data_root_lock(settings):
        initialize_database(settings)
        with closing(connect(settings)) as db:
            db.execute("INSERT INTO runtime_metadata VALUES ('fictional','pending checkpoint')")
            before = image(settings.data_root)
            with pytest.raises(StorageError, match="INSPECTION_WAL_PRESENT"):
                inspect_database(settings)
            assert image(settings.data_root) == before
        assert inspect_database(settings).pending_migration_versions == ()


def test_restore_auth_invalidation_preserves_business_and_rejects_all_old_secrets(tmp_path):
    settings = Settings.for_test(tmp_path / "restore")
    scenario = seed_approval_start_scenario(settings)
    with closing(connect(settings)) as db:
        before_jobs = rows(db, "SELECT * FROM jobs")
    invalidate_restored_authentication(settings, source_backup_id=uid(), operator_provenance=current_operator_provenance())
    with closing(connect(settings)) as db:
        assert rows(db, "SELECT * FROM jobs") == before_jobs
        assert all(a["password_hash"] is None and a["credential_state"] == "reset_required" for a in rows(db, "SELECT * FROM accounts"))
        assert not rows(db, "SELECT * FROM sessions") and not rows(db, "SELECT * FROM auth_codes")
        manager = one(db, "SELECT id FROM accounts WHERE login_id='fixture.manager'")["id"]
    recover_manager(settings, account_id=manager, password="restored fictional password", verification_method="in_person")
    with acquire_data_root_lock(settings):
        assert inspect_database(settings).schema_version == 9
