"""Unit tests for authorization domain and application contracts.

Verifies:
- IdentityContext properties, active validation, and fail-closed behaviors
- ResourceTarget factories and workspace boundary enforcement
- AccessibleScope scoping invariant (NULL project_id alone does not grant access)
- AuthorizationRepository abstract interface and AuthorizationService orchestration
- WorkspaceMismatchException, InvalidIdentityContextException, and AuthorizationDeniedException
"""

import uuid
import pytest

from app.application.authorization import (
    AccessibleScope,
    AuthorizationRepository,
    AuthorizationService,
    IdentityContext,
    ResourceTarget,
)
from app.domain.authorization import (
    Action,
    AuthorizationDeniedException,
    InvalidIdentityContextException,
    ProjectAction,
    ResourceType,
    TaskAction,
    WorkspaceAction,
    WorkspaceMismatchException,
)
from app.domain.enums import ProjectVisibility, UserStatus, WorkspaceRole
from app.shared.utils.uuid import generate_uuid7


# ---------------------------------------------------------------------------
# Test Mock Repository for AuthorizationRepository interface
# ---------------------------------------------------------------------------

class MockAuthorizationRepository(AuthorizationRepository):
    """In-memory mock repository implementing the abstract AuthorizationRepository port."""

    def __init__(self) -> None:
        self.permissions: set[tuple[uuid.UUID, str, uuid.UUID]] = set()
        self.accessible_projects: dict[uuid.UUID, set[uuid.UUID]] = {}
        self.calls_has_permission = 0
        self.calls_get_accessible_scope = 0

    def grant(self, user_id: uuid.UUID, action: Action, resource_id: uuid.UUID) -> None:
        act = action.value if hasattr(action, "value") else str(action)
        self.permissions.add((user_id, act, resource_id))

    def set_accessible_projects(self, user_id: uuid.UUID, project_ids: set[uuid.UUID]) -> None:
        self.accessible_projects[user_id] = project_ids

    async def get_accessible_scope(self, identity: IdentityContext) -> AccessibleScope:
        self.calls_get_accessible_scope += 1
        pids = self.accessible_projects.get(identity.user_id, set()) if identity.user_id else set()
        return AccessibleScope(
            workspace_id=identity.workspace_id,
            user_id=identity.user_id or uuid.uuid4(),
            accessible_project_ids=frozenset(pids),
            can_access_workspace_public=identity.can_access_workspace_public,
        )

    async def has_permission(
        self,
        identity: IdentityContext,
        action: Action,
        resource: ResourceTarget,
    ) -> bool:
        self.calls_has_permission += 1
        if identity.user_id is None:
            return False
        act = action.value if hasattr(action, "value") else str(action)
        return (identity.user_id, act, resource.resource_id) in self.permissions


# ---------------------------------------------------------------------------
# IdentityContext Tests
# ---------------------------------------------------------------------------

def test_identity_context_roles_and_flags():
    """Verify role flags and public access calculation for various workspace roles."""
    ws_id = generate_uuid7()
    user_id = generate_uuid7()

    # Owner
    owner_ctx = IdentityContext.create_user(ws_id, user_id, WorkspaceRole.OWNER)
    assert owner_ctx.is_owner is True
    assert owner_ctx.is_admin is True
    assert owner_ctx.is_member is True
    assert owner_ctx.is_guest is False
    assert owner_ctx.can_access_workspace_public is True
    assert owner_ctx.is_active is True

    # Admin
    admin_ctx = IdentityContext.create_user(ws_id, user_id, WorkspaceRole.ADMIN)
    assert admin_ctx.is_owner is False
    assert admin_ctx.is_admin is True
    assert admin_ctx.is_member is True
    assert admin_ctx.is_guest is False
    assert admin_ctx.can_access_workspace_public is True

    # Member
    member_ctx = IdentityContext.create_user(ws_id, user_id, WorkspaceRole.MEMBER)
    assert member_ctx.is_owner is False
    assert member_ctx.is_admin is False
    assert member_ctx.is_member is True
    assert member_ctx.is_guest is False
    assert member_ctx.can_access_workspace_public is True

    # Guest (restricted)
    guest_ctx = IdentityContext.create_user(ws_id, user_id, WorkspaceRole.GUEST)
    assert guest_ctx.is_owner is False
    assert guest_ctx.is_admin is False
    assert guest_ctx.is_member is False
    assert guest_ctx.is_guest is True
    assert guest_ctx.can_access_workspace_public is False  # Guest has zero public workspace access


