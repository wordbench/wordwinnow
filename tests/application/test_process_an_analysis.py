"""
The pipeline, end to end against fakes.

The text, the tables the fakes answer from, and the expected study items are
all written out, so the test reads as a worked example of what the pipeline
does.
"""

from asyncio import (
    get_running_loop,
)
from asyncio import (
    sleep as asyncio_sleep,
)
from collections.abc import (
    Mapping,
)
from collections.abc import (
    Set as AbstractSet,
)
from dataclasses import (
    replace,
)
from datetime import (
    timedelta,
)
from types import (
    MappingProxyType,
)
from typing import (
    Final,
    final,
)

from httpx2 import (
    AsyncClient,
    ConnectError,
    MockTransport,
    ReadTimeout,
    Request,
    Response,
)
from pytest import (
    raises,
)

from tests.fakes.ports import (
    EPOCH,
    FakeClock,
    FakeDictionary,
    FakeLevelEstimator,
    FakeLexicalSemantics,
    FakeLinguisticAnalyzer,
    FakeReferenceLexicon,
    FakeWordFrequency,
    InMemoryStorage,
    RaisingPublisher,
    RecordingProgress,
    RecordingPublisher,
)
from tests.fakes.virtual_time import (
    run_in_virtual_time,
)
from wordwinnow.application.errors import (
    AnalysisNotFoundError,
    MessagingUnavailableError,
)
from wordwinnow.application.ports.dictionary import (
    Dictionary,
)
from wordwinnow.application.use_cases.process_an_analysis import (
    Pipeline,
    PipelineResult,
    process_an_analysis,
    run_pipeline,
)
from wordwinnow.application.use_cases.request_an_analysis import (
    request_an_analysis,
)
from wordwinnow.domain.analysis import (
    AnalysisCompleted,
    AnalysisOptions,
    AnalysisStatus,
    Stage,
)
from wordwinnow.domain.cefr import (
    CefrLevel,
    LevelSource,
)
from wordwinnow.domain.dictionary import (
    Definition,
    DictionaryEntry,
    DictionaryLookup,
    LookupOutcome,
    Meaning,
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
    Sentence,
)
from wordwinnow.domain.learner import (
    LearnerProfile,
)
from wordwinnow.domain.senses import (
    LexicalSense,
)
from wordwinnow.domain.study import (
    STUDY_TIERS,
    GlossSource,
    StudyTier,
)
from wordwinnow.infrastructure.dictionary.free_dictionary import (
    FreeDictionaryClient,
)
from wordwinnow.infrastructure.dictionary.remote import (
    RemoteDictionary,
)
from wordwinnow.infrastructure.dictionary.wire import (
    to_wire,
)

_TEXT: Final = "Holmes became the drunken groom. The groom sauntered slowly."

_DOCUMENT: Final = Document(
    title="a-scandal-in-bohemia",
    text=_TEXT,
    origin=DocumentOrigin.CUSTOM_TEXT,
    reference="a-scandal-in-bohemia.txt",
)

# NOTE:
# WordNet 3.0's first sense of `groom`, with its real counts: the bridegroom, not the stableman Holmes plays.
_GROOM_SENSE: Final = LexicalSense(
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
)

_PROFILE: Final = LearnerProfile(
    level=CefrLevel.B1,
    target_level=CefrLevel.B2,
)


# NOTE:
# The tables hold the words' real Zipf frequencies and, for the three the reference lists know, their real levels, so
# the frequency heuristic places `drunken` at B2 and `saunter` at C2 exactly as it would for the real text.
#
# Both parameters are optional overrides of the worked example's pipeline, so they are named rather than positional.
def _pipeline(
    *,
    dictionary: FakeDictionary | None = None,
    level_estimator: FakeLevelEstimator | None = None,
) -> Pipeline:
    return Pipeline(
        linguistics=FakeLinguisticAnalyzer(
            parts_of_speech={
                "became": PartOfSpeech.VERB,
                "the": None,
                "drunken": PartOfSpeech.ADJECTIVE,
                "sauntered": PartOfSpeech.VERB,
                "slowly": PartOfSpeech.ADVERB,
            },
            lemmas={
                "became": "become",
                "sauntered": "saunter",
            },
            proper_nouns=frozenset(
                ("Holmes",),
            ),
            function_words=frozenset(
                ("the",),
            ),
        ),
        frequency=FakeWordFrequency(
            frequencies={
                "become": 5.4,
                "drunken": 3.66,
                "groom": 3.63,
                "saunter": 2.23,
                "slowly": 4.63,
            },
        ),
        reference_lexicon=FakeReferenceLexicon(
            levels={
                (
                    "become",
                    PartOfSpeech.VERB,
                ): CefrLevel.A1,
                (
                    "groom",
                    PartOfSpeech.NOUN,
                ): CefrLevel.B1,
                (
                    "slowly",
                    PartOfSpeech.ADVERB,
                ): CefrLevel.A2,
            },
        ),
        level_estimator=level_estimator or FakeLevelEstimator(),
        dictionary=(
            dictionary
            or FakeDictionary(
                lookups={
                    "groom": DictionaryLookup(
                        outcome=LookupOutcome.FOUND,
                        entries=(
                            DictionaryEntry(
                                headword="groom",
                                phonetic="/ɡruːm/",
                                meanings=(
                                    Meaning(
                                        word_class="noun",
                                        part_of_speech=PartOfSpeech.NOUN,
                                        definitions=(
                                            Definition(
                                                text="A person who looks after horses.",
                                                example="The groom rubbed down the horses.",
                                            ),
                                        ),
                                    ),
                                ),
                            ),
                        ),
                    ),
                    "saunter": DictionaryLookup(
                        outcome=LookupOutcome.UNAVAILABLE,
                        failure_reason="timed_out",
                    ),
                },
            )
        ),
        lexical_semantics=FakeLexicalSemantics(
            senses={
                (
                    "groom",
                    PartOfSpeech.NOUN,
                ): (_GROOM_SENSE,),
            },
        ),
        dictionary_concurrency=2,
    )


