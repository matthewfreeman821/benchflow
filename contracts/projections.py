"""Named allowlist response shapes shared by Backend and Frontend."""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, StrictBool, StrictInt, StrictStr, field_validator, model_validator

from contracts.actions import ReasonCode
from contracts.common import CurrencyCode, InstantString, KnownString, MoneyInput, PositiveRevision, StrictModel, UUIDString
from contracts.events import EventRef, EventType, OperatorProvenance
from contracts.errors import ErrorEnvelope

BlockerCode = Literal[
    "DIAGNOSTIC_PERMISSION_REQUIRED", "DIAGNOSTIC_PERMISSION_PENDING",
    "ESTIMATE_DECISION_REQUIRED", "ESTIMATE_EXPIRED_UNAPPROVED", "ESTIMATE_REVISION_HOLD",
    "CONSENT_REVOKED", "CANCELLED", "SCOPE_MISMATCH", "PARTS_SHORTAGE",
    "PARTS_NOT_RESERVED", "WORK_PAUSED", "QC_FAILED", "QC_INCOMPLETE", "DISPUTED",
    "SAFETY_STOP", "FOLLOW_UP_DUE", "UNCOLLECTED", "BALANCE_DUE",
]
JobPhase = Literal[
    "intake", "diagnosis", "estimate", "awaiting_decision", "parts",
    "repair", "quality", "collection", "return", "complete",
]


class AccountView(StrictModel):
    id: UUIDString
    kind: Literal["staff", "customer"]
    state: Literal["invited", "active", "suspended", "closed"]
    invite_id: UUIDString | None
    invite_state: Literal["pending", "revoked", "expired", "superseded", "redeemed"] | None
    credential_state: Literal["usable", "reset_required", "not_set"]
    login_id: StrictStr | None
    roles: list[Literal["intake", "technician", "manager"]]
    customer_id: UUIDString | None
    display_name: StrictStr
    account_rev: PositiveRevision
    auth_rev: PositiveRevision
    invitation_owner_staff_id: UUIDString | None
    invitation_next_action_due_at: InstantString | None


class Blocker(StrictModel):
    code: BlockerCode
    label: StrictStr
    detail: StrictStr
    since: InstantString


class BalanceMoney(StrictModel):
    currency: CurrencyCode
    amount_minor: StrictInt


class StaffQueueOwner(StrictModel):
    kind: Literal["staff"]
    staff_id: UUIDString
    label: StrictStr


class QueueOwner(StrictModel):
    kind: Literal["queue"]
    id: Literal["manager_intake"]
    label: Literal["Manager intake"]


Owner = StaffQueueOwner | QueueOwner


class QueueItem(StrictModel):
    job_id: UUIDString
    job_code: StrictStr
    customer_label: StrictStr
    device_label: StrictStr
    phase: JobPhase | None
    lifecycle: Literal["open", "cancelled", "closed"]
    blockers: list[Blocker]
    owner: Owner
    next_action: StrictStr
    next_action_due_at: InstantString
    updated_at: InstantString
    job_rev: PositiveRevision
    gate_rev: PositiveRevision


class QueuePage(StrictModel):
    items: list[QueueItem]
    next_cursor: StrictStr | None


class CustomerStatusEvent(StrictModel):
    code: Literal[
        "received", "diagnosing", "waiting_for_approval", "waiting_for_parts",
        "repairing", "quality_check", "ready_for_collection", "follow_up", "returned",
    ]
    label: StrictStr
    explanation: StrictStr
    recorded_at: InstantString


class CustomerSafeBlocker(StrictModel):
    code: BlockerCode
    label: StrictStr


class CustomerStatus(StrictModel):
    code: Literal[
        "received", "diagnosing", "waiting_for_approval", "waiting_for_parts",
        "repairing", "quality_check", "ready_for_collection", "follow_up", "returned", "demo_complete",
    ]
    label: StrictStr
    explanation: StrictStr
    blockers: list[CustomerSafeBlocker]


class CustomerJobSummary(StrictModel):
    job_id: UUIDString
    job_code: StrictStr
    device_label: StrictStr
    opened_at: InstantString
    customer_status: CustomerStatus
    updated_at: InstantString


class CustomerDevice(StrictModel):
    label: StrictStr


class AnonymousSessionView(StrictModel):
    authenticated: Literal[False]
    csrf_token: StrictStr
    account: Literal[None]
    demo_mode: StrictBool = False


