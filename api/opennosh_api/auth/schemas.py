from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


def normalize_email(value: str) -> str:
    normalized = value.strip().lower()
    local, separator, domain = normalized.partition("@")
    if not separator or not local or "." not in domain or domain.startswith("."):
        raise ValueError("Enter a valid email address")
    return normalized


def limit_password_bytes(value: str) -> str:
    if len(value.encode("utf-8")) > 1024:
        raise ValueError("Password must be at most 1024 bytes")
    return value


class Credentials(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=12, max_length=1024)

    _normalize_email = field_validator("email")(normalize_email)
    _limit_password_bytes = field_validator("password")(limit_password_bytes)


class AuthenticatedUser(BaseModel):
    id: UUID
    email: str
    onboarding_completed: bool = False
    preferred_units: Literal["metric", "us"] = "metric"


class SessionResponse(BaseModel):
    user: AuthenticatedUser
    csrf_token: str


class RegistrationResponse(SessionResponse):
    recovery_code: str


class SessionState(BaseModel):
    authenticated: bool
    user: AuthenticatedUser | None = None


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=12, max_length=1024)
    new_password: str = Field(min_length=12, max_length=1024)

    _limit_current_password = field_validator("current_password")(limit_password_bytes)
    _limit_new_password = field_validator("new_password")(limit_password_bytes)


class PasswordRecovery(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    recovery_code: str = Field(min_length=32, max_length=128)
    new_password: str = Field(min_length=12, max_length=1024)

    _normalize_email = field_validator("email")(normalize_email)
    _limit_new_password = field_validator("new_password")(limit_password_bytes)


class PasswordConfirmation(BaseModel):
    password: str = Field(min_length=12, max_length=1024)

    _limit_password = field_validator("password")(limit_password_bytes)


class AccountSettingsUpdate(BaseModel):
    onboarding_completed: bool | None = None
    preferred_units: Literal["metric", "us"] | None = None


class RecoveryCodeResponse(BaseModel):
    recovery_code: str