def test_identity_context_delegated_agent():
    """Verify delegated AI agent inherits caller's workspace, role, and records agent identity."""
    ws_id = generate_uuid7()
    user_id = generate_uuid7()
    user_ctx = IdentityContext.create_user(ws_id, user_id, WorkspaceRole.MEMBER)

    agent_ctx = IdentityContext.create_delegated_agent(user_ctx, acting_agent_id="KnowledgeAgent")
    assert agent_ctx.workspace_id == ws_id
    assert agent_ctx.user_id == user_id
    assert agent_ctx.workspace_role == WorkspaceRole.MEMBER
    assert agent_ctx.acting_agent_id == "KnowledgeAgent"
    assert agent_ctx.delegated_by_user_id == user_id
    assert agent_ctx.is_system is False


def test_identity_context_system_worker():
    """Verify system background worker context."""
    ws_id = generate_uuid7()
    sys_ctx = IdentityContext.create_system(ws_id, acting_agent_id="IngestionWorker")
    assert sys_ctx.workspace_id == ws_id
    assert sys_ctx.user_id is None
    assert sys_ctx.is_system is True
    assert sys_ctx.acting_agent_id == "IngestionWorker"
    assert sys_ctx.is_active is True
    sys_ctx.validate_active()  # Must not raise


def test_identity_context_validation_fails_closed():
    """Verify that unauthenticated, inactive, or malformed contexts raise InvalidIdentityContextException."""
    ws_id = generate_uuid7()
    user_id = generate_uuid7()

    # 1. Unauthenticated context
    unauth_ctx = IdentityContext(
        workspace_id=ws_id,
        user_id=user_id,
        workspace_role=WorkspaceRole.MEMBER,
        is_authenticated=False,
    )
    assert unauth_ctx.is_active is False
    with pytest.raises(InvalidIdentityContextException, match="not authenticated"):
        unauth_ctx.validate_active()

    # 2. Suspended user status
    suspended_ctx = IdentityContext.create_user(
        ws_id, user_id, WorkspaceRole.MEMBER, user_status=UserStatus.SUSPENDED
    )
    assert suspended_ctx.is_active is False
    with pytest.raises(InvalidIdentityContextException, match="suspended"):
        suspended_ctx.validate_active()

    # 3. Deactivated user status
    deactivated_ctx = IdentityContext.create_user(
        ws_id, user_id, WorkspaceRole.MEMBER, user_status=UserStatus.DEACTIVATED
    )
    assert deactivated_ctx.is_active is False
    with pytest.raises(InvalidIdentityContextException, match="deactivated"):
        deactivated_ctx.validate_active()

    # 4. Missing user_id on non-system identity
    no_user_ctx = IdentityContext(
        workspace_id=ws_id,
        user_id=None,
        workspace_role=WorkspaceRole.MEMBER,
        is_system=False,
    )
    with pytest.raises(InvalidIdentityContextException, match="missing user_id"):
        no_user_ctx.validate_active()


# ---------------------------------------------------------------------------
# ResourceTarget Tests
# ---------------------------------------------------------------------------

def test_resource_target_factories():
    """Verify resource target factories construct proper target types and scopes."""
    ws_id = generate_uuid7()
    team_id = generate_uuid7()
    proj_id = generate_uuid7()
    task_id = generate_uuid7()

    # Workspace target
    ws_target = ResourceTarget.for_workspace(ws_id)
    assert ws_target.resource_type == ResourceType.WORKSPACE
    assert ws_target.resource_id == ws_id
    assert ws_target.workspace_id == ws_id

    # Team target
    team_target = ResourceTarget.for_team(ws_id, team_id)
    assert team_target.resource_type == ResourceType.TEAM
    assert team_target.resource_id == team_id
    assert team_target.team_id == team_id

    # Project target
    proj_target = ResourceTarget.for_project(
        ws_id, proj_id, team_id=team_id, visibility=ProjectVisibility.PRIVATE
    )
    assert proj_target.resource_type == ResourceType.PROJECT
    assert proj_target.project_id == proj_id
    assert proj_target.visibility == ProjectVisibility.PRIVATE

    # Task target
    task_target = ResourceTarget.for_task(ws_id, proj_id, task_id)
    assert task_target.resource_type == ResourceType.TASK
    assert task_target.resource_id == task_id
    assert task_target.project_id == proj_id


