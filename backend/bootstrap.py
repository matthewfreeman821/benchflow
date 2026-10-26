"""Host-local first-manager bootstrap. There is no HTTP bootstrap route."""

from __future__ import annotations

from uuid import uuid4

from config.settings import Settings
from contracts.events import EventActor, EventRecord, EventRef
from contracts.runtime import BootstrapResult

from backend.audit import append_event
from backend.credentials import hash_password, normalize_login
from backend.database import acquire_data_root_lock, connect, immediate_transaction, initialize_database, utc_now
from backend.operator import current_operator_provenance


def bootstrap_manager(
    settings: Settings, *, login_id: str, display_name: str, password: str
) -> BootstrapResult:
    """Only an entirely empty identity store may bootstrap a first manager.

    Running this against an initialized shop never creates another account or
    overwrites any password. Operator recovery is the separate host-only path.
    """
    normalized = normalize_login(login_id)
    if not isinstance(display_name, str) or not 1 <= len(display_name.strip()) <= 2000:
        raise ValueError("DISPLAY_NAME_INVALID")
    encoded = hash_password(password)
    provenance = current_operator_provenance()
    with acquire_data_root_lock(settings):
        initialize_database(settings)
        connection = connect(settings)
        try:
            with immediate_transaction(connection):
                existing = connection.execute(
                    "SELECT a.id FROM accounts a JOIN staff_roles r ON r.account_id=a.id "
                    "WHERE r.role='manager' ORDER BY a.created_at, a.id LIMIT 1"
                ).fetchone()
                if existing is not None:
                    return BootstrapResult(existing["id"], "already_initialized")
                if connection.execute("SELECT 1 FROM accounts LIMIT 1").fetchone() is not None:
                    raise ValueError("OPERATOR_RECOVERY_REQUIRED")
                account_id, now = str(uuid4()), utc_now()
                connection.execute(
                    "INSERT INTO accounts(id,kind,state,display_name,login_id,login_normalized,"
                    "password_hash,credential_state,created_at,updated_at) "
                    "VALUES (?,'staff','active',?,?,?,?,'usable',?,?)",
                    (account_id, display_name, login_id, normalized, encoded, now, now),
                )
                connection.execute("INSERT INTO staff_roles(account_id,role) VALUES (?,'manager')", (account_id,))
                append_event(connection, EventRecord(
                    event_id=str(uuid4()), aggregate_type="account", aggregate_id=account_id,
                    event_type="account.invite_staff", event_version=1, command_id=None,
                    actor=EventActor(kind="operator", id=None), operator_provenance=provenance,
                    verification_method=None, occurred_at=now, reason_code=None,
                    summary_code="manager_bootstrapped", refs=[EventRef(type="account", id=account_id)],
                ))
                return BootstrapResult(account_id, "created")
        finally:
            connection.close()
