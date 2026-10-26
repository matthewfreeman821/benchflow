"""Authentication request/response models, including the narrow demo exception."""

from __future__ import annotations

import re
import unicodedata
from typing import Annotated, Literal

from pydantic import Field, StrictStr, field_validator, model_validator

from contracts.common import InstantString, StrictModel
from contracts.projections import AccountView


def _validate_login_id(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    if not 3 <= len(normalized) <= 64 or not re.fullmatch(r"[\w.-]+", normalized, re.UNICODE):
        raise ValueError("invalid login identifier shape")
    return value


SecretCode = Annotated[StrictStr, Field(min_length=43, max_length=43, pattern=r"^[A-Za-z0-9_-]{43}$")]
PasswordInput = Annotated[StrictStr, Field(min_length=1, max_length=256)]
NewPasswordInput = Annotated[StrictStr, Field(min_length=12, max_length=256)]


class LoginRequest(StrictModel):
    login_id: StrictStr
    password: PasswordInput

    @field_validator("login_id")
    @classmethod
    def validate_login_identifier(cls, value: str) -> str:
        return _validate_login_id(value)

class ActivateRequest(StrictModel):
    code: SecretCode
    login_id: StrictStr
    password: NewPasswordInput

    @field_validator("login_id")
    @classmethod
    def validate_login_identifier(cls, value: str) -> str:
        return _validate_login_id(value)

class RecoverRequest(StrictModel):
    code: SecretCode
    new_password: NewPasswordInput


class LogoutRequest(StrictModel):
    pass


class AuthenticationSuccess(StrictModel):
    authenticated: Literal[True]
    account: AccountView
    csrf_token: StrictStr
    idle_expires_at: InstantString
    absolute_expires_at: InstantString


class ActivationSuccess(StrictModel):
    activated: Literal[True]
    login_id: StrictStr


class RecoverySuccess(StrictModel):
    recovered: Literal[True]
    sign_in_required: Literal[True]


class DemoCredentialAccount(StrictModel):
    account_key: Literal[
        "staff.manager", "staff.intake", "staff.technician", "staff.dual",
        "customer.a", "customer.b",
    ]
    status: Literal["active"]
    login_id: StrictStr
    password: NewPasswordInput


class DemoActivationCredential(StrictModel):
    account_key: Literal["customer.invited"]
    status: Literal["invited"]
    login_id: None
    code: SecretCode
    label: Literal["NOT SENT — activate locally; no message was sent"]


class DemoCredentialsResponse(StrictModel):
    accounts: list[DemoCredentialAccount] = Field(min_length=6, max_length=6)
    activation: DemoActivationCredential

    @model_validator(mode="after")
    def require_exact_demo_account_set(self) -> "DemoCredentialsResponse":
        keys = [account.account_key for account in self.accounts]
        if keys != [
            "staff.manager", "staff.intake", "staff.technician", "staff.dual",
            "customer.a", "customer.b",
        ]:
            raise ValueError("demo credential accounts must use the frozen account order")
        return self