class AuthenticatedSessionView(StrictModel):
    authenticated: Literal[True]
    csrf_token: StrictStr
    account: AccountView
    idle_expires_at: InstantString
    absolute_expires_at: InstantString
    demo_mode: StrictBool = False


SessionView = Annotated[
    AnonymousSessionView | AuthenticatedSessionView,
    Field(discriminator="authenticated"),
]


class CustomerFinding(StrictModel):
    text: StrictStr
    recorded_at: InstantString


class CustomerDiagnosticPermission(StrictModel):
    permission_id: UUIDString
    state: Literal["granted", "declined", "revoked", "pending"]
    scope: StrictStr
    fee: MoneyInput
    fee_source: Literal["configured_policy"]
    fee_policy_rev: PositiveRevision | None
    method: Literal["customer_account", "phone", "in_person"]
    decided_at: InstantString


class CustomerEstimateLine(StrictModel):
    line_id: UUIDString
    description: StrictStr
    quantity: StrictInt
    unit_amount_minor: StrictInt
    line_amount_minor: StrictInt


class CustomerEstimateChange(StrictModel):
    field: StrictStr
    before: StrictStr | None
    after: StrictStr | None


class CustomerEstimate(StrictModel):
    version: PositiveRevision
    state: Literal["issued", "expired_unapproved"]
    issued_at: InstantString
    expires_at: InstantString
    shop_timezone: StrictStr
    currency: StrictStr
    subtotal_minor: StrictInt
    tax_minor: StrictInt
    total_minor: StrictInt
    tax_label: StrictStr
    tax_source: Literal["not_configured", "manual_shop_entry"]
    tax_policy_rev: PositiveRevision | None
    lines: list[CustomerEstimateLine]
    change_from_previous: list[CustomerEstimateChange]
    decision: Literal["none", "pending", "approved", "declined", "revoked"]


class CustomerDecisionEvent(StrictModel):
    decision_id: UUIDString
    estimate_version: PositiveRevision
    decision: Literal["approved", "declined", "revoked"]
    method: Literal["customer_account", "phone", "in_person"]
    recorded_by: Literal["you", "shop_staff"]
    at: InstantString


class CustomerQualityCheck(StrictModel):
    state: Literal["not_required", "not_tested", "in_progress", "passed", "failed"]
    method: Literal["same_technician", "different_technician", "not_applicable"]
    disclosure: StrictStr | None


class CustomerHandover(StrictModel):
    recorded_at: InstantString | None
    acknowledgement: StrictStr | None
    collector_name: StrictStr | None
    simulated: StrictBool = False


class CustomerCollection(StrictModel):
    ready: StrictBool
    balance: BalanceMoney
    what_to_bring: StrictStr
    authorized_collector: StrictStr
    handover: CustomerHandover


class CustomerReturnRequest(StrictModel):
    allowed: StrictBool
    existing_case_id: UUIDString | None
    linked_return_job_ids: list[UUIDString]


class AccessoriesView(StrictModel):
    known: StrictBool
    items: list[StrictStr]


class CustomerSummary(StrictModel):
    customer_id: UUIDString
    display_name: StrictStr
    phone_masked: StrictStr | None
    email_masked: StrictStr | None
    possible_duplicate: StrictBool
    account_state: Literal["none", "invited", "active", "suspended", "closed"]
    updated_at: InstantString


class CustomerContactChangeStatus(StrictModel):
    """Owner-safe status; never includes staff notes or another customer."""

    request_id: UUIDString
    requested_fields: list[Literal["display_name", "phone", "email"]]
    state: Literal["pending", "accepted", "rejected"]
    created_at: InstantString
    resolved_at: InstantString | None


class CustomerContactChangePage(StrictModel):
    items: list[CustomerContactChangeStatus]
    next_cursor: StrictStr | None


class ContactChangeQueueItem(StrictModel):
    request_id: UUIDString
    customer_label: StrictStr
    requested_fields: list[Literal["display_name", "phone", "email"]]
    state: Literal["pending", "accepted", "rejected"]
    created_at: InstantString


class ContactChangeQueuePage(StrictModel):
    items: list[ContactChangeQueueItem]
    next_cursor: StrictStr | None


class ContactChangeValues(StrictModel):
    """Values requested for the listed fields; unrequested fields are null."""

    display_name: KnownString | None
    phone: KnownString | None
    email: KnownString | None


