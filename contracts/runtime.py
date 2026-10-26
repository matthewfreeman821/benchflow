"""Backend/Infrastructure lifecycle interfaces and integration fixture records."""

from __future__ import annotations

from dataclasses import dataclass
import sqlite3
from contextlib import AbstractContextManager
from typing import Any, Literal, Protocol

from config.settings import Settings

from contracts.common import InstantString, UUIDString
from contracts.events import OperatorProvenance
from contracts.projections import BackupManifest


@dataclass(frozen=True, slots=True)
class DatabaseStatus:
    """Sanitized result of migration/integrity initialization."""

    schema_version: int
    ready: bool


@dataclass(frozen=True, slots=True)
class MigrationFingerprint:
    """One applied migration checked against the installed Backend catalog."""

    version: int
    name: str
    checksum: str


@dataclass(frozen=True, slots=True)
class SchemaInspection:
    """Read-only validation result for a current or forward-migratable DB.

    The inspector returns this only after validating all reported checks.
    Unsupported, corrupt, drifted, future, or unversioned databases raise a
    sanitized Backend StorageError instead of returning a partial result.
    """

    schema_version: int
    latest_schema_version: int
    applied_migrations: tuple[MigrationFingerprint, ...]
    pending_migration_versions: tuple[int, ...]
    integrity_check: Literal["ok"]
    foreign_key_check: Literal["ok"]


@dataclass(frozen=True, slots=True)
class ApprovalStartScenario:
    """Synthetic integration fixture with pending consent and a planned task.

    Passwords are generated for the isolated test process and must not be
    persisted to fixture files or emitted in test output.
    """

    customer_login_id: str
    customer_password: str
    technician_login_id: str
    technician_password: str
    job_id: str
    task_id: str
    scope_ref: str
    estimate_version: int
    total_minor: int
    currency: str
    expires_at: str
    job_rev: int
    gate_rev: int
    stock_revisions: dict[str, int]


@dataclass(frozen=True, slots=True)
class BootstrapResult:
    account_id: str
    state: Literal["created", "already_initialized"]


@dataclass(frozen=True, slots=True)
class HealthStatus:
    status: Literal["ready"]
    schema_version: int


class CreateApp(Protocol):
    def __call__(self, settings: Settings | None = None) -> Any:
        """Return FastAPI; absent settings load the validated environment."""
        ...


class DataRootLockFactory(Protocol):
    def __call__(self, settings: Settings) -> AbstractContextManager[None]:
        """Hold the exclusive one-process data-root lock through shutdown."""
        ...


class ConnectionFactory(Protocol):
    def __call__(self, settings: Settings) -> sqlite3.Connection:
        """Open a fresh caller-owned connection with agreed pragmas/timeouts."""
        ...


class DatabaseInitializer(Protocol):
    def __call__(self, settings: Settings) -> DatabaseStatus:
        """Apply and validate forward migrations while the lock is held."""
        ...


class DatabaseSchemaInspector(Protocol):
    def __call__(self, settings: Settings) -> SchemaInspection:
        """Read-only inspect settings.database_path while its root lock is held.

        Does not create/open a missing file, migrate, change journal mode, or
        acquire another lock. Caller owns the data-root lock for this exact
        Settings and the Inspector closes its own read-only SQLite handle.
        """
        ...


class ConsistentBackupSnapshotter(Protocol):
    def __call__(
        self, settings: Settings, *, backup_id: str, created_at: str
    ) -> BackupManifest:
        """Create and publish one validated snapshot under the fixed backup root.

        Caller already holds the matching Backend data-root lock (the web app
        owns it for its lifespan; an offline host operation acquires it).
        Uses SQLite's online backup API, validates the closed snapshot, writes
        the exact shared manifest atomically, and returns only after publication.
        It never imports or calls the operator-only Infrastructure package.
        """
        ...


class ManagerBootstrap(Protocol):
    def __call__(
        self, settings: Settings, *, login_id: str, display_name: str, password: str
    ) -> BootstrapResult:
        """Create the first manager; CLI obtains password with no-echo input."""
        ...


class RestoredAuthenticationInvalidator(Protocol):
    def __call__(
        self,
        settings: Settings,
        *,
        source_backup_id: str,
        operator_provenance: OperatorProvenance,
    ) -> None:
        """Invalidate authentication in an existing staged candidate.

        Backend owns/releases this staged-root lock internally. Infra must not
        call it while holding ``acquire_data_root_lock(settings)``.
        """
        ...


class ManagerRecovery(Protocol):
    def __call__(
        self,
        settings: Settings,
        *,
        account_id: str,
        password: str,
        verification_method: Literal["in_person", "phone"],
    ) -> None:
        """Recover an existing active manager; Backend owns the data-root lock."""
        ...


class OperatorProvenanceProvider(Protocol):
    def __call__(self) -> OperatorProvenance:
        """Return OS-derived provenance, never caller-supplied display data."""
        ...


class OfflineRestoreInterfaces(Protocol):
    """Existing Backend hooks plus the required read-only inspector.

    Resolve these from backend.database, backend.backups, backend.recovery,
    and backend.operator; there is no backend.operations module or restore
    wrapper in this interface. ``inspect_database`` and the consistent
    snapshotter do not acquire a data-root lock. Both the web app (which owns
    its lifecycle lock) and Infra (which acquires the offline lock) call the
    same Backend snapshotter. Infra still owns storage ACLs, bundle validation,
    replacement, recovery workflow and operator prompts.
    Infra holds the matching root lock during direct inspection/initialization,
    then releases it before invoking the self-locking invalidator.
    """

    acquire_data_root_lock: DataRootLockFactory
    initialize_database: DatabaseInitializer
    inspect_database: DatabaseSchemaInspector
    create_consistent_snapshot: ConsistentBackupSnapshotter
    invalidate_restored_authentication: RestoredAuthenticationInvalidator
    recover_manager: ManagerRecovery
    current_operator_provenance: OperatorProvenanceProvider


class RuntimeInterfaces(Protocol):
    """Named module-callable signatures required of the Backend package."""

    create_app: CreateApp
    acquire_data_root_lock: DataRootLockFactory
    connect: ConnectionFactory
    initialize_database: DatabaseInitializer
    inspect_database: DatabaseSchemaInspector
    create_consistent_snapshot: ConsistentBackupSnapshotter
    bootstrap_manager: ManagerBootstrap
