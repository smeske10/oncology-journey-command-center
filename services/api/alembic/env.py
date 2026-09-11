from __future__ import with_statement

import sys
from pathlib import Path

from sqlalchemy import engine_from_config, pool, text

from alembic import context

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings
from app.db.models import Base
from app.db.targets import DatabaseTargetMismatch, validate_database_target_pair

config = context.config
migration_database_url = settings.require_migration_database_url()
_, migration_target = validate_database_target_pair(
    application_url=settings.database_url,
    migration_url=migration_database_url,
)
config.set_main_option("sqlalchemy.url", migration_database_url.replace("%", "%%"))
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        current_user, current_database = connection.execute(
            text("SELECT current_user, current_database()")
        ).one()
        if current_user != migration_target.username:
            raise DatabaseTargetMismatch(
                "connected database user does not match MIGRATION_DATABASE_URL username"
            )
        if current_database != migration_target.database:
            raise DatabaseTargetMismatch(
                "connected database name does not match MIGRATION_DATABASE_URL database name"
            )
        connection.rollback()
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