@final
class _RecoveringDictionary:
    """
    Refuses the words in `refused` with a time to ask again until
    `recovers_at` on the loop's clock, and answers every other lookup, and any
    lookup after that, as `answers` does.

    Each lookup is recorded with the moment it was asked.
    """

    def __init__(
        self,
        *,
        refused: AbstractSet[str],
        recovers_at: float,
        retry_after_seconds: float,
        answers: Dictionary,
    ) -> None:
        self._refused: Final = refused

        self._recovers_at: Final = recovers_at

        self._retry_after: Final = retry_after_seconds

        self._answers: Final = answers

        self.asked: Final[list[tuple[float, str]]] = []

    async def look_up(
        self,
        *,
        lemma: str,
    ) -> DictionaryLookup:
        now = get_running_loop().time()

        self.asked.append(
            (
                now,
                lemma,
            ),
        )

        if lemma in self._refused and now < self._recovers_at:
            return DictionaryLookup(
                outcome=LookupOutcome.UNAVAILABLE,
                failure_reason="circuit_open",
                retry_after_seconds=self._retry_after,
            )

        return await self._answers.look_up(
            lemma=lemma,
        )


@final
class _RefusedUntil:
    """
    Refuses each word in `until` while the loop's clock is before its moment,
    naming the seconds left until then, and answers every other lookup, and
    any lookup after its moment, as `answers` does.
    """

    def __init__(
        self,
        *,
        until: Mapping[str, float],
        answers: Dictionary,
    ) -> None:
        self._until: Final = until

        self._answers: Final = answers

    async def look_up(
        self,
        *,
        lemma: str,
    ) -> DictionaryLookup:
        now = get_running_loop().time()

        moment = self._until.get(
            lemma,
        )

        if moment is not None and now < moment:
            return DictionaryLookup(
                outcome=LookupOutcome.UNAVAILABLE,
                failure_reason="circuit_open",
                retry_after_seconds=moment - now,
            )

        return await self._answers.look_up(
            lemma=lemma,
        )


@final
class _HangsWhenAskedAgain:
    """
    Refuses `lemma` with a time to ask again, `retry_after_seconds`, and holds
    every later lookup of it for `hangs_seconds` before refusing it again, as
    a trial that the provider never answers holds the words waiting for it;
    answers every other word as `answers` does.
    """

    def __init__(
        self,
        *,
        lemma: str,
        retry_after_seconds: float,
        hangs_seconds: float,
        answers: Dictionary,
    ) -> None:
        self._lemma: Final = lemma

        self._retry_after: Final = retry_after_seconds

        self._hangs: Final = hangs_seconds

        self._answers: Final = answers

        self._asked = 0

    async def look_up(
        self,
        *,
        lemma: str,
    ) -> DictionaryLookup:
        if lemma != self._lemma:
            return await self._answers.look_up(
                lemma=lemma,
            )

        self._asked += 1

        if self._asked > 1:
            await asyncio_sleep(
                delay=self._hangs,
            )

        return DictionaryLookup(
            outcome=LookupOutcome.UNAVAILABLE,
            failure_reason="circuit_open",
            retry_after_seconds=self._retry_after,
        )


@final
class _BackAfterAnOutage:
    """
    Refuses the words in `refused` with the seconds left until `back_at` on
    the loop's clock, and afterwards answers each of them as `answers` does
    after `answer_seconds`, as a provider that has come back answers at its
    own pace; answers every other word as `answers` does, at once.
    """

    def __init__(
        self,
        *,
        refused: AbstractSet[str],
        back_at: float,
        answer_seconds: float,
        answers: Dictionary,
    ) -> None:
        self._refused: Final = refused

        self._back_at: Final = back_at

        self._answer_seconds: Final = answer_seconds

        self._answers: Final = answers

    async def look_up(
        self,
        *,
        lemma: str,
    ) -> DictionaryLookup:
        if lemma in self._refused:
            now = get_running_loop().time()

            if now < self._back_at:
                return DictionaryLookup(
                    outcome=LookupOutcome.UNAVAILABLE,
                    failure_reason="circuit_open",
                    retry_after_seconds=self._back_at - now,
                )

            await asyncio_sleep(
                delay=self._answer_seconds,
            )

        return await self._answers.look_up(
            lemma=lemma,
        )


def _run_with_patience(
    *,
    dictionary: Dictionary,
    patience_seconds: float,
    concurrency: int = 2,
) -> tuple[PipelineResult, RecordingProgress, float]:
    """
    The worked example's pipeline over `dictionary`, run on virtual time, with
    what its progress heard and the moment it ended.
    """

    progress = RecordingProgress()

    async def main() -> tuple[PipelineResult, float]:
        loop = get_running_loop()

        result = await run_pipeline(
            text=_TEXT,
            profile=_PROFILE,
            include_dictionary=True,
            pipeline=replace(
                _pipeline(),
                dictionary=dictionary,
                dictionary_concurrency=concurrency,
                progress=progress,
                dictionary_patience_seconds=patience_seconds,
                clock=loop.time,
            ),
        )

        return (
            result,
            loop.time(),
        )

    (
        result,
        ended_at,
    ) = run_in_virtual_time(
        main=main,
    )

    return (
        result,
        progress,
        ended_at,
    )


