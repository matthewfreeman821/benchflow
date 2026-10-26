"""Pure shared-model checks, NOT the Backend schema-7/API regression fixture."""

from copy import deepcopy
import json

import pytest
from pydantic import ValidationError

from contracts.actions import parse_command
from contracts.common import EstimateLineInput
from contracts.projections import (
    CustomerEstimateLine,
    EstimateDraftLine,
    EstimateDraftLineView,
    EstimateDraftView,
    EstimateLineView,
)


LINE_ID = "ad271801-638e-42c7-8acb-a424ce561601"
SCOPE_ID = "ad271801-638e-42c7-8acb-a424ce561602"
PART_ID = "ad271801-638e-42c7-8acb-a424ce561603"
JOB_ID = "ad271801-638e-42c7-8acb-a424ce561604"
COMMAND_ID = "ad271801-638e-42c7-8acb-a424ce561605"
DRAFT_ID = "ad271801-638e-42c7-8acb-a424ce561606"
STAFF_ID = "ad271801-638e-42c7-8acb-a424ce561607"


def issued_line(part_id=None, kind="part"):
    return dict(
        line_id=LINE_ID, kind=kind, description="Fictional historical part",
        quantity=2, unit_amount_minor=120, line_amount_minor=240,
        part_id=part_id, scope_ref=SCOPE_ID,
    )


@pytest.mark.parametrize("part_id", [None, PART_ID])
def test_issued_history_preserves_exact_facts_and_explicit_null(part_id):
    facts = issued_line(part_id)
    original = deepcopy(facts)
    view = EstimateLineView.model_validate(facts)
    assert view.model_dump(mode="json") == original
    assert json.loads(view.model_dump_json()) == original
    assert EstimateLineView.model_validate_json(view.model_dump_json()) == view
    assert facts == original


@pytest.mark.parametrize("kind", ["labor", "fee", "credit"])
def test_non_part_read_identity_is_still_null_only(kind):
    assert EstimateLineView.model_validate(issued_line(kind=kind)).part_id is None
    with pytest.raises(ValidationError):
        EstimateLineView.model_validate(issued_line(PART_ID, kind))


@pytest.mark.parametrize("field,value", [
    ("part_id", ""), ("part_id", "unknown"), ("part_id", 123),
    ("scope_ref", None), ("scope_ref", "unknown"),
])
def test_unknown_identity_does_not_weaken_other_read_validation(field, value):
    facts = issued_line()
    facts[field] = value
    with pytest.raises(ValidationError):
        EstimateLineView.model_validate(facts)


def test_read_key_is_required_and_shadow_mapping_state_is_forbidden():
    facts = issued_line()
    del facts["part_id"]
    with pytest.raises(ValidationError):
        EstimateLineView.model_validate(facts)
    facts = issued_line()
    facts["catalog_identity_state"] = "unknown"
    with pytest.raises(ValidationError):
        EstimateLineView.model_validate(facts)


@pytest.mark.parametrize("model", [EstimateLineInput, EstimateDraftLine])
@pytest.mark.parametrize("missing", [True, False])
def test_unknown_historical_read_cannot_be_reused_as_a_new_write(model, missing):
    facts = issued_line()
    del facts["line_id"], facts["line_amount_minor"]
    if missing:
        del facts["part_id"]
    with pytest.raises(ValidationError):
        model.model_validate(facts)
    facts["part_id"] = PART_ID
    assert model.model_validate(facts).part_id == PART_ID
    facts["scope_ref"] = None
    with pytest.raises(ValidationError):
        model.model_validate(facts)


