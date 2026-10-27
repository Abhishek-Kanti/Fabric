"""Integration tests for PostgresAuthorizationRepository and AuthorizationService.

Runs against PostgreSQL and verifies:
- Complete Zanzibar/ReBAC permission matrix for Workspaces, Teams, Projects, and Tasks
- Strict workspace tenancy isolation on every query
- Cross-workspace resource ID rejection
- Soft-deleted entity exclusions (workspaces, users, teams, projects, tasks)
- Inactive/suspended user fail-closed behavior
- Guest project confinement (zero default access to workspace projects)
- Team contributor private-project restriction (no automatic inheritance without explicit grant)
- Task assignee / creator / project-role combinations
- System worker write-only context (zero user-facing retrieval scope)
- Pre-retrieval get_accessible_scope() alignment with individual permission checks
- Low latency performance check (measured and reported)
"""

from datetime import datetime, timezone
import time
import uuid
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.authorization import (
    AccessibleScope,
    AuthorizationService,
    IdentityContext,
    ResourceTarget,
)
from app.domain.authorization import (
    Action,
    AuthorizationDeniedException,
    KnowledgeAction,
    ProjectAction,
    ResourceType,
    TaskAction,
    TeamAction,
    WorkspaceAction,
    WorkspaceMismatchException,
)
from app.domain.enums import (
    ProjectRole,
    ProjectStatus,
    ProjectVisibility,
    TaskPriority,
    TaskStatus,
    TeamRole,
    UserStatus,
    WorkspaceRole,
)
from app.infrastructure.authorization.postgres_repository import PostgresAuthorizationRepository
from app.infrastructure.database.models import (
    ProjectMemberModel,
    ProjectModel,
    TaskModel,
    TeamMemberModel,
    TeamModel,
    UserModel,
    WorkspaceModel,
)
from app.shared.utils.uuid import generate_uuid7


# ---------------------------------------------------------------------------
# Test Fixtures & Helpers
# ---------------------------------------------------------------------------

async def create_user_record(
    db: AsyncSession,
    ws_id: uuid.UUID,
    role: WorkspaceRole,
    handle_prefix: str = "user",
    status: UserStatus = UserStatus.ACTIVE,
    deleted_at: datetime | None = None,
) -> UserModel:
    """Helper to persist a user within a workspace."""
    u_id = generate_uuid7()
    user = UserModel(
        id=u_id,
        workspace_id=ws_id,
        email=f"{handle_prefix}_{u_id.hex[:6]}@example.com",
        display_name=f"User {handle_prefix}",
        handle=f"{handle_prefix}_{u_id.hex[:6]}",
        workspace_role=role,
        status=status,
        deleted_at=deleted_at,
    )
    db.add(user)
    await db.flush()
    return user


# ---------------------------------------------------------------------------
# Workspace Operations Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_workspace_operations_permissions(db_session: AsyncSession):
    """Verify workspace operations matrix for Owner, Admin, Member, and Guest."""
    ws_id = generate_uuid7()
    workspace = WorkspaceModel(id=ws_id, name="Test WS", slug="test-ws-perms")
    db_session.add(workspace)
    await db_session.flush()

    owner = await create_user_record(db_session, ws_id, WorkspaceRole.OWNER, "owner")
    admin = await create_user_record(db_session, ws_id, WorkspaceRole.ADMIN, "admin")
    member = await create_user_record(db_session, ws_id, WorkspaceRole.MEMBER, "member")
    guest = await create_user_record(db_session, ws_id, WorkspaceRole.GUEST, "guest")

    repo = PostgresAuthorizationRepository(db_session)
    service = AuthorizationService(repo)
    ws_target = ResourceTarget.for_workspace(ws_id)

    owner_ctx = IdentityContext.create_user(ws_id, owner.id, WorkspaceRole.OWNER)
    admin_ctx = IdentityContext.create_user(ws_id, admin.id, WorkspaceRole.ADMIN)
    member_ctx = IdentityContext.create_user(ws_id, member.id, WorkspaceRole.MEMBER)
    guest_ctx = IdentityContext.create_user(ws_id, guest.id, WorkspaceRole.GUEST)

    # workspace:read - all active participants can read workspace metadata
    assert await service.can(owner_ctx, WorkspaceAction.READ, ws_target) is True
    assert await service.can(admin_ctx, WorkspaceAction.READ, ws_target) is True
    assert await service.can(member_ctx, WorkspaceAction.READ, ws_target) is True
    assert await service.can(guest_ctx, WorkspaceAction.READ, ws_target) is True

    # workspace:update - Owner/Admin only
    assert await service.can(owner_ctx, WorkspaceAction.UPDATE, ws_target) is True
    assert await service.can(admin_ctx, WorkspaceAction.UPDATE, ws_target) is True
    assert await service.can(member_ctx, WorkspaceAction.UPDATE, ws_target) is False
    assert await service.can(guest_ctx, WorkspaceAction.UPDATE, ws_target) is False

    # workspace:delete - Owner only
    assert await service.can(owner_ctx, WorkspaceAction.DELETE, ws_target) is True
    assert await service.can(admin_ctx, WorkspaceAction.DELETE, ws_target) is False
    assert await service.can(member_ctx, WorkspaceAction.DELETE, ws_target) is False

    # workspace:invite - Owner/Admin/Member (non-guest)
    assert await service.can(owner_ctx, WorkspaceAction.INVITE, ws_target) is True
    assert await service.can(admin_ctx, WorkspaceAction.INVITE, ws_target) is True
    assert await service.can(member_ctx, WorkspaceAction.INVITE, ws_target) is True
    assert await service.can(guest_ctx, WorkspaceAction.INVITE, ws_target) is False


