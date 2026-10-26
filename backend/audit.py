"""Append minimized shared events in the business transaction."""

from __future__ import annotations

import json
import sqlite3

from contracts.events import EventRecord


def append_event(connection: sqlite3.Connection, event: EventRecord) -> None:
    if not connection.in_transaction:
        raise RuntimeError("EVENT_REQUIRES_TRANSACTION")
    record = event.model_dump(mode="json")
    # Column identifiers and event mapping are static. Values are always bound.
    connection.execute(
        "INSERT INTO audit_events("
        "id,aggregate_type,aggregate_id,event_type,event_version,command_id,"
        "actor_kind,actor_id,operator_provenance_json,verification_method,"
        "occurred_at,reason_code,summary_code,refs_json"
        ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            record["event_id"], record["aggregate_type"], record["aggregate_id"],
            record["event_type"], record["event_version"], record["command_id"],
            record["actor"]["kind"], record["actor"]["id"],
            json.dumps(record["operator_provenance"], separators=(",", ":"))
            if record["operator_provenance"] is not None else None,
            record["verification_method"], record["occurred_at"], record["reason_code"],
            record["summary_code"], json.dumps(record["refs"], separators=(",", ":")),
        ),
    )