def test_resource_target_workspace_validation():
    """Verify that validate_workspace enforces strict workspace tenancy."""
    ws_a = generate_uuid7()
    ws_b = generate_uuid7()
    target = ResourceTarget.for_project(ws_a, generate_uuid7())

    # Matching workspace succeeds
    target.validate_workspace(ws_a)

    # Mismatched workspace raises WorkspaceMismatchException
    with pytest.raises(WorkspaceMismatchException) as exc_info:
        target.validate_workspace(ws_b)

    assert exc_info.value.expected_workspace_id == str(ws_b)
    assert exc_info.value.actual_workspace_id == str(ws_a)


# ---------------------------------------------------------------------------
# AccessibleScope Tests
# ---------------------------------------------------------------------------

def test_accessible_scope_project_checking():
    """Verify project accessibility membership checks."""
    ws_id = generate_uuid7()
    user_id = generate_uuid7()
    p1 = generate_uuid7()
    p2 = generate_uuid7()
    p3 = generate_uuid7()

    scope = AccessibleScope(
        workspace_id=ws_id,
        user_id=user_id,
        accessible_project_ids=frozenset([p1, p2]),
        can_access_workspace_public=True,
    )
    assert scope.is_project_accessible(p1) is True
    assert scope.is_project_accessible(p2) is True
    assert scope.is_project_accessible(p3) is False
    assert set(scope.project_id_list) == {p1, p2}


def test_accessible_scope_null_project_id_scoping_invariant():
    """Verify that NULL project_id alone NEVER grants access unless explicitly workspace-public."""
    ws_id = generate_uuid7()
    user_id = generate_uuid7()
    p1 = generate_uuid7()

    # Scope for standard member (can_access_workspace_public = True)
    member_scope = AccessibleScope(
        workspace_id=ws_id,
        user_id=user_id,
        accessible_project_ids=frozenset([p1]),
        can_access_workspace_public=True,
    )

    # 1. Project-scoped resource
    assert member_scope.is_resource_accessible(p1) is True
    assert member_scope.is_resource_accessible(generate_uuid7()) is False

    # 2. Unscoped resource with NO visibility attribute -> DENY
    assert member_scope.is_resource_accessible(project_id=None, visibility=None) is False

    # 3. Unscoped resource with private/internal visibility -> DENY
    assert member_scope.is_resource_accessible(project_id=None, visibility=ProjectVisibility.PRIVATE) is False
    assert member_scope.is_resource_accessible(project_id=None, visibility="internal") is False

    # 4. Unscoped resource with explicit 'workspace' visibility for non-guest member -> PERMIT
    assert member_scope.is_resource_accessible(project_id=None, visibility=ProjectVisibility.WORKSPACE) is True
    assert member_scope.is_resource_accessible(project_id=None, visibility="workspace") is True

    # 5. Guest scope (can_access_workspace_public = False)
    guest_scope = AccessibleScope(
        workspace_id=ws_id,
        user_id=user_id,
        accessible_project_ids=frozenset([p1]),
        can_access_workspace_public=False,
    )
    # Even with visibility='workspace', guest must NOT access unscoped resources
    assert guest_scope.is_resource_accessible(project_id=None, visibility=ProjectVisibility.WORKSPACE) is False


def test_accessible_scope_empty():
    """Verify AccessibleScope.empty() constructs zero-grant scope."""
    ws_id = generate_uuid7()
    user_id = generate_uuid7()
    empty = AccessibleScope.empty(ws_id, user_id)
    assert len(empty.accessible_project_ids) == 0
    assert empty.can_access_workspace_public is False
    assert empty.is_resource_accessible(generate_uuid7()) is False
    assert empty.is_resource_accessible(None, ProjectVisibility.WORKSPACE) is False


# ---------------------------------------------------------------------------
# AuthorizationService Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_authorization_service_authorize_success():
    """Verify successful authorization invocation."""
    ws_id = generate_uuid7()
    user_id = generate_uuid7()
    proj_id = generate_uuid7()

    repo = MockAuthorizationRepository()
    repo.grant(user_id, ProjectAction.READ, proj_id)

    service = AuthorizationService(repository=repo)
    identity = IdentityContext.create_user(ws_id, user_id, WorkspaceRole.MEMBER)
    target = ResourceTarget.for_project(ws_id, proj_id)

    # Must complete without exception
    await service.authorize(identity, ProjectAction.READ, target)
    assert repo.calls_has_permission == 1


