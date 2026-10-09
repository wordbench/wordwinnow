"""
The store against a real ClickHouse server: the same facts recorded twice are
summarized once.

The server runs in a throwaway container that this module starts and removes.
"""

from datetime import (
    UTC,
    datetime,
)
from os import (
    environ,
)
from typing import (
    Final,
    final,
)
from uuid import (
    UUID,
)

from httpx2 import (
    HTTPError,
    get,
)
from pydantic import (
    SecretStr,
)
from pytest import (
    fixture,
    mark,
    skip,
)

from wordwinnow.application.dto import (
    LemmaCount,
    VocabularyFact,
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
    ClickHouseVocabularyFacts,
    build_client,
)

# NOTE:
# The server `make up` starts, or another one named in the environment; the test skips when none answers.
_HOST: Final = environ.get(
    key="WORDWINNOW_TEST_CLICKHOUSE_HOST",
    default="127.0.0.1",
)

_PORT: Final = int(
    environ.get(
        key="WORDWINNOW_TEST_CLICKHOUSE_PORT",
        default="8123",
    ),
)

_USER: Final = environ.get(
    key="WORDWINNOW_TEST_CLICKHOUSE_USER",
    default="wordwinnow",
)

_PASSWORD: Final = environ.get(
    key="WORDWINNOW_TEST_CLICKHOUSE_PASSWORD",
    default="wordwinnow",
)

# NOTE:
# A pure reference instant with no product meaning of its own.
_EPOCH: Final = datetime(
    year=1970,
    month=1,
    day=1,
    tzinfo=UTC,
)


@fixture(
    scope="module",
)
def clickhouse() -> None:
    try:
        answered = (
            get(
                url=f"http://{_HOST}:{_PORT}/ping",
                timeout=2.0,
            ).status_code
            == 200
        )

    except HTTPError:
        answered = False

    if not answered:
        skip(
            reason=f"no ClickHouse server answers on {_HOST}:{_PORT}; run `make up`",
        )


# NOTE:
# Six fields of three types are unreadable positionally, which is why the builder is keyword-only.
def _fact(
    *,
    analysis_id: AnalysisId,
    lemma: str,
    part_of_speech: PartOfSpeech,
    level: CefrLevel,
    tier: StudyTier,
    occurrence_count: int,
) -> VocabularyFact:
    return VocabularyFact(
        analysis_id=analysis_id,
        completed_at=_EPOCH,
        origin=DocumentOrigin.CUSTOM_TEXT,
        learner_level=CefrLevel.B1,
        lemma=lemma,
        part_of_speech=part_of_speech,
        level=level,
        level_source=LevelSource.REFERENCE_LIST,
        tier=tier,
        occurrence_count=occurrence_count,
        lookup_outcome=LookupOutcome.FOUND,
    )


@final
class TestClickHouse:
    @mark.integration
    async def test_facts_recorded_twice_are_summarized_once(
        self,
        *,
        clickhouse: None,
    ) -> None:
        store = ClickHouseVocabularyFacts(
            database="baz",
            connect=lambda: build_client(
                host=_HOST,
                port=_PORT,
                user=_USER,
                password=SecretStr(
                    secret_value=_PASSWORD,
                ),
            ),
        )

        await store.ensure_schema()

        await store.ensure_schema()

        first = AnalysisId(
            value=UUID(
                int=1,
            ),
        )

        second = AnalysisId(
            value=UUID(
                int=2,
            ),
        )

        facts = (
            _fact(
                analysis_id=first,
                lemma="groom",
                part_of_speech=PartOfSpeech.NOUN,
                level=CefrLevel.B1,
                tier=StudyTier.FOCUS,
                occurrence_count=2,
            ),
            _fact(
                analysis_id=first,
                lemma="drunken",
                part_of_speech=PartOfSpeech.ADJECTIVE,
                level=CefrLevel.A2,
                tier=StudyTier.REVIEW,
                occurrence_count=1,
            ),
            _fact(
                analysis_id=second,
                lemma="groom",
                part_of_speech=PartOfSpeech.NOUN,
                level=CefrLevel.B1,
                tier=StudyTier.FOCUS,
                occurrence_count=3,
            ),
        )

        await store.record(
            facts=facts,
        )

        await store.record(
            facts=facts,
        )

        summary = await store.summarize(
            focus_limit=5,
        )

        assert summary.analysis_count == 2

        assert summary.fact_count == 3

        assert {level_count.level: level_count.count for level_count in summary.levels} == {
            CefrLevel.A1: 0,
            CefrLevel.A2: 1,
            CefrLevel.B1: 2,
            CefrLevel.B2: 0,
            CefrLevel.C1: 0,
            CefrLevel.C2: 0,
        }

        assert summary.focus_lemmas == (
            LemmaCount(
                lemma="groom",
                part_of_speech=PartOfSpeech.NOUN,
                analysis_count=2,
                occurrence_count=5,
            ),
        )
