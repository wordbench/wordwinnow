"""
The store writes one row per fact in the table's column order, reads with the
statements it declares, and maps the rows back into the summary.
"""

from collections.abc import (
    Mapping,
    Sequence,
)
from datetime import (
    UTC,
    datetime,
)
from typing import (
    Any,
    Final,
    final,
)
from uuid import (
    UUID,
)

from clickhouse_connect.driver.exceptions import (
    OperationalError,
)
from pytest import (
    raises,
)

from wordwinnow.application.dto import (
    LemmaCount,
    LevelCount,
    VocabularyFact,
)
from wordwinnow.application.errors import (
    FactStoreUnavailableError,
)
from wordwinnow.domain.cefr import (
    CefrLevel,
    LevelSource,
)
from wordwinnow.domain.dictionary import (
    LookupOutcome,
)
from wordwinnow.domain.document import (
    DocumentOrigin,
)
from wordwinnow.domain.identifiers import (
    AnalysisId,
)
from wordwinnow.domain.language import (
    PartOfSpeech,
)
from wordwinnow.domain.study import (
    StudyTier,
)
from wordwinnow.infrastructure.analytics.clickhouse import (
    COLUMNS,
    COUNTS_SQL,
    CREATE_DATABASE_SQL,
    CREATE_TABLE_SQL,
    FOCUS_SQL,
    LEVELS_SQL,
    TABLE,
    ClickHouseVocabularyFacts,
    QueryRows,
)

# NOTE:
# A pure reference instant with no product meaning of its own.
_EPOCH: Final = datetime(
    year=1970,
    month=1,
    day=1,
    tzinfo=UTC,
)

_ANALYSIS_ID: Final = AnalysisId(
    value=UUID(
        int=1,
    ),
)

_FACT: Final = VocabularyFact(
    analysis_id=_ANALYSIS_ID,
    completed_at=_EPOCH,
    origin=DocumentOrigin.CUSTOM_TEXT,
    learner_level=CefrLevel.B1,
    lemma="groom",
    part_of_speech=PartOfSpeech.NOUN,
    level=CefrLevel.B1,
    level_source=LevelSource.REFERENCE_LIST,
    tier=StudyTier.FOCUS,
    occurrence_count=2,
    lookup_outcome=LookupOutcome.FOUND,
)


@final
class Rows:
    """
    A query result made of fixed rows.
    """

    def __init__(
        self,
        *,
        rows: Sequence[Sequence[Any]],
    ) -> None:
        self._rows: Final = rows

    @property
    def result_rows(
        self,
    ) -> Sequence[Sequence[Any]]:
        return self._rows


@final
class FakeClient:
    """
    Records every statement and answers queries from a table keyed by their
    text.
    """

    def __init__(
        self,
        *,
        answers: Mapping[str, Sequence[Sequence[Any]]],
    ) -> None:
        self._answers: Final = answers

        self.commands: Final[list[tuple[str, dict[str, Any] | None]]] = []

        self.queries: Final[list[tuple[str, dict[str, Any] | None]]] = []

        self.inserts: Final[list[tuple[str, Sequence[Sequence[Any]], Sequence[str], str]]] = []

    def command(
        self,
        *,
        cmd: str,
        parameters: dict[str, Any] | None = None,
    ) -> object:
        self.commands.append(
            (
                cmd,
                parameters,
            ),
        )

        return None

    def query(
        self,
        *,
        query: str,
        parameters: dict[str, Any] | None = None,
    ) -> QueryRows:
        self.queries.append(
            (
                query,
                parameters,
            ),
        )

        return Rows(
            rows=self._answers[query],
        )

    def insert(
        self,
        *,
        table: str,
        data: Sequence[Sequence[Any]],
        column_names: Sequence[str],
        database: str,
    ) -> object:
        self.inserts.append(
            (
                table,
                data,
                column_names,
                database,
            ),
        )

        return None


def _refuse() -> FakeClient:
    raise OperationalError(
        "connection refused",
    )


