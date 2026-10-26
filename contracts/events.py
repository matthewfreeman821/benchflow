"""Minimized append-only event shape; write policy remains Backend-owned."""

from __future__ import annotations

from typing import Literal

import re

from pydantic import Field, StrictInt, StrictStr, field_validator

from contracts.actions import ACTION_NAMES, ReasonCode
from contracts.common import InstantString, StrictModel, UUIDString

_NON_ACTION_EVENT_TYPES = (
    "auth.login_succeeded", "auth.login_failed", "auth.logout",
    "auth.invite_issued", "auth.invite_revoked", "auth.invite_redeemed",
    "auth.recovery_issued", "auth.recovery_redeemed", "auth.account_suspended",
    "auth.roles_changed", "auth.account_unlinked", "contact.change_requested",
    "contact.change_resolved", "migration.applied", "backup.created", "restore.completed",
)
EventType = Literal.__getitem__(tuple(sorted(set(ACTION_NAMES) | set(_NON_ACTION_EVENT_TYPES))))


class EventActor(StrictModel):
    kind: Literal["staff", "customer", "operator", "system"]
    id: UUIDString | None


class EventRef(StrictModel):
    type: StrictStr
    id: UUIDString


class OperatorProvenance(StrictModel):
    windows_sid: StrictStr
    windows_account: StrictStr
    process_image: StrictStr
    pid: StrictInt


class EventRecord(StrictModel):
    event_id: UUIDString
    aggregate_type: Literal[
        "job", "account", "customer", "part", "qc_profile", "return_case",
        "contact_change_request", "shop", "backup",
    ]
    aggregate_id: UUIDString
    event_type: EventType
    event_version: StrictInt = Field(ge=1)
    command_id: UUIDString | None
    actor: EventActor
    operator_provenance: OperatorProvenance | None
    verification_method: Literal["in_person", "phone"] | None
    occurred_at: InstantString
    reason_code: ReasonCode | None
    summary_code: StrictStr
    refs: list[EventRef]

    @field_validator("summary_code")
    @classmethod
    def summary_code_is_stable_token(cls, value: str) -> str:
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", value):
            raise ValueError("summary_code must be a closed-format code")
        return value


__all__ = ["EventActor", "EventRecord", "EventRef", "OperatorProvenance"]
