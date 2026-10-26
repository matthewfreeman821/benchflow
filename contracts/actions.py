"""Closed command catalog and strict Pydantic command-data models.

The declarative action registry is the single source for Pydantic validation,
JSON Schema export, expected-revision checks, and target-shape checks. Domain
authorization and child-to-job membership remain backend responsibilities.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any, Literal, TypeAlias
from uuid import UUID

from pydantic import Field, StrictInt, StrictStr, create_model, field_validator

from contracts.common import (
    AccessoriesInput,
    AcknowledgementInput,
    ConsumeItemInput,
    CurrencyCode,
    DeviceInput,
    EstimateLineInput,
    InstantString,
    KnownString,
    MoneyInput,
    NonNegativeAmount,
    PositiveRevision,
    QCCheckInput,
    QCProfileCheckInput,
    Quantity,
    StrictModel,
    Text,
    UUIDString,
    is_canonical_uuid,
)

ReasonCode: TypeAlias = Literal[
    "customer_request",
    "customer_declined",
    "safety",
    "scope_change",
    "stock_count",
    "damage",
    "inspected_return",
    "record_correction",
    "verified_duplicate_merge",
    "mistaken_link",
    "role_change",
    "suspension",
    "reactivation",
    "invite_revocation",
    "contact_resolution",
    "coverage_review",
    "balance_exception",
    "unrepaired_return",
    "manual_record",
    "other_allowed_business_reason",
    "dispute_resolution",
    "no_fault_found",
    "unrepairable",
    "task_reassignment",
    "diagnostic_fee",
    "tax_recording",
]


class Target(StrictModel):
    type: Literal["job", "customer", "part", "account", "qc_profile", "return_case", "policy", "contact_change_request"]
    id: UUIDString


class CommandEnvelope(StrictModel):
    command_id: UUIDString
    expected: dict[StrictStr, StrictInt | None]
    action: StrictStr
    data: dict[StrictStr, Any]
    target: Target | None = None

    @field_validator("command_id")
    @classmethod
    def command_id_is_uuid4(cls, value: str) -> str:
        parsed = UUID(value)
        if parsed.version != 4 or str(parsed) != value:
            raise ValueError("command_id must be a canonical UUID v4")
        return value

    @field_validator("expected")
    @classmethod
    def revisions_are_positive_or_null(cls, value: dict[str, int | None]) -> dict[str, int | None]:
        if any(revision is not None and revision < 1 for revision in value.values()):
            raise ValueError("revision values must be positive integers")
        return value


@dataclass(frozen=True)
class ParsedCommand:
    envelope: CommandEnvelope
    typed_data: StrictModel


def _model(name: str, fields: dict[str, Any]) -> type[StrictModel]:
    """Create an exact, extras-forbidden data model from its frozen field map."""
    return create_model(
        name,
        __base__=StrictModel,
        **{field_name: (field_type, ...) for field_name, field_type in fields.items()},
    )


_LIST_UUID = list[UUIDString]
_ROLES = list[Literal["intake", "technician", "manager"]]
_REQUEST_FIELDS = list[Literal["display_name", "phone", "email"]]

ACTION_DATA_MODELS: dict[str, type[StrictModel]] = {
    "customer.create": _model("CustomerCreateData", {
        "display_name": Text, "phone": KnownString, "email": KnownString, "notes": StrictStr | None,
    }),
    "customer.update_contact": _model("CustomerUpdateContactData", {
        "display_name": Text, "phone": KnownString, "email": KnownString, "reason": ReasonCode,
    }),
    "customer.merge": _model("CustomerMergeData", {
        "source_customer_id": UUIDString, "target_customer_id": UUIDString,
        "verification_method": Literal["in_person", "phone"], "reason": ReasonCode,
    }),
    "account.invite_customer": _model("InviteCustomerData", {
        "customer_id": UUIDString, "verification_method": Literal["in_person", "phone"],
        "contact_handoff_note": StrictStr | None, "owner_staff_id": UUIDString,
        "next_action_due_at": InstantString,
    }),
    "account.invite_staff": _model("InviteStaffData", {
        "display_name": Text, "roles": _ROLES, "reason": ReasonCode,
        "owner_staff_id": UUIDString, "next_action_due_at": InstantString,
    }),
    "account.reissue_invite": _model("ReissueInviteData", {"account_id": UUIDString, "reason": ReasonCode}),
    "account.revoke_invite": _model("RevokeInviteData", {"account_id": UUIDString, "reason_code": ReasonCode}),
    "account.change_roles": _model("ChangeRolesData", {"account_id": UUIDString, "roles": _ROLES, "reason": ReasonCode}),
    "account.suspend": _model("SuspendAccountData", {"account_id": UUIDString, "reason": ReasonCode}),
    "account.reactivate": _model("ReactivateAccountData", {"account_id": UUIDString, "reason": ReasonCode}),
    "account.close": _model("CloseAccountData", {"account_id": UUIDString, "reason": ReasonCode}),
    "account.unlink_customer": _model("UnlinkCustomerData", {
        "account_id": UUIDString, "customer_id": UUIDString, "reason": ReasonCode,
    }),
    "account.issue_recovery": _model("IssueRecoveryData", {
        "account_id": UUIDString, "verification_method": Literal["in_person", "phone", "manager_verified"],
        "reason": ReasonCode,
    }),
    "contact.change_request": _model("ContactChangeRequestData", {
        "requested_fields": _REQUEST_FIELDS, "display_name": KnownString,
        "phone": KnownString, "email": KnownString,
    }),
    "contact.change_resolve": _model("ContactChangeResolveData", {
        "request_id": UUIDString, "decision": Literal["accept", "reject"],
        "confirmation_method": Literal["in_person", "phone"], "reason_code": ReasonCode,
    }),
    "job.create": _model("JobCreateData", {
        "customer_id": UUIDString, "device": DeviceInput, "reported_issue": Text,
        "condition": KnownString, "accessories": AccessoriesInput,
        "testing_constraints": KnownString, "received_by_customer_name": Text,
        "acknowledgement": AcknowledgementInput, "owner_staff_id": UUIDString,
        "next_action": Text, "next_action_due_at": InstantString,
    }),
    "job.assign": _model("JobAssignData", {
        "staff_ids": _LIST_UUID, "owner_staff_id": UUIDString, "reason": ReasonCode,
    }),
    "job.update_follow_up": _model("JobFollowUpData", {
        "owner_staff_id": UUIDString | None, "next_action": StrictStr | None,
        "next_action_due_at": InstantString | None, "state": Literal["due", "resolved"],
        "reason": ReasonCode | None,
    }),
    "job.record_contact_attempt": _model("ContactAttemptData", {
        "channel": Literal["phone", "in_person", "other_manual"],
        "outcome": Literal["no_answer", "message_left", "spoke", "customer_declined", "callback_requested"],
        "summary": Text, "occurred_at": InstantString | None,
    }),
    "job.cancel": _model("CancelJobData", {
        "requested_by": Literal["customer", "staff"], "requester_attribution": Text, "reason": ReasonCode,
    }),
    "job.reopen": _model("ReopenJobData", {
        "reason": ReasonCode, "next_action": Text, "next_action_due_at": InstantString,
    }),
    "job.record_disposition": _model("RecordDispositionData", {
        "disposition": Literal["no_fault_found", "unrepairable"],
        "finding": Text, "owner_staff_id": UUIDString, "next_action": Text,
        "next_action_due_at": InstantString, "reason": ReasonCode,
    }),
    "job.open_dispute": _model("OpenDisputeData", {
        "summary": Text, "owner_staff_id": UUIDString, "next_action": Text,
        "next_action_due_at": InstantString, "reason": ReasonCode,
    }),
    "job.resolve_dispute": _model("ResolveDisputeData", {
        "disposition_id": UUIDString, "resolution_summary": Text,
        "owner_staff_id": UUIDString, "next_action": Text,
        "next_action_due_at": InstantString, "reason": ReasonCode,
    }),
    "diagnostic.permission": _model("DiagnosticPermissionData", {
        "decision": Literal["request", "grant", "decline", "revoke"], "scope": Text,
        "fee": MoneyInput, "fee_source": Literal["configured_policy"],
        "method": Literal["customer_account", "phone", "in_person"],
        "attribution": StrictStr | None, "expected_decision_id": UUIDString | None,
    }),
    "diagnostic.record": _model("DiagnosticRecordData", {
        "task_id": UUIDString | None, "kind": Literal["test", "finding", "limitation"],
        "summary": Text, "customer_text": StrictStr | None, "customer_visible": bool,
        "uncertainty": StrictStr | None,
    }),
    "estimate.draft_save": _model("EstimateDraftSaveData", {
        "draft_id": UUIDString | None, "lines": list[EstimateLineInput],
        "currency": CurrencyCode, "tax_minor": NonNegativeAmount, "tax_label": Text,
        "tax_source": Literal["not_configured", "manual_shop_entry"],
    }),
    "estimate.issue": _model("EstimateIssueData", {
        "draft_id": UUIDString, "expires_at": InstantString, "change_reason": Text,
    }),
    "estimate.decision": _model("EstimateDecisionData", {
        "decision": Literal["approve", "decline"], "version": PositiveRevision,
        "method": Literal["customer_account", "phone", "in_person"],
        "attribution": StrictStr | None,
        "confirmation": _model("EstimateConfirmation", {
            "version": PositiveRevision, "total_minor": NonNegativeAmount,
            "currency": CurrencyCode, "expires_at": InstantString,
        }),
    }),
    "estimate.revoke": _model("EstimateRevokeData", {
        "version": PositiveRevision, "reason": ReasonCode, "attribution": StrictStr | None,
        "method": Literal["customer_account", "phone", "in_person"],
    }),
    "parts.create": _model("PartCreateData", {
        "sku": Text, "description": Text, "unit": Text,
        "initial_qty": Annotated[StrictInt, Field(ge=0, le=10_000)], "reason": ReasonCode,
    }),
    "parts.adjust": _model("PartAdjustData", {
        "part_id": UUIDString, "delta_qty": StrictInt, "reason": ReasonCode,
        "source": Literal["count", "damage", "return_to_stock", "correction"],
        "source_movement_id": UUIDString | None, "inspection_note": StrictStr | None,
    }),
    "parts.demand": _model("PartDemandData", {
        "part_id": UUIDString, "job_id": UUIDString, "qty": Quantity,
        "scope_ref": UUIDString, "needed_by": InstantString | None,
    }),
    "parts.reserve": _model("PartReserveData", {
        "part_id": UUIDString, "job_id": UUIDString, "qty": Quantity,
        "scope_ref": UUIDString, "reason": ReasonCode | None,
    }),
    "parts.release": _model("PartReleaseData", {
        "reservation_id": UUIDString, "qty": Quantity, "reason": ReasonCode,
    }),
    "parts.manual_order": _model("ManualOrderData", {
        "part_id": UUIDString, "job_id": UUIDString, "qty": Quantity,
        "supplier_label": Text, "eta_at": InstantString | None, "eta_evidence": StrictStr | None,
        "placement_state": Literal["planned", "placed_unconfirmed"], "reason": ReasonCode,
    }),
    "parts.record_order_outcome": _model("RecordOrderOutcomeData", {
        "order_id": UUIDString, "outcome": Literal[
            "supplier_confirmed", "backordered", "cancellation_reported", "unavailable"
        ], "evidence": Text, "reason": ReasonCode,
    }),
    "parts.receive": _model("PartReceiveData", {
        "order_id": UUIDString | None, "part_id": UUIDString,
        "qty": Quantity, "source_note": Text,
    }),
    "work.start": _model("WorkStartData", {
        "task_id": UUIDString, "scope_ref": UUIDString, "consume": list[ConsumeItemInput],
    }),
    "work.task_create": _model("WorkTaskCreateData", {
        "job_id": UUIDString, "scope_ref": UUIDString,
        "scope_kind": Literal["diagnostic", "estimate_line"],
        "assigned_staff_id": UUIDString, "summary": Text,
    }),
    "work.task_reassign": _model("WorkTaskReassignData", {
        "task_id": UUIDString, "assigned_staff_id": UUIDString, "reason": ReasonCode,
    }),
    "work.log": _model("WorkLogData", {
        "task_id": UUIDString, "kind": Literal["note", "pause", "resume", "complete"],
        "summary": Text, "owner_staff_id": UUIDString | None, "next_action": StrictStr | None,
        "next_action_due_at": InstantString | None,
    }),
    "work.consume": _model("WorkConsumeData", {
        "task_id": UUIDString, "items": list[ConsumeItemInput],
    }),
    "qc.record": _model("QCRecordData", {
        "task_id": UUIDString, "profile_version": PositiveRevision,
        "checks": list[QCCheckInput], "overall": Literal["pass", "fail", "not_tested"],
        "reason": ReasonCode | None,
    }),
    "qc.profile_create": _model("QCProfileCreateData", {
        "label": Text, "checks": list[QCProfileCheckInput], "reason": ReasonCode,
    }),
    "qc.profile_update": _model("QCProfileUpdateData", {
        "profile_id": UUIDString, "checks": list[QCProfileCheckInput], "reason": ReasonCode,
    }),
    "return.create": _model("ReturnCreateData", {
        "original_job_id": UUIDString, "reported_recurrence": Text,
        "customer_attribution": StrictStr | None,
    }),
    "return.assess": _model("ReturnAssessData", {
        "return_case_id": UUIDString, "coverage": Literal["covered", "not_covered", "undetermined"],
        "reason": ReasonCode, "inspection_permission_id": UUIDString,
    }),
    "settlement.charge": _model("SettlementChargeData", {
        "job_id": UUIDString, "source": Literal["estimate_line", "estimate_tax", "diagnostic_fee"],
        "estimate_version": PositiveRevision | None, "line_ref": UUIDString | None,
        "permission_id": UUIDString | None, "amount_minor": NonNegativeAmount,
        "currency": CurrencyCode, "reason": ReasonCode | None,
    }),
    "settlement.credit": _model("SettlementCreditData", {
        "job_id": UUIDString, "charge_id": UUIDString | None,
        "amount_minor": NonNegativeAmount, "currency": CurrencyCode,
        "kind": Literal["correction", "write_off", "customer_credit"], "reason": ReasonCode,
    }),
    "settlement.payment_record": _model("PaymentRecordData", {
        "job_id": UUIDString, "amount_minor": NonNegativeAmount,
        "currency": CurrencyCode, "method": Literal["cash", "card_external", "bank_external", "other_external"],
        "reference": StrictStr | None, "received_at": InstantString | None,
    }),
    "settlement.payment_simulate": _model("SimulatedPaymentData", {
        "job_id": UUIDString, "amount_minor": NonNegativeAmount, "currency": CurrencyCode,
    }),
    "settlement.refund_record": _model("RefundRecordData", {
        "job_id": UUIDString, "payment_id": UUIDString,
        "amount_minor": NonNegativeAmount, "currency": CurrencyCode,
        "reference": StrictStr | None, "refunded_at": InstantString | None, "reason": ReasonCode,
    }),
    "settlement.correct": _model("SettlementCorrectionData", {
        "entry_id": UUIDString, "delta_minor": StrictInt,
        "currency": CurrencyCode, "external_reference": StrictStr | None, "reason": ReasonCode,
    }),
    "property.authorize_collector": _model("AuthorizeCollectorData", {
        "job_id": UUIDString, "collector_name": Text,
        "method": Literal["in_person", "phone"], "reason": ReasonCode | None,
    }),
    "property.handover": _model("HandoverData", {
        "job_id": UUIDString, "handover_kind": Literal["completed_repair", "return_unrepaired"],
        "collector_name": Text, "acknowledgement": Text,
        "manager_balance_override_reason": ReasonCode | None,
    }),
    "property.handover_simulate": _model("SimulatedHandoverData", {
        "job_id": UUIDString, "collector_name": Text,
    }),
    "policy.update_demo": _model("UpdateDemoPolicyData", {
        "shop_timezone": Text, "estimate_window_days": StrictInt,
        "diagnostic_fee_minor": NonNegativeAmount, "tax_enabled": bool, "reason": ReasonCode,
    }),
}

# These sets are normative target resolution. A nested ID is never a substitute
# for the top-level target; backend additionally verifies child membership.
JOB_TARGET_ACTIONS = frozenset({
    "job.assign", "job.update_follow_up", "job.record_contact_attempt", "job.cancel", "job.reopen",
    "job.record_disposition", "job.open_dispute", "job.resolve_dispute",
    "diagnostic.permission", "diagnostic.record", "estimate.draft_save", "estimate.issue",
    "estimate.decision", "estimate.revoke", "parts.demand", "parts.reserve", "parts.release",
    "parts.manual_order", "parts.record_order_outcome", "work.task_create", "work.task_reassign", "work.start",
    "work.log", "work.consume", "qc.record", "return.create", "return.assess",
    "settlement.charge", "settlement.credit", "settlement.payment_record", "settlement.payment_simulate",
    "settlement.refund_record", "settlement.correct", "property.authorize_collector", "property.handover",
    "property.handover_simulate",
})
CUSTOMER_TARGET_ACTIONS = frozenset({"job.create"})
CUSTOMER_AGGREGATE_ACTIONS = frozenset({"customer.update_contact"})
PARTS_RECEIVE_ACTION = "parts.receive"
PER_PART_STOCK_REVISION_ACTIONS = frozenset({"work.start", "work.consume"})

# Expected revision sets follow the action catalog. Null is allowed only for
# estimate_version, and only where action-specific semantics below explicitly
# identify diagnostic scope, initial issuance, or a diagnostic-fee charge.
EXPECTED_KEYS: dict[str, frozenset[str]] = {
    "customer.create": frozenset(),
    "customer.update_contact": frozenset({"customer_rev"}),
    "customer.merge": frozenset({"source_customer_rev", "target_customer_rev"}),
    "account.invite_customer": frozenset({"customer_rev"}),
    "account.invite_staff": frozenset(),
    "account.reissue_invite": frozenset({"account_rev"}),
    "account.revoke_invite": frozenset({"account_rev"}),
    "account.change_roles": frozenset({"account_rev"}),
    "account.suspend": frozenset({"account_rev"}),
    "account.reactivate": frozenset({"account_rev"}),
    "account.close": frozenset({"account_rev"}),
    "account.unlink_customer": frozenset({"account_rev", "customer_rev"}),
    "account.issue_recovery": frozenset({"account_rev"}),
    "contact.change_request": frozenset(),
    "contact.change_resolve": frozenset({"request_rev", "customer_rev"}),
    "job.create": frozenset({"customer_rev"}),
    "job.assign": frozenset({"job_rev"}),
    "job.update_follow_up": frozenset({"job_rev"}),
    "job.record_contact_attempt": frozenset({"job_rev"}),
    "job.cancel": frozenset({"job_rev", "gate_rev"}),
    "job.reopen": frozenset({"job_rev", "gate_rev"}),
    "job.record_disposition": frozenset({"job_rev", "gate_rev"}),
    "job.open_dispute": frozenset({"job_rev", "gate_rev"}),
    "job.resolve_dispute": frozenset({"job_rev", "gate_rev", "disposition_rev"}),
    "diagnostic.permission": frozenset({"job_rev", "gate_rev"}),
    "diagnostic.record": frozenset({"job_rev"}),
    "estimate.draft_save": frozenset({"job_rev"}),
    "estimate.issue": frozenset({"job_rev", "gate_rev", "estimate_version"}),
    "estimate.decision": frozenset({"job_rev", "gate_rev", "estimate_version"}),
    "estimate.revoke": frozenset({"job_rev", "gate_rev", "estimate_version"}),
    "parts.create": frozenset(),
    "parts.adjust": frozenset({"stock_rev"}),
    "parts.demand": frozenset({"job_rev", "gate_rev"}),
    "parts.reserve": frozenset({"job_rev", "gate_rev", "stock_rev", "estimate_version"}),
    "parts.release": frozenset({"job_rev", "gate_rev", "stock_rev"}),
    "parts.manual_order": frozenset({"job_rev"}),
    "parts.record_order_outcome": frozenset({"job_rev"}),
    "parts.receive": frozenset({"stock_rev"}),
    "work.start": frozenset({"job_rev", "gate_rev", "estimate_version"}),
    "work.task_create": frozenset({"job_rev", "estimate_version"}),
    "work.task_reassign": frozenset({"job_rev", "task_rev"}),
    "work.log": frozenset({"job_rev", "gate_rev"}),
    "work.consume": frozenset({"job_rev", "gate_rev", "estimate_version"}),
    "qc.record": frozenset({"job_rev", "gate_rev"}),
    "qc.profile_create": frozenset(),
    "qc.profile_update": frozenset({"profile_rev"}),
    "return.create": frozenset({"job_rev", "gate_rev"}),
    "return.assess": frozenset({"job_rev", "gate_rev"}),
    "settlement.charge": frozenset({"job_rev", "gate_rev", "estimate_version"}),
    "settlement.credit": frozenset({"job_rev"}),
    "settlement.payment_record": frozenset({"job_rev"}),
    "settlement.payment_simulate": frozenset({"job_rev"}),
    "settlement.refund_record": frozenset({"job_rev"}),
    "settlement.correct": frozenset({"job_rev"}),
    "property.authorize_collector": frozenset({"job_rev"}),
    "property.handover": frozenset({"job_rev", "gate_rev"}),
    "property.handover_simulate": frozenset({"job_rev", "gate_rev"}),
    "policy.update_demo": frozenset({"policy_rev"}),
}

TARGET_SHAPE: dict[str, tuple[str, str | None]] = {
    action: ("job", None) for action in JOB_TARGET_ACTIONS
}
TARGET_SHAPE.update({"job.create": ("customer", "customer_id")})
TARGET_SHAPE.update({action: ("customer", None) for action in CUSTOMER_AGGREGATE_ACTIONS})

ACTION_NAMES = frozenset(ACTION_DATA_MODELS)
if ACTION_NAMES != frozenset(EXPECTED_KEYS):
    raise RuntimeError("action data and expected-revision catalogs diverged")


def parse_command(payload: Any) -> ParsedCommand:
    """Validate the exact envelope, action-specific data, revisions, and target shape.

    This helper deliberately stops at the shared wire boundary. It does not
    authenticate, authorize, resolve records, or check target-child membership.
    """
    envelope = CommandEnvelope.model_validate(payload)
    action = envelope.action
    if action not in ACTION_DATA_MODELS:
        raise ValueError("unsupported action")

    expected_keys = set(EXPECTED_KEYS[action])
    stock_revision_keys: set[str] = set()
    if action in PER_PART_STOCK_REVISION_ACTIONS:
        stock_revision_keys = {
            key for key in envelope.expected if key.startswith("stock_rev:")
        }
        if any(
            not key.startswith("stock_rev:")
            or not is_canonical_uuid(key.removeprefix("stock_rev:"))
            for key in stock_revision_keys
        ):
            raise ValueError("per-part stock revision keys must contain canonical part UUIDs")
        expected_keys.update(stock_revision_keys)
        if action == "work.consume" and not stock_revision_keys:
            raise ValueError("work.consume requires a stock revision for every consumed part")
    if set(envelope.expected) != expected_keys:
        raise ValueError("expected revision keys do not match the action contract")
    nullable_revision_actions = {
        "estimate.issue", "parts.reserve", "work.start", "work.consume", "work.task_create",
    }
    if action == "settlement.charge":
        nullable_revision_actions.add(action)
    if any(
        value is None and not (key == "estimate_version" and action in nullable_revision_actions)
        for key, value in envelope.expected.items()
    ):
        raise ValueError("null is not allowed for this expected revision")

    typed_data = ACTION_DATA_MODELS[action].model_validate(envelope.data)

    if action == "settlement.charge":
        source_fields = {
            "estimate_line": (typed_data.estimate_version is not None,
                              typed_data.line_ref is not None, typed_data.permission_id is None),
            "estimate_tax": (typed_data.estimate_version is not None,
                             typed_data.line_ref is None, typed_data.permission_id is None),
            "diagnostic_fee": (typed_data.estimate_version is None,
                               typed_data.line_ref is None, typed_data.permission_id is not None),
        }[typed_data.source]
        if not all(source_fields):
            raise ValueError("charge source and provenance fields do not match")
        if (typed_data.source == "diagnostic_fee") != (envelope.expected["estimate_version"] is None):
            raise ValueError("diagnostic-fee charges require a null estimate_version; other charges require a version")
    if action == "estimate.draft_save":
        if typed_data.tax_source == "not_configured" and typed_data.tax_minor != 0:
            raise ValueError("unconfigured tax must be zero")

    target_spec = TARGET_SHAPE.get(action)
    if action == PARTS_RECEIVE_ACTION:
        target_spec = ("part", "part_id") if typed_data.order_id is None else ("job", None)
    if target_spec is None:
        if "target" in envelope.model_fields_set:
            raise ValueError("this action does not accept a top-level target")
    else:
        if envelope.target is None or envelope.target.type != target_spec[0]:
            raise ValueError("action requires its canonical top-level target")
        if target_spec[1] is not None and envelope.target.id != getattr(typed_data, target_spec[1]):
            raise ValueError("target must equal the action's parent aggregate")

    if action == "work.start" and typed_data.consume and not stock_revision_keys:
        raise ValueError("work.start requires revisions for parts consumed at start")
    if action == "customer.merge" and typed_data.source_customer_id == typed_data.target_customer_id:
        raise ValueError("merge source and target must differ")
    if action == "parts.adjust" and typed_data.delta_qty == 0:
        raise ValueError("delta_qty must not be zero")
    if action in {"account.invite_staff", "account.change_roles"}:
        if not typed_data.roles or len(set(typed_data.roles)) != len(typed_data.roles):
            raise ValueError("roles must be non-empty and unique")
    if action == "job.assign":
        if len(set(typed_data.staff_ids)) != len(typed_data.staff_ids):
            raise ValueError("staff_ids must be unique")
        if typed_data.owner_staff_id not in typed_data.staff_ids:
            raise ValueError("the owner must be included in the assigned staff")
    if action == "contact.change_request":
        if not typed_data.requested_fields or len(set(typed_data.requested_fields)) != len(typed_data.requested_fields):
            raise ValueError("requested_fields must be non-empty and unique")
    if action in {"estimate.draft_save", "qc.profile_create", "qc.profile_update", "work.consume"}:
        list_field = {
            "estimate.draft_save": "lines",
            "qc.profile_create": "checks",
            "qc.profile_update": "checks",
            "work.consume": "items",
        }[action]
        if not getattr(typed_data, list_field):
            raise ValueError(f"{list_field} must not be empty")
    if action == "diagnostic.record" and (typed_data.customer_text is not None) != typed_data.customer_visible:
        raise ValueError("customer_text is required exactly when customer_visible is true")
    if action == "diagnostic.permission":
        if typed_data.method == "customer_account" and typed_data.attribution is not None:
            raise ValueError("customer-account decisions cannot contain staff attribution")
        if typed_data.method != "customer_account" and typed_data.decision != "request" and not typed_data.attribution:
            raise ValueError("staff-recorded permission decisions require attribution")
        if typed_data.decision == "request" and typed_data.attribution is not None:
            raise ValueError("permission requests cannot contain decision attribution")
    if action == "estimate.decision":
        if typed_data.confirmation.version != typed_data.version:
            raise ValueError("confirmation must name the selected estimate version")
        if typed_data.method == "customer_account" and typed_data.attribution is not None:
            raise ValueError("customer-account decisions cannot contain staff attribution")
        if typed_data.method != "customer_account" and not typed_data.attribution:
            raise ValueError("staff-recorded decisions require attribution")
    if action == "estimate.revoke":
        if typed_data.method == "customer_account" and typed_data.attribution is not None:
            raise ValueError("customer-account decisions cannot contain staff attribution")
        if typed_data.method != "customer_account" and not typed_data.attribution:
            raise ValueError("staff-recorded revocations require attribution")
    if action == "work.log":
        pause_shape = (
            typed_data.owner_staff_id is not None
            and typed_data.next_action is not None
            and typed_data.next_action_due_at is not None
        )
        if (typed_data.kind == "pause") != pause_shape:
            raise ValueError("pause requires owner/action/due; other work log kinds require null fields")
    if action == "job.update_follow_up" and typed_data.state == "due":
        if not typed_data.next_action or typed_data.next_action_due_at is None:
            raise ValueError("due follow-up requires an action and due instant")
    if action == "work.task_create":
        if typed_data.scope_kind == "diagnostic" and envelope.expected["estimate_version"] is not None:
            raise ValueError("diagnostic tasks use a null estimate_version")
        if typed_data.scope_kind == "estimate_line" and envelope.expected["estimate_version"] is None:
            raise ValueError("repair tasks require the current estimate_version")
    if action == "work.task_reassign" and envelope.expected["task_rev"] is None:
        raise ValueError("task_rev is required")

    if envelope.target is not None:
        same_job_fields = {
            "parts.demand": "job_id",
            "parts.reserve": "job_id",
            "parts.manual_order": "job_id",
            "work.task_create": "job_id",
            "settlement.charge": "job_id",
            "settlement.credit": "job_id",
            "settlement.payment_record": "job_id",
            "settlement.payment_simulate": "job_id",
            "settlement.refund_record": "job_id",
            "property.authorize_collector": "job_id",
            "property.handover": "job_id",
            "property.handover_simulate": "job_id",
        }
        field_name = same_job_fields.get(action)
        if field_name is not None and getattr(typed_data, field_name) != envelope.target.id:
            raise ValueError("nested job_id must match the top-level job target")
        if action == "return.create" and typed_data.original_job_id != envelope.target.id:
            raise ValueError("return.create must target its original job")
    return ParsedCommand(envelope=envelope, typed_data=typed_data)


def command_json_schema() -> dict[str, Any]:
    """Return action-specific data schemas for API/OpenAPI integration."""
    return {
        action: model.model_json_schema(mode="validation")
        for action, model in sorted(ACTION_DATA_MODELS.items())
    }
