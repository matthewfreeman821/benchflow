from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from contracts.actions import (
    ACTION_NAMES,
    EXPECTED_KEYS,
    JOB_TARGET_ACTIONS,
    CommandEnvelope,
    parse_command,
)
from contracts.auth import ActivateRequest, LoginRequest
from contracts.errors import ErrorEnvelope
from contracts.events import EventRecord, OperatorProvenance
from contracts.projections import (
    AssignableStaffDirectory,
    BackupCreateRequest,
    BackupCreateResponse,
    BackupDetailView,
    BackupManifest,
    BackupPage,
    ContactChangeRequestDetail,
    CustomerContactChangePage,
    CustomerDiagnosticPermission,
    CustomerJobDetail,
    JobDispositionView,
    JobStaffDetail,
    PartView,
    QCProfileDetail,
    QCProfileView,
    QCRunView,
    RestoreGuideView,
    ReturnView,
)
from contracts.serialization import DuplicateJSONKeyError, loads_request_json
from contracts.runtime import (
    DatabaseStatus,
    MigrationFingerprint,
    OfflineRestoreInterfaces,
    SchemaInspection,
)

FIXTURES = Path(__file__).parent.parent / "fixtures" / "contracts"


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_action_and_revision_catalogs_are_closed() -> None:
    assert ACTION_NAMES == frozenset(EXPECTED_KEYS)
    assert "account.revoke_invite" in ACTION_NAMES
    assert "contact.change_request" in ACTION_NAMES
    assert {
        "job.record_disposition", "job.open_dispute", "job.resolve_dispute",
        "work.task_reassign",
    } <= ACTION_NAMES
    assert "return.create" in JOB_TARGET_ACTIONS
    assert "job.create" not in JOB_TARGET_ACTIONS


def test_named_job_projection_keys_are_closed() -> None:
    assert set(JobStaffDetail.model_fields) == {
        "job_id", "job_code", "job_rev", "gate_rev", "lifecycle", "customer", "device",
        "intake", "assignment", "summary", "diagnostic", "estimate_drafts", "estimates",
        "parts", "work", "qc", "qc_history", "dispositions", "settlement", "property",
        "follow_ups", "return_cases", "timeline",
    }


def test_auth_shapes_bound_passwords_and_one_time_codes() -> None:
    LoginRequest.model_validate({"login_id": "desk.mira", "password": "entered-secret"})
    with pytest.raises(ValidationError):
        ActivateRequest.model_validate({
            "code": "x" * 43,
            "login_id": "customer.demo",
            "password": "short",
        })
    assert set(CustomerJobDetail.model_fields) == {
        "job_id", "job_code", "job_rev", "gate_rev", "opened_at", "device", "status", "findings",
        "diagnostic_permission", "current_estimate", "decision_history", "quality_check",
        "status_history", "collection", "return_request",
    }


def test_frozen_fixture_validates_and_job_create_target_is_parent() -> None:
    parsed = parse_command(_fixture("job-create-target.json"))
    assert parsed.envelope.target is not None
    assert parsed.envelope.target.type == "customer"
    assert parsed.typed_data.customer_id == parsed.envelope.target.id


def test_basic_customer_action_fixture_is_typed() -> None:
    parsed = parse_command(_fixture("customer-create.json"))
    assert parsed.envelope.action == "customer.create"
    assert parsed.typed_data.display_name == "Fictional Example"


def test_unlinked_receipt_targets_part_not_job() -> None:
    parsed = parse_command(_fixture("part-receive-unlinked.json"))
    assert parsed.envelope.target is not None
    assert parsed.envelope.target.type == "part"


def test_unknown_action_data_and_envelope_keys_are_rejected() -> None:
    bad_data = _fixture("customer-create.json")
    bad_data["data"]["unapproved"] = "must not pass"
    with pytest.raises(ValidationError):
        parse_command(bad_data)

    bad_envelope = _fixture("customer-create.json")
    bad_envelope["actor_id"] = "client-supplied"
    with pytest.raises(ValidationError):
        CommandEnvelope.model_validate(bad_envelope)


