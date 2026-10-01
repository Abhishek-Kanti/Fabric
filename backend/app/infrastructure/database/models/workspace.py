"""SQLAlchemy ORM model for Workspace entity."""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import Boolean, DateTime, Index, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.infrastructure.database.base import Base

if TYPE_CHECKING:
    from app.infrastructure.database.models.project import ProjectModel
    from app.infrastructure.database.models.task import TaskModel
    from app.infrastructure.database.models.team import TeamModel
    from app.infrastructure.database.models.user import UserModel


class WorkspaceModel(Base):
    """The root administrative tenant boundary."""

    __tablename__ = "workspaces"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    avatar_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    settings: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
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

    # Relationships (navigation only)
    users: Mapped[list["UserModel"]] = relationship(
        "UserModel",
        back_populates="workspace",
        cascade="all, delete-orphan",
    )
    teams: Mapped[list["TeamModel"]] = relationship(
        "TeamModel",
        back_populates="workspace",
        cascade="all, delete-orphan",
    )
    projects: Mapped[list["ProjectModel"]] = relationship(
        "ProjectModel",
        back_populates="workspace",
        cascade="all, delete-orphan",
        overlaps="team,projects",
    )
    tasks: Mapped[list["TaskModel"]] = relationship(
        "TaskModel",
        back_populates="workspace",
        cascade="all, delete-orphan",
        overlaps="project,tasks",
    )

    __table_args__ = (
        Index(
            "uq_workspaces_slug",
            "slug",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "idx_workspaces_active",
            "is_active",
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )
