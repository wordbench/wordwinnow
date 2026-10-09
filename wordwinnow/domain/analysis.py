"""
An analysis: one document, one learner profile, one lifecycle, one result.

An analysis is the aggregate of this domain.

It is requested, it starts, and it either completes with study items and stage
timings or fails with a reason; it records the two events that other parts of
the system react to, and a failure is state rather than an event because
nothing reacts to one.

The stage timings are data about the analysis, not events.
"""

from collections.abc import (
    Sequence,
)
from dataclasses import (
    dataclass,
    field,
)
from datetime import (
    datetime,
)
from enum import (
    auto,
)
from typing import (
    final,
    override,
)

from wordwinnow.domain.document import (
    Document,
)
from wordwinnow.domain.enums import (
    UnorderedStrEnum,
)
from wordwinnow.domain.identifiers import (
    AnalysisId,
)
from wordwinnow.domain.learner import (
    LearnerProfile,
)
from wordwinnow.domain.study import (
    StudyItem,
)
from wordwinnow.domain.time import (
    aware_datetime,
)


@final
class InvalidAnalysisError(
    ValueError,
):
    """
    Raised when an analysis would contain invalid data.
    """


@final
class AnalysisTransitionError(
    ValueError,
):
    """
    Raised when an analysis is asked to do something its status forbids.
    """


class AnalysisStatus(
    UnorderedStrEnum,
):
    """
    The lifecycle of an analysis.
    """

    REQUESTED = auto()

    PROCESSING = auto()

    COMPLETED = auto()

    FAILED = auto()


class Stage(
    UnorderedStrEnum,
):
    """
    The stages of processing, in pipeline order.
    """

    LINGUISTIC_ANALYSIS = auto()

    VOCABULARY_SELECTION = auto()

    # NOTE:
    # Senses come before levels because what the lexical network knows about a word is part of what the level model
    # sees.
    LEXICAL_SEMANTICS = auto()

    LEVEL_ASSESSMENT = auto()

    DICTIONARY_ENRICHMENT = auto()

    WINNOWING = auto()


@dataclass(
    frozen=True,
    kw_only=True,
    slots=True,
)
@final
class StageTiming:
    """
    How long one stage took, in seconds.
    """

    stage: Stage

    seconds: float

    def __post_init__(
        self,
    ) -> None:
        if self.seconds < 0:
            raise InvalidAnalysisError(
                f"a stage timing requires a non-negative duration, got {self.seconds} for {self.stage}",
            )


@dataclass(
    frozen=True,
    kw_only=True,
    slots=True,
)
@final
class AnalysisOptions:
    """
    Choices that shape one analysis without describing the learner.
    """

    include_dictionary: bool = True


@dataclass(
    frozen=True,
    kw_only=True,
    slots=True,
)
@final
class AnalysisRequested:
    """
    A learner asked for a document to be analyzed.
    """

    analysis_id: AnalysisId

    occurred_at: datetime


@dataclass(
    frozen=True,
    kw_only=True,
    slots=True,
)
@final
class AnalysisCompleted:
    """
    An analysis produced its study items.
    """

    analysis_id: AnalysisId

    occurred_at: datetime

    item_count: int


type AnalysisEvent = AnalysisRequested | AnalysisCompleted