def test_target_cannot_be_omitted_or_retargeted() -> None:
    command = _fixture("job-create-target.json")
    command.pop("target")
    with pytest.raises(ValueError):
        parse_command(command)

    command = _fixture("job-create-target.json")
    command["target"]["id"] = "6c8f23a0-c8b8-4c0f-a82e-890b463c4c0e"
    with pytest.raises(ValueError):
        parse_command(command)


def test_customer_contact_update_requires_explicit_customer_target() -> None:
    parsed = parse_command(_fixture("customer-update-contact.json"))
    assert parsed.envelope.target is not None
    assert parsed.envelope.target.type == "customer"
    assert parsed.envelope.target.id == "f5a169d6-e8ad-4d36-a695-5b241c1612cb"
    command = _fixture("customer-update-contact.json")
    command.pop("target")
    with pytest.raises(ValueError):
        parse_command(command)


def test_multi_part_work_consumption_has_each_part_revision() -> None:
    command = _fixture("work-consume-multipart.json")
    parsed = parse_command(command)
    assert len(parsed.typed_data.items) == 2
    assert {
        key for key in parsed.envelope.expected if key.startswith("stock_rev:")
    } == {
        "stock_rev:db32e36c-d5c1-4e7f-a53d-a8e2cbdad002",
        "stock_rev:cd2846b4-36c5-476d-9d6b-5a4b1b4c98d0",
    }
    # The pure parser cannot resolve opaque reservation IDs; Backend validates
    # one-to-one equality against each reservation's owning part. The parser
    # still rejects a command with no revision protection at all.
    command["expected"].pop("stock_rev:cd2846b4-36c5-476d-9d6b-5a4b1b4c98d0")
    command["expected"].pop("stock_rev:db32e36c-d5c1-4e7f-a53d-a8e2cbdad002")
    with pytest.raises(ValueError):
        parse_command(command)


def test_customer_safe_revision_and_diagnostic_permission_ids_are_required() -> None:
    assert {"job_rev", "gate_rev"} <= set(CustomerJobDetail.model_fields)
    assert "permission_id" in CustomerDiagnosticPermission.model_fields
    assert {"profile_rev", "version"} <= set(QCProfileView.model_fields)


def test_customer_job_and_versioned_inventory_models_round_trip() -> None:
    customer_job = CustomerJobDetail.model_validate({
        "job_id": "f5a169d6-e8ad-4d36-a695-5b241c1612cb",
        "job_code": "BF-000001",
        "job_rev": 4,
        "gate_rev": 2,
        "opened_at": "2026-10-23T12:00:00Z",
        "device": {"label": "Fictional laptop"},
        "status": {
            "code": "diagnosing",
            "label": "Inspection",
            "explanation": "The shop is diagnosing the device.",
            "blockers": [],
        },
        "findings": [],
        "diagnostic_permission": {
            "permission_id": "42b393b7-9aa7-4af8-97a1-7f9fb59778a1",
            "state": "granted",
            "scope": "Basic inspection",
            "fee": {"currency": "USD", "amount_minor": 0},
            "fee_source": "configured_policy",
            "fee_policy_rev": 1,
            "method": "customer_account",
            "decided_at": "2026-10-23T12:00:00Z",
        },
        "current_estimate": None,
        "decision_history": [],
        "quality_check": {
            "state": "not_tested",
            "method": "not_applicable",
            "disclosure": None,
        },
        "status_history": [],
        "collection": {
            "ready": False,
            "balance": {"currency": "USD", "amount_minor": 0},
            "what_to_bring": "Bring the receipt.",
            "authorized_collector": "Not recorded",
            "handover": {
                "recorded_at": None,
                "acknowledgement": None,
                "collector_name": None,
            },
        },
        "return_request": {
            "allowed": False,
            "existing_case_id": None,
            "linked_return_job_ids": [],
        },
    })
    assert CustomerJobDetail.model_validate(customer_job.model_dump(mode="json")) == customer_job

    profile = QCProfileView.model_validate({
        "profile_id": "db32e36c-d5c1-4e7f-a53d-a8e2cbdad002",
        "version": 3,
        "profile_rev": 7,
        "label": "Fictional power check",
        "checks": [],
        "active_for_new_tasks": True,
        "created_at": "2026-10-23T12:00:00Z",
        "created_by_staff_id": "36fc65f1-317d-4e59-b1c0-a1940d0a1ef6",
        "reason": "record_correction",
    })
    assert QCProfileView.model_validate(profile.model_dump(mode="json")) == profile

    part = PartView.model_validate({
        "requirement_ids": ["42b393b7-9aa7-4af8-97a1-7f9fb59778a1"],
        "part_id": "cd2846b4-36c5-476d-9d6b-5a4b1b4c98d0",
        "sku": "FIC-01",
        "description": "Fictional part",
        "required_qty": 1,
        "on_hand_qty": 2,
        "reserved_qty": 1,
        "available_qty": 1,
        "stock_rev": 11,
        "state": "reserved",
        "scope_ref": "7c3ea98a-b6ac-4bf6-9b3a-f77310fbf5b1",
        "reservations": [],
        "orders": [],
    })
    assert PartView.model_validate(part.model_dump(mode="json")) == part


