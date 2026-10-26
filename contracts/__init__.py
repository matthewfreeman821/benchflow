"""Shared, versioned BenchFlow API contracts.

This package describes the agreed wire boundary. It contains no business
implementation, persistence, authentication logic, or domain dispatch.
"""

from contracts.actions import (
    ACTION_NAMES,
    ACTION_DATA_MODELS,
    CommandEnvelope,
    ParsedCommand,
    parse_command,
)
from contracts.errors import APIError, ErrorCode, ErrorEnvelope
from contracts.auth import (
    ActivateRequest,
    AuthenticationSuccess,
    DemoCredentialsResponse,
    LoginRequest,
    LogoutRequest,
    RecoverRequest,
)

__all__ = [
    "ACTION_NAMES",
    "ACTION_DATA_MODELS",
    "ActivateRequest",
    "APIError",
    "AuthenticationSuccess",
    "CommandEnvelope",
    "DemoCredentialsResponse",
    "ErrorCode",
    "ErrorEnvelope",
    "LoginRequest",
    "LogoutRequest",
    "ParsedCommand",
    "parse_command",
    "RecoverRequest",
]
