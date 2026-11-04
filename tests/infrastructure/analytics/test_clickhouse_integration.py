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
from pathlib import (
    Path,
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
    fail,
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
from wordwinnow.infrastructure.settings import (
    Settings,
)

# NOTE:
# The server `make up` starts, or another one named in the environment; the test skips when none answers.
#
# The password is the one `make up` generated into the checkout's `.env`, unless `WORDWINNOW_TEST_CLICKHOUSE_PASSWORD`
# names another.
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
)

_ENV: Final = (
    Path(
        __file__,
    ).parents[3]
    / ".env"
)

# NOTE:
# A pure reference instant with no product meaning of its own.
_EPOCH: Final = datetime(
    year=1970,
    month=1,
    day=1,
    tzinfo=UTC,
)


def _generated_password() -> SecretStr | None:
    """
    The password the services would use, read the way they read it, the
    environment first and then the checkout's `.env`, or `None` when neither
    sets one, which the settings' default must not stand in for.
    """

    settings = Settings(
        _env_file=_ENV,
    )

    if "clickhouse_password" not in settings.model_fields_set:
        return None

    return settings.clickhouse_password


@fixture(
    scope="module",
)
def clickhouse() -> SecretStr:
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

    password = (
        SecretStr(
            secret_value=_PASSWORD,
        )
        if _PASSWORD
        else _generated_password()
    )

    # NOTE:
    # A server that answers has a password, and without the one `make up` generated the test would only be guessing
    # it, so it fails and says where the password comes from, where a skip would read as a server that is not there.
    if password is None:
        fail(
            reason=(
                f"the ClickHouse server on {_HOST}:{_PORT} answers, and no password is known for it: `make up` "
                "generates one into `.env`, and WORDWINNOW_TEST_CLICKHOUSE_PASSWORD names another"
            ),
            pytrace=False,
        )

    return password


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
        clickhouse: SecretStr,
    ) -> None:
        store = ClickHouseVocabularyFacts(
            database="baz",
            connect=lambda: build_client(
                host=_HOST,
                port=_PORT,
                user=_USER,
                password=clickhouse,
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
