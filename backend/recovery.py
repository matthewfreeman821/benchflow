"""Host-only offline authentication invalidation and verified manager recovery.

No function in this module is an HTTP route. Infra must call invalidation on
the staged restored database BEFORE replacement, with the application stopped.
"""

from __future__ import annotations

from uuid import uuid4

from config.settings import Settings
from contracts.common import is_canonical_uuid
from contracts.events import EventActor, EventRecord, EventRef, OperatorProvenance

from backend.audit import append_event
from backend.credentials import hash_password
from backend.database import acquire_data_root_lock, connect, immediate_transaction, initialize_database, utc_now
from backend.operator import current_operator_provenance


def invalidate_restored_authentication(
    settings: Settings, *, source_backup_id: str, operator_provenance: OperatorProvenance
) -> None:
    """Invalidate ALL authentication in a staged, lockable data root atomically.

    ``settings.database_path`` is the candidate staged SQLite database. It must
    already exist. Infra owns fixed-root containment/manifest/ACL validation,
    pre-restore preservation, same-volume staging/replacement and WAL handling.
    This helper never copies/replaces the live database and never starts an app.
    It fails if the app holds the candidate root's exclusive lock.

    Only the current complete checksum-valid schema is supported, or a ledger
    that initialize_database can migrate forward using every installed version.
    If migration/invalidation fails, Infra must discard the candidate and must
    not replace live storage. No password, code, or session is returned.
    """
    if not is_canonical_uuid(source_backup_id):
        raise ValueError("BACKUP_ID_INVALID")
    # Revalidate the exact shared model, rather than accepting arbitrary dicts.
    provenance = OperatorProvenance.model_validate(operator_provenance)
    if not settings.database_path.is_file():
        raise ValueError("STAGED_DATABASE_REQUIRED")
    with acquire_data_root_lock(settings):
        initialize_database(settings)
        connection = connect(settings)
        try:
            with immediate_transaction(connection):
                connection.execute("DELETE FROM sessions")
                connection.execute("DELETE FROM auth_codes")
                connection.execute("DELETE FROM auth_throttles")
                connection.execute("UPDATE invitations SET state='revoked' WHERE state='pending'")
                now = utc_now()
                connection.execute(
                    "UPDATE accounts SET password_hash=NULL, credential_state='reset_required', "
                    "auth_rev=auth_rev+1, account_rev=account_rev+1, updated_at=?", (now,),
                )
                append_event(connection, EventRecord(
                    event_id=str(uuid4()), aggregate_type="shop", aggregate_id=source_backup_id,
                    event_type="restore.completed", event_version=1, command_id=None,
                    actor=EventActor(kind="operator", id=None), operator_provenance=provenance,
                    verification_method=None, occurred_at=now, reason_code=None,
                    summary_code="restore_authentication_invalidated",
                    refs=[EventRef(type="backup", id=source_backup_id)],
                ))
        finally:
            connection.close()


def recover_manager(
    settings: Settings, *, account_id: str, password: str, verification_method: str
) -> None:
    """Verified manager personally enters their new password via no-echo CLI.

    Only an existing active manager is eligible. Never creates an account,
    changes roles, resurrects a closed account, or prints a recovery secret.
    """
    if not is_canonical_uuid(account_id) or verification_method not in {"in_person", "phone"}:
        raise ValueError("RECOVERY_INPUT_INVALID")
    provenance = current_operator_provenance()
    encoded = hash_password(password)
    with acquire_data_root_lock(settings):
        initialize_database(settings)
        connection = connect(settings)
        try:
            with immediate_transaction(connection):
                manager = connection.execute(
                    "SELECT a.id FROM accounts a JOIN staff_roles r ON r.account_id=a.id "
                    "WHERE a.id=? AND a.kind='staff' AND a.state='active' AND r.role='manager'",
                    (account_id,),
                ).fetchone()
                if manager is None:
                    raise ValueError("ACTIVE_MANAGER_REQUIRED")
                now = utc_now()
                connection.execute(
                    "UPDATE accounts SET password_hash=?,credential_state='usable',"
                    "auth_rev=auth_rev+1,account_rev=account_rev+1,updated_at=? WHERE id=?",
                    (encoded, now, account_id),
                )
                connection.execute("DELETE FROM sessions WHERE account_id=?", (account_id,))
                connection.execute(
                    "UPDATE auth_codes SET state='revoked' WHERE account_id=? AND state='pending'", (account_id,),
                )
                connection.execute(
                    "UPDATE invitations SET state='revoked' WHERE account_id=? AND state='pending'", (account_id,),
                )
                append_event(connection, EventRecord(
                    event_id=str(uuid4()), aggregate_type="account", aggregate_id=account_id,
                    event_type="auth.recovery_redeemed", event_version=1, command_id=None,
                    actor=EventActor(kind="operator", id=None), operator_provenance=provenance,
                    verification_method=verification_method, occurred_at=now, reason_code=None,
                    summary_code="manager_recovered",
                    refs=[EventRef(type="account", id=account_id)],
                ))
        finally:
            connection.close()