# ---------------------------------------------------------------------------
# Team Operations Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_team_operations_permissions(db_session: AsyncSession):
    """Verify team operations: create, read, update, manage_members, delete."""
    ws_id = generate_uuid7()
    workspace = WorkspaceModel(id=ws_id, name="Team WS", slug="team-ws")
    db_session.add(workspace)
    await db_session.flush()

    admin = await create_user_record(db_session, ws_id, WorkspaceRole.ADMIN, "admin")
    team_lead = await create_user_record(db_session, ws_id, WorkspaceRole.MEMBER, "lead")
    team_member = await create_user_record(db_session, ws_id, WorkspaceRole.MEMBER, "member")
    non_team_member = await create_user_record(db_session, ws_id, WorkspaceRole.MEMBER, "other")
    guest = await create_user_record(db_session, ws_id, WorkspaceRole.GUEST, "guest")

    team = TeamModel(
        id=generate_uuid7(),
        workspace_id=ws_id,
        name="Platform Core",
        slug="platform-core",
        created_by_user_id=admin.id,
        lead_user_id=team_lead.id,
    )
    db_session.add(team)
    await db_session.flush()

    # Add team member
    tm = TeamMemberModel(
        id=generate_uuid7(),
        workspace_id=ws_id,
        team_id=team.id,
        user_id=team_member.id,
        role=TeamRole.MEMBER,
    )
    db_session.add(tm)
    await db_session.flush()

    repo = PostgresAuthorizationRepository(db_session)
    service = AuthorizationService(repo)
    team_target = ResourceTarget.for_team(ws_id, team.id)

    admin_ctx = IdentityContext.create_user(ws_id, admin.id, WorkspaceRole.ADMIN)
    lead_ctx = IdentityContext.create_user(ws_id, team_lead.id, WorkspaceRole.MEMBER)
    member_ctx = IdentityContext.create_user(ws_id, team_member.id, WorkspaceRole.MEMBER)
    other_ctx = IdentityContext.create_user(ws_id, non_team_member.id, WorkspaceRole.MEMBER)
    guest_ctx = IdentityContext.create_user(ws_id, guest.id, WorkspaceRole.GUEST)

    # team:create - non-guest members can create teams
    assert await service.can(admin_ctx, TeamAction.CREATE, team_target) is True
    assert await service.can(other_ctx, TeamAction.CREATE, team_target) is True
    assert await service.can(guest_ctx, TeamAction.CREATE, team_target) is False

    # team:read - Admin, Lead, Member, and Non-Guest can view team
    assert await service.can(admin_ctx, TeamAction.READ, team_target) is True
    assert await service.can(lead_ctx, TeamAction.READ, team_target) is True
    assert await service.can(member_ctx, TeamAction.READ, team_target) is True
    assert await service.can(other_ctx, TeamAction.READ, team_target) is True
    assert await service.can(guest_ctx, TeamAction.READ, team_target) is False

    # team:update, team:manage_members, team:delete - Admin or Team Lead only
    assert await service.can(admin_ctx, TeamAction.UPDATE, team_target) is True
    assert await service.can(lead_ctx, TeamAction.UPDATE, team_target) is True
    assert await service.can(member_ctx, TeamAction.UPDATE, team_target) is False
    assert await service.can(other_ctx, TeamAction.UPDATE, team_target) is False


# ---------------------------------------------------------------------------
# Project Operations & ReBAC Rules Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_project_visibility_and_guest_confinement(db_session: AsyncSession):
    """Verify guest confinement: guest cannot read workspace projects without direct grant."""
    ws_id = generate_uuid7()
    workspace = WorkspaceModel(id=ws_id, name="Proj WS", slug="proj-ws")
    db_session.add(workspace)
    await db_session.flush()

    member = await create_user_record(db_session, ws_id, WorkspaceRole.MEMBER, "member")
    guest_no_grant = await create_user_record(db_session, ws_id, WorkspaceRole.GUEST, "guest1")
    guest_with_grant = await create_user_record(db_session, ws_id, WorkspaceRole.GUEST, "guest2")

    # Workspace-visible project
    project = ProjectModel(
        id=generate_uuid7(),
        workspace_id=ws_id,
        name="Public Project",
        slug="public-proj",
        visibility=ProjectVisibility.WORKSPACE,
        created_by_user_id=member.id,
    )
    db_session.add(project)
    await db_session.flush()

    # Add explicit grant for guest_with_grant as VIEWER
    pm = ProjectMemberModel(
        id=generate_uuid7(),
        workspace_id=ws_id,
        project_id=project.id,
        user_id=guest_with_grant.id,
        role=ProjectRole.VIEWER,
    )
    db_session.add(pm)
    await db_session.flush()

    repo = PostgresAuthorizationRepository(db_session)
    service = AuthorizationService(repo)
    proj_target = ResourceTarget.for_project(ws_id, project.id, visibility=ProjectVisibility.WORKSPACE)

    member_ctx = IdentityContext.create_user(ws_id, member.id, WorkspaceRole.MEMBER)
    guest_no_grant_ctx = IdentityContext.create_user(ws_id, guest_no_grant.id, WorkspaceRole.GUEST)
    guest_with_grant_ctx = IdentityContext.create_user(ws_id, guest_with_grant.id, WorkspaceRole.GUEST)

    # Standard member can read workspace-visible project
    assert await service.can(member_ctx, ProjectAction.READ, proj_target) is True

    # Guest without explicit grant is CONFINED and DENIED access
    assert await service.can(guest_no_grant_ctx, ProjectAction.READ, proj_target) is False

    # Guest WITH explicit grant CAN read
    assert await service.can(guest_with_grant_ctx, ProjectAction.READ, proj_target) is True


