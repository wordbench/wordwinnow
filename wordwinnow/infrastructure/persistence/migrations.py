"""
Applying the Alembic migrations from inside the process.
"""

from pathlib import (
    Path,
)
from typing import (
    Final,
)

from alembic.command import (
    upgrade,
)
from alembic.config import (
    Config,
)

# NOTE:
# The migration scripts ship inside the package, so an installed command applies them from wherever it is run; the
# `alembic.ini` beside `pyproject.toml` names the same directory for a developer's own `alembic` command.
MIGRATION_SCRIPTS: Final = (
    Path(
        __file__,
    ).parent
    / "migration_scripts"
)


def upgrade_to_head(
    *,
    database_url: str,
) -> None:
    """
    Apply every migration the database at `database_url` has not seen.

    The call is synchronous and runs its own event loop, so it belongs at a
    process entry point rather than inside a running one.
    """

    config = Config()

    # WARN:
    # A main option is read back through `ConfigParser` interpolation, where a bare `%` is the start of a reference,
    # so one that appears in a path or in a URL, such as a percent-encoded password, has to be doubled first.
    config.set_main_option(
        name="script_location",
        value=str(
            object=MIGRATION_SCRIPTS,
        ).replace(
            "%",
            "%%",
        ),
    )

    config.set_main_option(
        name="sqlalchemy.url",
        value=database_url.replace(
            "%",
            "%%",
        ),
    )

    upgrade(
        config=config,
        revision="head",
    )
