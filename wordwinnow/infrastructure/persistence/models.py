"""
The two tables of the analysis store, declared once for SQLite and PostgreSQL.

An analysis is one row of `analyses`, and each of its study items is one row
of `vocabulary_items` keyed by its position, so an analysis is read back in
the order it was winnowed.

Enumerations are stored as their values, timestamps as UTC, and an item's
nested detail, which no query filters on, as JSON.
"""

from datetime import (
    datetime,
)
from typing import (
    Any,
    ClassVar,
    final,
)

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    MetaData,
    String,
    Text,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
)
from sqlalchemy.types import (
    TypeEngine,
)


class Base(
    DeclarativeBase,
):
    """
    The declarative base every table of the store shares.
    """

    # NOTE:
    # SQLAlchemy's documented convention, key for key: without it PostgreSQL names each constraint itself and SQLite
    # leaves them unnamed, so a migration that refers to a constraint or an index by name would match one backend and
    # not the other; the `ck` key needs every check constraint to carry a name, and the store has none yet.
    metadata = MetaData(
        naming_convention={
            "ix": "ix_%(column_0_label)s",
            "uq": "uq_%(table_name)s_%(column_0_name)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
            "pk": "pk_%(table_name)s",
        },
    )

    # NOTE:
    # A bare `datetime` annotation would map to a zone-naive column, and a bare `float` to DOUBLE where the migrations
    # created FLOAT; every timestamp here keeps its zone, and the one float column stays what it was.
    type_annotation_map: ClassVar[dict[type, TypeEngine[Any]]] = {
        datetime: DateTime(
            timezone=True,
        ),
        float: Float(),
    }


@final
class AnalysisRow(
    Base,
):
    """
    One analysis: its document, its learner profile, its options, and its
    lifecycle.

    `expires_at` is set for a document whose text may be kept only for a
    while, and the purge deletes the row once it has passed.

    `progress` holds what a worker last reported while processing it, and
    saving the analysis clears it.
    """

    __tablename__ = "analyses"

    id: Mapped[str] = mapped_column(
        primary_key=True,
        type_=String(
            length=36,
        ),
    )

    title: Mapped[str] = mapped_column(
        type_=Text(),
    )

    text: Mapped[str] = mapped_column(
        type_=Text(),
    )

    origin: Mapped[str]

    reference: Mapped[str] = mapped_column(
        type_=Text(),
    )

    learner_level: Mapped[str]

    target_level: Mapped[str]

    known_lemmas: Mapped[list[str]] = mapped_column(
        type_=JSON(),
    )

    include_dictionary: Mapped[bool]

    status: Mapped[str] = mapped_column(
        index=True,
    )

    requested_at: Mapped[datetime] = mapped_column(
        index=True,
    )

    expires_at: Mapped[datetime | None] = mapped_column(
        index=True,
    )

    attribution: Mapped[str | None] = mapped_column(
        type_=Text(),
    )

    source_references: Mapped[list[dict[str, Any]]] = mapped_column(
        type_=JSON(),
    )

    started_at: Mapped[datetime | None]

    finished_at: Mapped[datetime | None]

    failure_reason: Mapped[str | None] = mapped_column(
        type_=Text(),
    )

    stage_timings: Mapped[list[dict[str, Any]]] = mapped_column(
        type_=JSON(),
    )

    # NOTE:
    # Without `none_as_null`, the save that forgets a worker's report would write JSON's null rather than SQL's NULL,
    # and a row whose progress was cleared would differ from one that never had any.
    progress: Mapped[dict[str, Any] | None] = mapped_column(
        type_=JSON(
            none_as_null=True,
        ),
    )


@final
class VocabularyItemRow(
    Base,
):
    """
    One study item of one analysis, at its position in the winnowed order.
    """

    __tablename__ = "vocabulary_items"

    id: Mapped[int] = mapped_column(
        primary_key=True,
    )

    analysis_id: Mapped[str] = mapped_column(
        ForeignKey(
            column="analyses.id",
            ondelete="CASCADE",
        ),
        type_=String(
            length=36,
        ),
        index=True,
    )

    position: Mapped[int]

    lemma: Mapped[str]

    part_of_speech: Mapped[str]

    occurrence_count: Mapped[int]

    example_sentence: Mapped[str] = mapped_column(
        type_=Text(),
    )

    zipf_frequency: Mapped[float]

    level: Mapped[str]

    level_source: Mapped[str]

    tier: Mapped[str]

    dictionary: Mapped[dict[str, Any] | None] = mapped_column(
        type_=JSON(
            none_as_null=True,
        ),
    )

    senses: Mapped[list[dict[str, Any]]] = mapped_column(
        type_=JSON(),
    )
