"""PostgreSQL-backed ReBAC authorization repository.

Implements the AuthorizationRepository port using native PostgreSQL queries and CTEs:
- Enforces strict workspace tenancy isolation on every query.
- Evaluates Zanzibar/ReBAC relationships: Workspace roles, Team roles, Project roles, and Visibility.
- Enforces guest project confinement and team-contributor private-project restrictions.
- Compiles pre-retrieval AccessibleScope via an indexed CTE query.
- Excludes soft-deleted records and inactive users across all entities.
"""

from typing import Any
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.authorization.context import AccessibleScope, IdentityContext, ResourceTarget
from app.application.authorization.ports import AuthorizationRepository
from app.domain.authorization.permissions import (
    Action,
    KnowledgeAction,
    ProjectAction,
    ResourceType,
    TaskAction,
    TeamAction,
    WorkspaceAction,
)
from app.domain.enums import ProjectVisibility


class PostgresAuthorizationRepository(AuthorizationRepository):
    """PostgreSQL implementation of AuthorizationRepository port."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_accessible_scope(self, identity: IdentityContext) -> AccessibleScope:
        """Deterministically compile the accessible scope for an identity within their workspace."""
        # 1. System workers have write-only pipeline access and zero user-facing retrieval scope
        if identity.is_system or not identity.user_id:
            return AccessibleScope.empty(
                workspace_id=identity.workspace_id,
                user_id=identity.user_id or uuid.UUID(int=0),
            )

        # 2. Authoritative check on user's active status and database workspace_role
        user_stmt = text(
            """
            SELECT workspace_role, status
            FROM users
            WHERE id = :user_id 
              AND workspace_id = :workspace_id 
              AND deleted_at IS NULL;
            """
        )
        user_res = await self._session.execute(
            user_stmt,
            {"user_id": identity.user_id, "workspace_id": identity.workspace_id},
        )
        user_row = user_res.fetchone()
        if not user_row or user_row._mapping["status"] != "active":
            return AccessibleScope.empty(
                workspace_id=identity.workspace_id,
                user_id=identity.user_id,
            )

        db_role = user_row._mapping["workspace_role"]
        can_access_public = (db_role != "guest")

        # 3. Execute the pre-retrieval compilation CTE query
        cte_query = text(
            """
            WITH user_context AS (
                SELECT id, workspace_id, workspace_role
                FROM users
                WHERE id = :user_id 
                  AND workspace_id = :workspace_id 
                  AND deleted_at IS NULL
                  AND status = 'active'
            ),
            direct_project_grants AS (
                SELECT pm.project_id
                FROM project_members pm
                JOIN user_context uc ON uc.id = pm.user_id AND uc.workspace_id = pm.workspace_id
                JOIN projects p ON p.id = pm.project_id AND p.workspace_id = pm.workspace_id
                WHERE p.deleted_at IS NULL
            ),
            team_project_grants AS (
                SELECT p.id AS project_id
                FROM projects p
                JOIN team_members tm ON tm.team_id = p.team_id AND tm.workspace_id = p.workspace_id
                JOIN teams t ON t.id = p.team_id AND t.workspace_id = p.workspace_id
                JOIN user_context uc ON uc.id = tm.user_id AND uc.workspace_id = tm.workspace_id
                WHERE p.deleted_at IS NULL 
                  AND t.deleted_at IS NULL
                  AND tm.role IN ('lead', 'member')
            ),
            workspace_public_projects AS (
                SELECT p.id AS project_id
                FROM projects p
                JOIN user_context uc ON uc.workspace_id = p.workspace_id
                WHERE p.visibility = 'workspace'
                  AND p.deleted_at IS NULL
                  AND uc.workspace_role != 'guest'
            ),
            admin_all_projects AS (
                SELECT p.id AS project_id
                FROM projects p
                JOIN user_context uc ON uc.workspace_id = p.workspace_id
                WHERE uc.workspace_role IN ('owner', 'admin')
                  AND p.deleted_at IS NULL
            )
            SELECT DISTINCT project_id 
            FROM (
                SELECT project_id FROM direct_project_grants
                UNION ALL
                SELECT project_id FROM team_project_grants
                UNION ALL
                SELECT project_id FROM workspace_public_projects
                UNION ALL
                SELECT project_id FROM admin_all_projects
            ) authorized_projects;
            """
        )
        result = await self._session.execute(
            cte_query,
            {"user_id": identity.user_id, "workspace_id": identity.workspace_id},
        )
        accessible_pids = {row[0] for row in result.fetchall()}

        return AccessibleScope(
            workspace_id=identity.workspace_id,
            user_id=identity.user_id,
            accessible_project_ids=frozenset(accessible_pids),
            can_access_workspace_public=can_access_public,
        )

    async def has_permission(
        self,
        identity: IdentityContext,
        action: Action,
        resource: ResourceTarget,
    ) -> bool:
        """Evaluate if the identity has permission to perform action on resource."""
        # 1. Tenancy isolation: Mismatched workspaces immediately evaluate to False
        if resource.workspace_id != identity.workspace_id:
            return False

        act_str = action.value if hasattr(action, "value") else str(action)

        # 2. System worker: Write-only pipeline access, no user-facing query or retrieval permissions
        if identity.is_system:
            if act_str.startswith("knowledge:") or act_str.endswith(":read"):
                return False
            # Pipeline writes within workspace
            return True

        if not identity.user_id:
            return False

        # 3. Query authoritative user status and role from database
        user_query = text(
            """
            SELECT workspace_role, status
            FROM users
            WHERE id = :user_id 
              AND workspace_id = :workspace_id 
              AND deleted_at IS NULL;
            """
        )
        res = await self._session.execute(
            user_query,
            {"user_id": identity.user_id, "workspace_id": identity.workspace_id},
        )
        user_row = res.fetchone()
        if not user_row or user_row._mapping["status"] != "active":
            return False

        db_role = user_row._mapping["workspace_role"]
        is_owner = (db_role == "owner")
        is_admin = (db_role in ("owner", "admin"))
        is_member = (db_role in ("owner", "admin", "member"))
        is_guest = (db_role == "guest")

        # 4. Dispatch based on resource type
        if resource.resource_type == ResourceType.WORKSPACE:
            return await self._eval_workspace_permission(
                identity.workspace_id, act_str, is_owner, is_admin, is_member
            )

        if resource.resource_type == ResourceType.TEAM:
            return await self._eval_team_permission(
                identity.workspace_id,
                identity.user_id,
                resource.resource_id,
                act_str,
                is_admin,
                is_guest,
            )

        if resource.resource_type == ResourceType.PROJECT:
            return await self._eval_project_permission(
                identity.workspace_id,
                identity.user_id,
                resource.resource_id,
                act_str,
                is_admin,
                is_guest,
            )

        if resource.resource_type == ResourceType.TASK:
            return await self._eval_task_permission(
                identity.workspace_id,
                identity.user_id,
                resource.resource_id,
                act_str,
                is_admin,
                is_guest,
            )

        if resource.resource_type in (ResourceType.KNOWLEDGE, ResourceType.EVIDENCE):
            return await self._eval_knowledge_permission(
                identity.workspace_id,
                identity.user_id,
                resource,
                act_str,
                is_admin,
                is_guest,
            )

        return False

    async def _eval_workspace_permission(
        self,
        workspace_id: uuid.UUID,
        action: str,
        is_owner: bool,
        is_admin: bool,
        is_member: bool,
    ) -> bool:
        """Evaluate workspace-level actions."""
        # Confirm workspace exists and is active
        ws_res = await self._session.execute(
            text("SELECT id FROM workspaces WHERE id = :id AND deleted_at IS NULL;"),
            {"id": workspace_id},
        )
        if not ws_res.fetchone():
            return False

        if action == WorkspaceAction.READ.value:
            # Active participants (including guests) can read workspace metadata
            return True
        if action == WorkspaceAction.UPDATE.value:
            return is_admin
        if action == WorkspaceAction.MANAGE_MEMBERS.value:
            return is_admin
        if action == WorkspaceAction.DELETE.value:
            return is_owner
        if action == WorkspaceAction.INVITE.value:
            return is_member

        return False

    async def _eval_team_permission(
        self,
        workspace_id: uuid.UUID,
        user_id: uuid.UUID,
        team_id: uuid.UUID,
        action: str,
        is_admin: bool,
        is_guest: bool,
    ) -> bool:
        """Evaluate team-level actions."""
        if action == TeamAction.CREATE.value:
            # Non-guest members can create teams
            return not is_guest

        # Query team existence and user team role
        team_query = text(
            """
            SELECT t.id, t.lead_user_id, tm.role AS team_role
            FROM teams t
            LEFT JOIN team_members tm ON tm.team_id = t.id 
                                      AND tm.workspace_id = t.workspace_id 
                                      AND tm.user_id = :user_id
            WHERE t.id = :team_id 
              AND t.workspace_id = :workspace_id 
              AND t.deleted_at IS NULL;
            """
        )
        res = await self._session.execute(
            team_query,
            {"team_id": team_id, "workspace_id": workspace_id, "user_id": user_id},
        )
        row = res.fetchone()
        if not row:
            return False

        m = row._mapping
        team_role = m["team_role"]
        is_team_lead = (is_admin or m["lead_user_id"] == user_id or team_role == "lead")
        is_team_member = (team_role in ("lead", "member"))
        is_team_contributor = (team_role == "contributor")

        if action == TeamAction.READ.value:
            # Admins, team members/contributors, or non-guests can view team metadata
            return is_admin or is_team_lead or is_team_member or is_team_contributor or (not is_guest)

        if action in (TeamAction.UPDATE.value, TeamAction.MANAGE_MEMBERS.value, TeamAction.DELETE.value):
            return is_team_lead

        return False

    async def _eval_project_permission(
        self,
        workspace_id: uuid.UUID,
        user_id: uuid.UUID,
        project_id: uuid.UUID,
        action: str,
        is_admin: bool,
        is_guest: bool,
    ) -> bool:
        """Evaluate project-level actions against ReBAC relationships."""
        if action == ProjectAction.CREATE.value:
            return not is_guest

        # Query project, direct project membership, and owning team membership
        query = text(
            """
            SELECT 
                p.id, 
                p.visibility, 
                p.lead_user_id, 
                p.team_id,
                pm.role AS project_role,
                tm.role AS team_role
            FROM projects p
            LEFT JOIN project_members pm ON pm.project_id = p.id 
                                         AND pm.workspace_id = p.workspace_id 
                                         AND pm.user_id = :user_id
            LEFT JOIN team_members tm ON tm.team_id = p.team_id 
                                      AND tm.workspace_id = p.workspace_id 
                                      AND tm.user_id = :user_id
            WHERE p.id = :project_id 
              AND p.workspace_id = :workspace_id 
              AND p.deleted_at IS NULL;
            """
        )
        res = await self._session.execute(
            query,
            {"project_id": project_id, "workspace_id": workspace_id, "user_id": user_id},
        )
        row = res.fetchone()
        if not row:
            return False

        m = row._mapping
        project_role = m["project_role"]
        team_role = m["team_role"]
        is_proj_lead = (is_admin or m["lead_user_id"] == user_id or project_role == "lead")
        is_proj_maintainer = (is_proj_lead or project_role == "maintainer")

        # Team contributor inheritance policy:
        # Members and leads inherit contributor access on team-owned projects.
        # Owning team contributors do NOT inherit access without an explicit project_members record.
        inherited_team_contributor = (team_role in ("lead", "member"))
        is_proj_contributor = (
            is_proj_maintainer
            or project_role == "contributor"
            or inherited_team_contributor
        )
        is_proj_viewer = (is_proj_contributor or project_role == "viewer")

        is_workspace_visible = (m["visibility"] == "workspace")

        if action == ProjectAction.READ.value:
            # Workspace superuser
            if is_admin:
                return True
            # Explicit project member
            if is_proj_viewer:
                return True
            # Owning team member (lead or member)
            if inherited_team_contributor:
                return True
            # Workspace-visible project for non-guests (guests require explicit project grant)
            if is_workspace_visible and not is_guest:
                return True
            return False

        if action in (
            ProjectAction.UPDATE.value,
            ProjectAction.MANAGE_MEMBERS.value,
            ProjectAction.DELETE.value,
        ):
            return is_proj_lead

        if action in (ProjectAction.ARCHIVE.value, ProjectAction.INVITE.value):
            return is_proj_maintainer

        return False

    async def _eval_task_permission(
        self,
        workspace_id: uuid.UUID,
        user_id: uuid.UUID,
        task_id: uuid.UUID,
        action: str,
        is_admin: bool,
        is_guest: bool,
    ) -> bool:
        """Evaluate task-level actions against parent project and task ownership."""
        query = text(
            """
            SELECT 
                tk.id AS task_id,
                tk.creator_user_id,
                tk.assignee_user_id,
                p.id AS project_id,
                p.visibility,
                p.lead_user_id AS project_lead_id,
                p.team_id,
                pm.role AS project_role,
                tm.role AS team_role
            FROM tasks tk
            JOIN projects p ON p.id = tk.project_id AND p.workspace_id = tk.workspace_id
            LEFT JOIN project_members pm ON pm.project_id = p.id 
                                         AND pm.workspace_id = p.workspace_id 
                                         AND pm.user_id = :user_id
            LEFT JOIN team_members tm ON tm.team_id = p.team_id 
                                      AND tm.workspace_id = p.workspace_id 
                                      AND tm.user_id = :user_id
            WHERE tk.id = :task_id 
              AND tk.workspace_id = :workspace_id 
              AND tk.deleted_at IS NULL
              AND p.deleted_at IS NULL;
            """
        )
        res = await self._session.execute(
            query,
            {"task_id": task_id, "workspace_id": workspace_id, "user_id": user_id},
        )
        row = res.fetchone()
        if not row:
            return False

        m = row._mapping
        project_role = m["project_role"]
        team_role = m["team_role"]
        is_proj_lead = (is_admin or m["project_lead_id"] == user_id or project_role == "lead")
        is_proj_maintainer = (is_proj_lead or project_role == "maintainer")
        inherited_team_contributor = (team_role in ("lead", "member"))
        is_proj_contributor = (
            is_proj_maintainer
            or project_role == "contributor"
            or inherited_team_contributor
        )
        is_proj_viewer = (is_proj_contributor or project_role == "viewer")

        can_read_project = (
            is_admin
            or is_proj_viewer
            or (m["visibility"] == "workspace" and not is_guest)
        )

        is_task_creator = (m["creator_user_id"] == user_id)
        is_task_assignee = (m["assignee_user_id"] == user_id)

        # Invariant: All task operations are strictly gated by access to the parent project
        if not can_read_project:
            return False

        if action == TaskAction.READ.value:
            return True

        if action == TaskAction.CREATE.value:
            return is_proj_contributor

        if action == TaskAction.UPDATE.value:
            return is_proj_contributor or is_task_creator or is_task_assignee

        if action == TaskAction.ASSIGN.value:
            return is_proj_maintainer or is_task_creator or is_task_assignee

        if action == TaskAction.DELETE.value:
            return is_proj_maintainer or is_task_creator

        return False

    async def _eval_knowledge_permission(
        self,
        workspace_id: uuid.UUID,
        user_id: uuid.UUID,
        resource: ResourceTarget,
        action: str,
        is_admin: bool,
        is_guest: bool,
    ) -> bool:
        """Evaluate knowledge graph and vector retrieval queries."""
        # Project-scoped knowledge
        if resource.project_id:
            return await self._eval_project_permission(
                workspace_id,
                user_id,
                resource.project_id,
                ProjectAction.READ.value,
                is_admin,
                is_guest,
            )

        # Unscoped knowledge: NULL project_id alone does NOT grant access
        vis_val = (
            resource.visibility.value
            if isinstance(resource.visibility, ProjectVisibility)
            else resource.visibility
        )
        if vis_val == ProjectVisibility.WORKSPACE.value and not is_guest:
            return True

        return False
