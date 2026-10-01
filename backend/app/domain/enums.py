"""Domain enumerations for the team-first Workspace model.

These enumerations define contextual relationship roles and lifecycle states
across the 8 core entities. They are pure Python enums without dependencies
on persistence frameworks.
"""

from enum import Enum


class WorkspaceRole(str, Enum):
    """Contextual role within a Workspace.

    Identifies workspace-level administrative tier.
    """

    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"
    GUEST = "guest"


class UserStatus(str, Enum):
    """Account operational state of a participant within a Workspace."""

    ACTIVE = "active"
    INVITED = "invited"
    SUSPENDED = "suspended"
    DEACTIVATED = "deactivated"


class TeamRole(str, Enum):
    """Contextual role within a collaborative Team."""

    LEAD = "lead"
    MEMBER = "member"
    CONTRIBUTOR = "contributor"


class ProjectStatus(str, Enum):
    """Operational lifecycle state of a Project."""

    PLANNED = "planned"
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    ARCHIVED = "archived"


class ProjectVisibility(str, Enum):
    """Visibility and read boundary of a Project within its Workspace."""

    WORKSPACE = "workspace"
    PRIVATE = "private"


class ProjectRole(str, Enum):
    """Contextual role and contribution tier within a Project."""

    LEAD = "lead"
    MAINTAINER = "maintainer"
    CONTRIBUTOR = "contributor"
    VIEWER = "viewer"


class InviteStatus(str, Enum):
    """Lifecycle status of a Project invitation token."""

    PENDING = "pending"
    ACCEPTED = "accepted"
    DECLINED = "declined"
    EXPIRED = "expired"
    REVOKED = "revoked"


class TaskStatus(str, Enum):
    """Workflow state of an actionable Task."""

    BACKLOG = "backlog"
    TODO = "todo"
    IN_PROGRESS = "in_progress"
    IN_REVIEW = "in_review"
    DONE = "done"
    CANCELLED = "cancelled"


class TaskPriority(str, Enum):
    """Urgency / priority ranking of a Task."""

    URGENT = "urgent"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    NONE = "none"
