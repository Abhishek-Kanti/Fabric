"""Application service orchestrating authorization evaluation and scope compilation."""

import uuid

from app.application.authorization.context import AccessibleScope, IdentityContext, ResourceTarget
from app.application.authorization.ports import AuthorizationRepository
from app.domain.authorization.exceptions import (
    AuthorizationDeniedException,
    InvalidIdentityContextException,
)
from app.domain.authorization.permissions import Action
from app.domain.enums import ProjectVisibility


class AuthorizationService:
    """Orchestrates deterministic authorization decisions and pre-retrieval scope compilation.
    
    Invariants enforced:
    - Zero LLM trust: All authorization is evaluated deterministically.
    - Tenant isolation: Mismatched workspace IDs fail immediately with WorkspaceMismatchException.
    - Fail closed: Inactive or unauthenticated identity contexts fail with InvalidIdentityContextException.
    - Enumeration prevention: Private resource denials carry metadata for 404 status mapping.
    """

    def __init__(self, repository: AuthorizationRepository) -> None:
        self._repository = repository

    async def get_accessible_scope(self, identity: IdentityContext) -> AccessibleScope:
        """Compile the accessible project scope for an authenticated identity.
        
        Args:
            identity: Authenticated IdentityContext.
            
        Returns:
            AccessibleScope containing accessible project IDs and public access flags.
            
        Raises:
            InvalidIdentityContextException: If identity is unauthenticated or inactive.
        """
        identity.validate_active()
        if identity.is_system:
            # System context has write-only pipeline access and zero user-facing retrieval scope
            return AccessibleScope.empty(
                workspace_id=identity.workspace_id,
                user_id=identity.user_id or uuid.UUID(int=0),
            )
        return await self._repository.get_accessible_scope(identity)

    async def can(
        self,
        identity: IdentityContext,
        action: Action,
        resource: ResourceTarget,
    ) -> bool:
        """Check if an identity has permission to perform an action on a resource without throwing on denial.
        
        Args:
            identity: Authenticated IdentityContext.
            action: Canonical authorization action.
            resource: Target ResourceTarget.
            
        Returns:
            True if authorized, False otherwise.
            
        Raises:
            WorkspaceMismatchException: If identity and resource workspaces do not match.
        """
        try:
            identity.validate_active()
        except InvalidIdentityContextException:
            return False

        # Strict workspace tenancy guard
        resource.validate_workspace(identity.workspace_id)

        return await self._repository.has_permission(identity, action, resource)

    async def authorize(
        self,
        identity: IdentityContext,
        action: Action,
        resource: ResourceTarget,
    ) -> None:
        """Enforce authorization; raises AuthorizationDeniedException if denied.
        
        Args:
            identity: Authenticated IdentityContext.
            action: Canonical authorization action.
            resource: Target ResourceTarget.
            
        Raises:
            InvalidIdentityContextException: If identity context is invalid or inactive.
            WorkspaceMismatchException: If identity and resource workspaces do not match.
            AuthorizationDeniedException: If permission is denied.
        """
        identity.validate_active()

        # Strict workspace tenancy guard
        resource.validate_workspace(identity.workspace_id)

        is_allowed = await self._repository.has_permission(identity, action, resource)
        if not is_allowed:
            is_private = resource.visibility == ProjectVisibility.PRIVATE
            action_str = action.value if hasattr(action, "value") else str(action)
            raise AuthorizationDeniedException(
                message=(
                    f"Identity '{identity.user_id}' is denied '{action_str}' "
                    f"on {resource.resource_type.value}:{resource.resource_id}"
                ),
                action=action_str,
                resource_type=resource.resource_type.value,
                resource_id=resource.resource_id,
                details={"is_private_resource": is_private},
            )
