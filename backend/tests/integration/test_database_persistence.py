"""Integration tests verifying PostgreSQL database schema, constraints, and semantics.

These tests run against real PostgreSQL and verify:
- Complete entity hierarchy persistence
- UUIDv7 application generation
- Database-enforced workspace consistency (composite FK rejection)
- Unique constraints and partial indexes
- Soft deletion lifecycle and slug reuse
- Physical deletion restrictions (RESTRICT vs CASCADE)
- Project task numbering
- Enum persistence
"""

from datetime import datetime, timedelta, timezone
import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.enums import (
    InviteStatus,
    ProjectRole,
    ProjectStatus,
    ProjectVisibility,
    TaskPriority,
    TaskStatus,
    TeamRole,
    UserStatus,
    WorkspaceRole,
)
from app.infrastructure.database.models import (
    ProjectInviteModel,
    ProjectMemberModel,
    ProjectModel,
    TaskModel,
    TeamMemberModel,
    TeamModel,
    UserModel,
    WorkspaceModel,
)
from app.shared.utils.uuid import generate_uuid7


@pytest.mark.asyncio
async def test_persist_valid_hierarchy(db_session: AsyncSession):
    """Verify that a complete, valid team-first hierarchy can be persisted."""
    ws_id = generate_uuid7()
    workspace = WorkspaceModel(
        id=ws_id,
        name="Robotics Research Lab",
        slug="robotics-lab",
        description="Autonomous systems research workspace",
    )
    db_session.add(workspace)
    await db_session.flush()

    user_id = generate_uuid7()
    user = UserModel(
        id=user_id,
        workspace_id=ws_id,
        email="lead@robotics.lab",
        display_name="Dr. Alex Vance",
        handle="alexvance",
        workspace_role=WorkspaceRole.OWNER,
        status=UserStatus.ACTIVE,
    )
    db_session.add(user)
    await db_session.flush()

    team_id = generate_uuid7()
    team = TeamModel(
        id=team_id,
        workspace_id=ws_id,
        name="Navigation Core",
        slug="nav-core",
        created_by_user_id=user_id,
        lead_user_id=user_id,
    )
    db_session.add(team)
    await db_session.flush()

    team_member = TeamMemberModel(
        id=generate_uuid7(),
        workspace_id=ws_id,
        team_id=team_id,
        user_id=user_id,
        role=TeamRole.LEAD,
    )
    db_session.add(team_member)
    await db_session.flush()

    project_id = generate_uuid7()
    project = ProjectModel(
        id=project_id,
        workspace_id=ws_id,
        team_id=team_id,
        name="SLAM Engine v2",
        slug="slam-v2",
        created_by_user_id=user_id,
        lead_user_id=user_id,
        status=ProjectStatus.ACTIVE,
        visibility=ProjectVisibility.WORKSPACE,
    )
    db_session.add(project)
    await db_session.flush()

    project_member = ProjectMemberModel(
        id=generate_uuid7(),
        workspace_id=ws_id,
        project_id=project_id,
        user_id=user_id,
        role=ProjectRole.LEAD,
    )
    db_session.add(project_member)

    invite = ProjectInviteModel(
        id=generate_uuid7(),
        workspace_id=ws_id,
        project_id=project_id,
        email="collab@robotics.lab",
        role=ProjectRole.CONTRIBUTOR,
        invited_by_user_id=user_id,
        token_hash="a" * 64,
        status=InviteStatus.PENDING,
        expires_at=datetime.now(timezone.utc) + timedelta(days=7),
    )
    db_session.add(invite)

    task_id = generate_uuid7()
    task = TaskModel(
        id=task_id,
        workspace_id=ws_id,
        project_id=project_id,
        number=1,
        title="Calibrate LiDAR sensors",
        status=TaskStatus.IN_PROGRESS,
        priority=TaskPriority.HIGH,
        creator_user_id=user_id,
        assignee_user_id=user_id,
    )
    db_session.add(task)
    await db_session.flush()

    # Query back and verify
    result = await db_session.execute(
        select(TaskModel).where(TaskModel.id == task_id)
    )
    loaded_task = result.scalar_one()
    assert loaded_task.title == "Calibrate LiDAR sensors"
    assert loaded_task.number == 1
    assert loaded_task.status == TaskStatus.IN_PROGRESS
    assert loaded_task.priority == TaskPriority.HIGH
    assert loaded_task.workspace_id == ws_id


