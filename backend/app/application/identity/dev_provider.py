"""Development identity provider strictly gated to development and testing."""

import uuid

from app.application.identity.ports import IdentityProvider
from app.domain.identity.exceptions import DevelopmentAuthDisabledException
from app.domain.identity.models import ProviderIdentityProfile


class DevIdentityProvider(IdentityProvider):
    """Deterministic development identity provider for testing and local development.
    
    Strictly disabled outside 'development' and 'testing' environments.
    """

    def __init__(self, environment: str = "development") -> None:
        self._environment = environment

    @property
    def provider_name(self) -> str:
        return "development"

    def ensure_enabled(self) -> None:
        """Validate that development authentication is permitted in the current environment."""
        if self._environment not in ("development", "testing"):
            raise DevelopmentAuthDisabledException(
                f"Development authentication is disabled in '{self._environment}' environment"
            )

    async def get_authorization_url(
        self,
        workspace_id: uuid.UUID,
        state: str,
        code_challenge: str,
    ) -> str:
        self.ensure_enabled()
        return f"/api/v1/auth/dev-callback?state={state}&workspace_id={workspace_id}"

    async def authenticate_code(
        self,
        code: str,
        code_verifier: str,
        expected_nonce: str | None = None,
    ) -> ProviderIdentityProfile:
        self.ensure_enabled()
        return ProviderIdentityProfile(
            provider="development",
            provider_subject_id=code,
            email=f"{code}@dev.local",
            email_verified=True,
            display_name=f"Dev User ({code})",
        )