class ContactChangeRequestDetail(StrictModel):
    request_id: UUIDString
    customer_id: UUIDString
    customer_label: StrictStr
    request_rev: PositiveRevision
    customer_rev: PositiveRevision
    requested_fields: list[Literal["display_name", "phone", "email"]]
    requested_values: ContactChangeValues
    state: Literal["pending", "accepted", "rejected"]
    created_at: InstantString
    resolved_at: InstantString | None
    confirmation_method: Literal["in_person", "phone"] | None
    reason_code: ReasonCode | None


class AssignableStaffOption(StrictModel):
    staff_id: UUIDString
    display_name: StrictStr
    roles: list[Literal["intake", "technician", "manager"]]


class AssignableStaffDirectory(StrictModel):
    items: list[AssignableStaffOption]


class PossibleDuplicate(StrictModel):
    customer_id: UUIDString
    display_name: StrictStr
    match_reasons: list[Literal["name", "phone", "email"]]


class CustomerStaffDetail(StrictModel):
    customer_id: UUIDString
    display_name: StrictStr
    phone: KnownString
    email: KnownString
    notes: StrictStr | None
    possible_duplicates: list[PossibleDuplicate]
    account_id: UUIDString | None
    jobs: list[QueueItem]
    customer_rev: PositiveRevision


class PartSummary(StrictModel):
    part_id: UUIDString
    sku: StrictStr
    description: StrictStr
    unit: StrictStr
    on_hand_qty: StrictInt
    reserved_qty: StrictInt
    available_qty: StrictInt
    stock_rev: PositiveRevision


class StockMovementView(StrictModel):
    movement_id: UUIDString
    part_id: UUIDString
    kind: Literal["opening", "adjustment", "receipt", "reservation", "release", "consumption", "return_to_stock"]
    on_hand_delta: StrictInt
    reserved_delta: StrictInt
    job_id: UUIDString | None
    reservation_id: UUIDString | None
    source_movement_id: UUIDString | None
    actor_staff_id: UUIDString
    occurred_at: InstantString
    reason: StrictStr
    source_note: StrictStr | None


class PartDetail(StrictModel):
    part_id: UUIDString
    sku: StrictStr
    description: StrictStr
    unit: StrictStr
    on_hand_qty: StrictInt
    reserved_qty: StrictInt
    available_qty: StrictInt
    stock_rev: PositiveRevision
    movements: list[StockMovementView]


class QCProfileCheck(StrictModel):
    check_id: UUIDString
    label: StrictStr
    required: StrictBool


class QCProfileView(StrictModel):
    profile_id: UUIDString
    version: PositiveRevision
    profile_rev: PositiveRevision
    label: StrictStr
    checks: list[QCProfileCheck]
    active_for_new_tasks: StrictBool
    created_at: InstantString
    created_by_staff_id: UUIDString
    reason: StrictStr


class QCProfileDetail(StrictModel):
    """Current profile and its complete immutable version history."""

    profile: QCProfileView
    history: list[QCProfileView]

    @model_validator(mode="after")
    def history_is_complete_and_ordered(self) -> "QCProfileDetail":
        versions = [entry.version for entry in self.history]
        if not versions or versions != list(range(1, self.profile.version + 1)):
            raise ValueError("QC profile history must include every immutable version in ascending order")
        if self.history[-1] != self.profile:
            raise ValueError("QC profile history must end with the current profile")
        if any(
            item.profile_id != self.profile.profile_id
            or item.profile_rev != self.profile.profile_rev
            or item.active_for_new_tasks != (item.version == self.profile.version)
            for item in self.history
        ):
            raise ValueError("QC profile history must share the aggregate revision and mark only current active")
        return self


class DemoPolicyView(StrictModel):
    policy_rev: PositiveRevision
    shop_timezone: StrictStr
    estimate_window_days: StrictInt
    diagnostic_fee_minor: StrictInt
    currency: StrictStr
    tax_enabled: StrictBool
    tax_label: StrictStr


class BackupView(StrictModel):
    backup_id: UUIDString
    created_at: InstantString
    schema_version: PositiveRevision
    sha256: Annotated[StrictStr, Field(pattern=r"^[0-9a-f]{64}$")]
    size_bytes: Annotated[StrictInt, Field(ge=1)]
    integrity: Literal["verified_at_creation"]


class BackupPage(StrictModel):
    items: list[BackupView]
    next_cursor: StrictStr | None


class BackupManifest(StrictModel):
    """Exact private on-disk manifest; contains no host path or secret."""

    format_version: Literal[1]
    backup_id: UUIDString
    created_at: InstantString
    database_file: Literal["benchflow.sqlite3"]
    schema_version: PositiveRevision
    size_bytes: Annotated[StrictInt, Field(ge=1)]
    sha256: Annotated[StrictStr, Field(pattern=r"^[0-9a-f]{64}$")]
    integrity: Literal["ok"]


