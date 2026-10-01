"""SQLAlchemy ORM models for Team and TeamMember entities."""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    Boolean,
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

from app.domain.enums import TeamRole
from app.infrastructure.database.base import Base, pg_enum

if TYPE_CHECKING:
    from app.infrastructure.database.models.project import ProjectModel
    from app.infrastructure.database.models.user import UserModel
    from app.infrastructure.database.models.workspace import WorkspaceModel


class TeamModel(Base):
    """A collaborative group within a Workspace."""

    __tablename__ = "teams"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="RESTRICT"),
        nullable=False,
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
    is_private: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )
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
        back_populates="teams",
    )
    members: Mapped[list["TeamMemberModel"]] = relationship(
        "TeamMemberModel",
        back_populates="team",
        cascade="all, delete-orphan",
    )
    projects: Mapped[list["ProjectModel"]] = relationship(
        "ProjectModel",
        back_populates="team",
        overlaps="workspace,projects",
    )

    __table_args__ = (
        UniqueConstraint("id", "workspace_id", name="uq_teams_id_workspace"),
        ForeignKeyConstraint(
            ["created_by_user_id", "workspace_id"],
            ["users.id", "users.workspace_id"],
            ondelete="RESTRICT",
            name="fk_teams_creator",
        ),
        ForeignKeyConstraint(
            ["lead_user_id", "workspace_id"],
            ["users.id", "users.workspace_id"],
            ondelete="SET NULL",
            name="fk_teams_lead",
        ),
        Index(
            "uq_teams_workspace_slug",
            "workspace_id",
            "slug",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index("idx_teams_workspace_lead", "workspace_id", "lead_user_id"),
        Index("idx_teams_workspace_private", "workspace_id", "is_private"),
    )


class TeamMemberModel(Base):
    """Association table linking Team and User."""

    __tablename__ = "team_members"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="RESTRICT"),
        nullable=False,
    )
    team_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    role: Mapped[TeamRole] = mapped_column(
        pg_enum(TeamRole, "team_role_enum"),
        nullable=False,
        default=TeamRole.MEMBER,
        server_default=TeamRole.MEMBER.value,
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
    team: Mapped["TeamModel"] = relationship(
        "TeamModel",
        back_populates="members",
    )

    __table_args__ = (
        UniqueConstraint("team_id", "user_id", name="uq_team_members_pair"),
        ForeignKeyConstraint(
            ["team_id", "workspace_id"],
            ["teams.id", "teams.workspace_id"],
            ondelete="CASCADE",
            name="fk_team_members_team",
        ),
        ForeignKeyConstraint(
            ["user_id", "workspace_id"],
            ["users.id", "users.workspace_id"],
            ondelete="CASCADE",
            name="fk_team_members_user",
        ),
        Index("idx_team_members_workspace_user", "workspace_id", "user_id"),
        Index("idx_team_members_workspace_roster", "workspace_id", "team_id", "role"),
    )
