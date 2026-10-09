"""
The ClickHouse adapter of the fact store and the fact query.
"""

from asyncio import (
    to_thread,
)
from collections.abc import (
    Callable,
    Sequence,
)
from logging import (
    getLogger,
)
from pathlib import (
    Path,
)
from typing import (
    Any,
    Final,
    Protocol,
    final,
)

from clickhouse_connect import (
    get_client,
)
from clickhouse_connect.driver.client import (
    Client,
)
from clickhouse_connect.driver.exceptions import (
    OperationalError,
)
from pydantic import (
    SecretStr,
)

from wordwinnow.application.dto import (
    LemmaCount,
    LevelCount,
    VocabularyFact,
    VocabularySummary,
)
from wordwinnow.application.errors import (
    FactStoreUnavailableError,
)
from wordwinnow.domain.cefr import (
    LEVELS_ASCENDING,
    CefrLevel,
)
from wordwinnow.domain.language import (
    PartOfSpeech,
)
from wordwinnow.domain.study import (
    StudyTier,
)

_logger: Final = getLogger(
    name="wordwinnow.analytics.clickhouse",
)

TABLE: Final = "vocabulary_facts"

# NOTE:
# The insert column order, which is the order `_row_of` writes a fact in.
COLUMNS: Final = (
    "analysis_id",
    "completed_at",
    "origin",
    "learner_level",
    "lemma",
    "part_of_speech",
    "level",
    "level_source",
    "tier",
    "occurrence_count",
    "lookup_outcome",
)

CREATE_DATABASE_SQL: Final = "CREATE DATABASE IF NOT EXISTS {database:Identifier}"

CREATE_TABLE_SQL: Final = (
    Path(
        __file__,
    )
    .with_name(
        name="schema.sql",
    )
    .read_text(
        encoding="utf-8",
    )
)

COUNTS_SQL: Final = """
SELECT uniqExact(analysis_id), count()
FROM {database:Identifier}.vocabulary_facts FINAL
"""

LEVELS_SQL: Final = """
SELECT level, count()
FROM {database:Identifier}.vocabulary_facts FINAL
GROUP BY level
"""

FOCUS_SQL: Final = """
SELECT lemma, part_of_speech, uniqExact(analysis_id) AS analysis_count, sum(occurrence_count) AS occurrence_count
FROM {database:Identifier}.vocabulary_facts FINAL
WHERE tier = {tier:String}
GROUP BY lemma, part_of_speech
ORDER BY analysis_count DESC, occurrence_count DESC, lemma, part_of_speech
LIMIT {focus_limit:UInt32}
"""


class QueryRows(
    Protocol,
):
    """
    The part of a `clickhouse_connect` query result the store reads.
    """

    @property
    def result_rows(
        self,
    ) -> Sequence[Sequence[Any]]:
        """
        The rows, one sequence of values each.
        """

        ...


# NOTE:
# The store never reads what `command` and `insert` answer, so the protocol promises only `object`: the real client's
# union of possible answers satisfies it, and so does a fake that answers `None`.
class ClickHouseClient(
    Protocol,
):
    """
    The part of a `clickhouse_connect` client the store uses.
    """

    def command(
        self,
        *,
        cmd: str,
        parameters: dict[str, Any] | None = None,
    ) -> object:
        """
        Run a statement that returns no rows.
        """

        ...

    def query(
        self,
        *,
        query: str,
        parameters: dict[str, Any] | None = None,
    ) -> QueryRows:
        """
        Run a statement and return its rows.
        """

        ...

    def insert(
        self,
        *,
        table: str,
        data: Sequence[Sequence[Any]],
        column_names: Sequence[str],
        database: str,
    ) -> object: ...


def build_client(
    *,
    host: str,
    port: int,
    user: str,
    password: SecretStr,
) -> Client:
    """
    Connect to a ClickHouse server, bound to no database.
    """

    # NOTE:
    # The store creates its own database and names it in every statement.
    return get_client(
        host=host,
        username=user,
        password=password.get_secret_value(),
        port=port,
    )


