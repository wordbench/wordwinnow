"""
The analysis store for the persistence tests: a fresh SQLite file per test,
analyses in every state of their lifecycle to put in it, and the way back from
the migrations.
"""

from collections.abc import (
    AsyncIterator,
    Callable,
)
from datetime import (
    UTC,
    datetime,
    timedelta,
)
from pathlib import (
    Path,
)
from typing import (
    Final,
)

from alembic.command import (
    downgrade,
)
from alembic.config import (
    Config,
)
from pytest import (
    fixture,
)
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
)

from wordwinnow.domain.analysis import (
    Analysis,
    AnalysisOptions,
    Stage,
    StageTiming,
    request_an_analysis,
)
from wordwinnow.domain.cefr import (
    CefrLevel,
    LevelAssessment,
    LevelSource,
)
from wordwinnow.domain.dictionary import (
    License,
    LookupOutcome,
    Provenance,
    SelectedDefinition,
)
from wordwinnow.domain.document import (
    Document,
    DocumentOrigin,
)
from wordwinnow.domain.identifiers import (
    AnalysisId,
)
from wordwinnow.domain.language import (
    PartOfSpeech,
)
from wordwinnow.domain.learner import (
    LearnerProfile,
)
from wordwinnow.domain.senses import (
    LexicalSense,
)
from wordwinnow.domain.study import (
    DictionaryInformation,
    StudyItem,
    StudyTier,
)
from wordwinnow.domain.vocabulary import (
    VocabularyItem,
)
from wordwinnow.infrastructure.persistence.engine import (
    build_engine,
)
from wordwinnow.infrastructure.persistence.migrations import (
    ALEMBIC_INI,
)
from wordwinnow.infrastructure.persistence.models import (
    Base,
)
from wordwinnow.infrastructure.persistence.uow import (
    SqlAlchemyUnitOfWork,
    new_unit_of_work_factory,
)

type NewUnitOfWork = Callable[[], SqlAlchemyUnitOfWork]


# NOTE:
# A pure reference instant with no product meaning of its own.
EPOCH: Final = datetime(
    year=1970,
    month=1,
    day=1,
    tzinfo=UTC,
)


DOCUMENT: Final = Document(
    title="a-scandal-in-bohemia",
    text="The groom found Irene Adler resolute. The resolute lady outwitted the groom.",
    origin=DocumentOrigin.CUSTOM_TEXT,
    reference="a-scandal-in-bohemia.txt",
)


PROFILE: Final = LearnerProfile(
    level=CefrLevel.B1,
    target_level=CefrLevel.C1,
    known_lemmas=frozenset(
        {
            "resolute",
        },
    ),
)


# NOTE:
# Between them the two items give every field a value to round-trip: the senses are WordNet 3.0's own, and the
# dictionary definitions are written for this test.
GROOM: Final = StudyItem(
    item=VocabularyItem(
        lemma="groom",
        part_of_speech=PartOfSpeech.NOUN,
        occurrence_count=2,
        example_sentence="The groom found Irene Adler resolute.",
    ),
    zipf_frequency=3.63,
    level=LevelAssessment(
        level=CefrLevel.B1,
        source=LevelSource.REFERENCE_LIST,
    ),
    tier=StudyTier.FOCUS,
    dictionary=DictionaryInformation(
        outcome=LookupOutcome.FOUND,
        failure_reason=None,
        phonetic="/ɡruːm/",
        definitions=(
            SelectedDefinition(
                text="A person who looks after horses.",
                example="The groom rubbed down the horses.",
                part_of_speech_matched=True,
                provenance=Provenance(
                    license=License(
                        name="CC BY-SA 3.0",
                        url="https://creativecommons.org/licenses/by-sa/3.0",
                    ),
                    source_urls=("https://example.com/groom",),
                ),
            ),
            SelectedDefinition(
                text="To clean and brush a horse.",
                example=None,
                part_of_speech_matched=False,
            ),
        ),
    ),
    senses=(
        LexicalSense(
            key="groom.n.01",
            part_of_speech=PartOfSpeech.NOUN,
            gloss="a man participant in his own marriage ceremony",
            example=None,
            synonyms=("bridegroom",),
            hypernyms=("participant",),
            antonyms=(),
            usage_count=1,
            hyponym_count=0,
            category="noun.person",
        ),
    ),
)