@pytest.mark.asyncio
async def test_cross_workspace_relationship_rejected(db_session: AsyncSession):
    """Verify that composite foreign keys strictly block cross-workspace linkages."""
    # Workspace A
    ws_a_id = generate_uuid7()
    ws_a = WorkspaceModel(id=ws_a_id, name="Workspace A", slug="workspace-a")
    db_session.add(ws_a)

    user_a_id = generate_uuid7()
    user_a = UserModel(
        id=user_a_id,
        workspace_id=ws_a_id,
        email="user_a@test.com",
        display_name="User A",
        handle="usera",
    )
    db_session.add(user_a)
    await db_session.flush()

    team_a_id = generate_uuid7()
    team_a = TeamModel(
        id=team_a_id,
        workspace_id=ws_a_id,
        name="Team A",
        slug="team-a",
        created_by_user_id=user_a_id,
    )
    db_session.add(team_a)

    proj_a_id = generate_uuid7()
    proj_a = ProjectModel(
        id=proj_a_id,
        workspace_id=ws_a_id,
        name="Project A",
        slug="project-a",
        created_by_user_id=user_a_id,
    )
    db_session.add(proj_a)
    await db_session.flush()

    # Workspace B
    ws_b_id = generate_uuid7()
    ws_b = WorkspaceModel(id=ws_b_id, name="Workspace B", slug="workspace-b")
    db_session.add(ws_b)

    user_b_id = generate_uuid7()
    user_b = UserModel(
        id=user_b_id,
        workspace_id=ws_b_id,
        email="user_b@test.com",
        display_name="User B",
        handle="userb",
    )
    db_session.add(user_b)
    await db_session.flush()

    # 1. Attempt to add User B (Workspace B) into Team A (Workspace A)
    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            invalid_team_member = TeamMemberModel(
                id=generate_uuid7(),
                workspace_id=ws_a_id,
                team_id=team_a_id,
                user_id=user_b_id,  # User from Workspace B!
            )
            db_session.add(invalid_team_member)
            await db_session.flush()

    # 2. Attempt to add User B (Workspace B) into Project A (Workspace A)
    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            invalid_proj_member = ProjectMemberModel(
                id=generate_uuid7(),
                workspace_id=ws_a_id,
                project_id=proj_a_id,
                user_id=user_b_id,  # User from Workspace B!
            )
            db_session.add(invalid_proj_member)
            await db_session.flush()

    # 3. Attempt to assign Task in Project A to User B (Workspace B)
    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            invalid_task = TaskModel(
                id=generate_uuid7(),
                workspace_id=ws_a_id,
                project_id=proj_a_id,
                number=99,
                title="Cross-tenant task",
                creator_user_id=user_a_id,
                assignee_user_id=user_b_id,  # User from Workspace B!
            )
            db_session.add(invalid_task)
            await db_session.flush()


@pytest.mark.asyncio
async def test_unique_constraints(db_session: AsyncSession):
    """Verify unique constraint enforcement across entities."""
    ws_id = generate_uuid7()
    ws = WorkspaceModel(id=ws_id, name="Test WS", slug="test-ws")
    db_session.add(ws)
    await db_session.flush()

    # 1. Duplicate workspace slug
    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            dup_ws = WorkspaceModel(id=generate_uuid7(), name="Other WS", slug="test-ws")
            db_session.add(dup_ws)
            await db_session.flush()

    # 2. Duplicate user email in same workspace
    user1_id = generate_uuid7()
    user1 = UserModel(
        id=user1_id,
        workspace_id=ws_id,
        email="dev@test.com",
        display_name="Dev One",
        handle="devone",
    )
    db_session.add(user1)
    await db_session.flush()

    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            dup_email_user = UserModel(
                id=generate_uuid7(),
                workspace_id=ws_id,
                email="dev@test.com",
                display_name="Dev Two",
                handle="devtwo",
            )
            db_session.add(dup_email_user)
            await db_session.flush()

    # 3. Duplicate user handle in same workspace
    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            dup_handle_user = UserModel(
                id=generate_uuid7(),
                workspace_id=ws_id,
                email="other@test.com",
                display_name="Dev Two",
                handle="devone",
            )
            db_session.add(dup_handle_user)
            await db_session.flush()

    # 4. Project task number uniqueness
    proj_id = generate_uuid7()
    proj = ProjectModel(
        id=proj_id,
        workspace_id=ws_id,
        name="Alpha Project",
        slug="alpha",
        created_by_user_id=user1_id,
    )
    db_session.add(proj)
    await db_session.flush()

    task1 = TaskModel(
        id=generate_uuid7(),
        workspace_id=ws_id,
        project_id=proj_id,
        number=1,
        title="First Task",
        creator_user_id=user1_id,
    )
    db_session.add(task1)
    await db_session.flush()

    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            dup_task_num = TaskModel(
                id=generate_uuid7(),
                workspace_id=ws_id,
                project_id=proj_id,
                number=1,  # Same number in same project!
                title="Conflicting Task",
                creator_user_id=user1_id,
            )
            db_session.add(dup_task_num)
            await db_session.flush()