def _event_types(
    publisher: RecordingPublisher,
    /,
) -> tuple[type[object], ...]:
    return tuple(
        type(
            event,
        )
        for (
            _,
            event,
        ) in publisher.published
    )


@final
class _BrokenLinguisticAnalyzer:
    """
    A linguistic analyzer whose tagger crashes.
    """

    @property
    def function_words(
        self,
    ) -> frozenset[str]:
        return frozenset()

    def analyze(
        self,
        *,
        text: str,
    ) -> tuple[Sentence, ...]:
        raise RuntimeError(
            "tagger crashed",
        )


@final
class TestRunPipeline:
    async def test_the_worked_example(
        self,
    ) -> None:
        result = await run_pipeline(
            text=_TEXT,
            profile=LearnerProfile(
                level=CefrLevel.B1,
                target_level=CefrLevel.B2,
                known_lemmas=frozenset(
                    ("become",),
                ),
            ),
            include_dictionary=True,
            pipeline=_pipeline(),
        )

        by_lemma = MappingProxyType(
            mapping={study_item.item.lemma: study_item for study_item in result.items},
        )

        assert tuple(
            (
                study_item.item.lemma,
                study_item.tier,
            )
            for study_item in result.items
        ) == (
            (
                "groom",
                StudyTier.FOCUS,
            ),
            (
                "drunken",
                StudyTier.FOCUS,
            ),
            (
                "saunter",
                StudyTier.STRETCH,
            ),
            (
                "slowly",
                StudyTier.REVIEW,
            ),
            (
                "become",
                StudyTier.KNOWN,
            ),
        )

        groom = by_lemma["groom"]

        assert groom.item.occurrence_count == 2

        assert groom.item.example_sentence == "Holmes became the drunken groom."

        assert groom.level.source is LevelSource.REFERENCE_LIST

        assert groom.senses == (_GROOM_SENSE,)

        assert groom.dictionary is not None

        assert groom.dictionary.outcome is LookupOutcome.FOUND

        assert groom.dictionary.phonetic == "/ɡruːm/"

        assert groom.dictionary.definitions[0].text == "A person who looks after horses."

        assert groom.dictionary.definitions[0].part_of_speech_matched

        saunter = by_lemma["saunter"]

        assert saunter.level.source is LevelSource.FREQUENCY_HEURISTIC

        assert saunter.level.level is CefrLevel.C2

        assert saunter.dictionary is not None

        assert saunter.dictionary.outcome is LookupOutcome.UNAVAILABLE

        assert saunter.dictionary.failure_reason == "timed_out"

        assert saunter.dictionary.definitions == ()

        assert tuple(timing.stage for timing in result.stage_timings) == tuple(
            Stage,
        )

    async def test_the_model_is_asked_only_for_words_outside_the_reference_list(
        self,
    ) -> None:
        estimator = FakeLevelEstimator(
            level=CefrLevel.C1,
        )

        result = await run_pipeline(
            text=_TEXT,
            profile=_PROFILE,
            include_dictionary=False,
            pipeline=_pipeline(
                level_estimator=estimator,
            ),
        )

        assert sorted(features.lemma for features in estimator.seen) == [
            "drunken",
            "saunter",
        ]

        by_lemma = MappingProxyType(
            mapping={study_item.item.lemma: study_item for study_item in result.items},
        )

        assert by_lemma["saunter"].level.source is LevelSource.MODEL

        assert by_lemma["groom"].level.source is LevelSource.REFERENCE_LIST

        assert all(features.sense_count == 0 for features in estimator.seen)

        assert frozenset(features.category for features in estimator.seen) == frozenset(
            (None,),
        )

    async def test_without_the_dictionary_no_lookup_happens(
        self,
    ) -> None:
        dictionary = FakeDictionary()

        result = await run_pipeline(
            text=_TEXT,
            profile=_PROFILE,
            include_dictionary=False,
            pipeline=_pipeline(
                dictionary=dictionary,
            ),
        )

        assert dictionary.looked_up == []

        assert all(study_item.dictionary is None for study_item in result.items)

    async def test_the_words_a_learner_studies_first_are_looked_up_first(
        self,
    ) -> None:
        dictionary = FakeDictionary()

        # NOTE:
        # The text meets the words as become, drunken, groom, saunter, slowly; one lookup at a time shows the order
        # the stage asks in instead.
        #
        # Of them `become` is known and `slowly` lies below the learner's level, so neither is asked.
        await run_pipeline(
            text=_TEXT,
            profile=LearnerProfile(
                level=CefrLevel.B1,
                target_level=CefrLevel.B2,
                known_lemmas=frozenset(
                    ("become",),
                ),
            ),
            include_dictionary=True,
            pipeline=replace(
                _pipeline(
                    dictionary=dictionary,
                ),
                dictionary_concurrency=1,
            ),
        )

        assert dictionary.looked_up == [
            "groom",
            "drunken",
            "saunter",
        ]

    async def test_only_the_words_to_study_are_looked_up(
        self,
    ) -> None:
        dictionary = FakeDictionary()

        result = await run_pipeline(
            text=_TEXT,
            profile=LearnerProfile(
                level=CefrLevel.B1,
                target_level=CefrLevel.B2,
                known_lemmas=frozenset(
                    ("become",),
                ),
            ),
            include_dictionary=True,
            pipeline=_pipeline(
                dictionary=dictionary,
            ),
        )

        assert frozenset(study_item.tier for study_item in result.items) == frozenset(
            StudyTier,
        )

        assert sorted(
            dictionary.looked_up,
        ) == sorted(study_item.item.lemma for study_item in result.items if study_item.tier in STUDY_TIERS)

    async def test_a_word_that_is_not_asked_carries_no_dictionary_information(
        self,
    ) -> None:
        result = await run_pipeline(
            text=_TEXT,
            profile=LearnerProfile(
                level=CefrLevel.B1,
                target_level=CefrLevel.B2,
                known_lemmas=frozenset(
                    ("become",),
                ),
            ),
            include_dictionary=True,
            pipeline=_pipeline(),
        )

        assert frozenset(study_item.tier for study_item in result.items) == frozenset(
            StudyTier,
        )

        for study_item in result.items:
            assert (study_item.dictionary is not None) is (study_item.tier in STUDY_TIERS)

    async def test_the_dictionary_changes_neither_a_words_tier_nor_its_place_in_the_order(
        self,
    ) -> None:
        async def placed(
            *,
            include_dictionary: bool,
        ) -> tuple[tuple[str, PartOfSpeech, StudyTier], ...]:
            result = await run_pipeline(
                text=_TEXT,
                profile=LearnerProfile(
                    level=CefrLevel.B1,
                    target_level=CefrLevel.B2,
                    known_lemmas=frozenset(
                        ("become",),
                    ),
                ),
                include_dictionary=include_dictionary,
                pipeline=_pipeline(),
            )

            return tuple(
                (
                    study_item.item.lemma,
                    study_item.item.part_of_speech,
                    study_item.tier,
                )
                for study_item in result.items
            )

        with_the_dictionary = await placed(
            include_dictionary=True,
        )

        assert with_the_dictionary

        assert with_the_dictionary == await placed(
            include_dictionary=False,
        )

    async def test_progress_hears_every_stage_in_order_and_every_lookup_with_its_outcome(
        self,
    ) -> None:
        dictionary = FakeDictionary()

        progress = RecordingProgress()

        await run_pipeline(
            text=_TEXT,
            profile=_PROFILE,
            include_dictionary=True,
            pipeline=replace(
                _pipeline(
                    dictionary=dictionary,
                ),
                progress=progress,
            ),
        )

        assert sorted(
            dictionary.looked_up,
        ) == [
            "drunken",
            "groom",
            "saunter",
        ]

        assert progress.events == [
            "linguistic_analysis",
            "vocabulary_selection",
            "lexical_semantics",
            "level_assessment",
            "dictionary_enrichment of 3",
            "not_found",
            "not_found",
            "not_found",
            "winnowing",
        ]

    async def test_an_analysis_with_no_word_to_study_starts_the_dictionary_stage_with_nought_and_looks_nothing_up(
        self,
    ) -> None:
        dictionary = FakeDictionary()

        progress = RecordingProgress()

        result = await run_pipeline(
            text=_TEXT,
            profile=LearnerProfile(
                level=CefrLevel.B1,
                target_level=CefrLevel.B2,
                known_lemmas=frozenset(
                    (
                        "drunken",
                        "groom",
                        "saunter",
                    ),
                ),
            ),
            include_dictionary=True,
            pipeline=replace(
                _pipeline(
                    dictionary=dictionary,
                ),
                progress=progress,
            ),
        )

        assert frozenset(study_item.tier for study_item in result.items) == frozenset(
            (
                StudyTier.REVIEW,
                StudyTier.KNOWN,
            ),
        )

        assert dictionary.looked_up == []

        assert progress.events == [
            "linguistic_analysis",
            "vocabulary_selection",
            "lexical_semantics",
            "level_assessment",
            "dictionary_enrichment of 0",
            "winnowing",
        ]

    async def test_without_the_dictionary_progress_hears_no_dictionary_stage(
        self,
    ) -> None:
        progress = RecordingProgress()

        await run_pipeline(
            text=_TEXT,
            profile=_PROFILE,
            include_dictionary=False,
            pipeline=replace(
                _pipeline(),
                progress=progress,
            ),
        )

        assert progress.events == [
            "linguistic_analysis",
            "vocabulary_selection",
            "lexical_semantics",
            "level_assessment",
            "winnowing",
        ]


