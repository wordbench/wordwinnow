"""
The Alembic environment: where the migrations find the database and the
models.

The URL comes from `sqlalchemy.url` when a programmatic caller set it,
otherwise from `WORDWINNOW_DATABASE_URL`, otherwise from the project
settings; a caller that already holds a connection hands it over through
the configuration's `connection` attribute and the migrations run on it.
"""

from asyncio import (
    run,
)
from os import (
    environ,
)
from typing import (
    Final,
)

from sqlalchemy import (
    Connection,
)

from alembic import (
    context,
)
from wordwinnow.infrastructure.persistence.engine import (
    build_engine,
)
from wordwinnow.infrastructure.persistence.models import (
    Base,
)
from wordwinnow.infrastructure.settings import (
    load_settings,
)

config: Final = context.config

target_metadata: Final = Base.metadata

_SQLITE_PREFIX: Final = "sqlite"


def run_migrations_offline() -> None:
    """
    Emit the SQL of the migrations without touching a database.
    """

    database_url = _database_url()

    context.configure(
        url=database_url,
        dialect_opts={
            "paramstyle": "named",
        },
        render_as_batch=database_url.startswith(
            _SQLITE_PREFIX,
        ),
        target_metadata=target_metadata,
        literal_binds=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """
    Apply the migrations over a connection: the one a caller supplied, or
    a fresh one on an engine built for the resolved URL.
    """

    connection = config.attributes.get(
        "connection",
    )

    if connection is None:
        run(
            main=_run_async_migrations(
                _database_url(),
            ),
        )

    else:
        _run_migrations(
            connection,
        )


def _database_url() -> str:
    configured = config.get_main_option(
        name="sqlalchemy.url",
    )

    if configured:
        return configured

    from_environment = environ.get(
        key="WORDWINNOW_DATABASE_URL",
    )

    if from_environment:
        return from_environment

    return load_settings().database_url


def _run_migrations(
    connection: Connection,
    /,
) -> None:
    # NOTE:
    # SQLite cannot alter a table in place, so a migration on it is rendered as a batch that rebuilds the table.
    context.configure(
        connection=connection,
        render_as_batch=connection.dialect.name == _SQLITE_PREFIX,
        target_metadata=target_metadata,
    )

    with context.begin_transaction():
        context.run_migrations()


async def _run_async_migrations(
    database_url: str,
    /,
) -> None:
    engine = build_engine(
        database_url=database_url,
    )

    try:
        async with engine.connect() as connection:
            await connection.run_sync(
                fn=_run_migrations,
            )

    finally:
        await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()

else:
    run_migrations_online()
