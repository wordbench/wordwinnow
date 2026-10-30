"""
The initial schema: the analyses and their vocabulary items.

Revision ID: 256cfbc06a10
Revises:
"""

from typing import (
    Final,
)

from alembic import (
    op,
)
from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKeyConstraint,
    Integer,
    PrimaryKeyConstraint,
    String,
    Text,
)

revision: Final = "256cfbc06a10"

down_revision: Final = None

branch_labels: Final = None

depends_on: Final = None


# WARN:
# `op.create_table` takes the table name positionally followed by its columns as `*args`, and `PrimaryKeyConstraint`
# takes its columns as `*args`, so those are written positionally; everything else here takes keywords.
def upgrade() -> None:
    op.create_table(
        "analyses",
        Column(
            name="id",
            type_=String(
                length=36,
            ),
            nullable=False,
        ),
        Column(
            name="title",
            type_=Text(),
            nullable=False,
        ),
        Column(
            name="text",
            type_=Text(),
            nullable=False,
        ),
        Column(
            name="origin",
            type_=String(),
            nullable=False,
        ),
        Column(
            name="reference",
            type_=Text(),
            nullable=False,
        ),
        Column(
            name="learner_level",
            type_=String(),
            nullable=False,
        ),
        Column(
            name="target_level",
            type_=String(),
            nullable=False,
        ),
        Column(
            name="known_lemmas",
            type_=JSON(),
            nullable=False,
        ),
        Column(
            name="include_dictionary",
            type_=Boolean(),
            nullable=False,
        ),
        Column(
            name="status",
            type_=String(),
            nullable=False,
        ),
        Column(
            name="requested_at",
            type_=DateTime(
                timezone=True,
            ),
            nullable=False,
        ),
        Column(
            name="expires_at",
            type_=DateTime(
                timezone=True,
            ),
            nullable=True,
        ),
        Column(
            name="attribution",
            type_=Text(),
            nullable=True,
        ),
        Column(
            name="source_references",
            type_=JSON(),
            nullable=False,
        ),
        Column(
            name="started_at",
            type_=DateTime(
                timezone=True,
            ),
            nullable=True,
        ),
        Column(
            name="finished_at",
            type_=DateTime(
                timezone=True,
            ),
            nullable=True,
        ),
        Column(
            name="failure_reason",
            type_=Text(),
            nullable=True,
        ),
        Column(
            name="stage_timings",
            type_=JSON(),
            nullable=False,
        ),
        Column(
            name="progress",
            type_=JSON(
                none_as_null=True,
            ),
            nullable=True,
        ),
        PrimaryKeyConstraint(
            "id",
            name="pk_analyses",
        ),
    )

    op.create_index(
        index_name="ix_analyses_expires_at",
        table_name="analyses",
        columns=[
            "expires_at",
        ],
        unique=False,
    )

    op.create_index(
        index_name="ix_analyses_requested_at",
        table_name="analyses",
        columns=[
            "requested_at",
        ],
        unique=False,
    )

    op.create_index(
        index_name="ix_analyses_status",
        table_name="analyses",
        columns=[
            "status",
        ],
        unique=False,
    )

    op.create_table(
        "vocabulary_items",
        Column(
            name="id",
            type_=Integer(),
            nullable=False,
        ),
        Column(
            name="analysis_id",
            type_=String(
                length=36,
            ),
            nullable=False,
        ),
        Column(
            name="position",
            type_=Integer(),
            nullable=False,
        ),
        Column(
            name="lemma",
            type_=String(),
            nullable=False,
        ),
        Column(
            name="part_of_speech",
            type_=String(),
            nullable=False,
        ),
        Column(
            name="occurrence_count",
            type_=Integer(),
            nullable=False,
        ),
        Column(
            name="example_sentence",
            type_=Text(),
            nullable=False,
        ),
        Column(
            name="zipf_frequency",
            type_=Float(),
            nullable=False,
        ),
        Column(
            name="level",
            type_=String(),
            nullable=False,
        ),
        Column(
            name="level_source",
            type_=String(),
            nullable=False,
        ),
        Column(
            name="tier",
            type_=String(),
            nullable=False,
        ),
        Column(
            name="dictionary",
            type_=JSON(
                none_as_null=True,
            ),
            nullable=True,
        ),
        Column(
            name="senses",
            type_=JSON(),
            nullable=False,
        ),
        ForeignKeyConstraint(
            columns=[
                "analysis_id",
            ],
            refcolumns=[
                "analyses.id",
            ],
            name="fk_vocabulary_items_analysis_id_analyses",
            ondelete="CASCADE",
        ),
        PrimaryKeyConstraint(
            "id",
            name="pk_vocabulary_items",
        ),
    )

    op.create_index(
        index_name="ix_vocabulary_items_analysis_id",
        table_name="vocabulary_items",
        columns=[
            "analysis_id",
        ],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        index_name="ix_vocabulary_items_analysis_id",
        table_name="vocabulary_items",
    )

    op.drop_table(
        table_name="vocabulary_items",
    )

    op.drop_index(
        index_name="ix_analyses_status",
        table_name="analyses",
    )

    op.drop_index(
        index_name="ix_analyses_requested_at",
        table_name="analyses",
    )

    op.drop_index(
        index_name="ix_analyses_expires_at",
        table_name="analyses",
    )

    op.drop_table(
        table_name="analyses",
    )
