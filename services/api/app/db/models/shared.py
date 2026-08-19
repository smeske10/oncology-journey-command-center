from __future__ import annotations

from enum import Enum as PythonEnum

from sqlalchemy import CheckConstraint, Enum, UniqueConstraint

from app.db.base import Base

__all__ = ["Base", "state_constraint", "state_enum", "tenant_identity_constraint"]


def state_enum(enum_class: type[PythonEnum], name: str) -> Enum:
    return Enum(
        enum_class,
        name=name,
        values_callable=lambda values: [member.value for member in values],
        native_enum=True,
        create_constraint=False,
    )


def state_constraint(column: str, enum_class: type[PythonEnum]) -> CheckConstraint:
    allowed_values = ", ".join(f"'{member.value}'" for member in enum_class)
    return CheckConstraint(f"{column} IN ({allowed_values})", name=f"{column}_state")


def tenant_identity_constraint(table_name: str) -> UniqueConstraint:
    return UniqueConstraint("organization_id", "id", name=f"uq_{table_name}_organization_id_id")