@pytest.mark.parametrize("missing", [True, False])
def test_command_parser_keeps_new_part_writes_strict(missing):
    line = issued_line()
    del line["line_id"], line["line_amount_minor"]
    if missing:
        del line["part_id"]
    command = dict(
        command_id=COMMAND_ID, action="estimate.draft_save",
        target=dict(type="job", id=JOB_ID),
        expected=dict(job_rev=1),
        data=dict(draft_id=None, lines=[line], currency="USD", tax_minor=0,
                  tax_label="Not configured", tax_source="not_configured"),
    )
    with pytest.raises(ValidationError) as rejected:
        parse_command(command)
    assert any("lines" in error["loc"] for error in rejected.value.errors())
    line["part_id"] = PART_ID
    assert parse_command(command).typed_data.lines[0].part_id == PART_ID


def test_customer_allowlist_retains_only_immutable_commercial_facts():
    staff = EstimateLineView.model_validate(issued_line()).model_dump(mode="json")
    safe = {key: staff[key] for key in CustomerEstimateLine.model_fields}
    assert CustomerEstimateLine.model_validate(safe).model_dump(mode="json") == safe
    with pytest.raises(ValidationError):
        CustomerEstimateLine.model_validate(staff)


def test_generated_read_schema_is_nullable_but_required_and_write_is_separate():
    schema = EstimateLineView.model_json_schema()
    assert "part_id" in schema["required"]
    assert {"type": "null"} in schema["properties"]["part_id"]["anyOf"]
    assert "fresh mapped estimate" in schema["properties"]["part_id"]["description"]
    assert EstimateDraftLine is not EstimateLineView


@pytest.mark.parametrize("include_part_id", [False, True], ids=["missing", "explicit-null"])
def test_retained_draft_json_reads_unknown_part_and_serializes_explicit_null(include_part_id):
    stored_line = dict(
        kind="part",
        description="Fictional retained legacy part",
        quantity=2,
        unit_amount_minor=120,
        scope_ref=SCOPE_ID,
    )
    if include_part_id:
        stored_line["part_id"] = None

    # Exercise the persisted lines_json shape, not a hand-built Pydantic object.
    lines_json = json.dumps([stored_line])
    view = EstimateDraftView.model_validate({
        "draft_id": DRAFT_ID,
        "currency": "USD",
        "tax_minor": 0,
        "tax_label": "Not configured",
        "lines": json.loads(lines_json),
        "updated_at": "2026-10-23T10:00:00Z",
        "updated_by_staff_id": STAFF_ID,
    })

    expected_line = {**stored_line, "part_id": None}
    assert view.lines[0].model_dump(mode="json") == expected_line
    serialized = json.loads(view.model_dump_json())
    assert serialized["lines"] == [expected_line]
    assert "part_id" in serialized["lines"][0]
    assert EstimateDraftView.model_validate_json(view.model_dump_json()) == view


def test_historical_draft_read_keeps_scope_and_other_shape_invariants():
    historical = dict(
        kind="part",
        description="Fictional retained legacy part",
        quantity=2,
        unit_amount_minor=120,
        part_id=None,
        scope_ref=SCOPE_ID,
    )
    assert EstimateDraftLineView.model_validate(historical).scope_ref == SCOPE_ID

    missing_scope = dict(historical)
    del missing_scope["scope_ref"]
    with pytest.raises(ValidationError):
        EstimateDraftLineView.model_validate(missing_scope)
    extra_mapping = {**historical, "catalog_identity_state": "unknown"}
    with pytest.raises(ValidationError):
        EstimateDraftLineView.model_validate(extra_mapping)
    malformed_identity = {**historical, "part_id": "unknown"}
    with pytest.raises(ValidationError):
        EstimateDraftLineView.model_validate(malformed_identity)


@pytest.mark.parametrize("part_id", [None, "missing"])
def test_historical_read_model_does_not_relax_strict_draft_writes(part_id):
    facts = dict(
        kind="part",
        description="Fictional retained legacy part",
        quantity=2,
        unit_amount_minor=120,
        scope_ref=SCOPE_ID,
    )
    if part_id != "missing":
        facts["part_id"] = part_id
    assert EstimateDraftLineView.model_validate(facts).part_id is None
    with pytest.raises(ValidationError):
        EstimateDraftLine.model_validate(facts)
