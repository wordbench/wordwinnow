"""
The migrations on PostgreSQL: the server `make up` starts, or the one
`WORDWINNOW_TEST_POSTGRES_URL` names, holding a database created for each test
and dropped after it.

Applied to a database that has never seen them, the migrations yield the
schema the models declare, every timestamp keeping its zone and an analysis's
items going with it, and the store keeps an analysis there as it went in;
undone, they leave only Alembic's own table, empty.
"""

from asyncio import (
    to_thread,
)
from collections.abc import (
    AsyncIterator,
)
from contextlib import (
    asynccontextmanager,
)
from os import (
    environ,
)
from typing import (
    Any,
    Final,
    final,
)

from alembic.autogenerate import (
    compare_metadata,
)
from alembic.config import (
    Config,
)
from alembic.runtime.migration import (
    MigrationContext,
)
from alembic.script import (
    ScriptDirectory,
)
from pytest import (
    mark,
    skip,
)
from sqlalchemy import (
    Connection,
    inspect,
    text,
)
from sqlalchemy.engine import (
    make_url,
)
from sqlalchemy.ext.asyncio import (
    create_async_engine,
)

from tests.infrastructure.persistence.conftest import (
    assert_same_state,
    completed_analysis,
    downgrade_to_base,
    load_analysis,
    store_analysis,
)
from wordwinnow.infrastructure.persistence.engine import (
    build_engine,
)
from wordwinnow.infrastructure.persistence.migrations import (
    ALEMBIC_INI,
    upgrade_to_head,
)
from wordwinnow.infrastructure.persistence.models import (
    Base,
)
from wordwinnow.infrastructure.persistence.uow import (
    new_unit_of_work_factory,
)

# NOTE:
# The server `make up` starts, or another one named in the environment; each test creates its database on the
# maintenance connection and skips when no server answers.
_POSTGRES_URL: Final = environ.get(
    key="WORDWINNOW_TEST_POSTGRES_URL",
    default="postgresql+asyncpg://wordwinnow:wordwinnow@127.0.0.1:5432/wordwinnow",
)

_MIGRATION_DATABASE: Final = "wordwinnow_migration_test"

_STAMPED: Final = "SELECT version_num FROM alembic_version"

_TIMESTAMP_TYPES: Final = (
    "SELECT data_type FROM information_schema.columns WHERE table_name = 'analyses' "
    "AND column_name IN ('expires_at', 'finished_at', 'requested_at', 'started_at')"
)


@asynccontextmanager
async def _fresh_database() -> AsyncIterator[str]:
    url = make_url(
        name_or_url=_POSTGRES_URL,
    )

    maintenance = create_async_engine(
        url=url,
        isolation_level="AUTOCOMMIT",
    )

    drop = text(
        text=f"DROP DATABASE IF EXISTS {_MIGRATION_DATABASE} WITH (FORCE)",
    )

    # NOTE:
    # Dropped before as well as after, so a run that was interrupted cannot hand the next one a database that has
    # already seen the migrations.
    try:
        async with maintenance.connect() as connection:
            await connection.execute(
                statement=drop,
            )

            await connection.execute(
                statement=text(
                    text=f"CREATE DATABASE {_MIGRATION_DATABASE}",
                ),
            )

    except OSError as exception:
        await maintenance.dispose()

        skip(
            reason=f"no PostgreSQL server answers on {url.host}:{url.port}; run `make up` ({exception})",
        )

    try:
        # WARN:
        # `str()` on a URL hides the password, so the engine would authenticate with three asterisks.
        yield url.set(
            database=_MIGRATION_DATABASE,
        ).render_as_string(
            hide_password=False,
        )

    finally:
        async with maintenance.connect() as connection:
            await connection.execute(
                statement=drop,
            )

        await maintenance.dispose()


def _table_names(
    connection: Connection,
    /,
) -> tuple[str, ...]:
    return tuple(
        sorted(
            inspect(
                subject=connection,
            ).get_table_names(),
        ),
    )


def _differences_from_the_models(
    connection: Connection,
    /,
) -> tuple[Any, ...]:
    return tuple(
        compare_metadata(
            context=MigrationContext.configure(
                connection=connection,
            ),
            metadata=Base.metadata,
        ),
    )


def _what_deleting_an_analysis_does_to_its_items(
    connection: Connection,
    /,
) -> str | None:
    (foreign_key,) = inspect(
        subject=connection,
    ).get_foreign_keys(
        table_name="vocabulary_items",
    )

    return foreign_key["options"].get(
        "ondelete",
    )


@final
class TestMigrationsOnPostgreSql:
    @mark.integration
    async def test_on_a_fresh_database_they_build_the_models_schema_and_the_store_keeps_an_analysis_there(
        self,
    ) -> None:
        async with _fresh_database() as database_url:
            # NOTE:
            # The upgrade runs its own event loop, so it runs in a worker thread rather than on the test's loop.
            await to_thread(
                upgrade_to_head,
                database_url=database_url,
            )

            engine = build_engine(
                database_url=database_url,
            )

            try:
                async with engine.connect() as connection:
                    names = await connection.run_sync(
                        fn=_table_names,
                    )

                    differences = await connection.run_sync(
                        fn=_differences_from_the_models,
                    )

                    on_delete = await connection.run_sync(
                        fn=_what_deleting_an_analysis_does_to_its_items,
                    )

                    timestamp_types = tuple(
                        await connection.scalars(
                            statement=text(
                                text=_TIMESTAMP_TYPES,
                            ),
                        ),
                    )

                    stamped = tuple(
                        await connection.scalars(
                            statement=text(
                                text=_STAMPED,
                            ),
                        ),
                    )

                new_unit_of_work = new_unit_of_work_factory(
                    engine=engine,
                )

                analysis = completed_analysis()

                await store_analysis(
                    analysis=analysis,
                    new_unit_of_work=new_unit_of_work,
                )

                loaded = await load_analysis(
                    analysis_id=analysis.id,
                    new_unit_of_work=new_unit_of_work,
                )

            finally:
                await engine.dispose()

        head = ScriptDirectory.from_config(
            config=Config(
                file_=ALEMBIC_INI,
            ),
        ).get_current_head()

        assert names == (
            "alembic_version",
            "analyses",
            "vocabulary_items",
        )

        assert differences == ()

        assert on_delete == "CASCADE"

        assert timestamp_types == ("timestamp with time zone",) * 4

        assert stamped == (head,)

        assert_same_state(
            original=analysis,
            loaded=loaded,
        )

    @mark.integration
    async def test_undone_they_leave_only_alembics_own_table_with_no_revision_in_it(
        self,
    ) -> None:
        async with _fresh_database() as database_url:
            await to_thread(
                upgrade_to_head,
                database_url=database_url,
            )

            await to_thread(
                downgrade_to_base,
                database_url=database_url,
            )

            engine = build_engine(
                database_url=database_url,
            )

            try:
                async with engine.connect() as connection:
                    names = await connection.run_sync(
                        fn=_table_names,
                    )

                    stamped = tuple(
                        await connection.scalars(
                            statement=text(
                                text=_STAMPED,
                            ),
                        ),
                    )

            finally:
                await engine.dispose()

        assert names == ("alembic_version",)

        assert stamped == ()
