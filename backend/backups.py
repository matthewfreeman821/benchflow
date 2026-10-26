"""Backend online snapshot seam. Caller holds the matching data-root lock.

No nested root lock, infra imports, owner-selected paths or live DB file copy.
The fixed-root directory rename publishes a complete verified bundle.
"""
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from uuid import UUID

from pydantic import ValidationError

from contracts.projections import BackupManifest, BackupView, RestoreGuideView
from contracts.serialization import loads_request_json
from backend.core import Rejected, event, insert, instant, one, require, stamp
from backend.database import (
    StorageError, _assert_local_plain_path, connect, immediate_transaction, inspect_database,
)


def bundle_path(settings, backup_id):
    try:
        require(str(UUID(backup_id)) == backup_id, "BACKUP_NOT_FOUND")
    except (ValueError, TypeError, AttributeError):
        raise Rejected("BACKUP_NOT_FOUND") from None
    path = settings.backup_root / backup_id
    _assert_local_plain_path(path)
    return path


def verify_bundle(settings, backup_id):
    try:
        path = bundle_path(settings, backup_id)
    except StorageError:
        raise Rejected("BACKUP_INVALID") from None
    require(path.exists(), "BACKUP_NOT_FOUND")
    try:
        _assert_local_plain_path(path / "manifest.json")
        _assert_local_plain_path(path / "benchflow.sqlite3")
        raw = (path / "manifest.json").read_bytes()
        require(len(raw) <= 16384, "BACKUP_INVALID")
        manifest = BackupManifest.model_validate(loads_request_json(raw, max_bytes=16384))
        require(manifest.backup_id == backup_id, "BACKUP_INVALID")
        database = path / manifest.database_file
        require(database.stat().st_size == manifest.size_bytes, "BACKUP_INVALID")
        with database.open("rb") as handle:
            require(hashlib.file_digest(handle, "sha256").hexdigest() == manifest.sha256, "BACKUP_INVALID")
        checked = inspect_database(settings.for_staged_root(path))
        require(checked.schema_version == manifest.schema_version, "BACKUP_INVALID")
        return manifest
    except Rejected:
        raise
    except (OSError, ValueError, ValidationError, StorageError, sqlite3.Error):
        raise Rejected("BACKUP_INVALID") from None


def metadata(manifest):
    return BackupView(**manifest.model_dump(exclude={"format_version", "database_file", "integrity"}),
                      integrity="verified_at_creation").model_dump(mode="json")


def create_consistent_snapshot(settings, *, backup_id, created_at) -> BackupManifest:
    final = bundle_path(settings, backup_id)
    if final.exists():
        return verify_bundle(settings, backup_id)
    stage = settings.backup_root / (backup_id + ".partial")
    try:
        _assert_local_plain_path(stage)
        settings.backup_root.mkdir(parents=True, exist_ok=True)
        # A partial bundle is evidence, never automatically discarded or reused.
        require(not stage.exists(), "BACKUP_INVALID")
        stage.mkdir()
        destination = stage / "benchflow.sqlite3"
        with closing(connect(settings)) as source, closing(sqlite3.connect(destination)) as target:
            source.backup(target)
            target.execute("PRAGMA journal_mode=DELETE")
        checked = inspect_database(settings.for_staged_root(stage))
        require(checked.schema_version == checked.latest_schema_version and not checked.pending_migration_versions,
                "BACKUP_INVALID")
        with destination.open("rb") as handle:
            digest = hashlib.file_digest(handle, "sha256").hexdigest()
        manifest = BackupManifest(format_version=1, backup_id=backup_id, created_at=stamp(instant(created_at)),
            database_file="benchflow.sqlite3", schema_version=checked.schema_version,
            size_bytes=destination.stat().st_size, sha256=digest, integrity="ok")
        with (stage / "manifest.json").open("x", encoding="utf-8") as handle:
            handle.write(manifest.model_dump_json())
            handle.flush()
            os.fsync(handle.fileno())
        with destination.open("r+b") as handle:
            os.fsync(handle.fileno())
        stage.rename(final)
        return verify_bundle(settings, backup_id)
    except Rejected:
        raise
    except (OSError, sqlite3.Error, StorageError):
        raise Rejected("BACKUP_UNAVAILABLE") from None


def create_for_actor(settings, actor, command_id, now):
    require(actor["kind"] == "staff" and "manager" in actor["roles"], "FORBIDDEN")
    # Durable claim precedes the snapshot. Reconciliation never attributes an
    # orphan bundle to a different actor after a crash.
    with closing(connect(settings)) as db, immediate_transaction(db):
        require(not one(db, "SELECT 1 FROM idempotency_results WHERE command_id=?", (command_id,), required=False),
                "IDEMPOTENCY_KEY_REUSED")
        op = one(db, "SELECT * FROM backup_operations WHERE command_id=?", (command_id,), required=False)
        if op:
            require(op["actor_id"] == actor["id"], "IDEMPOTENCY_KEY_REUSED")
        else:
            require(not bundle_path(settings, command_id).exists(), "IDEMPOTENCY_KEY_REUSED")
            insert(db, "backup_operations", command_id=command_id, actor_id=actor["id"], created_at=now, state="pending")
            op = dict(created_at=now, state="pending")
    replayed = op["state"] == "complete"
    if replayed:
        # A missing/corrupt completed artifact is not permission to take a new
        # snapshot under an old receipt.
        manifest = verify_bundle(settings, command_id)
        require(manifest == BackupManifest.model_validate_json(op["manifest_json"]), "BACKUP_INVALID")
    else:
        manifest = create_consistent_snapshot(settings, backup_id=command_id, created_at=op["created_at"])
        require(manifest.created_at == stamp(instant(op["created_at"])), "BACKUP_INVALID")
    with closing(connect(settings)) as db, immediate_transaction(db):
        if not replayed:
            eid = event(db, actor, "backup", command_id, "backup.created", now,
                        command_id=command_id, refs=[("backup", command_id)])
            db.execute("UPDATE backup_operations SET state='complete',manifest_json=?,event_id=? WHERE command_id=?",
                       (manifest.model_dump_json(), eid, command_id))
    return dict(command_id=command_id, status="replayed" if replayed else "applied", backup=metadata(manifest))


def restore_guide(manifest):
    id_ = manifest.backup_id
    return RestoreGuideView(title="Offline restore required",
        consequences="Restoration replaces local records with this snapshot and invalidates authentication. Preserve the current database first.",
        confirmation_phrase=f"RESTORE BENCHFLOW {id_}", integrity="verified_now",
        schema_status=f"Verified supported schema {manifest.schema_version}",
        stop_steps=["Stop BenchFlow using the local operator launcher; ensure its root lock is released."],
        restore_steps=[f"Use the offline operator restore command for backup {id_}.",
                       "Verify the manifest, stage the database, invalidate authentication, and preserve the current database before replacement."],
        start_steps=["Start BenchFlow using the local launcher; allow forward-only migrations."],
        verify_steps=["Verify health, schema, repair history, stock and settlement totals.",
                      "Use manager recovery to sign in; verify old sessions and codes are invalid."]).model_dump(mode="json")