# NOTE:
# `eq=False` is what makes the entity identity below reachable.
#
# A dataclass generates a structural `__eq__` by default, and a generated method defined in the class body would
# shadow the one written underneath it.
@dataclass(
    eq=False,
    kw_only=True,
    slots=True,
)
@final
class Analysis:
    """
    One analysis of one document for one learner profile.

    Two analyses are the same analysis exactly when their ids match.
    """

    id: AnalysisId

    document: Document

    profile: LearnerProfile

    options: AnalysisOptions

    requested_at: datetime

    _status: AnalysisStatus = field(
        default=AnalysisStatus.REQUESTED,
        init=False,
    )

    _started_at: datetime | None = field(
        default=None,
        init=False,
    )

    _finished_at: datetime | None = field(
        default=None,
        init=False,
    )

    _failure_reason: str | None = field(
        default=None,
        init=False,
    )

    _stage_timings: tuple[StageTiming, ...] = field(
        default=(),
        init=False,
    )

    _items: tuple[StudyItem, ...] = field(
        default=(),
        init=False,
    )

    _events: list[AnalysisEvent] = field(
        default_factory=list,
        init=False,
    )

    def __post_init__(
        self,
    ) -> None:
        self.requested_at = aware_datetime(
            value=self.requested_at,
            field_name="requested_at",
        )

    @property
    def status(
        self,
    ) -> AnalysisStatus:
        return self._status

    @property
    def started_at(
        self,
    ) -> datetime | None:
        return self._started_at

    @property
    def finished_at(
        self,
    ) -> datetime | None:
        return self._finished_at

    @property
    def failure_reason(
        self,
    ) -> str | None:
        return self._failure_reason

    @property
    def stage_timings(
        self,
    ) -> tuple[StageTiming, ...]:
        return self._stage_timings

    @property
    def items(
        self,
    ) -> tuple[StudyItem, ...]:
        return self._items

    @property
    def queue_wait_seconds(
        self,
    ) -> float | None:
        """
        How long the analysis waited between being requested and starting.
        """

        if self._started_at is None:
            return None

        return (self._started_at - self.requested_at).total_seconds()

    @property
    def expires_at(
        self,
    ) -> datetime | None:
        """
        When the analysis must be deleted because its text may no longer be
        kept, or `None` for a text the learner owns.
        """

        if self.document.retention is None:
            return None

        return self.requested_at + self.document.retention

    def is_expired(
        self,
        *,
        at: datetime,
    ) -> bool:
        """
        Whether the analysis must be deleted at the instant given.
        """

        expires_at = self.expires_at

        return expires_at is not None and expires_at <= at

    def start(
        self,
        *,
        at: datetime,
    ) -> None:
        """
        Record that processing began.
        """

        if self._status is not AnalysisStatus.REQUESTED:
            raise AnalysisTransitionError(
                f"analysis {self.id} is {self._status}; only a requested analysis can start",
            )

        self._status = AnalysisStatus.PROCESSING

        self._started_at = aware_datetime(
            value=at,
            field_name="start() at",
        )

    def complete(
        self,
        *,
        at: datetime,
        items: Sequence[StudyItem],
        stage_timings: Sequence[StageTiming],
    ) -> None:
        """
        Record the result of processing.
        """

        if self._status is not AnalysisStatus.PROCESSING:
            raise AnalysisTransitionError(
                f"analysis {self.id} is {self._status}; only a processing analysis can complete",
            )

        self._status = AnalysisStatus.COMPLETED

        self._finished_at = aware_datetime(
            value=at,
            field_name="complete() at",
        )

        self._items = tuple(
            items,
        )

        self._stage_timings = tuple(
            stage_timings,
        )

        self._events.append(
            self.completion(),
        )

    def completion(
        self,
    ) -> AnalysisCompleted:
        """
        The event that records this analysis's completion, rebuilt from its
        state, so it can be published again.
        """

        if self._status is not AnalysisStatus.COMPLETED or self._finished_at is None:
            raise AnalysisTransitionError(
                f"analysis {self.id} is {self._status}; only a completed analysis has a completion",
            )

        return AnalysisCompleted(
            analysis_id=self.id,
            occurred_at=self._finished_at,
            item_count=len(
                self._items,
            ),
        )

    def fail(
        self,
        *,
        at: datetime,
        reason: str,
    ) -> None:
        """
        Record that processing could not finish.

        An analysis may fail from either of its two live states, because
        processing can be interrupted before it starts.
        """

        if self._status not in {
            AnalysisStatus.REQUESTED,
            AnalysisStatus.PROCESSING,
        }:
            raise AnalysisTransitionError(
                f"analysis {self.id} is {self._status}; only a requested or processing analysis can fail",
            )

        if not reason.strip():
            raise InvalidAnalysisError(
                "a failure requires a non-blank reason",
            )

        self._status = AnalysisStatus.FAILED

        self._finished_at = aware_datetime(
            value=at,
            field_name="fail() at",
        )

        self._failure_reason = reason

    def reopen(
        self,
        *,
        at: datetime,
    ) -> None:
        """
        Put a failed analysis back in the queue.

        Only a failure is reversible; a completed analysis is a result.
        """

        if self._status is not AnalysisStatus.FAILED:
            raise AnalysisTransitionError(
                f"analysis {self.id} is {self._status}; only a failed analysis can be reopened",
            )

        self._status = AnalysisStatus.REQUESTED

        self._started_at = None

        self._finished_at = None

        self._failure_reason = None

        self._events.append(
            AnalysisRequested(
                analysis_id=self.id,
                occurred_at=aware_datetime(
                    value=at,
                    field_name="reopen() at",
                ),
            ),
        )

    def pull_events(
        self,
    ) -> tuple[AnalysisEvent, ...]:
        """
        Hand over the events recorded since the last pull.
        """

        events = tuple(
            self._events,
        )

        self._events.clear()

        return events

    @override
    def __eq__(
        self,
        other: object,
    ) -> bool:
        if not isinstance(
            other,
            Analysis,
        ):
            return NotImplemented

        return self.id == other.id

    @override
    def __hash__(
        self,
    ) -> int:
        return hash(
            self.id,
        )


