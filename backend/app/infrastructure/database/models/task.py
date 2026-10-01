"""SQLAlchemy ORM model for Task entity."""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.domain.enums import TaskPriority, TaskStatus
from app.infrastructure.database.base import Base, pg_enum

if TYPE_CHECKING:
    from app.infrastructure.database.models.project import ProjectModel
    from app.infrastructure.database.models.workspace import WorkspaceModel


class TaskModel(Base):
    """An actionable unit of work within a Project."""

    __tablename__ = "tasks"

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
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[TaskStatus] = mapped_column(
        pg_enum(TaskStatus, "task_status_enum"),
        nullable=False,
        default=TaskStatus.TODO,
        server_default=TaskStatus.TODO.value,
    )
    priority: Mapped[TaskPriority] = mapped_column(
        pg_enum(TaskPriority, "task_priority_enum"),
        nullable=False,
        default=TaskPriority.MEDIUM,
        server_default=TaskPriority.MEDIUM.value,
    )
    creator_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    assignee_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    parent_task_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    due_date: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
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
        back_populates="tasks",
        overlaps="project,tasks",
    )
    project: Mapped["ProjectModel"] = relationship(
        "ProjectModel",
        back_populates="tasks",
        overlaps="workspace,tasks",
    )
    subtasks: Mapped[list["TaskModel"]] = relationship(
        "TaskModel",
        back_populates="parent_task",
        foreign_keys=[parent_task_id],
    )
    parent_task: Mapped["TaskModel | None"] = relationship(
        "TaskModel",
        back_populates="subtasks",
        remote_side=[id],
        foreign_keys=[parent_task_id],
    )

    __table_args__ = (
        UniqueConstraint("id", "workspace_id", name="uq_tasks_id_workspace"),
        UniqueConstraint("project_id", "number", name="uq_tasks_project_number"),
        ForeignKeyConstraint(
            ["project_id", "workspace_id"],
            ["projects.id", "projects.workspace_id"],
            ondelete="RESTRICT",
            name="fk_tasks_project",
        ),
        ForeignKeyConstraint(
            ["creator_user_id", "workspace_id"],
            ["users.id", "users.workspace_id"],
            ondelete="RESTRICT",
            name="fk_tasks_creator",
        ),
        ForeignKeyConstraint(
            ["assignee_user_id", "workspace_id"],
            ["users.id", "users.workspace_id"],
            ondelete="SET NULL",
            name="fk_tasks_assignee",
        ),
        ForeignKeyConstraint(
            ["parent_task_id", "workspace_id"],
            ["tasks.id", "tasks.workspace_id"],
            ondelete="SET NULL",
            name="fk_tasks_parent",
        ),
        Index(
            "idx_tasks_workspace_project_status",
            "workspace_id",
            "project_id",
            "status",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "idx_tasks_workspace_assignee",
            "workspace_id",
            "assignee_user_id",
            "status",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "idx_tasks_workspace_creator",
            "workspace_id",
            "creator_user_id",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "idx_tasks_parent",
            "parent_task_id",
            postgresql_where=text("parent_task_id IS NOT NULL"),
        ),
        Index(
            "idx_tasks_due_calendar",
            "workspace_id",
            "due_date",
            postgresql_where=text(
                "due_date IS NOT NULL AND status NOT IN ('done', 'cancelled') AND deleted_at IS NULL"
            ),
        ),
    )