@pytest.mark.asyncio
async def test_soft_deletion_and_slug_reuse(db_session: AsyncSession):
    """Verify soft deletion semantics and that partial indexes allow slug reuse."""
    ws_id = generate_uuid7()
    ws = WorkspaceModel(id=ws_id, name="Old Lab", slug="reuse-slug")
    db_session.add(ws)
    await db_session.flush()

    # Soft delete the workspace
    ws.deleted_at = datetime.now(timezone.utc)
    await db_session.flush()

    # A new workspace can now reuse the same slug because of the partial index
    new_ws_id = generate_uuid7()
    new_ws = WorkspaceModel(id=new_ws_id, name="New Lab", slug="reuse-slug")
    db_session.add(new_ws)
    await db_session.flush()

    res = await db_session.execute(
        select(WorkspaceModel).where(WorkspaceModel.slug == "reuse-slug")
    )
    workspaces = res.scalars().all()
    assert len(workspaces) == 2
    active_workspaces = [w for w in workspaces if w.deleted_at is None]
    assert len(active_workspaces) == 1
    assert active_workspaces[0].id == new_ws_id


@pytest.mark.asyncio
async def test_physical_deletion_restrictions(db_session: AsyncSession):
    """Verify that ON DELETE RESTRICT guards prevent accidental destruction."""
    ws_id = generate_uuid7()
    ws = WorkspaceModel(id=ws_id, name="Protected Lab", slug="protected-lab")
    db_session.add(ws)
    await db_session.flush()

    user_id = generate_uuid7()
    user = UserModel(
        id=user_id,
        workspace_id=ws_id,
        email="protected@lab.org",
        display_name="Lead Researcher",
        handle="leadresearcher",
    )
    db_session.add(user)
    await db_session.flush()

    proj_id = generate_uuid7()
    proj = ProjectModel(
        id=proj_id,
        workspace_id=ws_id,
        name="Crucial Project",
        slug="crucial-proj",
        created_by_user_id=user_id,
    )
    db_session.add(proj)
    await db_session.flush()

    task_id = generate_uuid7()
    task = TaskModel(
        id=task_id,
        workspace_id=ws_id,
        project_id=proj_id,
        number=1,
        title="Crucial Task",
        creator_user_id=user_id,
    )
    db_session.add(task)
    await db_session.flush()

    # 1. Attempting to physically delete Project when Tasks exist MUST FAIL (RESTRICT)
    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            await db_session.delete(proj)
            await db_session.flush()

    # 2. Attempting to physically delete Workspace when Users/Projects exist MUST FAIL (RESTRICT)
    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            await db_session.delete(ws)
            await db_session.flush()


@pytest.mark.asyncio
async def test_subtask_hierarchy_in_same_workspace(db_session: AsyncSession):
    """Verify subtasks can reference parent tasks within the same workspace."""
    ws_id = generate_uuid7()
    ws = WorkspaceModel(id=ws_id, name="Subtask Lab", slug="subtask-lab")
    db_session.add(ws)

    user_id = generate_uuid7()
    user = UserModel(
        id=user_id,
        workspace_id=ws_id,
        email="dev@subtask.lab",
        display_name="Dev",
        handle="subdev",
    )
    db_session.add(user)
    await db_session.flush()

    proj_id = generate_uuid7()
    proj = ProjectModel(
        id=proj_id,
        workspace_id=ws_id,
        name="Subtask Project",
        slug="subtask-proj",
        created_by_user_id=user_id,
    )
    db_session.add(proj)
    await db_session.flush()

    parent_task_id = generate_uuid7()
    parent_task = TaskModel(
        id=parent_task_id,
        workspace_id=ws_id,
        project_id=proj_id,
        number=1,
        title="Parent Epic",
        creator_user_id=user_id,
    )
    db_session.add(parent_task)
    await db_session.flush()

    child_task_id = generate_uuid7()
    child_task = TaskModel(
        id=child_task_id,
        workspace_id=ws_id,
        project_id=proj_id,
        number=2,
        title="Child Subtask",
        creator_user_id=user_id,
        parent_task_id=parent_task_id,
    )
    db_session.add(child_task)
    await db_session.flush()

    # Query back child task
    res = await db_session.execute(
        select(TaskModel).where(TaskModel.id == child_task_id)
    )
    loaded_child = res.scalar_one()
    assert loaded_child.parent_task_id == parent_task_id


