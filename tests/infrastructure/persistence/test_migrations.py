"""
The migrations on SQLite: applied from nothing, they yield the schema the
models declare, and undone, they leave only Alembic's own table, empty.
"""

from asyncio import (
    to_thread,
)
from pathlib import (
    Path,
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
from pytest import (
    MonkeyPatch,
)
from sqlalchemy import (
    Connection,
    inspect,
    text,
)

from tests.fitness._scan import (
    PACKAGE_ROOT,
)
from tests.infrastructure.persistence.conftest import (
    downgrade_to_base,
)
from wordwinnow.infrastructure.persistence import (
    migrations,
)
from wordwinnow.infrastructure.persistence.engine import (
    build_engine,
)
from wordwinnow.infrastructure.persistence.migrations import (
    MIGRATION_SCRIPTS,
    upgrade_to_head,
)
from wordwinnow.infrastructure.persistence.models import (
    Base,
)

_STAMPED: Final = "SELECT version_num FROM alembic_version"


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


@final
class TestTheMigrationsShipInThePackage:
    def test_their_directory_is_inside_the_package_and_holds_the_environment_the_template_and_a_revision(
        self,
    ) -> None:
        assert MIGRATION_SCRIPTS.is_relative_to(
            other=PACKAGE_ROOT,
        )

        assert (MIGRATION_SCRIPTS / "env.py").is_file()

        assert (MIGRATION_SCRIPTS / "script.py.mako").is_file()

        assert (
            tuple(
                MIGRATION_SCRIPTS.glob(
                    pattern="versions/*.py",
                ),
            )
            != ()
        )


@final
class TestUpgradeToHead:
    def test_it_hands_alembic_no_configuration_file_and_the_directory_inside_the_package(
        self,
        *,
        monkeypatch: MonkeyPatch,
    ) -> None:
        received: list[Config] = []

        def upgrade(
            *,
            config: Config,
            revision: str,
        ) -> None:
            received.append(
                config,
            )

        monkeypatch.setattr(
            target=migrations,
            name="upgrade",
            value=upgrade,
        )

        upgrade_to_head(
            database_url="sqlite+aiosqlite:///foo.db",
        )

        assert (
            len(
                received,
            )
            == 1
        )

        config = received[0]

        assert config.config_file_name is None

        assert config.get_main_option(
            name="script_location",
        ) == str(
            object=MIGRATION_SCRIPTS,
        )

        assert MIGRATION_SCRIPTS.is_relative_to(
            other=PACKAGE_ROOT,
        )

        assert (
            config.get_main_option(
                name="sqlalchemy.url",
            )
            == "sqlite+aiosqlite:///foo.db"
        )

    async def test_from_a_directory_with_no_alembic_ini_it_brings_a_fresh_file_to_head(
        self,
        *,
        monkeypatch: MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        workspace = tmp_path / "workspace"

        workspace.mkdir()

        monkeypatch.chdir(
            path=workspace,
        )

        path = tmp_path / "wordwinnow.db"

        database_url = f"sqlite+aiosqlite:///{path}"

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

        finally:
            await engine.dispose()

        assert names == (
            "alembic_version",
            "analyses",
            "vocabulary_items",
        )

        assert not (workspace / "alembic.ini").exists()

        assert (
            tuple(
                workspace.rglob(
                    pattern="*",
                ),
            )
            == ()
        )

    async def test_from_nothing_it_creates_both_tables_as_the_models_declare_them(
        self,
        *,
        tmp_path: Path,
    ) -> None:
        path = tmp_path / "wordwinnow.db"

        database_url = f"sqlite+aiosqlite:///{path}"

        # NOTE:
        # The upgrade runs its own event loop, so it runs in a worker thread rather than on the test's loop; running
        # it twice shows that a database already at head is left alone.
        await to_thread(
            upgrade_to_head,
            database_url=database_url,
        )

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

        finally:
            await engine.dispose()

        assert names == (
            "alembic_version",
            "analyses",
            "vocabulary_items",
        )

        assert differences == ()


@final
class TestDowngradeToBase:
    async def test_undone_the_migrations_leave_only_alembics_own_table_with_no_revision_in_it(
        self,
        *,
        tmp_path: Path,
    ) -> None:
        path = tmp_path / "wordwinnow.db"

        database_url = f"sqlite+aiosqlite:///{path}"

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