class BackupDetailView(StrictModel):
    manifest: BackupManifest
    integrity: Literal["verified_now"]


class BackupCreateRequest(StrictModel):
    command_id: UUIDString

    @field_validator("command_id")
    @classmethod
    def operation_id_is_uuid4(cls, value: str) -> str:
        parsed = UUID(value)
        if parsed.version != 4 or str(parsed) != value:
            raise ValueError("backup command_id must be a canonical UUID v4")
        return value


class BackupCreateResponse(StrictModel):
    command_id: UUIDString
    status: Literal["applied", "replayed"]
    backup: BackupView


class CustomerJobIdentity(StrictModel):
    customer_id: UUIDString
    display_name: StrictStr
    phone: KnownString
    email: KnownString


class CustomerHandoverView(StrictModel):
    recorded_at: InstantString | None
    acknowledgement: StrictStr | None
    collector_name: StrictStr | None
    simulated: StrictBool = False


class FollowUpView(StrictModel):
    follow_up_id: UUIDString
    state: Literal["due", "resolved"]
    owner: Owner
    next_action: StrictStr
    due_at: InstantString
    created_at: InstantString
    resolved_at: InstantString | None


class DiagnosticPermissionView(StrictModel):
    permission_id: UUIDString
    state: Literal["pending", "granted", "declined", "revoked"]
    scope: StrictStr
    fee: MoneyInput
    fee_source: Literal["configured_policy"]
    fee_policy_rev: PositiveRevision | None
    method: Literal["customer_account", "phone", "in_person"]
    attribution: StrictStr | None
    actor_kind: Literal["staff", "customer"]
    actor_id: UUIDString
    decided_at: InstantString
    revoked_at: InstantString | None


class DiagnosticRecordView(StrictModel):
    record_id: UUIDString
    kind: Literal["test", "finding", "limitation"]
    summary: StrictStr
    customer_text: StrictStr | None
    customer_visible: StrictBool
    uncertainty: StrictStr | None
    actor_staff_id: UUIDString
    recorded_at: InstantString


class EstimateLineView(StrictModel):
    """Issued history only; a null part_id on a part means unknown catalog identity.

    M008 preserves this legacy fact, not permission to write or allocate it.
    Emit the key explicitly as null; never infer an ID from description/stock.
    New drafts and command inputs deliberately use separate strict models.
    """

    line_id: UUIDString
    kind: Literal["labor", "part", "fee", "credit"]
    description: StrictStr
    quantity: StrictInt
    unit_amount_minor: StrictInt
    line_amount_minor: StrictInt
    part_id: UUIDString | None = Field(
        description=(
            "For issued part history, null means catalog identity was not recorded. "
            "Catalog-dependent work is blocked until a fresh mapped estimate is approved. "
            "Non-part lines must have null. Not a write-model allowance."
        ),
    )
    scope_ref: UUIDString | None

    @model_validator(mode="after")
    def part_line_has_catalog_mapping(self) -> "EstimateLineView":
        if self.kind == "part" and self.scope_ref is None:
            raise ValueError("issued part lines require scope_ref")
        if self.kind != "part" and self.part_id is not None:
            raise ValueError("only part lines may identify a catalog part")
        return self


class EstimateDraftLine(StrictModel):
    kind: Literal["labor", "part", "fee", "credit"]
    description: StrictStr
    quantity: StrictInt
    unit_amount_minor: StrictInt
    part_id: UUIDString | None
    scope_ref: UUIDString | None

    @model_validator(mode="after")
    def part_line_has_catalog_mapping(self) -> "EstimateDraftLine":
        if self.kind == "part" and (self.part_id is None or self.scope_ref is None):
            raise ValueError("draft part lines require part_id and scope_ref")
        if self.kind != "part" and self.part_id is not None:
            raise ValueError("only part lines may identify a catalog part")
        return self


class EstimateDraftLineView(StrictModel):
    """Read-only projection for retained drafts, including pre-mapping history.

    A missing or null part_id on a historical part line is normalized to
    explicit null: its catalog identity was not recorded. The existing scope
    and all other line facts remain required and validated. This read model
    must not be reused to create, save, or issue a draft.
    """

    kind: Literal["labor", "part", "fee", "credit"]
    description: StrictStr
    quantity: StrictInt
    unit_amount_minor: StrictInt
    part_id: UUIDString | None = None
    scope_ref: UUIDString | None

    @model_validator(mode="after")
    def part_line_has_scope(self) -> "EstimateDraftLineView":
        if self.kind == "part" and self.scope_ref is None:
            raise ValueError("draft part lines require scope_ref")
        if self.kind != "part" and self.part_id is not None:
            raise ValueError("only part lines may identify a catalog part")
        return self