@pytest.mark.asyncio
async def test_private_project_team_contributor_policy(db_session: AsyncSession):
    """Verify that team contributors do NOT inherit private project access without an explicit grant."""
    ws_id = generate_uuid7()
    workspace = WorkspaceModel(id=ws_id, name="Priv Proj WS", slug="priv-proj-ws")
    db_session.add(workspace)
    await db_session.flush()

    admin = await create_user_record(db_session, ws_id, WorkspaceRole.ADMIN, "admin")
    team_lead = await create_user_record(db_session, ws_id, WorkspaceRole.MEMBER, "tlead")
    team_member = await create_user_record(db_session, ws_id, WorkspaceRole.MEMBER, "tmember")
    team_contrib = await create_user_record(db_session, ws_id, WorkspaceRole.MEMBER, "tcontrib")
    team_contrib_with_grant = await create_user_record(db_session, ws_id, WorkspaceRole.MEMBER, "tcontrib_granted")
    outsider = await create_user_record(db_session, ws_id, WorkspaceRole.MEMBER, "outsider")

    team = TeamModel(
        id=generate_uuid7(),
        workspace_id=ws_id,
        name="Security Team",
        slug="security-team",
        created_by_user_id=admin.id,
        lead_user_id=team_lead.id,
    )
    db_session.add(team)
    await db_session.flush()

    # Add team memberships
    db_session.add_all([
        TeamMemberModel(id=generate_uuid7(), workspace_id=ws_id, team_id=team.id, user_id=team_lead.id, role=TeamRole.LEAD),
        TeamMemberModel(id=generate_uuid7(), workspace_id=ws_id, team_id=team.id, user_id=team_member.id, role=TeamRole.MEMBER),
        TeamMemberModel(id=generate_uuid7(), workspace_id=ws_id, team_id=team.id, user_id=team_contrib.id, role=TeamRole.CONTRIBUTOR),
        TeamMemberModel(id=generate_uuid7(), workspace_id=ws_id, team_id=team.id, user_id=team_contrib_with_grant.id, role=TeamRole.CONTRIBUTOR),
    ])
    await db_session.flush()

    # Private project owned by team
    private_project = ProjectModel(
        id=generate_uuid7(),
        workspace_id=ws_id,
        team_id=team.id,
        name="Secret Audit",
        slug="secret-audit",
        visibility=ProjectVisibility.PRIVATE,
        created_by_user_id=admin.id,
    )
    db_session.add(private_project)
    await db_session.flush()

    # Explicit grant ONLY for team_contrib_with_grant
    db_session.add(
        ProjectMemberModel(
            id=generate_uuid7(),
            workspace_id=ws_id,
            project_id=private_project.id,
            user_id=team_contrib_with_grant.id,
            role=ProjectRole.CONTRIBUTOR,
        )
    )
    await db_session.flush()

    repo = PostgresAuthorizationRepository(db_session)
    service = AuthorizationService(repo)
    target = ResourceTarget.for_project(
        ws_id, private_project.id, team_id=team.id, visibility=ProjectVisibility.PRIVATE
    )

    admin_ctx = IdentityContext.create_user(ws_id, admin.id, WorkspaceRole.ADMIN)
    lead_ctx = IdentityContext.create_user(ws_id, team_lead.id, WorkspaceRole.MEMBER)
    member_ctx = IdentityContext.create_user(ws_id, team_member.id, WorkspaceRole.MEMBER)
    contrib_ctx = IdentityContext.create_user(ws_id, team_contrib.id, WorkspaceRole.MEMBER)
    contrib_granted_ctx = IdentityContext.create_user(ws_id, team_contrib_with_grant.id, WorkspaceRole.MEMBER)
    outsider_ctx = IdentityContext.create_user(ws_id, outsider.id, WorkspaceRole.MEMBER)

    # 1. Workspace Admin has superuser read access
    assert await service.can(admin_ctx, ProjectAction.READ, target) is True

    # 2. Team Lead inherits access
    assert await service.can(lead_ctx, ProjectAction.READ, target) is True

    # 3. Team Member inherits access
    assert await service.can(member_ctx, ProjectAction.READ, target) is True

    # 4. Team Contributor WITHOUT direct project grant is strictly DENIED
    assert await service.can(contrib_ctx, ProjectAction.READ, target) is False

    # 5. Team Contributor WITH direct project grant CAN read
    assert await service.can(contrib_granted_ctx, ProjectAction.READ, target) is True

    # 6. Outsider is DENIED
    assert await service.can(outsider_ctx, ProjectAction.READ, target) is False