@final
class TestWaitingForTheDictionary:
    def test_a_word_the_dictionary_asks_to_ask_later_is_asked_again_within_the_patience(
        self,
    ) -> None:
        dictionary = _RecoveringDictionary(
            refused=frozenset(
                ("groom",),
            ),
            recovers_at=60.0,
            retry_after_seconds=30.0,
            answers=_pipeline().dictionary,
        )

        (
            result,
            progress,
            ended_at,
        ) = _run_with_patience(
            dictionary=dictionary,
            patience_seconds=180.0,
        )

        (groom,) = (study_item for study_item in result.items if study_item.item.lemma == "groom")

        assert groom.dictionary is not None

        assert groom.dictionary.outcome is LookupOutcome.FOUND

        assert tuple(
            at
            for (
                at,
                lemma,
            ) in dictionary.asked
            if lemma == "groom"
        ) == (
            0.0,
            30.0,
            60.0,
        )

        assert ended_at == 60.0

        assert "waiting 30 s, 180 s left" in progress.events

        assert progress.events[-2:] == [
            "found",
            "winnowing",
        ]

        assert "resumed" in progress.events

    def test_the_progress_names_the_earliest_of_the_waiting_words_and_follows_each_wake(
        self,
    ) -> None:
        # NOTE:
        # `groom` is refused for ten seconds and `drunken` for thirty, in whichever order they are asked: while both
        # sleep, the line names the next try, `groom`'s, and once `groom` asks again it names `drunken`'s, twenty
        # seconds on.
        (
            _,
            progress,
            _,
        ) = _run_with_patience(
            dictionary=_RefusedUntil(
                until={
                    "groom": 10.0,
                    "drunken": 30.0,
                },
                answers=_pipeline().dictionary,
            ),
            patience_seconds=180.0,
        )

        waiting = [
            event
            for event in progress.events
            if event == "resumed"
            or event.startswith(
                "waiting",
            )
        ]

        assert "waiting 10 s, 180 s left" in waiting

        assert waiting[-2:] == [
            "waiting 20 s, 170 s left",
            "resumed",
        ]

    def test_without_patience_the_refused_word_takes_wordnets_gloss_at_once(
        self,
    ) -> None:
        dictionary = _RecoveringDictionary(
            refused=frozenset(
                ("groom",),
            ),
            recovers_at=60.0,
            retry_after_seconds=30.0,
            answers=_pipeline().dictionary,
        )

        (
            result,
            progress,
            ended_at,
        ) = _run_with_patience(
            dictionary=dictionary,
            patience_seconds=0.0,
        )

        (groom,) = (study_item for study_item in result.items if study_item.item.lemma == "groom")

        gloss = groom.gloss

        assert groom.dictionary is not None

        assert groom.dictionary.failure_reason == "circuit_open"

        assert gloss is not None

        assert (
            gloss.source,
            gloss.text,
        ) == (
            GlossSource.LEXICAL_NETWORK,
            _GROOM_SENSE.gloss,
        )

        assert ended_at == 0.0

        assert (
            progress.events.count(
                "unavailable: circuit_open",
            )
            == 1
        )

        assert not any(
            event.startswith(
                "waiting",
            )
            for event in progress.events
        )

    def test_patience_that_cannot_cover_the_next_wait_ends_it_before_it_is_spent(
        self,
    ) -> None:
        dictionary = _RecoveringDictionary(
            refused=frozenset(
                ("groom",),
            ),
            recovers_at=1_000.0,
            retry_after_seconds=30.0,
            answers=_pipeline().dictionary,
        )

        (
            result,
            _,
            ended_at,
        ) = _run_with_patience(
            dictionary=dictionary,
            patience_seconds=100.0,
        )

        (groom,) = (study_item for study_item in result.items if study_item.item.lemma == "groom")

        # NOTE:
        # Ninety seconds spent, ten left, and the next wait would be thirty: the word ends then rather than at a
        # hundred, and never after.
        assert groom.dictionary is not None

        assert groom.dictionary.failure_reason == "circuit_open"

        assert ended_at == 90.0

    def test_a_waiting_word_gives_its_place_to_the_words_that_can_be_answered(
        self,
    ) -> None:
        dictionary = _RecoveringDictionary(
            refused=frozenset(
                ("groom",),
            ),
            recovers_at=60.0,
            retry_after_seconds=30.0,
            answers=_pipeline().dictionary,
        )

        _run_with_patience(
            dictionary=dictionary,
            patience_seconds=180.0,
            concurrency=1,
        )

        # NOTE:
        # One lookup at a time, and the refused word is asked first: had it kept its place while it waited, every
        # other word would have waited a minute behind it.
        assert frozenset(
            at
            for (
                at,
                lemma,
            ) in dictionary.asked
            if lemma != "groom"
        ) == frozenset(
            (0.0,),
        )

    def test_a_word_still_asking_when_the_waiting_time_runs_out_is_let_go_then(
        self,
    ) -> None:
        # NOTE:
        # The word is refused at nought and asks again at ten, when the request it makes hangs for a minute: the
        # waiting time of fifteen seconds ends at fifteen, and the word ends there with the refusal it had rather than
        # when the request gives up at seventy.
        (
            result,
            progress,
            ended_at,
        ) = _run_with_patience(
            dictionary=_HangsWhenAskedAgain(
                lemma="groom",
                retry_after_seconds=10.0,
                hangs_seconds=60.0,
                answers=_pipeline().dictionary,
            ),
            patience_seconds=15.0,
        )

        (groom,) = (study_item for study_item in result.items if study_item.item.lemma == "groom")

        assert groom.dictionary is not None

        assert groom.dictionary.failure_reason == "circuit_open"

        assert ended_at == 15.0

        assert "unavailable: circuit_open" in progress.events

    def test_a_waiting_word_queued_behind_the_answers_after_the_dictionary_came_back_is_answered(
        self,
    ) -> None:
        # NOTE:
        # Both words are turned away until ten, the waiting time is fifteen, and one lookup runs at a time: the first
        # is answered at thirteen, and the second, queued behind it, at sixteen, past the waiting time, because an
        # answer to a waiting word says the dictionary answers again, which lifts the deadline of the words asking.
        (
            result,
            _,
            ended_at,
        ) = _run_with_patience(
            dictionary=_BackAfterAnOutage(
                refused=frozenset(
                    (
                        "groom",
                        "drunken",
                    ),
                ),
                back_at=10.0,
                answer_seconds=3.0,
                answers=_pipeline().dictionary,
            ),
            patience_seconds=15.0,
            concurrency=1,
        )

        outcomes = {
            value: value_object.outcome
            for study_item in result.items
            if (value_object := study_item.dictionary) is not None
            and (value := study_item.item.lemma)
            in {
                "groom",
                "drunken",
            }
        }

        assert outcomes == {
            "groom": LookupOutcome.FOUND,
            "drunken": LookupOutcome.NOT_FOUND,
        }

        assert ended_at == 16.0

    def test_a_provider_down_for_two_minutes_is_waited_out_without_a_request_storm(
        self,
    ) -> None:
        down_until: Final = 120.0

        async def main() -> tuple[PipelineResult, tuple[float, ...]]:
            loop = get_running_loop()

            sent: list[float] = []

            async def handler(
                request: Request,
                /,
            ) -> Response:
                arrived = loop.time()

                sent.append(
                    arrived,
                )

                await asyncio_sleep(
                    delay=0.1,
                )

                if arrived < down_until:
                    return Response(
                        status_code=503,
                    )

                return Response(
                    status_code=200,
                    json=[
                        {
                            "word": "groom",
                            "meanings": [
                                {
                                    "partOfSpeech": "noun",
                                    "definitions": [
                                        {
                                            "definition": "A person who looks after horses.",
                                        },
                                    ],
                                },
                            ],
                        },
                    ],
                )

            client = FreeDictionaryClient(
                base_url="https://dictionary.test/api/v2/entries/en",
                timeout_seconds=30.0,
                requests_per_second=2.0,
                burst=4,
                client=AsyncClient(
                    transport=MockTransport(
                        handler=handler,
                    ),
                ),
                clock=loop.time,
            )

            result = await run_pipeline(
                text=_TEXT,
                profile=_PROFILE,
                include_dictionary=True,
                pipeline=replace(
                    _pipeline(),
                    dictionary=client,
                    dictionary_patience_seconds=180.0,
                    clock=loop.time,
                ),
            )

            return (
                result,
                tuple(
                    sent,
                ),
            )

        (
            result,
            sent,
        ) = run_in_virtual_time(
            main=main,
        )

        during = tuple(at for at in sent if at < down_until)

        # NOTE:
        # Five failed requests open the circuit, and while the provider stays down only a trial asks, ten seconds
        # after the opening and after each trial that fails, less a random quarter at most, so the trials come 7.5
        # seconds apart at the least, and no more than fifteen of them and twenty requests in all reach a provider
        # that answers none of them in two minutes.
        assert all(
            (value_object := study_item.dictionary) is not None and value_object.outcome is LookupOutcome.FOUND
            for study_item in result.items
            if study_item.tier in STUDY_TIERS
        )

        assert (
            len(
                during,
            )
            <= 20
        )

    def test_a_lone_word_in_a_short_outage_is_answered_after_it_unless_the_analysis_fails_fast(
        self,
    ) -> None:
        # NOTE:
        # The one word the dictionary is asked about meets an outage of ten seconds: its three failed attempts are too
        # few to open the circuit, and nothing else was answered, so it waits within the analysis's waiting time, its
        # fifth failure opens the circuit, and the first trial after the outage is answered.
        #
        # Without waiting time it gets WordNet's gloss after its three attempts, as a word always did.
        down_until: Final = 10.0

        async def main(
            patience_seconds: float,
            /,
        ) -> tuple[LookupOutcome | None, int]:
            loop = get_running_loop()

            sent: list[float] = []

            async def handler(
                request: Request,
                /,
            ) -> Response:
                arrived = loop.time()

                sent.append(
                    arrived,
                )

                await asyncio_sleep(
                    delay=0.1,
                )

                if arrived < down_until:
                    return Response(
                        status_code=503,
                    )

                return Response(
                    status_code=200,
                    json=[
                        {
                            "word": "groom",
                            "meanings": [
                                {
                                    "partOfSpeech": "noun",
                                    "definitions": [
                                        {
                                            "definition": "A person who looks after horses.",
                                        },
                                    ],
                                },
                            ],
                        },
                    ],
                )

            client = FreeDictionaryClient(
                base_url="https://dictionary.test/api/v2/entries/en",
                timeout_seconds=30.0,
                requests_per_second=2.0,
                burst=4,
                client=AsyncClient(
                    transport=MockTransport(
                        handler=handler,
                    ),
                ),
                clock=loop.time,
            )

            result = await run_pipeline(
                text="The groom.",
                profile=_PROFILE,
                include_dictionary=True,
                pipeline=replace(
                    _pipeline(),
                    dictionary=client,
                    dictionary_patience_seconds=patience_seconds,
                    clock=loop.time,
                ),
            )

            dictionary = result.items[0].dictionary

            return (
                None if dictionary is None else dictionary.outcome,
                len(
                    sent,
                ),
            )

        (
            waited,
            asked_while_waiting,
        ) = run_in_virtual_time(
            main=lambda: main(
                180.0,
            ),
        )

        (
            failed_fast,
            asked_failing_fast,
        ) = run_in_virtual_time(
            main=lambda: main(
                0.0,
            ),
        )

        assert waited is LookupOutcome.FOUND

        # NOTE:
        # Five failures and one trial, or two when the first comes before the outage ends.
        assert 6 <= asked_while_waiting <= 7

        assert (
            failed_fast,
            asked_failing_fast,
        ) == (
            LookupOutcome.UNAVAILABLE,
            3,
        )

    def test_a_restart_of_the_enrichment_service_costs_no_definition(
        self,
    ) -> None:
        down_until: Final = 3.0

        async def main() -> tuple[
            PipelineResult,
            tuple[float, ...],
        ]:
            loop = get_running_loop()

            refused: list[float] = []

            def handler(
                request: Request,
                /,
            ) -> Response:
                if loop.time() < down_until:
                    refused.append(
                        loop.time(),
                    )

                    raise ConnectError(
                        message="connection refused",
                    )

                return Response(
                    status_code=200,
                    content=to_wire(
                        lookup=DictionaryLookup(
                            outcome=LookupOutcome.FOUND,
                            entries=(
                                DictionaryEntry(
                                    headword=request.url.path.rsplit(
                                        sep="/",
                                        maxsplit=1,
                                    )[-1],
                                    phonetic=None,
                                    meanings=(
                                        Meaning(
                                            word_class="noun",
                                            part_of_speech=PartOfSpeech.NOUN,
                                            definitions=(
                                                Definition(
                                                    text="A word.",
                                                ),
                                            ),
                                        ),
                                    ),
                                ),
                            ),
                        ),
                    ).model_dump_json(),
                )

            dictionary = RemoteDictionary(
                enrichment_url="http://enrichment.test",
                timeout_seconds=180.0,
                client=AsyncClient(
                    transport=MockTransport(
                        handler=handler,
                    ),
                ),
                clock=loop.time,
            )

            result = await run_pipeline(
                text=_TEXT,
                profile=_PROFILE,
                include_dictionary=True,
                pipeline=replace(
                    _pipeline(),
                    dictionary=dictionary,
                    dictionary_patience_seconds=180.0,
                    clock=loop.time,
                ),
            )

            return (
                result,
                tuple(
                    refused,
                ),
            )

        (
            result,
            refused,
        ) = run_in_virtual_time(
            main=main,
        )

        # NOTE:
        # The service refuses every connection for its first three seconds, as it does while it restarts: each word it
        # refused is asked again after as long as it has been unreachable, a second at the least, and every word to
        # study is answered once it is back, rather than taking WordNet's gloss at the first refusal.
        assert refused

        assert all(
            study_item.dictionary is not None and study_item.dictionary.outcome is LookupOutcome.FOUND
            for study_item in result.items
            if study_item.tier in STUDY_TIERS
        )

    def test_a_provider_that_never_answers_delays_the_analysis_by_the_waiting_time_at_most(
        self,
    ) -> None:
        # NOTE:
        # Every request hangs for the whole thirty seconds the client allows it, so a trial costs as much as a wait:
        # the time the words spend asking again counts toward the waiting time, and the analysis ends no later than
        # one that fails fast by the waiting time and the one request a word still asking when it runs out may take;
        # counting only the sleeps, it ended twice the waiting time later.
        def ended_after(
            *,
            patience_seconds: float,
        ) -> float:
            async def main() -> float:
                loop = get_running_loop()

                async def handler(
                    request: Request,
                    /,
                ) -> Response:
                    await asyncio_sleep(
                        delay=30.0,
                    )

                    raise ReadTimeout(
                        message="the provider never answered",
                        request=request,
                    )

                client = FreeDictionaryClient(
                    base_url="https://dictionary.test/api/v2/entries/en",
                    timeout_seconds=30.0,
                    requests_per_second=2.0,
                    burst=4,
                    client=AsyncClient(
                        transport=MockTransport(
                            handler=handler,
                        ),
                    ),
                    clock=loop.time,
                )

                await run_pipeline(
                    text=_TEXT,
                    profile=_PROFILE,
                    include_dictionary=True,
                    pipeline=replace(
                        _pipeline(),
                        dictionary=client,
                        dictionary_patience_seconds=patience_seconds,
                        clock=loop.time,
                    ),
                )

                return loop.time()

            return run_in_virtual_time(
                main=main,
            )

        fast = ended_after(
            patience_seconds=0.0,
        )

        patient = ended_after(
            patience_seconds=120.0,
        )

        assert patient - fast <= 120.0 + 30.0


