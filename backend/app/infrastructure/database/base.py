"""SQLAlchemy declarative base with PostgreSQL naming conventions."""

from enum import Enum as PyEnum
from typing import Any, TypeVar
from sqlalchemy import Enum, MetaData
from sqlalchemy.orm import DeclarativeBase

# Standard naming conventions for indexes and constraints
POSTGRES_NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy ORM models."""

    metadata = MetaData(naming_convention=POSTGRES_NAMING_CONVENTION)


E = TypeVar("E", bound=PyEnum)


def pg_enum(enum_cls: type[E], name: str) -> Any:
    """Create a PostgreSQL native ENUM type mapped to Python Enum string values."""
    return Enum(
        enum_cls,
        name=name,
        native_enum=True,
        values_callable=lambda x: [e.value for e in x],
    )