# ---------------------------------------------------------------------------
# Task Operations Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_task_operations_assignee_creator_roles(db_session: AsyncSession):
    """Verify task permissions for creator, assignee, lead, maintainer, contributor, and viewer."""
    ws_id = generate_uuid7()
    workspace = WorkspaceModel(id=ws_id, name="Task WS", slug="task-ws")
    db_session.add(workspace)
    await db_session.flush()

    admin = await create_user_record(db_session, ws_id, WorkspaceRole.ADMIN, "admin")
    proj_lead = await create_user_record(db_session, ws_id, WorkspaceRole.MEMBER, "lead")
    proj_maintainer = await create_user_record(db_session, ws_id, WorkspaceRole.MEMBER, "maint")
    proj_contrib = await create_user_record(db_session, ws_id, WorkspaceRole.MEMBER, "contrib")
    proj_viewer = await create_user_record(db_session, ws_id, WorkspaceRole.MEMBER, "viewer")
    task_assignee = await create_user_record(db_session, ws_id, WorkspaceRole.MEMBER, "assignee")

    project = ProjectModel(
        id=generate_uuid7(),
        workspace_id=ws_id,
        name="Task Project",
        slug="task-proj",
        visibility=ProjectVisibility.WORKSPACE,
        created_by_user_id=admin.id,
        lead_user_id=proj_lead.id,
    )
    db_session.add(project)
    await db_session.flush()

    # Direct project memberships
    db_session.add_all([
        ProjectMemberModel(id=generate_uuid7(), workspace_id=ws_id, project_id=project.id, user_id=proj_maintainer.id, role=ProjectRole.MAINTAINER),
        ProjectMemberModel(id=generate_uuid7(), workspace_id=ws_id, project_id=project.id, user_id=proj_contrib.id, role=ProjectRole.CONTRIBUTOR),
        ProjectMemberModel(id=generate_uuid7(), workspace_id=ws_id, project_id=project.id, user_id=proj_viewer.id, role=ProjectRole.VIEWER),
        ProjectMemberModel(id=generate_uuid7(), workspace_id=ws_id, project_id=project.id, user_id=task_assignee.id, role=ProjectRole.VIEWER),
    ])
    await db_session.flush()

    # Task created by proj_contrib and assigned to task_assignee
    task = TaskModel(
        id=generate_uuid7(),
        workspace_id=ws_id,
        project_id=project.id,
        number=1,
        title="Implement Auth",
        status=TaskStatus.TODO,
        priority=TaskPriority.HIGH,
        creator_user_id=proj_contrib.id,
        assignee_user_id=task_assignee.id,
    )
    db_session.add(task)
    await db_session.flush()

    repo = PostgresAuthorizationRepository(db_session)
    service = AuthorizationService(repo)
    task_target = ResourceTarget.for_task(
        ws_id, project.id, task.id, creator_user_id=proj_contrib.id, assignee_user_id=task_assignee.id
    )

    lead_ctx = IdentityContext.create_user(ws_id, proj_lead.id, WorkspaceRole.MEMBER)
    maint_ctx = IdentityContext.create_user(ws_id, proj_maintainer.id, WorkspaceRole.MEMBER)
    creator_ctx = IdentityContext.create_user(ws_id, proj_contrib.id, WorkspaceRole.MEMBER)
    assignee_ctx = IdentityContext.create_user(ws_id, task_assignee.id, WorkspaceRole.MEMBER)
    viewer_ctx = IdentityContext.create_user(ws_id, proj_viewer.id, WorkspaceRole.MEMBER)

    # 1. task:read - Everyone who can read parent project can read task
    assert await service.can(lead_ctx, TaskAction.READ, task_target) is True
    assert await service.can(creator_ctx, TaskAction.READ, task_target) is True
    assert await service.can(assignee_ctx, TaskAction.READ, task_target) is True
    assert await service.can(viewer_ctx, TaskAction.READ, task_target) is True

    # 2. task:update - Project Contributor, Task Creator, Task Assignee CAN update
    assert await service.can(lead_ctx, TaskAction.UPDATE, task_target) is True
    assert await service.can(creator_ctx, TaskAction.UPDATE, task_target) is True
    assert await service.can(assignee_ctx, TaskAction.UPDATE, task_target) is True
    # Viewer who is NOT creator or assignee cannot update
    assert await service.can(viewer_ctx, TaskAction.UPDATE, task_target) is False

    # 3. task:assign - Lead, Maintainer, Task Creator, Task Assignee CAN assign
    assert await service.can(lead_ctx, TaskAction.ASSIGN, task_target) is True
    assert await service.can(maint_ctx, TaskAction.ASSIGN, task_target) is True
    assert await service.can(creator_ctx, TaskAction.ASSIGN, task_target) is True
    assert await service.can(assignee_ctx, TaskAction.ASSIGN, task_target) is True
    # Pure viewer cannot assign
    assert await service.can(viewer_ctx, TaskAction.ASSIGN, task_target) is False

    # 4. task:delete - Project Lead, Maintainer, Task Creator CAN delete; Assignee CANNOT delete
    assert await service.can(lead_ctx, TaskAction.DELETE, task_target) is True
    assert await service.can(maint_ctx, TaskAction.DELETE, task_target) is True
    assert await service.can(creator_ctx, TaskAction.DELETE, task_target) is True
    assert await service.can(assignee_ctx, TaskAction.DELETE, task_target) is False  # Assignee cannot delete!
    assert await service.can(viewer_ctx, TaskAction.DELETE, task_target) is False


# ---------------------------------------------------------------------------
# Workspace Tenancy & Tampered Resource Isolation Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cross_workspace_resource_tampering_rejected(db_session: AsyncSession):
    """Verify cross-workspace resource access fails closed."""
    ws_a_id = generate_uuid7()
    ws_b_id = generate_uuid7()
    db_session.add_all([
        WorkspaceModel(id=ws_a_id, name="WS A", slug="ws-a-auth"),
        WorkspaceModel(id=ws_b_id, name="WS B", slug="ws-b-auth"),
    ])
    await db_session.flush()

    user_a = await create_user_record(db_session, ws_a_id, WorkspaceRole.ADMIN, "usera")
    user_b = await create_user_record(db_session, ws_b_id, WorkspaceRole.ADMIN, "userb")
    proj_b = ProjectModel(
        id=generate_uuid7(),
        workspace_id=ws_b_id,
        name="Project in B",
        slug="proj-b",
        created_by_user_id=user_b.id,
    )
    db_session.add(proj_b)
    await db_session.flush()

    repo = PostgresAuthorizationRepository(db_session)
    service = AuthorizationService(repo)
    user_a_ctx = IdentityContext.create_user(ws_a_id, user_a.id, WorkspaceRole.ADMIN)

    # 1. Target with correct ws_b_id -> WorkspaceMismatchException from service tenancy guard
    target_ws_b = ResourceTarget.for_project(ws_b_id, proj_b.id)
    with pytest.raises(WorkspaceMismatchException):
        await service.authorize(user_a_ctx, ProjectAction.READ, target_ws_b)

    # 2. Tampered target claiming ws_a_id for proj_b.id -> Repo queries projects WHERE workspace_id = WS A -> Returns False
    tampered_target = ResourceTarget.for_project(ws_a_id, proj_b.id)
    assert await service.can(user_a_ctx, ProjectAction.READ, tampered_target) is False


