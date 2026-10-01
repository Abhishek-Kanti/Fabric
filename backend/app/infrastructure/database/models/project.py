"""SQLAlchemy ORM models for Project, ProjectMember, and ProjectInvite entities."""

import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    Date,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.domain.enums import (
    InviteStatus,
    ProjectRole,
    ProjectStatus,
    ProjectVisibility,
)
from app.infrastructure.database.base import Base, pg_enum

if TYPE_CHECKING:
    from app.infrastructure.database.models.task import TaskModel
    from app.infrastructure.database.models.team import TeamModel
    from app.infrastructure.database.models.workspace import WorkspaceModel


class ProjectModel(Base):
    """A scoped initiative, code repository, or deliverable."""

    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="RESTRICT"),
        nullable=False,
    )
    team_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    lead_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    status: Mapped[ProjectStatus] = mapped_column(
        pg_enum(ProjectStatus, "project_status_enum"),
        nullable=False,
        default=ProjectStatus.ACTIVE,
        server_default=ProjectStatus.ACTIVE.value,
    )
    visibility: Mapped[ProjectVisibility] = mapped_column(
        pg_enum(ProjectVisibility, "project_visibility_enum"),
        nullable=False,
        default=ProjectVisibility.WORKSPACE,
        server_default=ProjectVisibility.WORKSPACE.value,
    )
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    target_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Relationships
    workspace: Mapped["WorkspaceModel"] = relationship(
        "WorkspaceModel",
        back_populates="projects",
    )
    team: Mapped["TeamModel | None"] = relationship(
        "TeamModel",
        back_populates="projects",
        foreign_keys=[team_id],
        overlaps="workspace,projects",
    )
    members: Mapped[list["ProjectMemberModel"]] = relationship(
        "ProjectMemberModel",
        back_populates="project",
        cascade="all, delete-orphan",
    )
    invites: Mapped[list["ProjectInviteModel"]] = relationship(
        "ProjectInviteModel",
        back_populates="project",
        cascade="all, delete-orphan",
    )
    tasks: Mapped[list["TaskModel"]] = relationship(
        "TaskModel",
        back_populates="project",
        overlaps="workspace,tasks",
    )

    __table_args__ = (
        UniqueConstraint("id", "workspace_id", name="uq_projects_id_workspace"),
        ForeignKeyConstraint(
            ["team_id", "workspace_id"],
            ["teams.id", "teams.workspace_id"],
            ondelete="SET NULL",
            name="fk_projects_team",
        ),
        ForeignKeyConstraint(
            ["created_by_user_id", "workspace_id"],
            ["users.id", "users.workspace_id"],
            ondelete="RESTRICT",
            name="fk_projects_creator",
        ),
        ForeignKeyConstraint(
            ["lead_user_id", "workspace_id"],
            ["users.id", "users.workspace_id"],
            ondelete="SET NULL",
            name="fk_projects_lead",
        ),
        Index(
            "uq_projects_workspace_slug",
            "workspace_id",
            "slug",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "idx_projects_workspace_status",
            "workspace_id",
            "status",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index("idx_projects_workspace_team", "workspace_id", "team_id"),
        Index("idx_projects_workspace_lead", "workspace_id", "lead_user_id"),
        Index("idx_projects_visibility", "workspace_id", "visibility"),
    )


class ProjectMemberModel(Base):
    """Explicit project-level user access grants and roles."""

    __tablename__ = "project_members"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="RESTRICT"),
        nullable=False,
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    role: Mapped[ProjectRole] = mapped_column(
        pg_enum(ProjectRole, "project_role_enum"),
        nullable=False,
        default=ProjectRole.CONTRIBUTOR,
        server_default=ProjectRole.CONTRIBUTOR.value,
    )
    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    # Relationships
    project: Mapped["ProjectModel"] = relationship(
        "ProjectModel",
        back_populates="members",
    )

    __table_args__ = (
        UniqueConstraint("project_id", "user_id", name="uq_project_members_pair"),
        ForeignKeyConstraint(
            ["project_id", "workspace_id"],
            ["projects.id", "projects.workspace_id"],
            ondelete="CASCADE",
            name="fk_project_members_project",
        ),
        ForeignKeyConstraint(
            ["user_id", "workspace_id"],
            ["users.id", "users.workspace_id"],
            ondelete="CASCADE",
            name="fk_project_members_user",
        ),
        Index("idx_project_members_workspace_user", "workspace_id", "user_id"),
        Index("idx_project_members_workspace_roster", "workspace_id", "project_id", "role"),
    )


class ProjectInviteModel(Base):
    """Secure invitation tokens for prospective project contributors."""

    __tablename__ = "project_invites"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="RESTRICT"),
        nullable=False,
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    role: Mapped[ProjectRole] = mapped_column(
        pg_enum(ProjectRole, "project_role_enum"),
        nullable=False,
        default=ProjectRole.CONTRIBUTOR,
        server_default=ProjectRole.CONTRIBUTOR.value,
    )
    invited_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    token_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[InviteStatus] = mapped_column(
        pg_enum(InviteStatus, "invite_status_enum"),
        nullable=False,
        default=InviteStatus.PENDING,
        server_default=InviteStatus.PENDING.value,
    )
    accepted_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    accepted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    # Relationships
    project: Mapped["ProjectModel"] = relationship(
        "ProjectModel",
        back_populates="invites",
    )

    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_project_invites_token_hash"),
        ForeignKeyConstraint(
            ["project_id", "workspace_id"],
            ["projects.id", "projects.workspace_id"],
            ondelete="CASCADE",
            name="fk_project_invites_project",
        ),
        ForeignKeyConstraint(
            ["invited_by_user_id", "workspace_id"],
            ["users.id", "users.workspace_id"],
            ondelete="CASCADE",
            name="fk_project_invites_inviter",
        ),
        ForeignKeyConstraint(
            ["accepted_by_user_id", "workspace_id"],
            ["users.id", "users.workspace_id"],
            ondelete="SET NULL",
            name="fk_project_invites_claimed_by",
        ),
        Index(
            "uq_project_invites_active",
            "project_id",
            "email",
            unique=True,
            postgresql_where=text("status = 'pending'"),
        ),
        Index(
            "idx_project_invites_status_expires",
            "status",
            "expires_at",
            postgresql_where=text("status = 'pending'"),
        ),
        Index("idx_project_invites_workspace", "workspace_id", "status"),
    )
