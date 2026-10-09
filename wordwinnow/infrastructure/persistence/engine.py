"""
The engine the analysis store runs on, built once per process from the
database URL.

Nothing here runs at import time: a composition root asks for an engine when
it builds a profile.
"""

from pathlib import (
    Path,
)
from typing import (
    Final,
)

from sqlalchemy.engine import (
    make_url,
)
from sqlalchemy.engine.interfaces import (
    DBAPIConnection,
)
from sqlalchemy.event import (
    listen,
)
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    create_async_engine,
)
from sqlalchemy.pool import (
    ConnectionPoolEntry,
)

_SQLITE: Final = "sqlite"

# NOTE:
# The database name SQLite reads as "no file at all"; an empty name means the same thing.
_IN_MEMORY: Final = ":memory:"


def is_sqlite(
    *,
    database_url: str,
) -> bool:
    """
    Whether the URL names a SQLite store, which is the local mode's.
    """

    return (
        make_url(
            name_or_url=database_url,
        ).get_backend_name()
        == _SQLITE
    )


def build_engine(
    *,
    database_url: str,
) -> AsyncEngine:
    """
    The engine for a database URL, ready for either engine the store supports.

    A SQLite file gets its parent directory created and its foreign keys
    enforced, which SQLite does not do on its own.
    """

    url = make_url(
        name_or_url=database_url,
    )

    # WARN:
    # An error's message names the statement that failed and, by default, the values it carried, the text of an
    # analysis among them; a worker logs such a message when the store fails under it, so the values stay out.
    engine = create_async_engine(
        url=url,
        hide_parameters=True,
    )

    if url.get_backend_name() != _SQLITE:
        return engine

    database = url.database

    if database and database != _IN_MEMORY:
        Path(
            database,
        ).parent.mkdir(
            parents=True,
            exist_ok=True,
        )

    # WARN:
    # The listener takes its two arguments positionally, and it has to be: SQLAlchemy calls every `connect` listener
    # as `fn(dbapi_connection, connection_record)`, so the signature is the event's rather than this module's.
    def enable_foreign_keys(
        dbapi_connection: DBAPIConnection,
        connection_record: ConnectionPoolEntry,
        /,
    ) -> None:
        cursor = dbapi_connection.cursor()

        cursor.execute(
            operation="PRAGMA foreign_keys=ON",
        )

        cursor.close()

    listen(
        target=engine.sync_engine,
        identifier="connect",
        fn=enable_foreign_keys,
    )

    return engine