class EstimateDraftView(StrictModel):
    draft_id: UUIDString
    currency: StrictStr
    tax_minor: StrictInt
    tax_label: StrictStr
    lines: list[EstimateDraftLineView]
    updated_at: InstantString
    updated_by_staff_id: UUIDString


class EstimateChangeView(StrictModel):
    field: StrictStr
    before: StrictStr | None
    after: StrictStr | None


class DecisionView(StrictModel):
    decision_id: UUIDString | None
    value: Literal["none", "pending", "approved", "declined", "revoked"]
    method: Literal["customer_account", "phone", "in_person"] | None
    actor_kind: Literal["staff", "customer"] | None
    actor_id: UUIDString | None
    attribution: StrictStr | None
    decided_at: InstantString | None


class EstimateView(StrictModel):
    estimate_id: UUIDString
    version: PositiveRevision
    state: Literal["issued", "expired_unapproved", "superseded"]
    issued_at: InstantString
    expires_at: InstantString
    shop_timezone: StrictStr
    currency: StrictStr
    subtotal_minor: StrictInt
    tax_minor: StrictInt
    total_minor: StrictInt
    tax_label: StrictStr
    tax_source: Literal["not_configured", "manual_shop_entry"]
    tax_policy_rev: PositiveRevision | None
    lines: list[EstimateLineView]
    change_from_previous: list[EstimateChangeView]
    decision: DecisionView


class ReservationView(StrictModel):
    reservation_id: UUIDString
    qty: StrictInt
    state: Literal["active", "partially_released", "released", "consumed"]
    created_at: InstantString
    released_qty: StrictInt
    consumed_qty: StrictInt


class SupplierOrderView(StrictModel):
    order_id: UUIDString
    qty: StrictInt
    supplier_label: StrictStr
    eta_at: InstantString | None
    eta_evidence: StrictStr | None
    state: Literal[
        "planned", "placed_unconfirmed", "supplier_confirmed", "backordered",
        "received", "cancellation_reported", "unavailable",
    ]
    recorded_at: InstantString
    outcome_evidence: StrictStr | None


class PartView(StrictModel):
    requirement_ids: Annotated[list[UUIDString], Field(min_length=1)]
    part_id: UUIDString
    sku: StrictStr
    description: StrictStr
    required_qty: StrictInt
    on_hand_qty: StrictInt
    reserved_qty: StrictInt
    available_qty: StrictInt
    stock_rev: PositiveRevision
    state: Literal["needed", "short", "reserved", "consumed", "released", "substitution_pending"]
    scope_ref: UUIDString
    reservations: list[ReservationView]
    orders: list[SupplierOrderView]


class WorkLogView(StrictModel):
    log_id: UUIDString
    kind: Literal["start", "pause", "resume", "complete", "note"]
    actor_staff_id: UUIDString
    recorded_at: InstantString
    ended_at: InstantString | None
    summary: StrictStr
    owner_staff_id: UUIDString | None
    next_action: StrictStr | None
    next_action_due_at: InstantString | None


class WorkTaskView(StrictModel):
    task_id: UUIDString
    task_rev: PositiveRevision
    rework_of_task_id: UUIDString | None
    scope_ref: UUIDString
    scope_kind: Literal["diagnostic", "estimate_line"]
    state: Literal["not_started", "active", "paused", "completed", "rework"]
    assigned_staff_id: UUIDString | None
    logs: list[WorkLogView]


class QCView(StrictModel):
    run_id: UUIDString
    check_id: UUIDString
    profile_version: PositiveRevision
    label: StrictStr
    required: StrictBool
    state: Literal["not_tested", "passed", "failed"]
    result_note: StrictStr | None
    actor_staff_id: UUIDString | None
    recorded_at: InstantString | None
    task_id: UUIDString


class QCResultView(StrictModel):
    check_id: UUIDString
    result: Literal["not_tested", "passed", "failed"]
    note: StrictStr | None


class QCRunView(StrictModel):
    run_id: UUIDString
    task_id: UUIDString
    profile_version: PositiveRevision
    overall: Literal["pass", "fail", "not_tested"]
    actor_staff_id: UUIDString
    recorded_at: InstantString
    reason: StrictStr | None
    checks: list[QCResultView]


