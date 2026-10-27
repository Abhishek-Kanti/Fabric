"""Domain authorization actions, permissions, and resource types."""

from enum import Enum
from typing import Union


class WorkspaceAction(str, Enum):
    """Actions that can be performed on a Workspace resource."""

    READ = "workspace:read"
    UPDATE = "workspace:update"
    MANAGE_MEMBERS = "workspace:manage_members"
    DELETE = "workspace:delete"
    INVITE = "workspace:invite"


class TeamAction(str, Enum):
    """Actions that can be performed on a Team resource."""

    READ = "team:read"
    CREATE = "team:create"
    UPDATE = "team:update"
    MANAGE_MEMBERS = "team:manage_members"
    DELETE = "team:delete"


class ProjectAction(str, Enum):
    """Actions that can be performed on a Project resource."""

    READ = "project:read"
    CREATE = "project:create"
    UPDATE = "project:update"
    MANAGE_MEMBERS = "project:manage_members"
    DELETE = "project:delete"
    ARCHIVE = "project:archive"
    INVITE = "project:invite"


class TaskAction(str, Enum):
    """Actions that can be performed on a Task resource."""

    READ = "task:read"
    CREATE = "task:create"
    UPDATE = "task:update"
    ASSIGN = "task:assign"
    DELETE = "task:delete"


class KnowledgeAction(str, Enum):
    """Actions that can be performed on Knowledge Graph and Vector search resources."""

    QUERY_GRAPH = "knowledge:query_graph"
    SEARCH_VECTOR = "knowledge:search_vector"
    READ_EVIDENCE = "knowledge:read_evidence"
    VIEW_PROVENANCE = "knowledge:view_provenance"
    EXPORT = "knowledge:export"


class ResourceType(str, Enum):
    """Classification of resources subject to authorization."""

    WORKSPACE = "workspace"
    TEAM = "team"
    PROJECT = "project"
    TASK = "task"
    KNOWLEDGE = "knowledge"
    EVIDENCE = "evidence"


# Type alias representing any valid authorization action
Action = Union[WorkspaceAction, TeamAction, ProjectAction, TaskAction, KnowledgeAction, str]