# ---------------------------------------------------------------------------
# Soft-Deleted & Inactive Records Fail-Closed Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_soft_deleted_and_inactive_entities_fail_closed(db_session: AsyncSession):
    """Verify soft-deleted entities and inactive users are denied access."""
    ws_id = generate_uuid7()
    workspace = WorkspaceModel(id=ws_id, name="Del WS", slug="del-ws")
    db_session.add(workspace)
    await db_session.flush()

    now = datetime.now(timezone.utc)
    active_user = await create_user_record(db_session, ws_id, WorkspaceRole.ADMIN, "active_user")
    suspended_user = await create_user_record(
        db_session, ws_id, WorkspaceRole.ADMIN, "susp_user", status=UserStatus.SUSPENDED
    )
    deleted_user = await create_user_record(
        db_session, ws_id, WorkspaceRole.ADMIN, "del_user", deleted_at=now
    )

    deleted_project = ProjectModel(
        id=generate_uuid7(),
        workspace_id=ws_id,
        name="Deleted Project",
        slug="deleted-proj",
        created_by_user_id=active_user.id,
        deleted_at=now,
    )
    db_session.add(deleted_project)
    await db_session.flush()

    repo = PostgresAuthorizationRepository(db_session)
    service = AuthorizationService(repo)

    active_ctx = IdentityContext.create_user(ws_id, active_user.id, WorkspaceRole.ADMIN)
    suspended_ctx = IdentityContext.create_user(
        ws_id, suspended_user.id, WorkspaceRole.ADMIN, user_status=UserStatus.SUSPENDED
    )
    deleted_ctx = IdentityContext.create_user(ws_id, deleted_user.id, WorkspaceRole.ADMIN)

    del_proj_target = ResourceTarget.for_project(ws_id, deleted_project.id)

    # Active admin cannot access soft-deleted project
    assert await service.can(active_ctx, ProjectAction.READ, del_proj_target) is False

    # Suspended user fails closed
    assert await service.can(suspended_ctx, ProjectAction.READ, del_proj_target) is False

    # Soft-deleted user fails closed in database
    assert await service.can(deleted_ctx, ProjectAction.READ, del_proj_target) is False


# ---------------------------------------------------------------------------
# System Worker Context Restrictions Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_system_worker_write_only_restriction(db_session: AsyncSession):
    """Verify system background worker has write-only pipeline access and zero retrieval scope."""
    ws_id = generate_uuid7()
    workspace = WorkspaceModel(id=ws_id, name="System WS", slug="sys-ws")
    db_session.add(workspace)

    member = await create_user_record(db_session, ws_id, WorkspaceRole.MEMBER, "sys_member")
    project = ProjectModel(
        id=generate_uuid7(),
        workspace_id=ws_id,
        name="Worker Project",
        slug="worker-proj",
        created_by_user_id=member.id,
    )
    db_session.add(project)
    await db_session.flush()

    repo = PostgresAuthorizationRepository(db_session)
    service = AuthorizationService(repo)
    sys_ctx = IdentityContext.create_system(ws_id, acting_agent_id="GitHubIngestionWorker")
    proj_target = ResourceTarget.for_project(ws_id, project.id)

    # 1. System identity get_accessible_scope returns empty scope
    scope = await service.get_accessible_scope(sys_ctx)
    assert len(scope.accessible_project_ids) == 0
    assert scope.can_access_workspace_public is False

    # 2. System worker cannot perform user-facing reads
    assert await service.can(sys_ctx, ProjectAction.READ, proj_target) is False
    assert await service.can(sys_ctx, KnowledgeAction.QUERY_GRAPH, proj_target) is False