class ChargeView(StrictModel):
    charge_id: UUIDString
    source: Literal["estimate_line", "estimate_tax", "diagnostic_fee"]
    estimate_version: PositiveRevision | None
    estimate_line_id: UUIDString | None
    diagnostic_permission_id: UUIDString | None
    scope_ref: UUIDString | None
    tax_label: StrictStr | None
    amount: MoneyInput
    authorized_at: InstantString
    posted_at: InstantString
    actor_staff_id: UUIDString
    correction_of: UUIDString | None

    @model_validator(mode="after")
    def source_has_matching_provenance(self) -> "ChargeView":
        if self.source == "estimate_line":
            valid = (
                self.estimate_version is not None
                and self.estimate_line_id is not None
                and self.diagnostic_permission_id is None
                and self.scope_ref is not None
                and self.tax_label is None
            )
        elif self.source == "estimate_tax":
            valid = (
                self.estimate_version is not None
                and self.estimate_line_id is None
                and self.diagnostic_permission_id is None
                and self.scope_ref is None
                and self.tax_label is not None
            )
        else:
            valid = (
                self.estimate_version is None
                and self.estimate_line_id is None
                and self.diagnostic_permission_id is not None
                and self.scope_ref is None
                and self.tax_label is None
            )
        if not valid:
            raise ValueError("charge source and provenance fields do not match")
        return self


class CreditView(StrictModel):
    credit_id: UUIDString
    charge_id: UUIDString | None
    kind: Literal["correction", "write_off", "customer_credit"]
    amount: MoneyInput
    reason: StrictStr
    posted_at: InstantString
    actor_staff_id: UUIDString
    correction_of: UUIDString | None


class PaymentView(StrictModel):
    payment_id: UUIDString
    amount: MoneyInput
    method: Literal["cash", "card_external", "bank_external", "other_external", "fictional_simulation"]
    simulated: StrictBool = False
    external_reference: StrictStr | None
    occurred_at: InstantString | None
    recorded_at: InstantString
    actor_staff_id: UUIDString


class RefundView(StrictModel):
    refund_id: UUIDString
    payment_id: UUIDString
    amount: MoneyInput
    external_reference: StrictStr | None
    occurred_at: InstantString
    recorded_at: InstantString
    actor_staff_id: UUIDString
    reason: StrictStr


class CorrectionView(StrictModel):
    correction_id: UUIDString
    target_entry_id: UUIDString
    bucket: Literal["charge", "credit", "payment", "refund"]
    delta_minor: StrictInt
    currency: StrictStr
    external_reference: StrictStr | None
    reason: StrictStr
    posted_at: InstantString
    actor_staff_id: UUIDString


class CollectorView(StrictModel):
    authorization_id: UUIDString
    collector_name: StrictStr
    method: Literal["in_person", "phone"]
    verified_by_staff_id: UUIDString
    verified_at: InstantString
    reason: StrictStr | None


class HandoverView(StrictModel):
    collector_name: StrictStr
    acknowledgement: StrictStr
    recorded_at: InstantString
    actor_staff_id: UUIDString
    balance_override_reason: StrictStr | None
    balance_minor_at_handover: StrictInt
    simulated: StrictBool = False


class ReturnView(StrictModel):
    return_case_id: UUIDString
    return_rev: PositiveRevision
    return_job_id: UUIDString
    return_job_rev: PositiveRevision
    original_job_id: UUIDString
    original_job_rev: PositiveRevision
    reported_recurrence: StrictStr
    coverage: Literal["covered", "not_covered", "undetermined"] | None
    coverage_reason_code: ReasonCode | None
    state: Literal["open", "closed"]
    owner: Owner
    next_action: StrictStr
    next_action_due_at: InstantString


class JobDispositionView(StrictModel):
    disposition_id: UUIDString
    disposition_rev: PositiveRevision
    kind: Literal["no_fault_found", "unrepairable", "dispute"]
    state: Literal["recorded", "open", "resolved"]
    summary: StrictStr
    actor_staff_id: UUIDString
    recorded_at: InstantString
    owner_staff_id: UUIDString
    next_action: StrictStr
    next_action_due_at: InstantString
    resolved_at: InstantString | None
    resolved_by_staff_id: UUIDString | None
    resolution_summary: StrictStr | None

    @model_validator(mode="after")
    def resolution_matches_state(self) -> "JobDispositionView":
        resolved = self.state == "resolved"
        has_resolution = (
            self.resolved_at is not None
            and self.resolved_by_staff_id is not None
            and self.resolution_summary is not None
        )
        if resolved != has_resolution:
            raise ValueError("resolved disposition requires complete resolution provenance")
        if self.kind == "dispute" and self.state == "recorded":
            raise ValueError("a dispute must be open or resolved")
        if self.kind != "dispute" and self.state != "recorded":
            raise ValueError("no-fault and unrepairable dispositions are recorded outcomes")
        return self