def test_new_read_wrappers_construct_and_round_trip() -> None:
    request = ContactChangeRequestDetail.model_validate({
        "request_id": "42b393b7-9aa7-4af8-97a1-7f9fb59778a1",
        "customer_id": "f5a169d6-e8ad-4d36-a695-5b241c1612cb",
        "customer_label": "Fictional Example",
        "request_rev": 1,
        "customer_rev": 2,
        "requested_fields": ["phone"],
        "requested_values": {
            "display_name": None,
            "phone": {"value": "555-0101", "known": True},
            "email": None,
        },
        "state": "pending",
        "created_at": "2026-10-23T12:00:00Z",
        "resolved_at": None,
        "confirmation_method": None,
        "reason_code": None,
    })
    assert ContactChangeRequestDetail.model_validate(request.model_dump(mode="json")) == request
    assert CustomerContactChangePage.model_validate({
        "items": [{
            "request_id": request.request_id,
            "requested_fields": ["phone"],
            "state": "pending",
            "created_at": request.created_at,
            "resolved_at": None,
        }],
        "next_cursor": None,
    })
    assert AssignableStaffDirectory.model_validate({
        "items": [{
            "staff_id": "36fc65f1-317d-4e59-b1c0-a1940d0a1ef6",
            "display_name": "Fictional Technician",
            "roles": ["technician"],
        }]
    })
    guide = RestoreGuideView.model_validate({
        "title": "Offline restore",
        "consequences": "Restored work may be lost.",
        "confirmation_phrase": "RESTORE BENCHFLOW 42b393b7-9aa7-4af8-97a1-7f9fb59778a1",
        "integrity": "Validated before replacement.",
        "schema_status": "Supported schema only.",
        "stop_steps": ["Stop the owned process."],
        "restore_steps": ["Run the offline operator restore."],
        "start_steps": ["Start explicitly after recovery."],
        "verify_steps": ["Check readiness and sign in."],
    })
    assert RestoreGuideView.model_validate(guide.model_dump(mode="json")) == guide