RESOLUTE: Final = StudyItem(
    item=VocabularyItem(
        lemma="resolute",
        part_of_speech=PartOfSpeech.ADJECTIVE,
        occurrence_count=2,
        example_sentence="The groom found Irene Adler resolute.",
    ),
    zipf_frequency=3.12,
    level=LevelAssessment(
        level=CefrLevel.B2,
        source=LevelSource.MODEL,
    ),
    tier=StudyTier.KNOWN,
    dictionary=DictionaryInformation(
        outcome=LookupOutcome.UNAVAILABLE,
        failure_reason="transport_error",
        phonetic=None,
        definitions=(),
    ),
    senses=(
        LexicalSense(
            key="resolute.a.01",
            part_of_speech=PartOfSpeech.ADJECTIVE,
            gloss="firm in purpose or belief; characterized by firmness and determination",
            example="stood resolute against the enemy",
            synonyms=(),
            hypernyms=(),
            antonyms=("irresolute",),
            usage_count=1,
            hyponym_count=0,
            category="adj.all",
        ),
    ),
)


TIMINGS: Final = (
    StageTiming(
        stage=Stage.LINGUISTIC_ANALYSIS,
        seconds=0.25,
    ),
    StageTiming(
        stage=Stage.WINNOWING,
        seconds=0.5,
    ),
)


@fixture
async def engine(
    *,
    tmp_path: Path,
) -> AsyncIterator[AsyncEngine]:
    path = tmp_path / "wordwinnow.db"

    built = build_engine(
        database_url=f"sqlite+aiosqlite:///{path}",
    )

    await create_schema(
        engine=built,
    )

    yield built

    await built.dispose()


@fixture
def new_unit_of_work(
    *,
    engine: AsyncEngine,
) -> NewUnitOfWork:
    return new_unit_of_work_factory(
        engine=engine,
    )


# NOTE:
# The four builders take the one instant a case may move as a named optional, which is why they are keyword-only.
def requested_analysis(
    *,
    at: datetime = EPOCH,
) -> Analysis:
    analysis = request_an_analysis(
        document=DOCUMENT,
        profile=PROFILE,
        options=AnalysisOptions(
            include_dictionary=True,
        ),
        at=at,
    )

    analysis.pull_events()

    return analysis


def processing_analysis(
    *,
    at: datetime = EPOCH,
) -> Analysis:
    analysis = requested_analysis(
        at=at,
    )

    analysis.start(
        at=at
        + timedelta(
            seconds=1,
        ),
    )

    return analysis


def completed_analysis(
    *,
    at: datetime = EPOCH,
) -> Analysis:
    analysis = processing_analysis(
        at=at,
    )

    analysis.complete(
        at=at
        + timedelta(
            seconds=2,
        ),
        items=(
            GROOM,
            RESOLUTE,
        ),
        stage_timings=TIMINGS,
    )

    analysis.pull_events()

    return analysis


def failed_analysis(
    *,
    at: datetime = EPOCH,
) -> Analysis:
    analysis = processing_analysis(
        at=at,
    )

    analysis.fail(
        at=at
        + timedelta(
            seconds=2,
        ),
        reason="tagger crashed",
    )

    analysis.pull_events()

    return analysis


async def create_schema(
    *,
    engine: AsyncEngine,
) -> None:
    # TEST:
    # The tables come straight from the models here; the migration tests prove, on SQLite and on PostgreSQL, that the
    # migrations produce the same.
    async with engine.begin() as connection:
        await connection.run_sync(
            fn=Base.metadata.create_all,
        )


def downgrade_to_base(
    *,
    database_url: str,
) -> None:
    # TEST:
    # What `alembic downgrade base` does, for the tests that undo the migrations; like the upgrade, it runs its own
    # event loop, so a test calls it in a worker thread.
    #
    # WARN:
    # A main option is read back through `ConfigParser` interpolation, so a `%` in the URL is doubled, as the upgrade
    # does it.
    config = Config(
        file_=ALEMBIC_INI,
    )

    config.set_main_option(
        name="sqlalchemy.url",
        value=database_url.replace(
            "%",
            "%%",
        ),
    )

    downgrade(
        config=config,
        revision="base",
    )


async def store_analysis(
    *,
    analysis: Analysis,
    new_unit_of_work: NewUnitOfWork,
) -> None:
    async with new_unit_of_work() as uow:
        await uow.analyses.add(
            analysis=analysis,
        )

        await uow.commit()


async def load_analysis(
    *,
    analysis_id: AnalysisId,
    new_unit_of_work: NewUnitOfWork,
) -> Analysis | None:
    async with new_unit_of_work() as uow:
        return await uow.analyses.get(
            analysis_id=analysis_id,
        )


def assert_same_state(
    *,
    original: Analysis,
    loaded: Analysis | None,
) -> None:
    assert loaded is not None

    assert loaded == original

    assert loaded.document == original.document

    assert loaded.profile == original.profile

    assert loaded.options == original.options

    assert loaded.requested_at == original.requested_at

    assert loaded.status is original.status

    assert loaded.started_at == original.started_at

    assert loaded.finished_at == original.finished_at

    assert loaded.failure_reason == original.failure_reason

    assert loaded.stage_timings == original.stage_timings

    assert loaded.items == original.items

    assert loaded.pull_events() == ()
