"""Application authorization value objects and context models.

Defines:
- IdentityContext: Authenticated subject context (user, agent, or worker)
- ResourceTarget: Targeted object for authorization evaluation
- AccessibleScope: Deterministically compiled retrieval authorization scope
"""

from dataclasses import dataclass, field
from typing import Self
import uuid

from app.domain.authorization.exceptions import (
    InvalidIdentityContextException,
    WorkspaceMismatchException,
)
from app.domain.authorization.permissions import ResourceType
from app.domain.enums import ProjectVisibility, UserStatus, WorkspaceRole


@dataclass(frozen=True)
class IdentityContext:
    """Authenticated subject context representing a user, delegated agent, or system worker within a Workspace."""

    workspace_id: uuid.UUID
    user_id: uuid.UUID | None
    workspace_role: WorkspaceRole
    user_status: UserStatus = UserStatus.ACTIVE
    is_authenticated: bool = True
    is_system: bool = False
    acting_agent_id: str | None = None
    delegated_by_user_id: uuid.UUID | None = None

    @property
    def is_active(self) -> bool:
        """Return True if identity is authenticated and canonical user status is active."""
        if not self.is_authenticated:
            return False
        if self.is_system:
            return True
        return self.user_status == UserStatus.ACTIVE

    @property
    def is_owner(self) -> bool:
        """Return True if identity is an active human Workspace Owner."""
        if self.is_system:
            return False
        return self.workspace_role == WorkspaceRole.OWNER

    @property
    def is_admin(self) -> bool:
        """Return True if identity is an active human Workspace Admin or Owner."""
        if self.is_system:
            return False
        return self.workspace_role in (WorkspaceRole.OWNER, WorkspaceRole.ADMIN)

    @property
    def is_member(self) -> bool:
        """Return True if identity is an active human Workspace Member, Admin, or Owner."""
        if self.is_system:
            return False
        return self.workspace_role in (
            WorkspaceRole.OWNER,
            WorkspaceRole.ADMIN,
            WorkspaceRole.MEMBER,
        )

    @property
    def is_guest(self) -> bool:
        """Return True if identity has the Guest role."""
        return self.workspace_role == WorkspaceRole.GUEST

    @property
    def can_access_workspace_public(self) -> bool:
        """Return True if user can access workspace-visible public projects and resources."""
        return self.is_active and not self.is_guest and not self.is_system

    def validate_active(self) -> None:
        """Validate that the identity context is authenticated, active, and well-formed.
        
        Raises:
            InvalidIdentityContextException: If the context fails validation.
        """
        if not self.is_authenticated:
            raise InvalidIdentityContextException("Identity context is not authenticated")

        if not self.workspace_id:
            raise InvalidIdentityContextException("Identity context is missing workspace_id")

        if self.is_system:
            return

        if not self.user_id:
            raise InvalidIdentityContextException("User identity context is missing user_id")

        if self.user_status != UserStatus.ACTIVE:
            raise InvalidIdentityContextException(
                f"User status is '{self.user_status.value}', not active"
            )

    @classmethod
    def create_user(
        cls,
        workspace_id: uuid.UUID,
        user_id: uuid.UUID,
        workspace_role: WorkspaceRole,
        user_status: UserStatus = UserStatus.ACTIVE,
    ) -> Self:
        """Factory for a standard authenticated human user context."""
        return cls(
            workspace_id=workspace_id,
            user_id=user_id,
            workspace_role=workspace_role,
            user_status=user_status,
            is_authenticated=True,
            is_system=False,
        )

    @classmethod
    def create_system(
        cls,
        workspace_id: uuid.UUID,
        acting_agent_id: str | None = None,
    ) -> Self:
        """Factory for an internal pipeline or ingestion worker context."""
        return cls(
            workspace_id=workspace_id,
            user_id=None,
            workspace_role=WorkspaceRole.ADMIN,
            user_status=UserStatus.ACTIVE,
            is_authenticated=True,
            is_system=True,
            acting_agent_id=acting_agent_id,
        )

    @classmethod
    def create_delegated_agent(
        cls,
        user_identity: "IdentityContext",
        acting_agent_id: str,
    ) -> Self:
        """Factory for an AI agent acting on behalf of an authenticated user."""
        user_identity.validate_active()
        return cls(
            workspace_id=user_identity.workspace_id,
            user_id=user_identity.user_id,
            workspace_role=user_identity.workspace_role,
            user_status=user_identity.user_status,
            is_authenticated=True,
            is_system=False,
            acting_agent_id=acting_agent_id,
            delegated_by_user_id=user_identity.user_id,
        )