def test_shared_event_runtime_import_and_model_round_trip() -> None:
    provenance = OperatorProvenance.model_validate({
        "windows_sid": "S-1-5-21-1000",
        "windows_account": "FICTIONAL\\operator",
        "process_image": "C:\\Python\\python.exe",
        "pid": 1234,
    })
    event = EventRecord.model_validate({
        "event_id": "42b393b7-9aa7-4af8-97a1-7f9fb59778a1",
        "aggregate_type": "customer",
        "aggregate_id": "f5a169d6-e8ad-4d36-a695-5b241c1612cb",
        "event_type": "customer.update_contact",
        "event_version": 1,
        "command_id": "3a4a23ef-1ef1-44f5-b3bb-d0c039bd9fc8",
        "actor": {"kind": "operator", "id": None},
        "operator_provenance": provenance.model_dump(),
        "verification_method": None,
        "occurred_at": "2026-10-23T12:00:00Z",
        "reason_code": "contact_resolution",
        "summary_code": "contact_updated",
        "refs": [{
            "type": "customer",
            "id": "f5a169d6-e8ad-4d36-a695-5b241c1612cb",
        }],
    })
    assert EventRecord.model_validate(event.model_dump(mode="json")) == event
    backup_event = EventRecord.model_validate({
        "event_id": "a6d8df53-0179-4bdc-bae0-50a540d20245",
        "aggregate_type": "backup",
        "aggregate_id": "86f1340b-f690-463f-bfe1-3df267c38331",
        "event_type": "backup.created",
        "event_version": 1,
        "command_id": "3a4a23ef-1ef1-44f5-b3bb-d0c039bd9fc8",
        "actor": {"kind": "staff", "id": "36fc65f1-317d-4e59-b1c0-a1940d0a1ef6"},
        "operator_provenance": None,
        "verification_method": None,
        "occurred_at": "2026-10-23T12:00:00Z",
        "reason_code": None,
        "summary_code": "backup_created",
        "refs": [{"type": "backup", "id": "86f1340b-f690-463f-bfe1-3df267c38331"}],
    })
    assert backup_event.aggregate_type == "backup"
    assert DatabaseStatus(schema_version=6, ready=True).ready is True
    assert OfflineRestoreInterfaces.__module__ == "contracts.runtime"
    inspection_shape = SchemaInspection(
        schema_version=4,
        latest_schema_version=6,
        applied_migrations=(
            MigrationFingerprint(1, "M001_identity.sql", "a" * 64),
            MigrationFingerprint(2, "M002_intake.sql", "b" * 64),
            MigrationFingerprint(3, "M003_consent.sql", "c" * 64),
            MigrationFingerprint(4, "M004_parts_work.sql", "d" * 64),
        ),
        pending_migration_versions=(5, 6),
        integrity_check="ok",
        foreign_key_check="ok",
    )
    assert inspection_shape.schema_version == 4
    assert inspection_shape.pending_migration_versions == (5, 6)


def test_not_found_error_shape_requires_field_errors_and_uuid() -> None:
    fixture_text = (FIXTURES / "not-found-error.json").read_text(encoding="utf-8")
    model = ErrorEnvelope.model_validate_json(fixture_text)
    assert model.error.code == "NOT_FOUND"
    assert model.error.field_errors == []


def test_duplicate_json_members_and_oversize_bodies_are_rejected() -> None:
    with pytest.raises(DuplicateJSONKeyError):
        loads_request_json(b'{"action":"customer.create","action":"job.create"}')
    with pytest.raises(ValueError):
        loads_request_json(b'{"x":1}', max_bytes=1)


