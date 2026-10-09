"""
In-memory and recording doubles for the application ports.
"""

from collections.abc import (
    Mapping,
    Sequence,
)
from collections.abc import (
    Set as AbstractSet,
)
from copy import (
    deepcopy,
)
from datetime import (
    UTC,
    datetime,
)
from types import (
    MappingProxyType,
    TracebackType,
)
from typing import (
    Final,
    Self,
    final,
)

from wordwinnow.application.dto import (
    AnalysisSummary,
    ProcessingProgress,
)
from wordwinnow.application.errors import (
    MessagingUnavailableError,
    SourceUnavailableError,
)
from wordwinnow.domain.analysis import (
    Analysis,
    AnalysisEvent,
    AnalysisStatus,
    Stage,
)
from wordwinnow.domain.cefr import (
    CefrLevel,
)
from wordwinnow.domain.dictionary import (
    DictionaryLookup,
    LookupOutcome,
)
from wordwinnow.domain.difficulty import (
    LexicalFeatures,
)
from wordwinnow.domain.document import (
    Document,
)
from wordwinnow.domain.identifiers import (
    AnalysisId,
)
from wordwinnow.domain.language import (
    PartOfSpeech,
    Sentence,
    Token,
)
from wordwinnow.domain.senses import (
    LexicalSense,
)
from wordwinnow.infrastructure.lexicon.reference_lists import (
    ReferenceEntry,
)

# NOTE:
# A pure reference instant with no product meaning of its own.
EPOCH: Final = datetime(
    year=1970,
    month=1,
    day=1,
    tzinfo=UTC,
)


@final
class FakeClock:
    """
    A clock that always answers the instant it was given.
    """

    def __init__(
        self,
        *,
        at: datetime,
    ) -> None:
        self._at: Final = at

    def now(
        self,
    ) -> datetime:
        return self._at


@final
class InMemoryAnalysisRepository:
    """
    Analyses kept in memory, shared by every unit of work, with what workers
    reported about the ones processing.
    """

    def __init__(
        self,
        *,
        storage: dict[AnalysisId, Analysis],
        progress: dict[AnalysisId, ProcessingProgress],
    ) -> None:
        self._storage: Final = storage

        self._progress: Final = progress

    async def add(
        self,
        *,
        analysis: Analysis,
    ) -> None:
        self._storage[analysis.id] = deepcopy(
            x=analysis,
        )

        self._progress.pop(
            analysis.id,
            None,
        )

    async def get(
        self,
        *,
        analysis_id: AnalysisId,
    ) -> Analysis | None:
        stored = self._storage.get(
            analysis_id,
        )

        return (
            deepcopy(
                x=stored,
            )
            if stored is not None
            else None
        )

    async def save(
        self,
        *,
        analysis: Analysis,
    ) -> None:
        self._storage[analysis.id] = deepcopy(
            x=analysis,
        )

        self._progress.pop(
            analysis.id,
            None,
        )

    async def record_progress(
        self,
        *,
        analysis_id: AnalysisId,
        progress: ProcessingProgress,
    ) -> None:
        stored = self._storage.get(
            analysis_id,
        )

        if stored is not None and stored.status is AnalysisStatus.PROCESSING:
            self._progress[analysis_id] = progress

    async def progress_of(
        self,
        *,
        analysis_id: AnalysisId,
    ) -> ProcessingProgress | None:
        stored = self._storage.get(
            analysis_id,
        )

        if stored is None or stored.status is not AnalysisStatus.PROCESSING:
            return None

        return self._progress.get(
            analysis_id,
        )

    async def list_recent(
        self,
        *,
        limit: int,
    ) -> tuple[AnalysisSummary, ...]:
        ordered = sorted(
            self._storage.values(),
            key=lambda analysis: analysis.requested_at,
            reverse=True,
        )

        return tuple(
            AnalysisSummary(
                analysis_id=analysis.id,
                title=analysis.document.title,
                origin=analysis.document.origin,
                learner_level=analysis.profile.level,
                status=analysis.status,
                requested_at=analysis.requested_at,
                item_count=len(
                    analysis.items,
                ),
            )
            for analysis in ordered[:limit]
        )

    async def list_stale(
        self,
        *,
        stale_before: datetime,
    ) -> tuple[Analysis, ...]:
        return tuple(
            deepcopy(
                x=analysis,
            )
            for analysis in self._storage.values()
            if (analysis.status is AnalysisStatus.REQUESTED and analysis.requested_at < stale_before)
            or (
                analysis.status is AnalysisStatus.PROCESSING
                and analysis.started_at is not None
                and analysis.started_at < stale_before
            )
        )

    async def delete_expired(
        self,
        *,
        now: datetime,
    ) -> int:
        expired = tuple(
            analysis_id
            for (
                analysis_id,
                analysis,
            ) in self._storage.items()
            if analysis.is_expired(
                at=now,
            )
        )

        for analysis_id in expired:
            del self._storage[analysis_id]

        return len(
            expired,
        )