@final
class TestClickHouseVocabularyFacts:
    async def test_the_schema_is_created_in_the_named_database(
        self,
    ) -> None:
        client = FakeClient(
            answers={},
        )

        store = ClickHouseVocabularyFacts(
            database="foo",
            connect=lambda: client,
        )

        await store.ensure_schema()

        assert client.commands == [
            (
                CREATE_DATABASE_SQL,
                {
                    "database": "foo",
                },
            ),
            (
                CREATE_TABLE_SQL,
                {
                    "database": "foo",
                },
            ),
        ]

        assert "ReplacingMergeTree" in CREATE_TABLE_SQL

    async def test_facts_are_inserted_one_row_each_in_column_order(
        self,
    ) -> None:
        client = FakeClient(
            answers={},
        )

        store = ClickHouseVocabularyFacts(
            database="foo",
            connect=lambda: client,
        )

        await store.record(
            facts=(
                _FACT,
                VocabularyFact(
                    analysis_id=_ANALYSIS_ID,
                    completed_at=_EPOCH,
                    origin=DocumentOrigin.CUSTOM_TEXT,
                    learner_level=CefrLevel.B1,
                    lemma="drunken",
                    part_of_speech=PartOfSpeech.ADJECTIVE,
                    level=CefrLevel.A2,
                    level_source=LevelSource.MODEL,
                    tier=StudyTier.REVIEW,
                    occurrence_count=1,
                    lookup_outcome=None,
                ),
            ),
        )

        (
            (
                table,
                rows,
                column_names,
                database,
            ),
        ) = client.inserts

        assert table == TABLE

        assert column_names == COLUMNS

        assert database == "foo"

        assert tuple(
            rows,
        ) == (
            (
                _ANALYSIS_ID.value,
                _EPOCH,
                "custom_text",
                "B1",
                "groom",
                "noun",
                "B1",
                "reference_list",
                "focus",
                2,
                "found",
            ),
            (
                _ANALYSIS_ID.value,
                _EPOCH,
                "custom_text",
                "B1",
                "drunken",
                "adjective",
                "A2",
                "model",
                "review",
                1,
                None,
            ),
        )

    async def test_recording_nothing_touches_nothing(
        self,
    ) -> None:
        client = FakeClient(
            answers={},
        )

        store = ClickHouseVocabularyFacts(
            database="foo",
            connect=lambda: client,
        )

        await store.record(
            facts=(),
        )

        assert client.inserts == []

    async def test_the_summary_lists_every_level_and_the_focus_lemmas_in_order(
        self,
    ) -> None:
        client = FakeClient(
            answers={
                COUNTS_SQL: [
                    (
                        3,
                        40,
                    ),
                ],
                LEVELS_SQL: [
                    (
                        "B2",
                        30,
                    ),
                    (
                        "A1",
                        10,
                    ),
                ],
                FOCUS_SQL: [
                    (
                        "groom",
                        "noun",
                        3,
                        9,
                    ),
                    (
                        "witness",
                        "verb",
                        1,
                        2,
                    ),
                ],
            },
        )

        store = ClickHouseVocabularyFacts(
            database="foo",
            connect=lambda: client,
        )

        summary = await store.summarize(
            focus_limit=2,
        )

        assert summary.analysis_count == 3

        assert summary.fact_count == 40

        assert summary.levels == (
            LevelCount(
                level=CefrLevel.A1,
                count=10,
            ),
            LevelCount(
                level=CefrLevel.A2,
                count=0,
            ),
            LevelCount(
                level=CefrLevel.B1,
                count=0,
            ),
            LevelCount(
                level=CefrLevel.B2,
                count=30,
            ),
            LevelCount(
                level=CefrLevel.C1,
                count=0,
            ),
            LevelCount(
                level=CefrLevel.C2,
                count=0,
            ),
        )

        assert summary.focus_lemmas == (
            LemmaCount(
                lemma="groom",
                part_of_speech=PartOfSpeech.NOUN,
                analysis_count=3,
                occurrence_count=9,
            ),
            LemmaCount(
                lemma="witness",
                part_of_speech=PartOfSpeech.VERB,
                analysis_count=1,
                occurrence_count=2,
            ),
        )

        assert client.queries[-1] == (
            FOCUS_SQL,
            {
                "database": "foo",
                "tier": "focus",
                "focus_limit": 2,
            },
        )

        assert all(
            "FINAL" in query
            for (
                query,
                _parameters,
            ) in client.queries
        )

    async def test_a_refused_connection_is_the_store_error(
        self,
    ) -> None:
        store = ClickHouseVocabularyFacts(
            database="foo",
            connect=_refuse,
        )

        with raises(
            expected_exception=FactStoreUnavailableError,
            match="foo",
        ):
            await store.summarize(
                focus_limit=1,
            )
