"""Single-owner SQLite lifecycle and forward-only, checksum-verified migrations.

Callers of ``connect`` own and close the returned connection. The application
lifespan must hold ``acquire_data_root_lock`` for its whole lifetime, including
initialization. Host-only entry points acquire the same lock themselves.
"""

from __future__ import annotations

from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import re
import sqlite3
import stat
from typing import Iterator

from config.settings import Settings
from contracts.runtime import DatabaseStatus, MigrationFingerprint, SchemaInspection

MIGRATIONS_ROOT = Path(__file__).parent / "migrations"
_MIGRATION_NAME = re.compile(r"M([0-9]{3})_[a-z0-9_]+\.sql")


class StorageError(RuntimeError):
    """Internal storage failure with a stable non-sensitive code, never SQL."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class Migration:
    version: int
    name: str
    checksum: str
    sql: str


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _assert_local_plain_path(path: Path) -> None:
    """Refuse existing symlink/junction/reparse components, not just final files.

    Infra additionally owns Windows ACLs and reparse-safe offline staging.
    This check does not claim to eliminate races with a hostile OS account.
    """
    for item in (path, *path.parents):
        try:
            info = item.lstat()
        except FileNotFoundError:
            continue
        attributes = getattr(info, "st_file_attributes", 0)
        if stat.S_ISLNK(info.st_mode) or attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
            raise StorageError("UNSAFE_STORAGE_PATH")


def _prepare_root(settings: Settings) -> None:
    _assert_local_plain_path(settings.data_root)
    settings.data_root.mkdir(parents=True, exist_ok=True)
    _assert_local_plain_path(settings.data_root)


@contextmanager
def _data_root_lock(settings: Settings) -> Iterator[None]:
    _prepare_root(settings)
    path = settings.data_root / ".benchflow.lock"
    _assert_local_plain_path(path)
    handle = path.open("a+b")
    acquired = False
    try:
        # Lock at least one real byte; never unlink the lock file on release.
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
            os.fsync(handle.fileno())
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise StorageError("DATA_ROOT_LOCKED") from error
        acquired = True
        yield
    finally:
        if acquired:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def acquire_data_root_lock(settings: Settings) -> AbstractContextManager[None]:
    return _data_root_lock(settings)


def connect(settings: Settings) -> sqlite3.Connection:
    """Open a fresh connection, without changing journal mode inside requests."""
    _prepare_root(settings)
    _assert_local_plain_path(settings.database_path)
    for suffix in ("-wal", "-shm"):
        _assert_local_plain_path(Path(str(settings.database_path) + suffix))
    connection = sqlite3.connect(
        settings.database_path,
        timeout=settings.busy_timeout_ms / 1000,
        isolation_level=None,
    )
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA synchronous = FULL")
        # Fixed Settings value, not interpolated from request input.
        connection.execute("PRAGMA busy_timeout = 2000")
        if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
            raise StorageError("FOREIGN_KEYS_UNAVAILABLE")
        return connection
    except (sqlite3.Error, StorageError):
        connection.close()
        raise


@contextmanager
def immediate_transaction(connection: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """No automatic retry. A known rollback is distinguishable from commit loss.

    HTTP callers must map a failed COMMIT to unknown outcome (500), not to a
    retryable/no-commit response. Recovery uses the unchanged command ID.
    """
    if connection.in_transaction:
        raise StorageError("NESTED_TRANSACTION")
    connection.execute("BEGIN IMMEDIATE")
    try:
        yield connection
    except BaseException:
        if connection.in_transaction:
            connection.rollback()
        raise
    else:
        try:
            connection.commit()
        except sqlite3.Error as error:
            if connection.in_transaction:
                connection.rollback()
            raise StorageError("COMMIT_OUTCOME_UNKNOWN") from error


def _load_migrations(root: Path) -> tuple[Migration, ...]:
    records = []
    for path in sorted(root.glob("*.sql")):
        match = _MIGRATION_NAME.fullmatch(path.name)
        if match is None or path.is_symlink():
            raise StorageError("MIGRATION_CATALOG_INVALID")
        raw = path.read_bytes()
        records.append(Migration(int(match[1]), path.name, hashlib.sha256(raw).hexdigest(), raw.decode("utf-8")))
    versions = [item.version for item in records]
    if not versions or versions != list(range(1, len(records) + 1)):
        raise StorageError("MIGRATION_CATALOG_INVALID")
    return tuple(records)


def _statements(sql: str) -> Iterator[str]:
    """Split trusted migration SQL including trigger bodies, with no executescript.

    sqlite3.executescript implicitly commits, which would break migration
    atomicity. Every statement here stays in the caller's explicit transaction.
    """
    pending = ""
    for line in sql.splitlines(keepends=True):
        for char in line:
            pending += char
            if char == ";" and sqlite3.complete_statement(pending):
                yield pending
                pending = ""
    if pending.strip():
        raise StorageError("MIGRATION_SQL_INCOMPLETE")


def _verify_integrity(connection: sqlite3.Connection) -> None:
    if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise StorageError("FOREIGN_KEY_CHECK_FAILED")
    result = [row[0] for row in connection.execute("PRAGMA integrity_check")]
    if result != ["ok"]:
        raise StorageError("INTEGRITY_CHECK_FAILED")


def _read_ledger(connection: sqlite3.Connection, migrations: tuple[Migration, ...]) -> int:
    tables = {
        row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )
    }
    if "schema_migrations" not in tables:
        if tables:
            raise StorageError("UNVERSIONED_DATABASE")
        return 0
    rows = connection.execute(
        "SELECT version, name, checksum FROM schema_migrations ORDER BY version"
    ).fetchall()
    if len(rows) > len(migrations):
        raise StorageError("FUTURE_SCHEMA")
    if [row["version"] for row in rows] != list(range(1, len(rows) + 1)):
        raise StorageError("MIGRATION_HISTORY_GAP")
    for row, migration in zip(rows, migrations):
        if row["name"] != migration.name or row["checksum"] != migration.checksum:
            raise StorageError("MIGRATION_CHECKSUM_MISMATCH")
    if not rows and tables != {"schema_migrations"}:
        raise StorageError("UNVERSIONED_DATABASE")
    return len(rows)


def _initialize_database(settings: Settings, migrations_root: Path) -> DatabaseStatus:
    migrations = _load_migrations(migrations_root)
    try:
        connection = connect(settings)
    except sqlite3.Error as error:
        raise StorageError("MIGRATION_UNAVAILABLE") from error
    try:
        current = _read_ledger(connection, migrations)
        if connection.execute("PRAGMA user_version").fetchone()[0] != current:
            raise StorageError("SCHEMA_VERSION_MISMATCH")
        _verify_integrity(connection)
        # Journal-mode setup is explicitly outside any migration transaction.
        mode = connection.execute("PRAGMA journal_mode = WAL").fetchone()[0]
        if mode.casefold() != "wal":
            raise StorageError("WAL_UNAVAILABLE")
        for migration in migrations[current:]:
            # SQLite's documented table-rebuild procedure requires FK enforcement
            # off outside the transaction. Validate *all* FKs before commit below.
            # The exclusive root owner prevents application access during migration.
            connection.execute("PRAGMA foreign_keys = OFF")
            with immediate_transaction(connection):
                if migration.version == 1:
                    connection.execute(
                        "CREATE TABLE schema_migrations ("
                        "version INTEGER PRIMARY KEY CHECK (version >= 1),"
                        "name TEXT NOT NULL UNIQUE, checksum TEXT NOT NULL,"
                        "applied_at TEXT NOT NULL)"
                    )
                for statement in _statements(migration.sql):
                    connection.execute(statement)
                connection.execute(
                    "INSERT INTO schema_migrations(version, name, checksum, applied_at) VALUES (?, ?, ?, ?)",
                    (migration.version, migration.name, migration.checksum, utc_now()),
                )
                connection.execute(f"PRAGMA user_version = {migration.version:d}")
                _verify_integrity(connection)
            connection.execute("PRAGMA foreign_keys = ON")
        current = _read_ledger(connection, migrations)
        if connection.execute("PRAGMA user_version").fetchone()[0] != current:
            raise StorageError("SCHEMA_VERSION_MISMATCH")
        _verify_integrity(connection)
        return DatabaseStatus(schema_version=current, ready=True)
    except sqlite3.Error as error:
        raise StorageError("MIGRATION_UNAVAILABLE") from error
    finally:
        connection.close()


def initialize_database(settings: Settings) -> DatabaseStatus:
    return _initialize_database(settings, MIGRATIONS_ROOT)


def inspect_database(settings: Settings) -> SchemaInspection:
    """Inspect an existing database, without initialization or a nested lock.

    The caller holds this Settings' root lock. SQLite mode=ro deliberately
    refuses missing files; unlike connect(), this never prepares a directory.
    The owning process must have closed its writable connections. Immutable
    read-only mode avoids SQLite creating WAL/SHM beside an offline WAL-mode
    database. A nonempty WAL is refused, never silently ignored: the operator
    must checkpoint/close with the normal initializer/connection lifecycle
    before inspection. This inspector itself never checkpoints or repairs.
    """
    migrations = _load_migrations(MIGRATIONS_ROOT)
    _assert_local_plain_path(settings.database_path)
    if not settings.database_path.is_file():
        raise StorageError("DATABASE_MISSING")
    wal = Path(str(settings.database_path) + "-wal")
    shm = Path(str(settings.database_path) + "-shm")
    _assert_local_plain_path(wal)
    _assert_local_plain_path(shm)
    if wal.exists() and wal.stat().st_size:
        raise StorageError("INSPECTION_WAL_PRESENT")
    connection = None
    try:
        connection = sqlite3.connect(
            settings.database_path.as_uri() + "?mode=ro&immutable=1",
            uri=True, isolation_level=None, timeout=settings.busy_timeout_ms / 1000,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        current = _read_ledger(connection, migrations)
        if current < 1:
            raise StorageError("UNVERSIONED_DATABASE")
        if connection.execute("PRAGMA user_version").fetchone()[0] != current:
            raise StorageError("SCHEMA_VERSION_MISMATCH")
        _verify_integrity(connection)
        return SchemaInspection(
            schema_version=current, latest_schema_version=len(migrations),
            applied_migrations=tuple(
                MigrationFingerprint(m.version, m.name, m.checksum) for m in migrations[:current]
            ),
            pending_migration_versions=tuple(m.version for m in migrations[current:]),
            integrity_check="ok", foreign_key_check="ok",
        )
    except sqlite3.Error as error:
        raise StorageError("INSPECTION_UNAVAILABLE") from error
    finally:
        if connection is not None:
            connection.close()