def test_disposition_dispute_reassignment_and_charge_actions_are_closed() -> None:
    job_id = "f5a169d6-e8ad-4d36-a695-5b241c1612cb"
    related_id = "42b393b7-9aa7-4af8-97a1-7f9fb59778a1"
    staff_id = "36fc65f1-317d-4e59-b1c0-a1940d0a1ef6"
    command_id = "3a4a23ef-1ef1-44f5-b3bb-d0c039bd9fc8"
    target = {"type": "job", "id": job_id}
    common_data = {
        "owner_staff_id": staff_id,
        "next_action": "Contact the fictional customer.",
        "next_action_due_at": "2026-10-24T12:00:00Z",
    }

    no_fault = parse_command({
        "command_id": command_id, "target": target,
        "expected": {"job_rev": 4, "gate_rev": 2},
        "action": "job.record_disposition",
        "data": {
            "disposition": "no_fault_found",
            "finding": "Fictional tests found no fault.",
            **common_data, "reason": "no_fault_found",
        },
    })
    assert no_fault.typed_data.disposition == "no_fault_found"

    dispute = parse_command({
        "command_id": "a6d8df53-0179-4bdc-bae0-50a540d20245",
        "target": target, "expected": {"job_rev": 4, "gate_rev": 2},
        "action": "job.open_dispute",
        "data": {"summary": "Fictional customer dispute.", **common_data, "reason": "other_allowed_business_reason"},
    })
    assert dispute.envelope.expected == {"job_rev": 4, "gate_rev": 2}

    resolve = parse_command({
        "command_id": "86f1340b-f690-463f-bfe1-3df267c38331",
        "target": target,
        "expected": {"job_rev": 5, "gate_rev": 2, "disposition_rev": 1},
        "action": "job.resolve_dispute",
        "data": {
            "disposition_id": related_id, "resolution_summary": "Fictional review recorded.",
            **common_data, "reason": "dispute_resolution",
        },
    })
    assert resolve.typed_data.disposition_id == related_id

    reassignment = parse_command({
        "command_id": "b05fcf1d-18b5-4d4d-8eef-4220fc123afe",
        "target": target, "expected": {"job_rev": 5, "task_rev": 2},
        "action": "work.task_reassign",
        "data": {"task_id": related_id, "assigned_staff_id": staff_id, "reason": "task_reassignment"},
    })
    assert reassignment.envelope.expected["task_rev"] == 2

    fee = parse_command({
        "command_id": "1c17f54a-2d9b-4d1b-9c88-9e4f4fa5d6e1",
        "target": target,
        "expected": {"job_rev": 5, "gate_rev": 2, "estimate_version": None},
        "action": "settlement.charge",
        "data": {
            "job_id": job_id, "source": "diagnostic_fee", "estimate_version": None,
            "line_ref": None, "permission_id": related_id, "amount_minor": 0,
            "currency": "USD", "reason": "diagnostic_fee",
        },
    })
    assert fee.typed_data.source == "diagnostic_fee"

    invalid_fee = {
        "command_id": "75f02e44-a1f0-41c1-92a5-5fb6d2012873",
        "target": target,
        "expected": {"job_rev": 5, "gate_rev": 2, "estimate_version": 2},
        "action": "settlement.charge",
        "data": {
            "job_id": job_id, "source": "diagnostic_fee", "estimate_version": 2,
            "line_ref": None, "permission_id": related_id, "amount_minor": 0,
            "currency": "USD", "reason": "diagnostic_fee",
        },
    }
    with pytest.raises(ValueError, match="source and provenance"):
        parse_command(invalid_fee)


def test_part_lines_require_catalog_mapping_and_tax_source_is_explicit() -> None:
    line = {
        "kind": "part", "description": "Fictional replacement part",
        "quantity": 1, "unit_amount_minor": 1200,
        "part_id": "cd2846b4-36c5-476d-9d6b-5a4b1b4c98d0",
        "scope_ref": "7c3ea98a-b6ac-4bf6-9b3a-f77310fbf5b1",
    }
    command = {
        "command_id": "3a4a23ef-1ef1-44f5-b3bb-d0c039bd9fc8",
        "target": {"type": "job", "id": "f5a169d6-e8ad-4d36-a695-5b241c1612cb"},
        "expected": {"job_rev": 4},
        "action": "estimate.draft_save",
        "data": {
            "draft_id": None, "lines": [line], "currency": "USD",
            "tax_minor": 0, "tax_label": "Tax not configured",
            "tax_source": "not_configured",
        },
    }
    assert parse_command(command).typed_data.lines[0].part_id == line["part_id"]

    missing_mapping = {**command, "data": {**command["data"], "lines": [{**line, "part_id": None}]}}
    with pytest.raises(ValidationError):
        parse_command(missing_mapping)
    missing_scope = {**command, "data": {**command["data"], "lines": [{**line, "scope_ref": None}]}}
    with pytest.raises(ValidationError):
        parse_command(missing_scope)
    false_tax = {**command, "data": {**command["data"], "tax_minor": 500}}
    with pytest.raises(ValueError, match="unconfigured tax"):
        parse_command(false_tax)


