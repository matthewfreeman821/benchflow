"""Stable public API error envelope and mapping helpers."""

from __future__ import annotations

from enum import StrEnum

from pydantic import StrictBool, StrictStr

from contracts.common import StrictModel, UUIDString

class ErrorCode(StrEnum):
    INVALID_REQUEST = "INVALID_REQUEST"
    AUTH_CODE_INVALID = "AUTH_CODE_INVALID"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    AUTH_INVALID = "AUTH_INVALID"
    FORBIDDEN = "FORBIDDEN"
    CSRF_INVALID = "CSRF_INVALID"
    ORIGIN_INVALID = "ORIGIN_INVALID"
    NOT_FOUND = "NOT_FOUND"
    BACKUP_NOT_FOUND = "BACKUP_NOT_FOUND"
    DEMO_CREDENTIALS_UNAVAILABLE = "DEMO_CREDENTIALS_UNAVAILABLE"
    STALE_REVISION = "STALE_REVISION"
    ESTIMATE_STALE = "ESTIMATE_STALE"
    IDEMPOTENCY_KEY_REUSED = "IDEMPOTENCY_KEY_REUSED"
    ESTIMATE_WINDOW_CLOSED = "ESTIMATE_WINDOW_CLOSED"
    DECISION_ALREADY_RECORDED = "DECISION_ALREADY_RECORDED"
    REVISION_HOLD = "REVISION_HOLD"
    JOB_CANCELLED = "JOB_CANCELLED"
    SCOPE_NOT_APPROVED = "SCOPE_NOT_APPROVED"
    STOCK_SHORTAGE = "STOCK_SHORTAGE"
    QC_INCOMPLETE = "QC_INCOMPLETE"
    REFUND_LIMIT = "REFUND_LIMIT"
    LAST_MANAGER = "LAST_MANAGER"
    DUPLICATE_LOGIN = "DUPLICATE_LOGIN"
    POLICY_CONFLICT = "POLICY_CONFLICT"
    DISPOSITION_CONFLICT = "DISPOSITION_CONFLICT"
    TASK_NOT_REASSIGNABLE = "TASK_NOT_REASSIGNABLE"
    BACKUP_INVALID = "BACKUP_INVALID"
    BODY_TOO_LARGE = "BODY_TOO_LARGE"
    CONTENT_TYPE_REQUIRED = "CONTENT_TYPE_REQUIRED"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    RATE_LIMITED = "RATE_LIMITED"
    DATABASE_BUSY = "DATABASE_BUSY"
    STORAGE_UNAVAILABLE = "STORAGE_UNAVAILABLE"
    MIGRATION_UNAVAILABLE = "MIGRATION_UNAVAILABLE"
    BACKUP_UNAVAILABLE = "BACKUP_UNAVAILABLE"
    INTERNAL_ERROR = "INTERNAL_ERROR"


HTTP_STATUS_BY_CODE = {
    ErrorCode.INVALID_REQUEST: 400,
    ErrorCode.AUTH_CODE_INVALID: 400,
    ErrorCode.AUTH_REQUIRED: 401,
    ErrorCode.AUTH_INVALID: 401,
    ErrorCode.FORBIDDEN: 403,
    ErrorCode.CSRF_INVALID: 403,
    ErrorCode.ORIGIN_INVALID: 403,
    ErrorCode.NOT_FOUND: 404,
    ErrorCode.BACKUP_NOT_FOUND: 404,
    ErrorCode.DEMO_CREDENTIALS_UNAVAILABLE: 404,
    ErrorCode.STALE_REVISION: 409,
    ErrorCode.ESTIMATE_STALE: 409,
    ErrorCode.IDEMPOTENCY_KEY_REUSED: 409,
    ErrorCode.ESTIMATE_WINDOW_CLOSED: 409,
    ErrorCode.DECISION_ALREADY_RECORDED: 409,
    ErrorCode.REVISION_HOLD: 409,
    ErrorCode.JOB_CANCELLED: 409,
    ErrorCode.SCOPE_NOT_APPROVED: 409,
    ErrorCode.STOCK_SHORTAGE: 409,
    ErrorCode.QC_INCOMPLETE: 409,
    ErrorCode.REFUND_LIMIT: 409,
    ErrorCode.LAST_MANAGER: 409,
    ErrorCode.DUPLICATE_LOGIN: 409,
    ErrorCode.POLICY_CONFLICT: 409,
    ErrorCode.DISPOSITION_CONFLICT: 409,
    ErrorCode.TASK_NOT_REASSIGNABLE: 409,
    ErrorCode.BACKUP_INVALID: 409,
    ErrorCode.BODY_TOO_LARGE: 413,
    ErrorCode.CONTENT_TYPE_REQUIRED: 415,
    ErrorCode.VALIDATION_FAILED: 422,
    ErrorCode.RATE_LIMITED: 429,
    ErrorCode.DATABASE_BUSY: 503,
    ErrorCode.STORAGE_UNAVAILABLE: 503,
    ErrorCode.MIGRATION_UNAVAILABLE: 503,
    ErrorCode.BACKUP_UNAVAILABLE: 503,
    ErrorCode.INTERNAL_ERROR: 500,
}


class FieldError(StrictModel):
    field: StrictStr
    code: StrictStr
    message: StrictStr


class ErrorBody(StrictModel):
    code: ErrorCode
    message: StrictStr
    request_id: UUIDString
    retryable: StrictBool
    field_errors: list[FieldError]


class ErrorEnvelope(StrictModel):
    error: ErrorBody


class APIError(Exception):
    """Sanitized transport exception; callers must supply approved public text."""

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        request_id: str,
        *,
        retryable: bool = False,
        field_errors: list[FieldError] | None = None,
    ) -> None:
        self.status_code = HTTP_STATUS_BY_CODE[code]
        self.envelope = ErrorEnvelope(
            error=ErrorBody(
                code=code,
                message=message,
                request_id=request_id,
                retryable=retryable,
                field_errors=field_errors or [],
            )
        )
        super().__init__(message)
