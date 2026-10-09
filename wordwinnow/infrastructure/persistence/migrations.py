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
# `alembic.ini` sits at the project root, three directories above this module, and the migration scripts are located
# relative to it.
ALEMBIC_INI: Final = (
    Path(
        __file__,
    ).parents[3]
    / "alembic.ini"
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

    config = Config(
        file_=ALEMBIC_INI,
    )

    # WARN:
    # A main option is read back through `ConfigParser` interpolation, where a bare `%` is the start of a reference,
    # so one that appears in a URL, such as a percent-encoded password, has to be doubled first.
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