@final
class InMemoryUnitOfWork:
    """
    A unit of work whose changes are visible only after `commit()`.

    Entering the block snapshots the shared storage; leaving without a commit
    restores the snapshot, which is what a rollback does.
    """

    def __init__(
        self,
        *,
        storage: dict[AnalysisId, Analysis],
        progress: dict[AnalysisId, ProcessingProgress],
    ) -> None:
        self._storage: Final = storage

        self._progress: Final = progress

        self._snapshot: dict[AnalysisId, Analysis] = {}

        self._progress_snapshot: dict[AnalysisId, ProcessingProgress] = {}

        self._committed = False

        self._analyses: InMemoryAnalysisRepository | None = None

    @property
    def analyses(
        self,
    ) -> InMemoryAnalysisRepository:
        if self._analyses is None:
            raise RuntimeError(
                "the unit of work is not open; use it with `async with`",
            )

        return self._analyses

    async def __aenter__(
        self,
    ) -> Self:
        self._snapshot = dict(
            self._storage,
        )

        self._progress_snapshot = dict(
            self._progress,
        )

        self._committed = False

        self._analyses = InMemoryAnalysisRepository(
            storage=self._storage,
            progress=self._progress,
        )

        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
        /,
    ) -> None:
        if not self._committed:
            await self.rollback()

        self._analyses = None

    async def commit(
        self,
    ) -> None:
        self._committed = True

    async def rollback(
        self,
    ) -> None:
        self._storage.clear()

        self._storage.update(
            self._snapshot,
        )

        self._progress.clear()

        self._progress.update(
            self._progress_snapshot,
        )


@final
class InMemoryStorage:
    """
    One shared in-memory store and the factory that scopes units of work over
    it.
    """

    def __init__(
        self,
    ) -> None:
        self.analyses: Final[dict[AnalysisId, Analysis]] = {}

        self.progress: Final[dict[AnalysisId, ProcessingProgress]] = {}

    def new_unit_of_work(
        self,
    ) -> InMemoryUnitOfWork:
        return InMemoryUnitOfWork(
            storage=self.analyses,
            progress=self.progress,
        )


@final
class RecordingPublisher:
    """
    Keeps every published event instead of sending it.
    """

    def __init__(
        self,
    ) -> None:
        self.published: Final[list[tuple[AnalysisId, AnalysisEvent]]] = []

    async def publish(
        self,
        *,
        analysis: Analysis,
        event: AnalysisEvent,
    ) -> None:
        self.published.append(
            (
                analysis.id,
                event,
            ),
        )


@final
class RaisingPublisher:
    """
    Fails every publish, the way an unreachable broker would.
    """

    async def publish(
        self,
        *,
        analysis: Analysis,
        event: AnalysisEvent,
    ) -> None:
        raise MessagingUnavailableError(
            "the message broker could not be reached to publish the event (broker unreachable)",
        )


@final
class FakeLinguisticAnalyzer:
    """
    Splits on periods and spaces, tags and lemmatizes every word from fixed
    tables.

    Words absent from the tables are nouns that are their own lemma, so a test
    can build a text without describing every token.
    """

    def __init__(
        self,
        *,
        parts_of_speech: Mapping[str, PartOfSpeech | None] | None = None,
        lemmas: Mapping[str, str] | None = None,
        proper_nouns: AbstractSet[str] = frozenset(),
        function_words: AbstractSet[str] = frozenset(),
    ) -> None:
        self._parts_of_speech: Final = MappingProxyType(
            mapping=dict(
                parts_of_speech or {},
            ),
        )

        self._lemmas: Final = MappingProxyType(
            mapping=dict(
                lemmas or {},
            ),
        )

        self._proper_nouns: Final = frozenset(
            proper_nouns,
        )

        self._function_words: Final = frozenset(
            function_words,
        )

    @property
    def function_words(
        self,
    ) -> frozenset[str]:
        return self._function_words

    def analyze(
        self,
        *,
        text: str,
    ) -> tuple[Sentence, ...]:
        sentences: list[Sentence] = []

        parts = text.split(
            sep=".",
        )

        for (
            index,
            raw,
        ) in enumerate(
            iterable=tuple(stripped for part in parts if (stripped := part.strip())),
        ):
            tokens = tuple(
                Token(
                    surface=word,
                    lemma=self._lemmas.get(
                        word.lower(),
                        word.lower(),
                    ),
                    part_of_speech=self._parts_of_speech.get(
                        word.lower(),
                        PartOfSpeech.NOUN,
                    ),
                    is_proper_noun=word in self._proper_nouns,
                )
                for word in raw.split()
            )

            sentences.append(
                Sentence(
                    index=index,
                    text=f"{raw}.",
                    tokens=tokens,
                ),
            )

        return tuple(
            sentences,
        )