@final
class ClickHouseVocabularyFacts:
    """
    Keeps and summarizes the facts of completed analyses in ClickHouse.

    Satisfies `VocabularyFactQuery` structurally, and is the store the
    aggregation worker writes to.
    """

    def __init__(
        self,
        *,
        database: str,
        connect: Callable[[], ClickHouseClient],
    ) -> None:
        # NOTE:
        # The driver is synchronous, so every call runs in a worker thread; the client connects on first use, because
        # the process that records facts starts before the database is necessarily reachable.
        self._database: Final = database

        self._connect: Final = connect

        self._session: ClickHouseClient | None = None

    async def ensure_schema(
        self,
    ) -> None:
        """
        Create the database and the table when they do not exist yet.
        """

        await self._run(
            self._ensure_schema_blocking,
            "create its schema",
        )

        _logger.info(
            msg="analytics.schema_ensured",
            extra={
                "database": self._database,
                "table": TABLE,
            },
        )

    async def record(
        self,
        *,
        facts: Sequence[VocabularyFact],
    ) -> None:
        if not facts:
            return

        rows = tuple(
            _row_of(
                fact,
            )
            for fact in facts
        )

        await self._run(
            lambda: self._insert_blocking(
                rows,
            ),
            "record facts",
        )

    async def summarize(
        self,
        *,
        focus_limit: int,
    ) -> VocabularySummary:
        return await self._run(
            lambda: self._summarize_blocking(
                focus_limit,
            ),
            "summarize facts",
        )

    async def _run[T](
        self,
        operation: Callable[[], T],
        name: str,
        /,
    ) -> T:
        try:
            return await to_thread(
                operation,
            )

        except OperationalError as exception:
            kind = type(
                exception,
            ).__name__

            raise FactStoreUnavailableError(
                f"the fact store could not be reached to {name} in database {self._database!r} ({kind})",
            ) from exception

    def _client(
        self,
    ) -> ClickHouseClient:
        if self._session is None:
            self._session = self._connect()

        return self._session

    def _ensure_schema_blocking(
        self,
    ) -> None:
        client = self._client()

        client.command(
            cmd=CREATE_DATABASE_SQL,
            parameters={
                "database": self._database,
            },
        )

        client.command(
            cmd=CREATE_TABLE_SQL,
            parameters={
                "database": self._database,
            },
        )

    def _insert_blocking(
        self,
        rows: Sequence[tuple[Any, ...]],
        /,
    ) -> None:
        self._client().insert(
            table=TABLE,
            data=rows,
            column_names=COLUMNS,
            database=self._database,
        )

    def _summarize_blocking(
        self,
        focus_limit: int,
        /,
    ) -> VocabularySummary:
        client = self._client()

        (
            analysis_count,
            fact_count,
        ) = client.query(
            query=COUNTS_SQL,
            parameters={
                "database": self._database,
            },
        ).result_rows[0]

        level_rows = client.query(
            query=LEVELS_SQL,
            parameters={
                "database": self._database,
            },
        ).result_rows

        counts = {
            CefrLevel(
                value=level,
            ): count
            for (
                level,
                count,
            ) in level_rows
        }

        focus_rows = client.query(
            query=FOCUS_SQL,
            parameters={
                "database": self._database,
                "tier": StudyTier.FOCUS,
                "focus_limit": focus_limit,
            },
        ).result_rows

        return VocabularySummary(
            analysis_count=analysis_count,
            fact_count=fact_count,
            levels=tuple(
                LevelCount(
                    level=level,
                    count=counts.get(
                        level,
                        0,
                    ),
                )
                for level in LEVELS_ASCENDING
            ),
            focus_lemmas=tuple(
                LemmaCount(
                    lemma=lemma,
                    part_of_speech=PartOfSpeech(
                        value=part_of_speech,
                    ),
                    analysis_count=lemma_analysis_count,
                    occurrence_count=occurrence_count,
                )
                for (
                    lemma,
                    part_of_speech,
                    lemma_analysis_count,
                    occurrence_count,
                ) in focus_rows
            ),
        )


def _row_of(
    fact: VocabularyFact,
    /,
) -> tuple[Any, ...]:
    return (
        fact.analysis_id.value,
        fact.completed_at,
        fact.origin,
        fact.learner_level,
        fact.lemma,
        fact.part_of_speech,
        fact.level,
        fact.level_source,
        fact.tier,
        fact.occurrence_count,
        fact.lookup_outcome,
    )