@final
class TestProcessAnAnalysis:
    async def test_a_requested_analysis_is_completed_stored_and_announced(
        self,
    ) -> None:
        storage = InMemoryStorage()

        analysis = await request_an_analysis(
            document=_DOCUMENT,
            profile=_PROFILE,
            options=AnalysisOptions(),
            new_unit_of_work=storage.new_unit_of_work,
            publisher=RecordingPublisher(),
            clock=FakeClock(
                at=EPOCH,
            ),
        )

        publisher = RecordingPublisher()

        processed = await process_an_analysis(
            analysis_id=analysis.id,
            pipeline=_pipeline(),
            new_unit_of_work=storage.new_unit_of_work,
            publisher=publisher,
            clock=FakeClock(
                at=EPOCH
                + timedelta(
                    seconds=1,
                ),
            ),
        )

        assert not processed.redelivered

        assert processed.analysis.status is AnalysisStatus.COMPLETED

        assert processed.analysis.queue_wait_seconds == 1.0

        stored = storage.analyses[analysis.id]

        assert stored.status is AnalysisStatus.COMPLETED

        assert len(
            stored.items,
        ) == len(
            processed.analysis.items,
        )

        assert _event_types(
            publisher,
        ) == (AnalysisCompleted,)

    async def test_processing_twice_returns_the_stored_result_and_republishes_the_same_completion(
        self,
    ) -> None:
        storage = InMemoryStorage()

        clock = FakeClock(
            at=EPOCH,
        )

        analysis = await request_an_analysis(
            document=_DOCUMENT,
            profile=_PROFILE,
            options=AnalysisOptions(),
            new_unit_of_work=storage.new_unit_of_work,
            publisher=RecordingPublisher(),
            clock=clock,
        )

        publisher = RecordingPublisher()

        first = await process_an_analysis(
            analysis_id=analysis.id,
            pipeline=_pipeline(),
            new_unit_of_work=storage.new_unit_of_work,
            publisher=publisher,
            clock=clock,
        )

        second = await process_an_analysis(
            analysis_id=analysis.id,
            pipeline=_pipeline(),
            new_unit_of_work=storage.new_unit_of_work,
            publisher=publisher,
            clock=clock,
        )

        assert second.redelivered

        assert not first.redelivered

        assert second.analysis.finished_at == first.analysis.finished_at

        assert _event_types(
            publisher,
        ) == (
            AnalysisCompleted,
            AnalysisCompleted,
        )

        assert publisher.published[1] == publisher.published[0]

    async def test_an_analysis_interrupted_mid_processing_is_processed_again(
        self,
    ) -> None:
        storage = InMemoryStorage()

        clock = FakeClock(
            at=EPOCH,
        )

        analysis = await request_an_analysis(
            document=_DOCUMENT,
            profile=_PROFILE,
            options=AnalysisOptions(),
            new_unit_of_work=storage.new_unit_of_work,
            publisher=RecordingPublisher(),
            clock=clock,
        )

        async with storage.new_unit_of_work() as uow:
            stored = await uow.analyses.get(
                analysis_id=analysis.id,
            )

            assert stored is not None

            stored.start(
                at=EPOCH,
            )

            await uow.analyses.save(
                analysis=stored,
            )

            await uow.commit()

        processed = await process_an_analysis(
            analysis_id=analysis.id,
            pipeline=_pipeline(),
            new_unit_of_work=storage.new_unit_of_work,
            publisher=RecordingPublisher(),
            clock=clock,
        )

        assert processed.analysis.status is AnalysisStatus.COMPLETED

    async def test_a_completion_whose_message_was_lost_is_announced_when_delivered_again(
        self,
    ) -> None:
        storage = InMemoryStorage()

        clock = FakeClock(
            at=EPOCH,
        )

        analysis = await request_an_analysis(
            document=_DOCUMENT,
            profile=_PROFILE,
            options=AnalysisOptions(),
            new_unit_of_work=storage.new_unit_of_work,
            publisher=RecordingPublisher(),
            clock=clock,
        )

        # NOTE:
        # The result is committed and the broker then refuses the completed message, the window a worker can also die
        # in; the requested message is not committed, so it is delivered again.
        with raises(
            expected_exception=MessagingUnavailableError,
        ):
            await process_an_analysis(
                analysis_id=analysis.id,
                pipeline=_pipeline(),
                new_unit_of_work=storage.new_unit_of_work,
                publisher=RaisingPublisher(),
                clock=clock,
            )

        assert storage.analyses[analysis.id].status is AnalysisStatus.COMPLETED

        publisher = RecordingPublisher()

        processed = await process_an_analysis(
            analysis_id=analysis.id,
            pipeline=_pipeline(),
            new_unit_of_work=storage.new_unit_of_work,
            publisher=publisher,
            clock=clock,
        )

        assert processed.redelivered

        assert _event_types(
            publisher,
        ) == (AnalysisCompleted,)

    async def test_a_pipeline_failure_is_recorded_and_re_raised_without_an_event(
        self,
    ) -> None:
        storage = InMemoryStorage()

        clock = FakeClock(
            at=EPOCH,
        )

        analysis = await request_an_analysis(
            document=_DOCUMENT,
            profile=_PROFILE,
            options=AnalysisOptions(),
            new_unit_of_work=storage.new_unit_of_work,
            publisher=RecordingPublisher(),
            clock=clock,
        )

        broken = Pipeline(
            linguistics=_BrokenLinguisticAnalyzer(),
            frequency=FakeWordFrequency(),
            reference_lexicon=FakeReferenceLexicon(),
            level_estimator=FakeLevelEstimator(),
            dictionary=FakeDictionary(),
            lexical_semantics=FakeLexicalSemantics(),
        )

        publisher = RecordingPublisher()

        with raises(
            expected_exception=RuntimeError,
            match="tagger crashed",
        ):
            await process_an_analysis(
                analysis_id=analysis.id,
                pipeline=broken,
                new_unit_of_work=storage.new_unit_of_work,
                publisher=publisher,
                clock=clock,
            )

        stored = storage.analyses[analysis.id]

        assert stored.status is AnalysisStatus.FAILED

        assert stored.failure_reason == "RuntimeError: tagger crashed"

        assert (
            _event_types(
                publisher,
            )
            == ()
        )

    async def test_an_unknown_id_is_a_not_found_error(
        self,
    ) -> None:
        with raises(
            expected_exception=AnalysisNotFoundError,
        ):
            await process_an_analysis(
                analysis_id=AnalysisId.new(),
                pipeline=_pipeline(),
                new_unit_of_work=InMemoryStorage().new_unit_of_work,
                publisher=RecordingPublisher(),
                clock=FakeClock(
                    at=EPOCH,
                ),
            )
