"""Shared strict scalar and nested wire types."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID as _UUID

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    model_validator,
)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, validate_default=True)


def _validate_uuid(value: str) -> str:
    if not is_canonical_uuid(value):
        raise ValueError("must be a canonical lowercase UUID")
    return value


def _validate_instant(value: str) -> str:
    try:
        datetime.fromisoformat(value[:-1] + "+00:00")
    except (ValueError, TypeError) as error:
        raise ValueError("must be a real UTC RFC 3339 instant ending in Z") from error
    if not value.endswith("Z"):
        raise ValueError("must be a UTC instant ending in Z")
    return value


UUIDString = Annotated[
    StrictStr,
    Field(
        min_length=36,
        max_length=36,
        pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    ),
    AfterValidator(_validate_uuid),
]
InstantString = Annotated[
    StrictStr,
    Field(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$"),
    AfterValidator(_validate_instant),
]
Text = Annotated[StrictStr, Field(min_length=1, max_length=2000)]
CurrencyCode = Annotated[StrictStr, Field(pattern=r"^[A-Z]{3}$")]
NonNegativeAmount = Annotated[StrictInt, Field(ge=0, le=10_000_000_000)]
PositiveRevision = Annotated[StrictInt, Field(ge=1)]
Quantity = Annotated[StrictInt, Field(ge=1, le=10_000)]


def is_canonical_uuid(value: str) -> bool:
    """Return whether *value* is a lowercase, canonical UUID string."""
    try:
        return str(_UUID(value)) == value
    except (ValueError, AttributeError):
        return False


class KnownString(StrictModel):
    value: StrictStr | None
    known: StrictBool

    @model_validator(mode="after")
    def reject_unknown_text_value(self) -> "KnownString":
        if self.value is not None and not self.known:
            raise ValueError("a supplied value must be marked known")
        return self


class MoneyInput(StrictModel):
    currency: CurrencyCode
    amount_minor: NonNegativeAmount


class AccessoriesInput(StrictModel):
    known: StrictBool
    items: list[StrictStr]

    @model_validator(mode="after")
    def unknown_has_no_items(self) -> "AccessoriesInput":
        if not self.known and self.items:
            raise ValueError("unknown accessories must have an empty items list")
        return self


class DeviceInput(StrictModel):
    make: KnownString
    model: KnownString
    serial: KnownString


class AcknowledgementInput(StrictModel):
    text_version: Text
    method: Literal["in_person", "phone"]


class EstimateLineInput(StrictModel):
    kind: Literal["labor", "part", "fee", "credit"]
    description: Text
    quantity: Quantity
    unit_amount_minor: NonNegativeAmount
    part_id: UUIDString | None
    scope_ref: UUIDString | None

    @model_validator(mode="after")
    def part_line_has_explicit_catalog_link(self) -> "EstimateLineInput":
        if self.kind == "part" and (self.part_id is None or self.scope_ref is None):
            raise ValueError("part estimate lines require a catalog part_id and scope_ref")
        if self.kind != "part" and self.part_id is not None:
            raise ValueError("only part estimate lines may identify a catalog part")
        return self

class ConsumeItemInput(StrictModel):
    reservation_id: UUIDString
    qty: Quantity


class QCCheckInput(StrictModel):
    check_id: UUIDString
    result: Literal["pass", "fail", "not_tested"]
    note: StrictStr | None

class QCProfileCheckInput(StrictModel):
    label: Text
    required: StrictBool


def check_uuid_fields(*values: str) -> bool:
    return all(is_canonical_uuid(value) for value in values)