@pytest.mark.asyncio
async def test_authorization_service_authorize_denied():
    """Verify AuthorizationDeniedException is raised on unauthorized access."""
    ws_id = generate_uuid7()
    user_id = generate_uuid7()
    proj_id = generate_uuid7()

    repo = MockAuthorizationRepository()  # No permission granted
    service = AuthorizationService(repository=repo)
    identity = IdentityContext.create_user(ws_id, user_id, WorkspaceRole.MEMBER)
    target = ResourceTarget.for_project(
        ws_id, proj_id, visibility=ProjectVisibility.PRIVATE
    )

    with pytest.raises(AuthorizationDeniedException) as exc_info:
        await service.authorize(identity, ProjectAction.DELETE, target)

    exc = exc_info.value
    assert exc.action == ProjectAction.DELETE.value
    assert exc.resource_type == ResourceType.PROJECT.value
    assert exc.resource_id == str(proj_id)
    assert exc.details.get("is_private_resource") is True  # Metadata for 404 mapping per AD-AUTH-07


@pytest.mark.asyncio
async def test_authorization_service_workspace_mismatch_fails_closed():
    """Verify that cross-workspace operations are rejected before querying repository."""
    ws_a = generate_uuid7()
    ws_b = generate_uuid7()
    user_id = generate_uuid7()
    proj_id = generate_uuid7()

    repo = MockAuthorizationRepository()
    service = AuthorizationService(repository=repo)

    identity = IdentityContext.create_user(ws_a, user_id, WorkspaceRole.MEMBER)
    target_in_ws_b = ResourceTarget.for_project(ws_b, proj_id)

    with pytest.raises(WorkspaceMismatchException):
        await service.authorize(identity, ProjectAction.READ, target_in_ws_b)

    # Repository was never consulted
    assert repo.calls_has_permission == 0

    with pytest.raises(WorkspaceMismatchException):
        await service.can(identity, ProjectAction.READ, target_in_ws_b)


@pytest.mark.asyncio
async def test_authorization_service_inactive_identity_fails_closed():
    """Verify that suspended or invalid identities fail closed immediately."""
    ws_id = generate_uuid7()
    user_id = generate_uuid7()
    proj_id = generate_uuid7()

    repo = MockAuthorizationRepository()
    service = AuthorizationService(repository=repo)

    suspended_identity = IdentityContext.create_user(
        ws_id, user_id, WorkspaceRole.MEMBER, user_status=UserStatus.SUSPENDED
    )
    target = ResourceTarget.for_project(ws_id, proj_id)

    # authorize() raises InvalidIdentityContextException
    with pytest.raises(InvalidIdentityContextException):
        await service.authorize(suspended_identity, ProjectAction.READ, target)

    # can() returns False without raising or querying repo
    assert await service.can(suspended_identity, ProjectAction.READ, target) is False
    assert repo.calls_has_permission == 0


@pytest.mark.asyncio
async def test_authorization_service_can_boolean_query():
    """Verify can() returns boolean without throwing on permission denial."""
    ws_id = generate_uuid7()
    user_id = generate_uuid7()
    proj_id = generate_uuid7()

    repo = MockAuthorizationRepository()
    repo.grant(user_id, ProjectAction.READ, proj_id)

    service = AuthorizationService(repository=repo)
    identity = IdentityContext.create_user(ws_id, user_id, WorkspaceRole.MEMBER)
    target = ResourceTarget.for_project(ws_id, proj_id)

    assert await service.can(identity, ProjectAction.READ, target) is True
    assert await service.can(identity, ProjectAction.DELETE, target) is False


@pytest.mark.asyncio
async def test_authorization_service_get_accessible_scope():
    """Verify get_accessible_scope delegation and validation."""
    ws_id = generate_uuid7()
    user_id = generate_uuid7()
    p1 = generate_uuid7()

    repo = MockAuthorizationRepository()
    repo.set_accessible_projects(user_id, {p1})

    service = AuthorizationService(repository=repo)
    identity = IdentityContext.create_user(ws_id, user_id, WorkspaceRole.MEMBER)

    scope = await service.get_accessible_scope(identity)
    assert scope.workspace_id == ws_id
    assert scope.user_id == user_id
    assert p1 in scope.accessible_project_ids
    assert scope.can_access_workspace_public is True
    assert repo.calls_get_accessible_scope == 1

    # Inactive identity fails closed
    inactive_identity = IdentityContext.create_user(
        ws_id, user_id, WorkspaceRole.MEMBER, user_status=UserStatus.DEACTIVATED
    )
    with pytest.raises(InvalidIdentityContextException):
        await service.get_accessible_scope(inactive_identity)
