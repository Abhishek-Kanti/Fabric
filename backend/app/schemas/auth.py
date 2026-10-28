"""Pydantic schemas for authentication and identity endpoints."""

from typing import Any
import uuid
from pydantic import BaseModel, ConfigDict, Field

from app.domain.enums import UserStatus, WorkspaceRole


class DevTokenRequest(BaseModel):
    """Request payload for issuing a development session token."""

    workspace_id: uuid.UUID
    user_id: uuid.UUID


class TokenResponse(BaseModel):
    """Bearer token response."""

    access_token: str
    token_type: str = "bearer"
    expires_in: int


class ExternalIdentityDTO(BaseModel):
    """External identity representation in profile responses."""

    model_config = ConfigDict(from_attributes=True)

    provider: str
    provider_subject_id: str
    provider_email: str | None = None
    provider_username: str | None = None
    provider_display_name: str | None = None
    is_primary: bool = False


class AuthenticatedUserResponse(BaseModel):
    """Current authenticated user profile and live workspace role."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    workspace_id: uuid.UUID
    email: str
    display_name: str
    handle: str | None = None
    workspace_role: WorkspaceRole
    status: UserStatus
    external_identities: list[ExternalIdentityDTO] = Field(default_factory=list)