class AuditActor(StrictModel):
    kind: Literal["staff", "customer", "operator", "system"]
    display: StrictStr


class AuditView(StrictModel):
    event_id: UUIDString
    event_type: EventType
    occurred_at: InstantString
    actor: AuditActor
    operator_provenance: OperatorProvenance | None
    verification_method: Literal["in_person", "phone"] | None
    reason_code: ReasonCode | None
    summary_code: StrictStr
    refs: list[EventRef]


class JobDevice(StrictModel):
    device_id: UUIDString
    make: KnownString
    model: KnownString
    serial: KnownString


class IntakeAcknowledgement(StrictModel):
    text_version: StrictStr
    method: Literal["in_person", "phone"]
    actor_id: UUIDString
    at: InstantString


class JobIntake(StrictModel):
    reported_issue: StrictStr
    condition: KnownString
    accessories: AccessoriesView
    testing_constraints: KnownString
    received_by_staff_id: UUIDString
    received_at: InstantString
    acknowledgement: IntakeAcknowledgement


class JobAssignment(StrictModel):
    owner: Owner
    assigned_staff_ids: list[UUIDString]


class JobSummaryProjection(StrictModel):
    phase: JobPhase | None
    blockers: list[Blocker]
    diagnostic_permission: Literal["not_requested", "pending", "granted", "declined", "revoked"]
    repair_gate: Literal["open", "held"]
    parts_state: Literal[
        "not_required", "needed", "short", "reserved", "partially_reserved",
        "ready", "consumed", "released", "substitution_pending",
    ]
    work_state: Literal["not_started", "active", "paused", "completed", "rework"]
    qc_state: Literal["not_required", "not_tested", "in_progress", "passed", "failed", "rework_required"]
    property_state: Literal["shop", "return_planned", "ready_for_handover", "handed_over", "simulated_handover"]
    balance: BalanceMoney
    follow_ups: list[FollowUpView]


class DiagnosticDetail(StrictModel):
    current: DiagnosticPermissionView | None
    records: list[DiagnosticRecordView]


class SettlementView(StrictModel):
    currency: StrictStr
    charges: list[ChargeView]
    credits: list[CreditView]
    payments: list[PaymentView]
    refunds: list[RefundView]
    corrections: list[CorrectionView]
    balance_minor: StrictInt


class PropertyDetail(StrictModel):
    state: Literal["shop", "return_planned", "ready_for_handover", "handed_over", "simulated_handover"]
    authorized_collectors: list[CollectorView]
    handover: HandoverView | None


class TimelineActor(StrictModel):
    kind: Literal["staff", "customer", "operator", "system"]
    display: StrictStr


class TimelineEvent(StrictModel):
    event_id: UUIDString
    event_type: EventType
    occurred_at: InstantString
    actor: TimelineActor
    reason_code: ReasonCode | None
    summary_code: StrictStr
    refs: list[EventRef]


class JobStaffDetail(StrictModel):
    job_id: UUIDString
    job_code: StrictStr
    job_rev: PositiveRevision
    gate_rev: PositiveRevision
    lifecycle: Literal["open", "cancelled", "closed"]
    customer: CustomerJobIdentity
    device: JobDevice
    intake: JobIntake
    assignment: JobAssignment
    summary: JobSummaryProjection
    diagnostic: DiagnosticDetail
    estimate_drafts: list[EstimateDraftView]
    estimates: list[EstimateView]
    parts: list[PartView]
    work: list[WorkTaskView]
    qc: list[QCView]
    qc_history: list[QCRunView]
    dispositions: list[JobDispositionView]
    settlement: SettlementView
    property: PropertyDetail
    follow_ups: list[FollowUpView]
    return_cases: list[ReturnView]
    timeline: list[TimelineEvent]


