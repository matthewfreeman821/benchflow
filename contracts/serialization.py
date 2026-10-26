"""Strict JSON parsing helpers for duplicate-key and size rejection."""

from __future__ import annotations

import json
from typing import Any


class DuplicateJSONKeyError(ValueError):
    """Raised when an object repeats a member name."""


class JSONBodyTooLargeError(ValueError):
    """Raised when a request body exceeds the configured byte ceiling."""


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateJSONKeyError("duplicate JSON object key")
        result[key] = value
    return result


def _reject_nonfinite(value: str) -> None:
    raise ValueError("non-finite JSON numbers are not permitted")


def loads_request_json(body: bytes, *, max_bytes: int = 256 * 1024) -> Any:
    """Decode UTF-8 JSON while rejecting oversize bodies, duplicate keys and NaN.

    The HTTP layer still owns content-type/status mapping. This function never
    includes body contents in raised exceptions.
    """
    if len(body) > max_bytes:
        raise JSONBodyTooLargeError("request body exceeds the configured limit")
    text = body.decode("utf-8", errors="strict")
    return json.loads(text, object_pairs_hook=_unique_object, parse_constant=_reject_nonfinite)

