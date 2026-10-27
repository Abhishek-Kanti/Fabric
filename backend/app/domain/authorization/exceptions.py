"""Domain exceptions for authorization and access control."""

from typing import Any
import uuid


class AuthorizationDomainException(Exception):
    """Base exception for all authorization domain errors."""

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class AuthorizationDeniedException(AuthorizationDomainException):
    """Raised when an identity is denied permission to perform an action on a resource."""

    def __init__(
        self,
        message: str = "Access denied",
        action: str | None = None,
        resource_type: str | None = None,
        resource_id: uuid.UUID | str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, details)
        self.action = action
        self.resource_type = resource_type
        self.resource_id = str(resource_id) if resource_id else None


class WorkspaceMismatchException(AuthorizationDomainException):
    """Raised when an identity or resource belongs to a different workspace than the target operation."""

    def __init__(
        self,
        message: str = "Workspace boundary violation: identity and resource workspaces do not match",
        expected_workspace_id: uuid.UUID | str | None = None,
        actual_workspace_id: uuid.UUID | str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, details)
        self.expected_workspace_id = str(expected_workspace_id) if expected_workspace_id else None
        self.actual_workspace_id = str(actual_workspace_id) if actual_workspace_id else None


class InvalidIdentityContextException(AuthorizationDomainException):
    """Raised when an IdentityContext is missing required fields, unauthenticated, inactive, or malformed."""

    def __init__(
        self,
        message: str = "Invalid or incomplete identity context",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, details)