class CustomerJobDetail(StrictModel):
    job_id: UUIDString
    job_code: StrictStr
    job_rev: PositiveRevision
    gate_rev: PositiveRevision
    opened_at: InstantString
    device: CustomerDevice
    status: CustomerStatus
    findings: list[CustomerFinding]
    diagnostic_permission: CustomerDiagnosticPermission | None
    current_estimate: CustomerEstimate | None
    decision_history: list[CustomerDecisionEvent]
    quality_check: CustomerQualityCheck
    status_history: list[CustomerStatusEvent]
    collection: CustomerCollection
    return_request: CustomerReturnRequest


class HealthView(StrictModel):
    status: Literal["ready"]
    schema_version: StrictInt


class RestoreGuideView(StrictModel):
    title: StrictStr
    consequences: StrictStr
    confirmation_phrase: StrictStr
    integrity: StrictStr
    schema_status: StrictStr
    stop_steps: list[StrictStr]
    restore_steps: list[StrictStr]
    start_steps: list[StrictStr]
    verify_steps: list[StrictStr]


class AggregateRef(StrictModel):
    type: Literal[
        "job", "account", "customer", "part", "qc_profile", "return_case",
        "contact_change_request", "policy", "backup",
    ]
    id: UUIDString
    rev: PositiveRevision


class OneTimeCode(StrictModel):
    value: StrictStr
    label: Literal["NOT SENT — give in person or read out by phone"]
    expires_at: InstantString
    shown_once: Literal[True]


class CommandResult(StrictModel):
    type: Literal[
        "job", "account", "customer", "invite", "auth_code", "part", "stock_movement",
        "reservation", "supplier_order", "work_task", "work_log", "qc_run", "qc_profile",
        "diagnostic_permission", "diagnostic_record", "estimate_draft", "estimate_version",
        "estimate_decision", "job_part_requirement", "charge", "credit", "payment", "simulated_payment",
        "refund", "compensation", "collector_authorization", "handover", "simulated_handover", "return_case",
        "contact_change_request", "policy", "disposition", "none",
    ]
    id: UUIDString | None
    linked_job_id: UUIDString | None
    linked_job_rev: PositiveRevision | None
    one_time_code: OneTimeCode | None
    code_retrieval: Literal["available", "not_available", "not_applicable"]


class CommandSuccess(StrictModel):
    command_id: UUIDString
    status: Literal["applied", "replayed"]
    applied_at: InstantString
    event_id: UUIDString
    aggregate: AggregateRef
    result: CommandResult
    job: JobStaffDetail | CustomerJobDetail | None


__all__ = [
    "AccountView", "AggregateRef", "AccessoriesView", "AnonymousSessionView",
    "AuthenticatedSessionView", "AuditView", "BackupView", "BalanceMoney", "Blocker",
    "BackupCreateRequest", "BackupCreateResponse", "BackupDetailView", "BackupManifest", "BackupPage",
    "BlockerCode",
    "ChargeView", "CommandResult", "CommandSuccess", "CorrectionView", "CreditView",
    "AssignableStaffDirectory", "AssignableStaffOption",
    "ContactChangeQueueItem", "ContactChangeQueuePage", "ContactChangeRequestDetail",
    "ContactChangeValues",
    "CustomerCollection", "CustomerContactChangePage", "CustomerContactChangeStatus",
    "CustomerDecisionEvent", "CustomerDiagnosticPermission",
    "CustomerFinding", "CustomerHandover", "CustomerHandoverView", "CustomerJobDetail",
    "CustomerJobIdentity", "CustomerJobSummary", "CustomerReturnRequest", "CustomerStaffDetail",
    "CustomerSafeBlocker", "CustomerStatus", "CustomerStatusEvent", "CustomerSummary",
    "DecisionView", "DemoPolicyView",
    "DiagnosticPermissionView", "DiagnosticRecordView", "ErrorEnvelope", "EstimateChangeView",
    "EstimateDraftLineView", "EstimateDraftView", "EstimateLineView", "EstimateView", "FollowUpView", "HandoverView",
    "HealthView", "JobAssignment", "JobDevice", "JobIntake", "JobPhase", "JobStaffDetail",
    "JobSummaryProjection", "OneTimeCode", "Owner", "PartDetail", "PartSummary", "PartView",
    "PaymentView", "PropertyDetail", "QueueItem", "QueuePage", "QCProfileView", "QCView",
    "RefundView", "ReservationView", "RestoreGuideView", "ReturnView", "SessionView", "StockMovementView",
    "SettlementView", "SupplierOrderView", "TimelineEvent", "WorkLogView", "WorkTaskView",
    "CollectorView", "QCProfileDetail", "QCRunView", "QCResultView", "JobDispositionView",
]