@final
class FakeWordFrequency:
    """
    Zipf frequencies from a table, 0.0 for anything else.
    """

    def __init__(
        self,
        *,
        frequencies: Mapping[str, float] | None = None,
    ) -> None:
        self._frequencies: Final = MappingProxyType(
            mapping=dict(
                frequencies or {},
            ),
        )

    def zipf(
        self,
        *,
        lemma: str,
    ) -> float:
        return self._frequencies.get(
            lemma,
            0.0,
        )


@final
class FakeReferenceLexicon:
    """
    Levels from a table keyed by lemma and part of speech.
    """

    def __init__(
        self,
        *,
        levels: Mapping[tuple[str, PartOfSpeech], CefrLevel] | None = None,
    ) -> None:
        self._levels: Final = MappingProxyType(
            mapping=dict(
                levels or {},
            ),
        )

    def level_of(
        self,
        *,
        lemma: str,
        part_of_speech: PartOfSpeech,
    ) -> CefrLevel | None:
        return self._levels.get(
            (
                lemma,
                part_of_speech,
            ),
        )

    def entries(
        self,
    ) -> tuple[ReferenceEntry, ...]:
        return tuple(
            ReferenceEntry(
                lemma=lemma,
                part_of_speech=part_of_speech,
                level=level,
                source="fake",
            )
            for (
                (
                    lemma,
                    part_of_speech,
                ),
                level,
            ) in self._levels.items()
        )


@final
class FakeLevelEstimator:
    """
    Predicts one fixed level for every word, or nothing at all.
    """

    def __init__(
        self,
        *,
        level: CefrLevel | None = None,
    ) -> None:
        self._level: Final = level

        self.seen: Final[list[LexicalFeatures]] = []

    def estimate_many(
        self,
        *,
        features: Sequence[LexicalFeatures],
    ) -> tuple[CefrLevel | None, ...]:
        self.seen.extend(
            features,
        )

        return tuple(self._level for _ in features)


@final
class FakeDictionary:
    """
    Answers from a table of lookups, `NOT_FOUND` for anything else, and
    records the order of lookups.
    """

    def __init__(
        self,
        *,
        lookups: Mapping[str, DictionaryLookup] | None = None,
    ) -> None:
        self._lookups: Final = MappingProxyType(
            mapping=dict(
                lookups or {},
            ),
        )

        self.looked_up: Final[list[str]] = []

    async def look_up(
        self,
        *,
        lemma: str,
    ) -> DictionaryLookup:
        self.looked_up.append(
            lemma,
        )

        return self._lookups.get(
            lemma,
            DictionaryLookup(
                outcome=LookupOutcome.NOT_FOUND,
            ),
        )


@final
class RecordingProgress:
    """
    Records what the pipeline reports: each stage by name, followed by its
    steps when it named them, and each lookup by its outcome, followed by its
    failure reason when it has one.
    """

    def __init__(
        self,
    ) -> None:
        self.events: Final[list[str]] = []

    def stage_started(
        self,
        *,
        stage: Stage,
        steps: int | None,
    ) -> None:
        self.events.append(
            f"{stage}" if steps is None else f"{stage} of {steps}",
        )

    def looked_up(
        self,
        *,
        lookup: DictionaryLookup,
    ) -> None:
        self.events.append(
            f"{lookup.outcome}" if lookup.failure_reason is None else f"{lookup.outcome}: {lookup.failure_reason}",
        )


@final
class FakeLexicalSemantics:
    """
    Senses from a table keyed by lemma and part of speech.
    """

    def __init__(
        self,
        *,
        senses: Mapping[tuple[str, PartOfSpeech], Sequence[LexicalSense]] | None = None,
    ) -> None:
        self._senses: Final = MappingProxyType(
            mapping=dict(
                senses or {},
            ),
        )

    def senses_of(
        self,
        *,
        lemma: str,
        part_of_speech: PartOfSpeech,
    ) -> tuple[LexicalSense, ...]:
        return tuple(
            self._senses.get(
                (
                    lemma,
                    part_of_speech,
                ),
                (),
            ),
        )


@final
class FakeDocumentSource:
    """
    Returns one fixed document for any topic.
    """

    def __init__(
        self,
        *,
        document: Document,
    ) -> None:
        self._document: Final = document

        self.requests: Final[list[tuple[str, int]]] = []

    async def acquire(
        self,
        *,
        topic: str,
        limit: int,
    ) -> Document:
        self.requests.append(
            (
                topic,
                limit,
            ),
        )

        return self._document


@final
class UnavailableDocumentSource:
    """
    A source that cannot be reached.
    """

    async def acquire(
        self,
        *,
        topic: str,
        limit: int,
    ) -> Document:
        raise SourceUnavailableError(
            f"the source could not be reached for {topic!r}",
        )