def test_qc_profile_history_and_qc_run_ids_are_reachable() -> None:
    profile_id = "db32e36c-d5c1-4e7f-a53d-a8e2cbdad002"
    staff_id = "36fc65f1-317d-4e59-b1c0-a1940d0a1ef6"
    first = QCProfileView.model_validate({
        "profile_id": profile_id, "version": 1, "profile_rev": 2,
        "label": "Fictional QC profile", "checks": [],
        "active_for_new_tasks": False, "created_at": "2026-10-22T12:00:00Z",
        "created_by_staff_id": staff_id, "reason": "record_correction",
    })
    current = QCProfileView.model_validate({
        **first.model_dump(mode="json"),
        "version": 2, "active_for_new_tasks": True,
        "created_at": "2026-10-23T12:00:00Z",
    })
    detail = QCProfileDetail(profile=current, history=[first, current])
    assert detail.history[-1] == detail.profile
    with pytest.raises(ValidationError):
        QCProfileDetail(profile=current, history=[current])

    run = QCRunView.model_validate({
        "run_id": "42b393b7-9aa7-4af8-97a1-7f9fb59778a1",
        "task_id": "a6d8df53-0179-4bdc-bae0-50a540d20245",
        "profile_version": 2, "overall": "fail", "actor_staff_id": staff_id,
        "recorded_at": "2026-10-23T12:00:00Z", "reason": "record_correction",
        "checks": [{
            "check_id": "86f1340b-f690-463f-bfe1-3df267c38331",
            "result": "failed", "note": "Fictional failed check.",
        }],
    })
    assert run.run_id == "42b393b7-9aa7-4af8-97a1-7f9fb59778a1"


def test_return_revisions_dispositions_and_backup_manifest_are_strict() -> None:
    staff_id = "36fc65f1-317d-4e59-b1c0-a1940d0a1ef6"
    disposition = JobDispositionView.model_validate({
        "disposition_id": "42b393b7-9aa7-4af8-97a1-7f9fb59778a1",
        "disposition_rev": 1, "kind": "unrepairable", "state": "recorded",
        "summary": "Fictional finding.", "actor_staff_id": staff_id,
        "recorded_at": "2026-10-23T12:00:00Z", "owner_staff_id": staff_id,
        "next_action": "Contact the fictional customer.",
        "next_action_due_at": "2026-10-24T12:00:00Z",
        "resolved_at": None, "resolved_by_staff_id": None, "resolution_summary": None,
    })
    assert disposition.disposition_rev == 1

    returned = ReturnView.model_validate({
        "return_case_id": "42b393b7-9aa7-4af8-97a1-7f9fb59778a1",
        "return_rev": 2, "return_job_id": "a6d8df53-0179-4bdc-bae0-50a540d20245",
        "return_job_rev": 4, "original_job_id": "86f1340b-f690-463f-bfe1-3df267c38331",
        "original_job_rev": 7, "reported_recurrence": "Fictional recurrence.",
        "coverage": None, "coverage_reason_code": None, "state": "open",
        "owner": {"kind": "staff", "staff_id": staff_id, "label": "Fictional staff"},
        "next_action": "Inspect the return.", "next_action_due_at": "2026-10-24T12:00:00Z",
    })
    assert returned.return_job_rev == 4 and returned.original_job_rev == 7

    manifest = BackupManifest.model_validate({
        "format_version": 1, "backup_id": "42b393b7-9aa7-4af8-97a1-7f9fb59778a1",
        "created_at": "2026-10-23T12:00:00Z", "database_file": "benchflow.sqlite3",
        "schema_version": 7, "size_bytes": 4096, "sha256": "a" * 64,
        "integrity": "ok",
    })
    assert BackupDetailView(manifest=manifest, integrity="verified_now").manifest == manifest
    assert BackupPage(items=[], next_cursor=None).items == []
    assert BackupCreateRequest(command_id="3a4a23ef-1ef1-44f5-b3bb-d0c039bd9fc8")
    assert BackupCreateResponse(
        command_id="3a4a23ef-1ef1-44f5-b3bb-d0c039bd9fc8",
        status="applied",
        backup={
            "backup_id": manifest.backup_id, "created_at": manifest.created_at,
            "schema_version": manifest.schema_version, "sha256": manifest.sha256,
            "size_bytes": manifest.size_bytes, "integrity": "verified_at_creation",
        },
    )
    with pytest.raises(ValidationError):
        BackupManifest.model_validate({
            **manifest.model_dump(), "database_file": "C:/private/owner-data.sqlite3",
        })
