"""
The pipeline, end to end against fakes.

The text, the tables the fakes answer from, and the expected study items are
all written out, so the test reads as a worked example of what the pipeline
does.
"""

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
from wordwinnow.application.errors import (
    AnalysisNotFoundError,
    MessagingUnavailableError,
)
from wordwinnow.application.use_cases.process_an_analysis import (
    Pipeline,
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
    StudyTier,
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
                {
                    "Holmes",
                },
            ),
            function_words=frozenset(
                {
                    "the",
                },
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
                    {
                        "become",
                    },
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

        assert {features.category for features in estimator.seen} == {
            None,
        }

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
            "become",
            "drunken",
            "groom",
            "saunter",
            "slowly",
        ]

        assert progress.events == [
            "linguistic_analysis",
            "vocabulary_selection",
            "lexical_semantics",
            "level_assessment",
            "dictionary_enrichment of 5",
            "not_found",
            "not_found",
            "not_found",
            "not_found",
            "not_found",
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
