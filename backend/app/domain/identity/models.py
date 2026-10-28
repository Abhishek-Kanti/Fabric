"""Domain value objects and models for identity and authentication."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
import uuid


@dataclass(frozen=True)
class ProviderIdentityProfile:
    """An identity profile authenticated and asserted by an external identity provider."""

    provider: str
    provider_subject_id: str
    email: str | None = None
    email_verified: bool = False
    username: str | None = None
    display_name: str | None = None
    tenant_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SessionTokenClaims:
    """Decoded and cryptographically verified claims from a Company Brain native session token.
    
    Contains strictly identity and session scoping claims. Does NOT contain
    authorization roles or permissions, which are resolved dynamically from PostgreSQL.
    """

    user_id: uuid.UUID
    workspace_id: uuid.UUID
    jti: uuid.UUID
    issued_at: datetime
    expires_at: datetime
    issuer: str
    audience: str
