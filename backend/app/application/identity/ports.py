"""Abstract ports and interfaces for identity providers and session tokens."""

from abc import ABC, abstractmethod
import uuid

from app.domain.identity.models import ProviderIdentityProfile, SessionTokenClaims


class IdentityProvider(ABC):
    """Abstract port for an external identity provider (OIDC / OAuth 2.0)."""

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Canonical provider identifier (e.g. 'google', 'github', 'development')."""
        ...

    @abstractmethod
    async def get_authorization_url(
        self,
        workspace_id: uuid.UUID,
        state: str,
        code_challenge: str,
    ) -> str:
        """Construct the external authorization redirect URL with PKCE parameters."""
        ...

    @abstractmethod
    async def authenticate_code(
        self,
        code: str,
        code_verifier: str,
        expected_nonce: str | None = None,
    ) -> ProviderIdentityProfile:
        """Exchange the authorization code with the provider using PKCE and return the verified profile."""
        ...


class TokenService(ABC):
    """Abstract port for issuing and validating native Company Brain session tokens."""

    @abstractmethod
    def create_session_token(
        self,
        workspace_id: uuid.UUID,
        user_id: uuid.UUID,
        expire_seconds: int | None = None,
    ) -> str:
        """Generate a cryptographically signed native session token."""
        ...

    @abstractmethod
    def verify_session_token(self, token: str) -> SessionTokenClaims:
        """Validate signature, issuer, audience, and expiry, returning verified session claims.
        
        Raises:
            TokenExpiredException: If token is expired.
            InvalidTokenException: If signature, claims, or format is invalid.
        """
        ...
