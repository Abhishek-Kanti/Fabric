"""Abstract ports and interfaces for authorization persistence and evaluation."""

from abc import ABC, abstractmethod

from app.application.authorization.context import AccessibleScope, IdentityContext, ResourceTarget
from app.domain.authorization.permissions import Action


class AuthorizationRepository(ABC):
    """Abstract port for querying and evaluating authorization state.
    
    Decoupled from FastAPI, SQLAlchemy, or specific storage engines.
    Concrete implementations (e.g., PostgreSQL CTEs, OpenFGA, SpiceDB)
    must implement this interface.
    """

    @abstractmethod
    async def get_accessible_scope(self, identity: IdentityContext) -> AccessibleScope:
        """Deterministically resolve all accessible project IDs for the identity within their workspace.
        
        Args:
            identity: Authenticated IdentityContext of the subject.
            
        Returns:
            AccessibleScope with compiled accessible_project_ids and can_access_workspace_public.
        """
        ...

    @abstractmethod
    async def has_permission(
        self,
        identity: IdentityContext,
        action: Action,
        resource: ResourceTarget,
    ) -> bool:
        """Evaluate if the identity is authorized to execute the action on the resource.
        
        Args:
            identity: Authenticated IdentityContext of the subject.
            action: Canonical authorization action (e.g. project:read).
            resource: Target entity/scope being operated upon.
            
        Returns:
            True if permitted, False otherwise.
        """
        ...