@dataclass(frozen=True)
class ResourceTarget:
    """Target entity or object of an authorization evaluation."""

    resource_type: ResourceType
    resource_id: uuid.UUID
    workspace_id: uuid.UUID
    project_id: uuid.UUID | None = None
    team_id: uuid.UUID | None = None
    visibility: ProjectVisibility | None = None
    creator_user_id: uuid.UUID | None = None
    assignee_user_id: uuid.UUID | None = None

    def validate_workspace(self, expected_workspace_id: uuid.UUID) -> None:
        """Ensure the target resource belongs to the expected workspace.
        
        Raises:
            WorkspaceMismatchException: If the resource workspace does not match expected_workspace_id.
        """
        if self.workspace_id != expected_workspace_id:
            raise WorkspaceMismatchException(
                message=(
                    f"Workspace boundary violation: resource workspace '{self.workspace_id}' "
                    f"does not match identity workspace '{expected_workspace_id}'"
                ),
                expected_workspace_id=expected_workspace_id,
                actual_workspace_id=self.workspace_id,
            )

    @classmethod
    def for_workspace(cls, workspace_id: uuid.UUID) -> Self:
        """Create a target for workspace-level operations."""
        return cls(
            resource_type=ResourceType.WORKSPACE,
            resource_id=workspace_id,
            workspace_id=workspace_id,
        )

    @classmethod
    def for_team(cls, workspace_id: uuid.UUID, team_id: uuid.UUID) -> Self:
        """Create a target for team-level operations."""
        return cls(
            resource_type=ResourceType.TEAM,
            resource_id=team_id,
            workspace_id=workspace_id,
            team_id=team_id,
        )

    @classmethod
    def for_project(
        cls,
        workspace_id: uuid.UUID,
        project_id: uuid.UUID,
        team_id: uuid.UUID | None = None,
        visibility: ProjectVisibility | None = None,
    ) -> Self:
        """Create a target for project-level operations."""
        return cls(
            resource_type=ResourceType.PROJECT,
            resource_id=project_id,
            workspace_id=workspace_id,
            project_id=project_id,
            team_id=team_id,
            visibility=visibility,
        )

    @classmethod
    def for_task(
        cls,
        workspace_id: uuid.UUID,
        project_id: uuid.UUID,
        task_id: uuid.UUID,
        creator_user_id: uuid.UUID | None = None,
        assignee_user_id: uuid.UUID | None = None,
    ) -> Self:
        """Create a target for task-level operations."""
        return cls(
            resource_type=ResourceType.TASK,
            resource_id=task_id,
            workspace_id=workspace_id,
            project_id=project_id,
            creator_user_id=creator_user_id,
            assignee_user_id=assignee_user_id,
        )

    @classmethod
    def for_knowledge(
        cls,
        workspace_id: uuid.UUID,
        resource_id: uuid.UUID,
        project_id: uuid.UUID | None = None,
        visibility: ProjectVisibility | None = None,
    ) -> Self:
        """Create a target for Knowledge Graph / Vector retrieval operations."""
        return cls(
            resource_type=ResourceType.KNOWLEDGE,
            resource_id=resource_id,
            workspace_id=workspace_id,
            project_id=project_id,
            visibility=visibility,
        )

    @classmethod
    def for_evidence(
        cls,
        workspace_id: uuid.UUID,
        evidence_id: uuid.UUID,
        project_id: uuid.UUID | None = None,
    ) -> Self:
        """Create a target for raw source evidence operations."""
        return cls(
            resource_type=ResourceType.EVIDENCE,
            resource_id=evidence_id,
            workspace_id=workspace_id,
            project_id=project_id,
        )


@dataclass(frozen=True)
class AccessibleScope:
    """Pre-compiled authorized scope for retrieval operations within a Workspace.
    
    Adheres strictly to the architectural invariant that a resource having
    `project_id == NULL` is NOT automatically readable. An unscoped resource is
    accessible if and only if visibility is explicitly 'workspace' and the user
    is a non-guest.
    
    NOTE: AccessibleScope is strictly an in-memory retrieval scope filter helper
    within an already workspace-isolated query. It does not replace tenant
    boundary validation; all database queries must still enforce caller workspace_id.
    """

    workspace_id: uuid.UUID
    user_id: uuid.UUID
    accessible_project_ids: frozenset[uuid.UUID] = field(default_factory=frozenset)
    can_access_workspace_public: bool = True

    def is_project_accessible(self, project_id: uuid.UUID) -> bool:
        """Return True if project_id is in the user's authorized accessible project set."""
        return project_id in self.accessible_project_ids

    def is_resource_accessible(
        self,
        project_id: uuid.UUID | None,
        visibility: ProjectVisibility | str | None = None,
    ) -> bool:
        """Evaluate whether a resource is accessible given its project_id and visibility.
        
        This is an in-memory scope helper. The caller must ensure that the resource
        belongs to the same workspace_id as this scope.
        
        Rules:
        1. If project_id is set: MUST be present in accessible_project_ids.
        2. If project_id is None: MUST have explicit visibility == 'workspace' AND
           can_access_workspace_public must be True. NULL project_id alone NEVER grants access.
        """
        if project_id is not None:
            return project_id in self.accessible_project_ids

        # Unscoped resource: NULL project_id alone does NOT grant access
        vis_val = visibility.value if isinstance(visibility, ProjectVisibility) else visibility
        if vis_val == ProjectVisibility.WORKSPACE.value and self.can_access_workspace_public:
            return True

        return False

    @property
    def project_id_list(self) -> list[uuid.UUID]:
        """Return accessible project IDs as a list for query parameter binding."""
        return list(self.accessible_project_ids)

    @classmethod
    def empty(cls, workspace_id: uuid.UUID, user_id: uuid.UUID) -> Self:
        """Return an empty scope denying all project and public workspace access."""
        return cls(
            workspace_id=workspace_id,
            user_id=user_id,
            accessible_project_ids=frozenset(),
            can_access_workspace_public=False,
        )
