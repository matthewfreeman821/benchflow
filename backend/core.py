"""Internal typed failures and bound SQL helpers; never include business text in errors."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from contracts.errors import ErrorCode
from contracts.events import EventActor, EventRecord, EventRef
from backend.audit import append_event


class Rejected(Exception):
    def __init__(self, code: str, public_message: str | None = None):
        self.code = ErrorCode(code)
        # Only explicitly approved, non-sensitive wording is carried to HTTP.
        # Domain details, SQL and persisted business text must never be placed here.
        self.public_message = public_message
        super().__init__(code)


def require(condition, code="VALIDATION_FAILED"):
    if not condition:
        raise Rejected(code)


def uid():
    return str(uuid4())


def instant(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def stamp(value):
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def later(now, **delta):
    return stamp(instant(now) + timedelta(**delta))


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def one(db, sql, args=(), *, required=True):
    row = db.execute(sql, args).fetchone()
    if required and row is None:
        raise Rejected("NOT_FOUND")
    return dict(row) if row is not None else None


def rows(db, sql, args=()):
    return [dict(row) for row in db.execute(sql, args)]


def insert(db, table, **values):
    # All callers supply backend-owned constant identifiers, never request keys.
    names = ",".join(values)
    db.execute(f"INSERT INTO {table} ({names}) VALUES ({','.join('?' for _ in values)})",
               tuple(values.values()))
    return values.get("id")


def event(db, actor, aggregate_type, aggregate_id, action, now, *, command_id=None,
          reason=None, refs=(), verification=None):
    event_id = uid()
    append_event(db, EventRecord(
        event_id=event_id, aggregate_type=aggregate_type, aggregate_id=aggregate_id,
        event_type=action, event_version=1, command_id=command_id,
        actor=EventActor(kind=actor["kind"], id=actor["id"]),
        operator_provenance=None, verification_method=verification, occurred_at=now,
        reason_code=reason, summary_code=action.replace(".", "_"),
        refs=[EventRef(type=kind, id=id_) for kind, id_ in refs],
    ))
    return event_id


def revision(expected, key, actual):
    require(expected[key] == actual, "ESTIMATE_STALE" if key == "estimate_version" else "STALE_REVISION")


def staff_roles(db, account_id):
    return [row["role"] for row in rows(
        db, "SELECT role FROM staff_roles WHERE account_id=? ORDER BY role", (account_id,))]


def account_actor(db, account_id):
    account = one(db, "SELECT * FROM accounts WHERE id=?", (account_id,))
    account["roles"] = staff_roles(db, account_id)
    link = one(db, "SELECT customer_id FROM customer_accounts WHERE account_id=?",
               (account_id,), required=False)
    account["customer_id"] = link["customer_id"] if link else None
    return account


def visible_job(db, actor, job_id):
    job = one(db, "SELECT * FROM jobs WHERE id=?", (job_id,))
    if actor["kind"] == "customer":
        require(actor["customer_id"] == job["customer_id"], "NOT_FOUND")
    elif not ({"manager", "intake"} & set(actor["roles"])):
        require(one(db, "SELECT 1 FROM job_assignments WHERE job_id=? AND staff_id=?",
                    (job_id, actor["id"]), required=False) is not None, "NOT_FOUND")
    return job


def eligible_staff(db, account_id, roles=None):
    staff = account_actor(db, account_id)
    require(staff["kind"] == "staff" and staff["state"] == "active")
    if roles:
        require(bool(set(staff["roles"]) & set(roles)))
    return staff