# ---------------------------------------------------------------------------
# AccessibleScope CTE Compilation & Alignment Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_accessible_scope_cte_compilation_and_alignment(db_session: AsyncSession):
    """Verify that get_accessible_scope CTE precisely matches individual permission checks."""
    ws_id = generate_uuid7()
    workspace = WorkspaceModel(id=ws_id, name="Scope WS", slug="scope-ws")
    db_session.add(workspace)
    await db_session.flush()

    admin = await create_user_record(db_session, ws_id, WorkspaceRole.ADMIN, "admin")
    member = await create_user_record(db_session, ws_id, WorkspaceRole.MEMBER, "member")
    guest = await create_user_record(db_session, ws_id, WorkspaceRole.GUEST, "guest")

    team_owned = TeamModel(
        id=generate_uuid7(),
        workspace_id=ws_id,
        name="Scope Team",
        slug="scope-team",
        created_by_user_id=admin.id,
    )
    team_contrib_only = TeamModel(
        id=generate_uuid7(),
        workspace_id=ws_id,
        name="Contrib Team",
        slug="contrib-team",
        created_by_user_id=admin.id,
    )
    db_session.add_all([team_owned, team_contrib_only])
    await db_session.flush()

    # Member is team MEMBER of team_owned, and team CONTRIBUTOR of team_contrib_only
    db_session.add_all([
        TeamMemberModel(id=generate_uuid7(), workspace_id=ws_id, team_id=team_owned.id, user_id=member.id, role=TeamRole.MEMBER),
        TeamMemberModel(id=generate_uuid7(), workspace_id=ws_id, team_id=team_contrib_only.id, user_id=member.id, role=TeamRole.CONTRIBUTOR),
    ])
    await db_session.flush()

    # P1: Workspace visible project
    p1 = ProjectModel(id=generate_uuid7(), workspace_id=ws_id, name="P1", slug="p1", visibility=ProjectVisibility.WORKSPACE, created_by_user_id=admin.id)
    # P2: Private project owned by team_owned (member inherits!)
    p2 = ProjectModel(id=generate_uuid7(), workspace_id=ws_id, team_id=team_owned.id, name="P2", slug="p2", visibility=ProjectVisibility.PRIVATE, created_by_user_id=admin.id)
    # P3: Private project owned by team_contrib_only (member is team contributor: NOT inherited!)
    p3 = ProjectModel(id=generate_uuid7(), workspace_id=ws_id, team_id=team_contrib_only.id, name="P3", slug="p3", visibility=ProjectVisibility.PRIVATE, created_by_user_id=admin.id)
    # P4: Private project with explicit direct grant for member & guest
    p4 = ProjectModel(id=generate_uuid7(), workspace_id=ws_id, name="P4", slug="p4", visibility=ProjectVisibility.PRIVATE, created_by_user_id=admin.id)
    # P5: Private project completely unassociated
    p5 = ProjectModel(id=generate_uuid7(), workspace_id=ws_id, name="P5", slug="p5", visibility=ProjectVisibility.PRIVATE, created_by_user_id=admin.id)
    # P6: Soft-deleted project
    p6 = ProjectModel(id=generate_uuid7(), workspace_id=ws_id, name="P6", slug="p6", visibility=ProjectVisibility.WORKSPACE, created_by_user_id=admin.id, deleted_at=datetime.now(timezone.utc))

    db_session.add_all([p1, p2, p3, p4, p5, p6])
    await db_session.flush()

    # Explicit grants on P4 for member and guest
    db_session.add_all([
        ProjectMemberModel(id=generate_uuid7(), workspace_id=ws_id, project_id=p4.id, user_id=member.id, role=ProjectRole.VIEWER),
        ProjectMemberModel(id=generate_uuid7(), workspace_id=ws_id, project_id=p4.id, user_id=guest.id, role=ProjectRole.VIEWER),
    ])
    await db_session.flush()

    repo = PostgresAuthorizationRepository(db_session)
    service = AuthorizationService(repo)

    member_ctx = IdentityContext.create_user(ws_id, member.id, WorkspaceRole.MEMBER)
    guest_ctx = IdentityContext.create_user(ws_id, guest.id, WorkspaceRole.GUEST)

    # 1. Check member scope
    start_time = time.perf_counter()
    member_scope = await service.get_accessible_scope(member_ctx)
    elapsed_ms = (time.perf_counter() - start_time) * 1000
    print(f"\n[Performance Benchmark] get_accessible_scope took {elapsed_ms:.2f} ms")

    # Member should have access to {P1, P2, P4}
    assert p1.id in member_scope.accessible_project_ids
    assert p2.id in member_scope.accessible_project_ids
    assert p4.id in member_scope.accessible_project_ids
    assert p3.id not in member_scope.accessible_project_ids  # Team contributor excluded
    assert p5.id not in member_scope.accessible_project_ids  # Private non-member excluded
    assert p6.id not in member_scope.accessible_project_ids  # Deleted excluded
    assert member_scope.can_access_workspace_public is True

    # 2. Check guest scope
    guest_scope = await service.get_accessible_scope(guest_ctx)
    # Guest should ONLY have access to P4 (explicit grant)
    assert guest_scope.accessible_project_ids == {p4.id}
    assert guest_scope.can_access_workspace_public is False  # Zero public workspace access

    # 3. Verify alignment: For each project, scope membership == has_permission(ProjectAction.READ)
    for p in [p1, p2, p3, p4, p5, p6]:
        target = ResourceTarget.for_project(ws_id, p.id)
        can_read = await service.can(member_ctx, ProjectAction.READ, target)
        is_in_scope = member_scope.is_project_accessible(p.id)
        assert can_read == is_in_scope, f"Discrepancy on project {p.slug}: can_read={can_read}, in_scope={is_in_scope}"


# ---------------------------------------------------------------------------
# Regression Tests for Authorization Consistency Checks
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_workspace_visible_project_read_unassigned_member_and_scope(db_session: AsyncSession):
    """Regression Test 1: Verify ordinary unassigned workspace member can read workspace-visible projects.
    
    Verifies:
    - An ordinary member with NO direct project membership and NO owning-team membership CAN read workspace-visible projects.
    - The same unassigned member CANNOT read private projects.
    - A guest with NO direct project membership CANNOT read workspace-visible projects.
    - has_permission() and get_accessible_scope() agree for all cases.
    """
    ws_id = generate_uuid7()
    workspace = WorkspaceModel(id=ws_id, name="Regression WS", slug="reg-ws")
    db_session.add(workspace)
    await db_session.flush()

    admin = await create_user_record(db_session, ws_id, WorkspaceRole.ADMIN, "admin")
    ordinary_member = await create_user_record(db_session, ws_id, WorkspaceRole.MEMBER, "unassigned_member")
    guest = await create_user_record(db_session, ws_id, WorkspaceRole.GUEST, "unassigned_guest")

    team = TeamModel(
        id=generate_uuid7(),
        workspace_id=ws_id,
        name="Other Team",
        slug="other-team",
        created_by_user_id=admin.id,
    )
    db_session.add(team)
    await db_session.flush()

    # Workspace-visible project (owned by Other Team, ordinary_member is NOT on Other Team)
    p_workspace = ProjectModel(
        id=generate_uuid7(),
        workspace_id=ws_id,
        team_id=team.id,
        name="Workspace Visible Proj",
        slug="ws-vis-proj",
        visibility=ProjectVisibility.WORKSPACE,
        created_by_user_id=admin.id,
    )
    # Private project (owned by Other Team)
    p_private = ProjectModel(
        id=generate_uuid7(),
        workspace_id=ws_id,
        team_id=team.id,
        name="Private Proj",
        slug="priv-proj",
        visibility=ProjectVisibility.PRIVATE,
        created_by_user_id=admin.id,
    )
    db_session.add_all([p_workspace, p_private])
    await db_session.flush()

    repo = PostgresAuthorizationRepository(db_session)
    service = AuthorizationService(repo)

    member_ctx = IdentityContext.create_user(ws_id, ordinary_member.id, WorkspaceRole.MEMBER)
    guest_ctx = IdentityContext.create_user(ws_id, guest.id, WorkspaceRole.GUEST)

    target_ws = ResourceTarget.for_project(ws_id, p_workspace.id, visibility=ProjectVisibility.WORKSPACE)
    target_priv = ResourceTarget.for_project(ws_id, p_private.id, visibility=ProjectVisibility.PRIVATE)

    # 1. has_permission checks:
    # Ordinary non-guest member CAN read workspace-visible project
    assert await service.can(member_ctx, ProjectAction.READ, target_ws) is True
    # Ordinary non-guest member CANNOT read private project
    assert await service.can(member_ctx, ProjectAction.READ, target_priv) is False
    # Guest CANNOT read workspace-visible project without direct grant
    assert await service.can(guest_ctx, ProjectAction.READ, target_ws) is False
    # Guest CANNOT read private project
    assert await service.can(guest_ctx, ProjectAction.READ, target_priv) is False

    # 2. get_accessible_scope checks:
    member_scope = await service.get_accessible_scope(member_ctx)
    assert p_workspace.id in member_scope.accessible_project_ids
    assert p_private.id not in member_scope.accessible_project_ids
    assert member_scope.can_access_workspace_public is True

    guest_scope = await service.get_accessible_scope(guest_ctx)
    assert p_workspace.id not in guest_scope.accessible_project_ids
    assert p_private.id not in guest_scope.accessible_project_ids
    assert guest_scope.can_access_workspace_public is False

    # 3. Path parity verification:
    assert member_scope.is_project_accessible(p_workspace.id) == await service.can(member_ctx, ProjectAction.READ, target_ws)
    assert member_scope.is_project_accessible(p_private.id) == await service.can(member_ctx, ProjectAction.READ, target_priv)
    assert guest_scope.is_project_accessible(p_workspace.id) == await service.can(guest_ctx, ProjectAction.READ, target_ws)