@pytest.mark.asyncio
async def test_membership_constraints_and_cascade_lifecycle(db_session: AsyncSession):
    """Verify team/project membership uniqueness and cascade deletion behavior."""
    ws_id = generate_uuid7()
    ws = WorkspaceModel(id=ws_id, name="Member Lab", slug="member-lab")
    db_session.add(ws)

    user_id = generate_uuid7()
    user = UserModel(
        id=user_id,
        workspace_id=ws_id,
        email="collab@member.lab",
        display_name="Collab User",
        handle="collab",
    )
    db_session.add(user)
    await db_session.flush()

    team_id = generate_uuid7()
    team = TeamModel(
        id=team_id,
        workspace_id=ws_id,
        name="Collab Team",
        slug="collab-team",
        created_by_user_id=user_id,
    )
    db_session.add(team)
    await db_session.flush()

    # 1. Add team member
    tm_id = generate_uuid7()
    tm = TeamMemberModel(
        id=tm_id,
        workspace_id=ws_id,
        team_id=team_id,
        user_id=user_id,
        role=TeamRole.MEMBER,
    )
    db_session.add(tm)
    await db_session.flush()

    # 2. Reject duplicate team member pair (uq_team_members_pair)
    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            dup_tm = TeamMemberModel(
                id=generate_uuid7(),
                workspace_id=ws_id,
                team_id=team_id,
                user_id=user_id,
                role=TeamRole.LEAD,
            )
            db_session.add(dup_tm)
            await db_session.flush()

    # 3. Add project and project member
    proj_id = generate_uuid7()
    proj = ProjectModel(
        id=proj_id,
        workspace_id=ws_id,
        team_id=team_id,
        name="Collab Project",
        slug="collab-proj",
        created_by_user_id=user_id,
    )
    db_session.add(proj)
    await db_session.flush()

    pm_id = generate_uuid7()
    pm = ProjectMemberModel(
        id=pm_id,
        workspace_id=ws_id,
        project_id=proj_id,
        user_id=user_id,
        role=ProjectRole.CONTRIBUTOR,
    )
    db_session.add(pm)

    invite_id = generate_uuid7()
    invite = ProjectInviteModel(
        id=invite_id,
        workspace_id=ws_id,
        project_id=proj_id,
        email="invitee@member.lab",
        role=ProjectRole.VIEWER,
        invited_by_user_id=user_id,
        token_hash="tok_hash_unique_12345",
        expires_at=datetime.now(timezone.utc) + timedelta(days=3),
    )
    db_session.add(invite)
    await db_session.flush()

    # 4. Reject duplicate project member pair (uq_project_members_pair)
    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            dup_pm = ProjectMemberModel(
                id=generate_uuid7(),
                workspace_id=ws_id,
                project_id=proj_id,
                user_id=user_id,
                role=ProjectRole.MAINTAINER,
            )
            db_session.add(dup_pm)
            await db_session.flush()

    # 5. Reject duplicate invite token hash (uq_project_invites_token_hash)
    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            dup_invite = ProjectInviteModel(
                id=generate_uuid7(),
                workspace_id=ws_id,
                project_id=proj_id,
                email="another@member.lab",
                role=ProjectRole.VIEWER,
                invited_by_user_id=user_id,
                token_hash="tok_hash_unique_12345",  # Same token hash
                expires_at=datetime.now(timezone.utc) + timedelta(days=3),
            )
            db_session.add(dup_invite)
            await db_session.flush()

    # 6. Physical cascade deletion: Deleting project removes project_members and project_invites
    await db_session.delete(proj)
    await db_session.flush()

    res_pm = await db_session.execute(
        select(ProjectMemberModel).where(ProjectMemberModel.id == pm_id)
    )
    assert res_pm.scalar_one_or_none() is None

    res_inv = await db_session.execute(
        select(ProjectInviteModel).where(ProjectInviteModel.id == invite_id)
    )
    assert res_inv.scalar_one_or_none() is None

    # 7. Physical cascade deletion: Deleting team removes team_members
    await db_session.delete(team)
    await db_session.flush()

    res_tm = await db_session.execute(
        select(TeamMemberModel).where(TeamMemberModel.id == tm_id)
    )
    assert res_tm.scalar_one_or_none() is None

