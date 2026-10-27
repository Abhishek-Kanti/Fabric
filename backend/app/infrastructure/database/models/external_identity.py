"""SQLAlchemy ORM model for ExternalIdentity entity."""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.domain.enums import ExternalIdentityStatus
from app.infrastructure.database.base import Base, pg_enum

if TYPE_CHECKING:
    from app.infrastructure.database.models.user import UserModel
    from app.infrastructure.database.models.workspace import WorkspaceModel


class ExternalIdentityModel(Base):
    """An external authenticated account linked to a canonical User within a Workspace."""

    __tablename__ = "external_identities"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="RESTRICT"),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    provider: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )
    provider_subject_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    provider_email: Mapped[str | None] = mapped_column(
        String(320),
        nullable=True,
    )
    provider_username: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    provider_display_name: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    provider_tenant_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    provider_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    is_primary: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )
    status: Mapped[ExternalIdentityStatus] = mapped_column(
        pg_enum(ExternalIdentityStatus, "external_identity_status_enum"),
        nullable=False,
        default=ExternalIdentityStatus.ACTIVE,
        server_default=ExternalIdentityStatus.ACTIVE.value,
    )
    verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    last_authenticated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
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
    )
    user: Mapped["UserModel"] = relationship(
        "UserModel",
        back_populates="external_identities",
        foreign_keys=[user_id, workspace_id],
        overlaps="workspace",
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id", "workspace_id"],
            ["users.id", "users.workspace_id"],
            name="fk_external_identities_user",
            ondelete="CASCADE",
        ),
        Index(
            "uq_external_identities_provider_subject",
            "workspace_id",
            "provider",
            "provider_subject_id",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "uq_external_identities_primary_user",
            "workspace_id",
            "user_id",
            unique=True,
            postgresql_where=text("is_primary = TRUE AND deleted_at IS NULL"),
        ),
        Index(
            "idx_external_identities_user_lookup",
            "workspace_id",
            "user_id",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "idx_external_identities_email_lookup",
            "workspace_id",
            "provider_email",
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )
