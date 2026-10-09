## NOTE:
## The template every generated migration starts from.
##
## Autogenerate renders types as `sa.Column`, `sa.String`, and so on, which is why the alias import below stays:
## `alembic/versions` is Alembic's to shape, and the project excludes it from its own formatting and checks.
##
## The header leaves out Alembic's creation date: the revision chain already orders the migrations, and a date would
## record only when each was written.
"""
${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
"""

from typing import (
    Final,
)

from alembic import (
    op,
)
import sqlalchemy as sa
${imports if imports else ""}

revision: Final = ${repr(up_revision)}

down_revision: Final = ${repr(down_revision)}

branch_labels: Final = ${repr(branch_labels)}

depends_on: Final = ${repr(depends_on)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
