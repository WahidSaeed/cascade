"""Alembic environment: one migration chain for SQLite and PostgreSQL."""
from alembic import context

from app.config import settings
from app.db import make_engine
from app.models import Base

target_metadata = Base.metadata


def run_migrations_online() -> None:
    url = context.config.get_main_option("sqlalchemy.url") or settings.resolved_database_url()
    connectable = make_engine(url)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata,
                          render_as_batch=connection.dialect.name == "sqlite")
        with context.begin_transaction():
            context.run_migrations()
    connectable.dispose()


run_migrations_online()
