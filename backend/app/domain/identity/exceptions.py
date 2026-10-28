"""Domain exceptions for identity, authentication, and session handling."""

from typing import Any


class IdentityDomainException(Exception):
    """Base exception for all identity domain errors."""

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class AuthenticationFailedException(IdentityDomainException):
    """Raised when authentication credentials or token validation fails."""


class InvalidTokenException(AuthenticationFailedException):
    """Raised when a session token is malformed, has an invalid signature, or fails cryptographic verification."""


class TokenExpiredException(InvalidTokenException):
    """Raised when a session token has expired."""


class UserNotFoundOrInactiveException(AuthenticationFailedException):
    """Raised when the user does not exist or has an inactive/suspended status."""


class WorkspaceNotFoundOrInactiveException(AuthenticationFailedException):
    """Raised when the target workspace does not exist or is deleted."""


class DevelopmentAuthDisabledException(IdentityDomainException):
    """Raised when development-only authentication is invoked outside development/testing environments."""


class InvalidOAuthStateException(AuthenticationFailedException):
    """Raised when an OAuth state parameter is missing, expired, CSRF-mismatched, or replayed."""


class OIDCValidationException(AuthenticationFailedException):
    """Raised when an OIDC ID token fails signature, issuer, audience, nonce, or claims verification."""