def request_an_analysis(
    *,
    document: Document,
    profile: LearnerProfile,
    options: AnalysisOptions,
    at: datetime,
) -> Analysis:
    """
    Ask for a document to be analyzed for a learner.
    """

    analysis = Analysis(
        id=AnalysisId.new(),
        document=document,
        profile=profile,
        options=options,
        requested_at=at,
    )

    analysis._events.append(
        AnalysisRequested(
            analysis_id=analysis.id,
            occurred_at=analysis.requested_at,
        ),
    )

    return analysis


def reconstitute(
    *,
    id: AnalysisId,
    document: Document,
    profile: LearnerProfile,
    options: AnalysisOptions,
    requested_at: datetime,
    status: AnalysisStatus,
    started_at: datetime | None,
    finished_at: datetime | None,
    failure_reason: str | None,
    stage_timings: Sequence[StageTiming],
    items: Sequence[StudyItem],
) -> Analysis:
    """
    Rebuild an analysis from a record of its state, the store's or the intake
    service's answer, without replaying its events.

    An event that already happened is not recorded again.
    """

    analysis = Analysis(
        id=id,
        document=document,
        profile=profile,
        options=options,
        requested_at=requested_at,
    )

    if status is AnalysisStatus.REQUESTED:
        return analysis

    if started_at is None and status is not AnalysisStatus.FAILED:
        raise InvalidAnalysisError(
            f"a {status} analysis requires started_at",
        )

    analysis._status = status

    analysis._started_at = (
        aware_datetime(
            value=started_at,
            field_name="started_at",
        )
        if started_at is not None
        else None
    )

    if status is AnalysisStatus.PROCESSING:
        return analysis

    if finished_at is None:
        raise InvalidAnalysisError(
            f"a {status} analysis requires finished_at",
        )

    analysis._finished_at = aware_datetime(
        value=finished_at,
        field_name="finished_at",
    )

    if status is AnalysisStatus.FAILED:
        if not failure_reason:
            raise InvalidAnalysisError(
                "a failed analysis requires a failure reason",
            )

        analysis._failure_reason = failure_reason

        return analysis

    analysis._stage_timings = tuple(
        stage_timings,
    )

    analysis._items = tuple(
        items,
    )

    return analysis
