"""Alembic entry point; schema changes only happen through explicit migration commands."""

from alembic import context
from sqlalchemy import Connection

from stockentry_guard.config import Settings
from stockentry_guard.infrastructure.persistence.database import create_database_engine
from stockentry_guard.infrastructure.persistence.models import Base

config = context.config
target_metadata = Base.metadata


def run_with_connection(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations() -> None:
    url = config.get_main_option("sqlalchemy.url") or Settings().database_url
    if context.is_offline_mode():
        context.configure(
            url=url,
            target_metadata=target_metadata,
            literal_binds=True,
            dialect_opts={"paramstyle": "named"},
        )
        with context.begin_transaction():
            context.run_migrations()
    elif config.attributes.get("connection") is not None:
        run_with_connection(config.attributes["connection"])
    else:
        engine = create_database_engine(url)
        try:
            with engine.connect() as connection:
                run_with_connection(connection)
        finally:
            engine.dispose()


run_migrations()