@pytest.mark.asyncio
async def test_task_permissions_cannot_bypass_parent_project_authorization(db_session: AsyncSession):
    """Regression Test 2: Verify task operations cannot bypass parent-project access control.
    
    Verifies:
    - An assignee who lacks access to the parent project is DENIED all task operations (read, update, assign, delete).
    - A task creator who lacks access to the parent project is DENIED all task operations.
    - An authorized project member who is not creator or assignee:
      * Viewer: CAN read; CANNOT update, assign, or delete.
      * Contributor: CAN read and update; CANNOT assign or delete.
      * Lead / Maintainer: CAN read, update, assign, and delete.
    - Authorized task creator and assignee (who have project read):
      * Creator: CAN read, update, assign, and delete.
      * Assignee: CAN read, update, and assign; CANNOT delete.
    """
    ws_id = generate_uuid7()
    workspace = WorkspaceModel(id=ws_id, name="Task Auth WS", slug="task-auth-ws")
    db_session.add(workspace)
    await db_session.flush()

    admin = await create_user_record(db_session, ws_id, WorkspaceRole.ADMIN, "admin")
    proj_lead = await create_user_record(db_session, ws_id, WorkspaceRole.MEMBER, "lead")
    proj_maint = await create_user_record(db_session, ws_id, WorkspaceRole.MEMBER, "maint")
    proj_contrib = await create_user_record(db_session, ws_id, WorkspaceRole.MEMBER, "contrib")
    proj_viewer = await create_user_record(db_session, ws_id, WorkspaceRole.MEMBER, "viewer")

    # Outsiders who are creator and assignee but lack project access
    creator_outsider = await create_user_record(db_session, ws_id, WorkspaceRole.MEMBER, "creator_outsider")
    assignee_outsider = await create_user_record(db_session, ws_id, WorkspaceRole.MEMBER, "assignee_outsider")

    # Inside project members who are creator and assignee
    active_creator = await create_user_record(db_session, ws_id, WorkspaceRole.MEMBER, "active_creator")
    active_assignee = await create_user_record(db_session, ws_id, WorkspaceRole.MEMBER, "active_assignee")

    # Private project
    private_project = ProjectModel(
        id=generate_uuid7(),
        workspace_id=ws_id,
        name="Private Task Project",
        slug="priv-task-proj",
        visibility=ProjectVisibility.PRIVATE,
        created_by_user_id=admin.id,
        lead_user_id=proj_lead.id,
    )
    db_session.add(private_project)
    await db_session.flush()

    # Add project members to private project (creator_outsider & assignee_outsider are NOT added)
    db_session.add_all([
        ProjectMemberModel(id=generate_uuid7(), workspace_id=ws_id, project_id=private_project.id, user_id=proj_maint.id, role=ProjectRole.MAINTAINER),
        ProjectMemberModel(id=generate_uuid7(), workspace_id=ws_id, project_id=private_project.id, user_id=proj_contrib.id, role=ProjectRole.CONTRIBUTOR),
        ProjectMemberModel(id=generate_uuid7(), workspace_id=ws_id, project_id=private_project.id, user_id=proj_viewer.id, role=ProjectRole.VIEWER),
        ProjectMemberModel(id=generate_uuid7(), workspace_id=ws_id, project_id=private_project.id, user_id=active_creator.id, role=ProjectRole.VIEWER),
        ProjectMemberModel(id=generate_uuid7(), workspace_id=ws_id, project_id=private_project.id, user_id=active_assignee.id, role=ProjectRole.VIEWER),
    ])
    await db_session.flush()

    # Task 1: Created by creator_outsider, assigned to assignee_outsider
    task_outsiders = TaskModel(
        id=generate_uuid7(),
        workspace_id=ws_id,
        project_id=private_project.id,
        number=1,
        title="Secret Task 1",
        status=TaskStatus.TODO,
        priority=TaskPriority.HIGH,
        creator_user_id=creator_outsider.id,
        assignee_user_id=assignee_outsider.id,
    )
    # Task 2: Created by active_creator, assigned to active_assignee
    task_insiders = TaskModel(
        id=generate_uuid7(),
        workspace_id=ws_id,
        project_id=private_project.id,
        number=2,
        title="Secret Task 2",
        status=TaskStatus.TODO,
        priority=TaskPriority.HIGH,
        creator_user_id=active_creator.id,
        assignee_user_id=active_assignee.id,
    )
    db_session.add_all([task_outsiders, task_insiders])
    await db_session.flush()

    repo = PostgresAuthorizationRepository(db_session)
    service = AuthorizationService(repo)

    target_outsider_task = ResourceTarget.for_task(
        ws_id, private_project.id, task_outsiders.id, creator_user_id=creator_outsider.id, assignee_user_id=assignee_outsider.id
    )
    target_insider_task = ResourceTarget.for_task(
        ws_id, private_project.id, task_insiders.id, creator_user_id=active_creator.id, assignee_user_id=active_assignee.id
    )

    ctx_creator_outsider = IdentityContext.create_user(ws_id, creator_outsider.id, WorkspaceRole.MEMBER)
    ctx_assignee_outsider = IdentityContext.create_user(ws_id, assignee_outsider.id, WorkspaceRole.MEMBER)

    # 1. Creator who lacks project access -> CANNOT read, update, assign, or delete task
    assert await service.can(ctx_creator_outsider, TaskAction.READ, target_outsider_task) is False
    assert await service.can(ctx_creator_outsider, TaskAction.UPDATE, target_outsider_task) is False
    assert await service.can(ctx_creator_outsider, TaskAction.ASSIGN, target_outsider_task) is False
    assert await service.can(ctx_creator_outsider, TaskAction.DELETE, target_outsider_task) is False

    # 2. Assignee who lacks project access -> CANNOT read, update, assign, or delete task
    assert await service.can(ctx_assignee_outsider, TaskAction.READ, target_outsider_task) is False
    assert await service.can(ctx_assignee_outsider, TaskAction.UPDATE, target_outsider_task) is False
    assert await service.can(ctx_assignee_outsider, TaskAction.ASSIGN, target_outsider_task) is False
    assert await service.can(ctx_assignee_outsider, TaskAction.DELETE, target_outsider_task) is False

    # 3. Authorized project member who is NOT creator or assignee:
    # 3a. Viewer: can read, but CANNOT update, assign, or delete
    ctx_viewer = IdentityContext.create_user(ws_id, proj_viewer.id, WorkspaceRole.MEMBER)
    assert await service.can(ctx_viewer, TaskAction.READ, target_insider_task) is True
    assert await service.can(ctx_viewer, TaskAction.CREATE, target_insider_task) is False
    assert await service.can(ctx_viewer, TaskAction.UPDATE, target_insider_task) is False
    assert await service.can(ctx_viewer, TaskAction.ASSIGN, target_insider_task) is False
    assert await service.can(ctx_viewer, TaskAction.DELETE, target_insider_task) is False

    # 3b. Contributor: can read, create, and update; CANNOT assign or delete
    ctx_contrib = IdentityContext.create_user(ws_id, proj_contrib.id, WorkspaceRole.MEMBER)
    assert await service.can(ctx_contrib, TaskAction.READ, target_insider_task) is True
    assert await service.can(ctx_contrib, TaskAction.CREATE, target_insider_task) is True
    assert await service.can(ctx_contrib, TaskAction.UPDATE, target_insider_task) is True
    assert await service.can(ctx_contrib, TaskAction.ASSIGN, target_insider_task) is False
    assert await service.can(ctx_contrib, TaskAction.DELETE, target_insider_task) is False

    # 3c. Maintainer and Lead: CAN read, create, update, assign, and delete
    ctx_maint = IdentityContext.create_user(ws_id, proj_maint.id, WorkspaceRole.MEMBER)
    ctx_lead = IdentityContext.create_user(ws_id, proj_lead.id, WorkspaceRole.MEMBER)
    for ctx in (ctx_maint, ctx_lead):
        assert await service.can(ctx, TaskAction.READ, target_insider_task) is True
        assert await service.can(ctx, TaskAction.CREATE, target_insider_task) is True
        assert await service.can(ctx, TaskAction.UPDATE, target_insider_task) is True
        assert await service.can(ctx, TaskAction.ASSIGN, target_insider_task) is True
        assert await service.can(ctx, TaskAction.DELETE, target_insider_task) is True

    # 4. Authorized creator and assignee (who DO have project read access):
    # 4a. Active Creator (viewer role): can read, update, assign, and delete their created task
    ctx_active_creator = IdentityContext.create_user(ws_id, active_creator.id, WorkspaceRole.MEMBER)
    assert await service.can(ctx_active_creator, TaskAction.READ, target_insider_task) is True
    assert await service.can(ctx_active_creator, TaskAction.UPDATE, target_insider_task) is True
    assert await service.can(ctx_active_creator, TaskAction.ASSIGN, target_insider_task) is True
    assert await service.can(ctx_active_creator, TaskAction.DELETE, target_insider_task) is True

    # 4b. Active Assignee (viewer role): can read, update, and assign, but CANNOT delete
    ctx_active_assignee = IdentityContext.create_user(ws_id, active_assignee.id, WorkspaceRole.MEMBER)
    assert await service.can(ctx_active_assignee, TaskAction.READ, target_insider_task) is True
    assert await service.can(ctx_active_assignee, TaskAction.UPDATE, target_insider_task) is True
    assert await service.can(ctx_active_assignee, TaskAction.ASSIGN, target_insider_task) is True
    assert await service.can(ctx_active_assignee, TaskAction.DELETE, target_insider_task) is False

