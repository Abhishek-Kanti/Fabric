"""SQLAlchemy ORM model for User entity."""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.domain.enums import UserStatus, WorkspaceRole
from app.infrastructure.database.base import Base, pg_enum

if TYPE_CHECKING:
    from app.infrastructure.database.models.workspace import WorkspaceModel


class UserModel(Base):
    """An individual participant within a Workspace."""

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="RESTRICT"),
        nullable=False,
    )
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    handle: Mapped[str] = mapped_column(String(100), nullable=False)
    avatar_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    auth_provider: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="local",
        server_default="local",
    )
    auth_provider_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    workspace_role: Mapped[WorkspaceRole] = mapped_column(
        pg_enum(WorkspaceRole, "workspace_role_enum"),
        nullable=False,
        default=WorkspaceRole.MEMBER,
        server_default=WorkspaceRole.MEMBER.value,
    )
    status: Mapped[UserStatus] = mapped_column(
        pg_enum(UserStatus, "user_status_enum"),
        nullable=False,
        default=UserStatus.ACTIVE,
        server_default=UserStatus.ACTIVE.value,
    )
    timezone: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="UTC",
        server_default="UTC",
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
        back_populates="users",
    )

    __table_args__ = (
        UniqueConstraint("id", "workspace_id", name="uq_users_id_workspace"),
        Index(
            "uq_users_workspace_email",
            "workspace_id",
            "email",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "uq_users_workspace_handle",
            "workspace_id",
            "handle",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index("idx_users_workspace_status", "workspace_id", "status"),
        Index(
            "idx_users_auth_lookup",
            "workspace_id",
            "auth_provider",
            "auth_provider_id",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index("idx_users_workspace_role", "workspace_id", "workspace_role"),
    )
