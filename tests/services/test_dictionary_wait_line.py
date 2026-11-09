"""
What the progress line says while words wait for the dictionary, scenario by
scenario, with the real client, circuit breaker, and line on virtual time.

Every scenario runs the dictionary stage, `_look_up_all`, against a provider
faked with a mock transport that answers after a tenth of a second, with the
random generator seeded.

The line is sampled at every whole second, after every lookup, and after every
report of a wait, and the same events feed a stored report that a second line
draws, so the command line and the distributed mode are compared at the same
instants.
"""

from asyncio import (
    Event,
    Lock,
    Task,
    create_task,
    gather,
    get_running_loop,
)
from asyncio import (
    sleep as asyncio_sleep,
)
from collections.abc import (
    Callable,
    Mapping,
    Sequence,
)
from dataclasses import (
    dataclass,
    field,
    replace,
)
from datetime import (
    datetime,
    timedelta,
)
from hashlib import (
    sha256,
)
from io import (
    StringIO,
)
from itertools import (
    pairwise,
)
from math import (
    ceil,
)
from random import (
    Random,
    getstate,
    seed,
    setstate,
)
from re import (
    compile as compile_pattern,
)
from re import (
    findall,
)
from types import (
    MappingProxyType,
)
from typing import (
    Final,
    final,
)
from unittest.mock import (
    patch,
)

from httpx2 import (
    ASGITransport,
    AsyncClient,
    MockTransport,
    Request,
    Response,
)
from pydantic import (
    BaseModel,
)
from pytest import (
    approx,
    mark,
)
from rich.console import (
    Console,
)
from rich.progress import (
    BarColumn,
)

from tests.application.test_process_an_analysis import (
    _pipeline as stage_pipeline,
)
from tests.fakes.ports import (
    EPOCH,
    InMemoryStorage,
)
from tests.fakes.progress import (
    held_words,
)
from tests.fakes.virtual_time import (
    run_in_virtual_time,
)
from tests.infrastructure.messaging.analyses import (
    requested_analysis,
)
from wordwinnow.application.dto import (
    DictionaryWait,
    ProcessingProgress,
)
from wordwinnow.application.ports.pipeline_progress import (
    HeldWords,
)
from wordwinnow.application.use_cases.process_an_analysis import (
    _look_up_all,
    _Patience,
)
from wordwinnow.domain.analysis import (
    Stage,
)
from wordwinnow.domain.dictionary import (
    Definition,
    DictionaryEntry,
    DictionaryLookup,
    LookupOutcome,
    Meaning,
)
from wordwinnow.domain.identifiers import (
    AnalysisId,
)
from wordwinnow.domain.language import (
    PartOfSpeech,
)
from wordwinnow.domain.vocabulary import (
    VocabularyItem,
)
from wordwinnow.infrastructure.dictionary.activity import (
    DictionaryActivity,
)
from wordwinnow.infrastructure.dictionary.cache import (
    CachingDictionary,
    InMemoryDictionaryCache,
    cache_key,
)
from wordwinnow.infrastructure.dictionary.free_dictionary import (
    FreeDictionaryClient,
)
from wordwinnow.infrastructure.dictionary.remote import (
    RemoteDictionary,
)
from wordwinnow.infrastructure.dictionary.wire import (
    FailureReason,
    to_wire,
    unavailable,
)
from wordwinnow.infrastructure.observability.metrics import (
    build_metrics,
)
from wordwinnow.services.cli.progress import (
    TerminalProgress,
    _count_and_time,
    _description,
)
from wordwinnow.services.enrichment.app import (
    EnrichmentDependencies,
    build_enrichment_app,
    lookup_observer,
)
from wordwinnow.services.intake.schemas import (
    ProgressOut,
)
from wordwinnow.services.progress import (
    StoredProgress,
)

# NOTE:
# The seed every scenario runs under, so the breaker's recovery period and the backoff between a lookup's attempts are
# the same on every run.
SEED: Final = 20261104

# NOTE:
# How long the analysis waits at most for words the dictionary turned away, the default the command line gives it.
PATIENCE_SECONDS: Final = 180.0

STAGE_WORDS: Final = "looking the words up in the dictionary"

_FOUND: Final = [
    {
        "word": "w",
        "meanings": [
            {
                "partOfSpeech": "noun",
                "definitions": [
                    {
                        "definition": "A meaning.",
                    },
                ],
            },
        ],
    },
]

_CACHED: Final = to_wire(
    lookup=DictionaryLookup(
        outcome=LookupOutcome.FOUND,
        entries=(
            DictionaryEntry(
                headword="w",
                phonetic=None,
                meanings=(
                    Meaning(
                        word_class="noun",
                        part_of_speech=None,
                        definitions=(
                            Definition(
                                text="m",
                            ),
                        ),
                    ),
                ),
            ),
        ),
    ),
).model_dump_json()

# NOTE:
# What each scenario's provider requests and every word's outcome added up to, as the tree held them before the line
# learned to name what words wait for, so a line that changes a request, a wait, or an outcome fails here.
_POLICY_DIGESTS: Final = MappingProxyType(
    mapping={
        "two_long_waits": "554562bd6fd0f52cc4e13df57f70cde6ae629270c4591a0c3076ed17b2045083",
        "a_wait_that_starts_later": "8416f111fa69072e67d7899f9f908cf5695c6fba40ba841cbc8495729b8dea61",
        "a_wait_kept_inside_a_lookup": "eea5dfd27b0f7097e3b81ffd1a8ab88f8d54c4750a3f057848979f57b2457804",
        "an_outage_that_ends_quickly": "6ea7c6df3d310f7e97be30a6d7d1997cb9a3ab593416cd4d5ba2560c211625fe",
        "an_outage_with_failed_trials": "15266b906d409307ccabef3b70509d75cc276c3a1beb579435f9810ddf681ec0",
        "an_outage_longer_than_the_waiting_time": "1e5a2ff1835cbb67748f7d5d57199bf990c8a5c8f4effafc2dce4b62ee94506d",
        "a_pause_the_provider_asks_for": "2d9523e371c2fb85a4ba6db21077fa4c75eb399a5b525f4360e37373eefa1012",
        "two_analyses_one_outage": "f2695242eefec0dad990d5d8f38871f86a83f31362b570834e07235e330d2a6a",
        "a_failing_word_beside_another_analysis": "c73f48d654844393e46413099f45865fa4ee32189c360a078fa39bdfdd91f67b",
        "a_word_that_fails_three_times": "5c30c744828bdaa0c8083b6a4314106a4df4c15678f8a884d8a70a8f35f877c6",
        "a_word_that_always_fails": "e1f1cdbd5473cef339542bce5ea28afc61aca90b99509e4b9bd3910c6a2a66a8",
        "a_wait_of_two_minutes_for_one_word": "a1a6eb2a79980eb3d9eb03e1ab7872dfd67dcbf940e1458daa6654c11e8c7b9c",
        "a_long_wait_while_the_circuit_opens": "4d0c01eca729b5401c61fa272baf74fd21f43ff686c8e91556b68ddc61325a3d",
        "a_long_wait_while_the_client_pauses": "f3eb6ba4c23dc73e40800e21beb0dfad4b3a101d861a73c64e7dfd1bc85fc83f",
    },
)

type _Answer = Callable[[str, float], Response]


def _words(
    prefix: str,
    count: int,
    /,
) -> tuple[VocabularyItem, ...]:
    return tuple(
        VocabularyItem(
            lemma=f"{prefix}{index:02d}",
            part_of_speech=PartOfSpeech.NOUN,
            occurrence_count=1,
            example_sentence="x",
        )
        for index in range(
            count,
        )
    )


def _named(
    lemmas: Sequence[str],
    /,
) -> tuple[VocabularyItem, ...]:
    return tuple(
        VocabularyItem(
            lemma=lemma,
            part_of_speech=PartOfSpeech.NOUN,
            occurrence_count=1,
            example_sentence="x",
        )
        for lemma in lemmas
    )


def _answered() -> Response:
    return Response(
        status_code=200,
        json=_FOUND,
    )


def _asks_later(
    status_code: int,
    seconds: int,
    /,
) -> Response:
    return Response(
        status_code=status_code,
        headers={
            "Retry-After": str(
                object=seconds,
            ),
        },
    )


def _first_time(
    responses: Mapping[str, Response],
    /,
) -> _Answer:
    """
    The listed words' first request gets the given response; every other
    request is answered.
    """

    asked: set[str] = set()

    def answer(
        lemma: str,
        at: float,
        /,
    ) -> Response:
        if lemma in responses and lemma not in asked:
            asked.add(
                lemma,
            )

            return responses[lemma]

        return _answered()

    return answer


def _down(
    until: float,
    /,
) -> _Answer:
    def answer(
        lemma: str,
        at: float,
        /,
    ) -> Response:
        if at < until:
            return Response(
                status_code=503,
            )

        return _answered()

    return answer


def _always_failing(
    failing: str,
    /,
) -> _Answer:
    def answer(
        lemma: str,
        at: float,
        /,
    ) -> Response:
        if lemma == failing:
            return Response(
                status_code=503,
            )

        return _answered()

    return answer


def _failing_then_answering(
    failing: str,
    times: int,
    failure: Callable[[], Response],
    /,
) -> _Answer:
    failed = [
        0,
    ]

    def answer(
        lemma: str,
        at: float,
        /,
    ) -> Response:
        if lemma == failing and failed[0] < times:
            failed[0] += 1

            return failure()

        return _answered()

    return answer


def _a_long_wait_while_the_circuit_opens() -> _Answer:
    asked: set[str] = set()

    def answer(
        lemma: str,
        at: float,
        /,
    ) -> Response:
        if lemma == "w00" and lemma not in asked:
            asked.add(
                lemma,
            )

            return _asks_later(
                522,
                120,
            )

        if 3.0 <= at < 40.0:
            return Response(
                status_code=503,
            )

        return _answered()

    return answer


@dataclass(
    frozen=True,
    kw_only=True,
    slots=True,
)
@final
class _Plan:
    """
    One analysis a scenario runs: its name, its words, and when it starts.
    """

    name: str

    items: tuple[VocabularyItem, ...]

    start_at: float = 0.0


@dataclass(
    frozen=True,
    kw_only=True,
    slots=True,
)
@final
class _Scenario:
    """
    A provider, the analyses that ask it, and the words already cached.

    Analyses of a shared scenario meet one enrichment service and so one
    breaker, as two commands on one machine do.
    """

    answer: Callable[[], _Answer]

    plans: tuple[_Plan, ...]

    cached: tuple[str, ...] = ()

    shared: bool = False


_THREE: Final = (
    "groom",
    "drunken",
    "saunter",
)

_SCENARIOS: Final = MappingProxyType(
    mapping={
        "two_long_waits": _Scenario(
            answer=lambda: _first_time(
                {
                    "w00": _asks_later(
                        522,
                        40,
                    ),
                    "w01": _asks_later(
                        522,
                        90,
                    ),
                },
            ),
            plans=(
                _Plan(
                    name="A",
                    items=_words(
                        "w",
                        40,
                    ),
                ),
            ),
        ),
        "a_wait_that_starts_later": _Scenario(
            answer=lambda: _first_time(
                {
                    "w00": _asks_later(
                        522,
                        90,
                    ),
                    "w20": _asks_later(
                        522,
                        45,
                    ),
                },
            ),
            plans=(
                _Plan(
                    name="A",
                    items=_words(
                        "w",
                        40,
                    ),
                ),
            ),
        ),
        "a_wait_kept_inside_a_lookup": _Scenario(
            answer=lambda: _first_time(
                {
                    "w00": _asks_later(
                        522,
                        90,
                    ),
                    "w20": _asks_later(
                        522,
                        30,
                    ),
                },
            ),
            plans=(
                _Plan(
                    name="A",
                    items=_words(
                        "w",
                        40,
                    ),
                ),
            ),
        ),
        "an_outage_that_ends_quickly": _Scenario(
            answer=lambda: _down(
                2.0,
            ),
            plans=(
                _Plan(
                    name="A",
                    items=_words(
                        "w",
                        40,
                    ),
                ),
            ),
        ),
        "an_outage_with_failed_trials": _Scenario(
            answer=lambda: _down(
                60.0,
            ),
            plans=(
                _Plan(
                    name="A",
                    items=_words(
                        "w",
                        40,
                    ),
                ),
            ),
        ),
        "an_outage_longer_than_the_waiting_time": _Scenario(
            answer=lambda: _down(
                300.0,
            ),
            plans=(
                _Plan(
                    name="A",
                    items=_words(
                        "w",
                        40,
                    ),
                ),
            ),
        ),
        "a_pause_the_provider_asks_for": _Scenario(
            answer=lambda: _first_time(
                {
                    "w02": _asks_later(
                        429,
                        45,
                    ),
                },
            ),
            plans=(
                _Plan(
                    name="A",
                    items=_words(
                        "w",
                        40,
                    ),
                ),
            ),
        ),
        "two_analyses_one_outage": _Scenario(
            answer=lambda: _down(
                30.0,
            ),
            plans=(
                _Plan(
                    name="X",
                    items=_words(
                        "x",
                        20,
                    ),
                ),
                _Plan(
                    name="Y",
                    items=_words(
                        "y",
                        20,
                    ),
                    start_at=15.0,
                ),
            ),
            shared=True,
        ),
        "a_failing_word_beside_another_analysis": _Scenario(
            answer=lambda: _always_failing(
                "x00",
            ),
            plans=(
                _Plan(
                    name="X",
                    items=_words(
                        "x",
                        10,
                    ),
                ),
                _Plan(
                    name="Y",
                    items=_words(
                        "y",
                        20,
                    ),
                    start_at=5.0,
                ),
            ),
            cached=tuple(
                f"x{index:02d}"
                for index in range(
                    1,
                    10,
                )
            ),
            shared=True,
        ),
        "a_word_that_fails_three_times": _Scenario(
            answer=lambda: _failing_then_answering(
                "groom",
                3,
                lambda: Response(
                    status_code=503,
                ),
            ),
            plans=(
                _Plan(
                    name="A",
                    items=_named(
                        _THREE,
                    ),
                ),
            ),
            cached=(
                "drunken",
                "saunter",
            ),
        ),
        "a_word_that_always_fails": _Scenario(
            answer=lambda: _always_failing(
                "groom",
            ),
            plans=(
                _Plan(
                    name="A",
                    items=_named(
                        _THREE,
                    ),
                ),
            ),
            cached=(
                "drunken",
                "saunter",
            ),
        ),
        "a_wait_of_two_minutes_for_one_word": _Scenario(
            answer=lambda: _failing_then_answering(
                "groom",
                1,
                lambda: _asks_later(
                    522,
                    120,
                ),
            ),
            plans=(
                _Plan(
                    name="A",
                    items=_named(
                        _THREE,
                    ),
                ),
            ),
        ),
        "a_long_wait_while_the_circuit_opens": _Scenario(
            answer=_a_long_wait_while_the_circuit_opens,
            plans=(
                _Plan(
                    name="A",
                    items=_words(
                        "w",
                        40,
                    ),
                ),
            ),
        ),
        "a_long_wait_while_the_client_pauses": _Scenario(
            answer=lambda: _first_time(
                {
                    "w00": _asks_later(
                        522,
                        120,
                    ),
                    "w05": _asks_later(
                        429,
                        45,
                    ),
                },
            ),
            plans=(
                _Plan(
                    name="A",
                    items=_words(
                        "w",
                        40,
                    ),
                ),
            ),
        ),
    },
)


@dataclass(
    frozen=True,
    kw_only=True,
    slots=True,
)
@final
class _Line:
    """
    What a drawn line says, read back from its words: the state, how many
    words wait, and the two times in whole seconds.
    """

    state: str

    words: int | None

    next_in: int | None

    bound: int | None

    text: str


_SOME_WORDS_WAIT: Final = compile_pattern(
    pattern=r".*, ([\d,]+) waiting(?:, no answer for \d+ s)?",
)

_ASKED_AGAIN: Final = compile_pattern(
    pattern=r"([\d,]+) words? waits? to be asked again; next (.+)",
)

_NOT_ANSWERING: Final = compile_pattern(
    pattern=r"the dictionary is not answering; next check (.+)",
)

_PAUSED: Final = compile_pattern(
    pattern=r"paused, as the dictionary asked; resumes (.+)",
)

_BOUND: Final = compile_pattern(
    pattern=r"(?:([\d,]+) waiting, )?WordNet's meaning (.+)",
)


def _seconds(
    text: str,
    /,
) -> int:
    return sum(
        int(
            number,
        )
        * (60 if unit == "min" else 1)
        for (
            number,
            unit,
        ) in findall(
            pattern=r"(\d+) (min|s)\b",
            string=text,
        )
    )


def _number(
    text: str,
    /,
) -> int:
    return int(
        text.replace(
            ",",
            "",
        ),
    )


def _read(
    description: str,
    count: str,
    /,
) -> _Line:
    text = f"{description} | {count}"

    bound = _BOUND.fullmatch(
        string=count,
    )

    bound_words: int | None = None

    bound_seconds: int | None = None

    if bound is not None:
        bound_seconds = _seconds(
            bound.group(
                2,
            ),
        )

        counted = bound.group(
            1,
        )

        if counted is not None:
            bound_words = _number(
                counted,
            )

    for (
        pattern,
        state,
    ) in (
        (
            _ASKED_AGAIN,
            "waiting words",
        ),
        (
            _NOT_ANSWERING,
            "not answering",
        ),
        (
            _PAUSED,
            "asked to pause",
        ),
    ):
        matched = pattern.fullmatch(
            string=description,
        )

        if matched is None:
            continue

        return _Line(
            state=state,
            words=(
                _number(
                    matched.group(
                        1,
                    ),
                )
                if state == "waiting words"
                else bound_words
            ),
            next_in=_seconds(
                matched.group(
                    matched.lastindex or 0,
                ),
            ),
            bound=bound_seconds,
            text=text,
        )

    if description in {
        "the dictionary is not answering",
        "paused, as the dictionary asked",
    }:
        return _Line(
            state="held",
            words=bound_words,
            next_in=None,
            bound=bound_seconds,
            text=text,
        )

    if description == "the dictionary is not answering; WordNet's meaning stands in":
        return _Line(
            state="stands in",
            words=None,
            next_in=None,
            bound=None,
            text=text,
        )

    waiting = _SOME_WORDS_WAIT.fullmatch(
        string=count,
    )

    return _Line(
        state="stage" if waiting is None else "some words",
        words=(
            None
            if waiting is None
            else _number(
                waiting.group(
                    1,
                ),
            )
        ),
        next_in=None,
        bound=None,
        text=text,
    )


@dataclass(
    frozen=True,
    kw_only=True,
    slots=True,
)
@final
class _Fact:
    """
    One report of a wait, as the stage made it, with the loop's time.
    """

    at: float

    held: tuple[HeldWords, ...]

    ending: bool

    retry_in_seconds: float | None

    patience_left_seconds: float


@dataclass(
    frozen=True,
    kw_only=True,
    slots=True,
)
@final
class _Sample:
    """
    One drawing of the line: when, why, how far the stage had come, the line
    read back, and the last report of a wait before it.
    """

    at: float

    why: str

    done: int

    line: _Line

    fact: _Fact | None


@dataclass(
    kw_only=True,
    slots=True,
)
@final
class _Watch:
    """
    What one analysis's run left to check.
    """

    name: str

    samples: list[_Sample] = field(
        default_factory=list,
    )

    facts: list[_Fact] = field(
        default_factory=list,
    )

    looked_up: list[tuple[float, DictionaryLookup]] = field(
        default_factory=list,
    )

    compared: list[tuple[float, str, str]] = field(
        default_factory=list,
    )

    stored: list[tuple[int, ProcessingProgress]] = field(
        default_factory=list,
    )

    ended: float | None = None

    outcomes: tuple[tuple[str, str, str], ...] = ()


@dataclass(
    kw_only=True,
    slots=True,
)
@final
class _Run:
    """
    What a scenario did: each analysis's watch, the provider's requests, and
    the digest of the requests and outcomes.
    """

    watches: dict[str, _Watch]

    requests: list[tuple[float, str, int]]

    digest: str


def _now() -> float:
    return get_running_loop().time()


@final
class _LoopClock:
    """
    The wall clock the line and the stored report read: the epoch plus the
    loop's time.
    """

    def now(
        self,
    ) -> datetime:
        return EPOCH + timedelta(
            seconds=_now(),
        )

    def __call__(
        self,
    ) -> datetime:
        return self.now()


def _text_of(
    line: TerminalProgress,
    /,
) -> tuple[str, str]:
    task = line.line.tasks[-1]

    at = EPOCH + timedelta(
        seconds=_now(),
    )

    return (
        _description(
            task,
            at,
        ).plain,
        _count_and_time(
            task,
            at,
        ).plain,
    )


@final
class _Both:
    """
    What the stage reports to, fanned out to the command line's own line and
    to a worker's stored report, the second drawn again by a reader as the
    command waiting elsewhere draws it.
    """

    def __init__(
        self,
        *,
        watch: _Watch,
        storage: InMemoryStorage,
        analysis_id: AnalysisId,
        tasks: list[Task[None]],
    ) -> None:
        clock = _LoopClock()

        self._watch: Final = watch

        self._tasks: Final = tasks

        self._line: Final = TerminalProgress(
            console=Console(
                file=StringIO(),
                force_terminal=True,
                width=160,
            ),
            clock=clock,
        )

        self._reader: Final = TerminalProgress(
            console=Console(
                file=StringIO(),
                force_terminal=True,
                width=160,
            ),
            clock=clock,
        )

        self._stored: Final = StoredProgress(
            analysis_id=analysis_id,
            new_unit_of_work=storage.new_unit_of_work,
            clock=clock,
        )

        self._storage: Final = storage

        self._analysis_id: Final = analysis_id

        self._done = 0

        self._started = False

        # NOTE:
        # The last report the line has drawn from: one that a word is leaving with is drawn at the `looked_up` that
        # follows it, and so is kept back until then.
        self._drawn_fact: _Fact | None = None

        self._pending_fact: _Fact | None = None

        # NOTE:
        # One comparison at a time, so that the report a comparison reads is the one its own flush wrote, and the
        # reports the stage had made by then are the ones it was written from.
        self._turn: Final = Lock()

    def stage_started(
        self,
        *,
        stage: Stage,
        steps: int | None,
    ) -> None:
        self._line.stage_started(
            stage=stage,
            steps=steps,
        )

        self._stored.stage_started(
            stage=stage,
            steps=steps,
        )

        self._started = True

        self._sample(
            "started",
        )

    def looked_up(
        self,
        *,
        lookup: DictionaryLookup,
    ) -> None:
        self._line.looked_up(
            lookup=lookup,
        )

        self._stored.looked_up(
            lookup=lookup,
        )

        self._done += 1

        if self._pending_fact is not None:
            self._drawn_fact = self._pending_fact

            self._pending_fact = None

        self._watch.looked_up.append(
            (
                _now(),
                lookup,
            ),
        )

        self._sample(
            "looked up",
        )

    def dictionary_waiting(
        self,
        *,
        held: Sequence[HeldWords],
        ending: bool,
        retry_in_seconds: float | None,
        patience_left_seconds: float,
    ) -> None:
        fact = _Fact(
            at=_now(),
            held=tuple(
                held,
            ),
            ending=ending,
            retry_in_seconds=retry_in_seconds,
            patience_left_seconds=patience_left_seconds,
        )

        self._watch.facts.append(
            fact,
        )

        if ending:
            self._pending_fact = fact

        else:
            self._drawn_fact = fact

            self._pending_fact = None

        self._line.dictionary_waiting(
            held=held,
            ending=ending,
            retry_in_seconds=retry_in_seconds,
            patience_left_seconds=patience_left_seconds,
        )

        self._stored.dictionary_waiting(
            held=held,
            ending=ending,
            retry_in_seconds=retry_in_seconds,
            patience_left_seconds=patience_left_seconds,
        )

        self._sample(
            "waiting",
        )

    def sample_second(
        self,
    ) -> None:
        if self._started:
            self._sample(
                "second",
            )

    def compare_soon(
        self,
    ) -> None:
        if self._started:
            self._tasks.append(
                create_task(
                    coro=self.compare(),
                ),
            )

    async def compare(
        self,
    ) -> None:
        """
        Draw the line and the stored report the stage's state at this moment
        gives, which a flush writes before it awaits anything.
        """

        if not self._started:
            return

        async with self._turn:
            await self._compare()

    async def _compare(
        self,
    ) -> None:
        local = _text_of(
            self._line,
        )

        seen = len(
            self._watch.facts,
        )

        await self._stored.flush()

        report = self._storage.progress.get(
            self._analysis_id,
        )

        if report is None:
            return

        self._watch.stored.append(
            (
                seen,
                report,
            ),
        )

        through = ProgressOut.from_domain(
            progress=report,
        )

        self._reader.processing(
            progress=ProgressOut.model_validate_json(
                json_data=through.model_dump_json(),
            ).to_domain(),
        )

        stored = _text_of(
            self._reader,
        )

        self._watch.compared.append(
            (
                _now(),
                " | ".join(
                    local,
                ),
                " | ".join(
                    stored,
                ),
            ),
        )

    def _sample(
        self,
        why: str,
        /,
    ) -> None:
        (
            description,
            count,
        ) = _text_of(
            self._line,
        )

        self._watch.samples.append(
            _Sample(
                at=_now(),
                why=why,
                done=self._done,
                line=_read(
                    description,
                    count,
                ),
                fact=self._drawn_fact,
            ),
        )

        if why != "second":
            self.compare_soon()


@dataclass(
    kw_only=True,
    slots=True,
)
@final
class _Request:
    """
    One request the provider received: when it arrived, for which word, and
    with what status it was answered.
    """

    at: float

    lemma: str

    status: int


def _client_for(
    answer: _Answer,
    requests: list[_Request],
    sent: list[tuple[float, str, int]],
    /,
) -> FreeDictionaryClient:
    async def handler(
        request: Request,
        /,
    ) -> Response:
        at = _now()

        lemma = request.url.path.rsplit(
            sep="/",
            maxsplit=1,
        )[-1]

        received = _Request(
            at=at,
            lemma=lemma,
            status=0,
        )

        requests.append(
            received,
        )

        await asyncio_sleep(
            delay=0.1,
        )

        response = answer(
            lemma,
            at,
        )

        sent.append(
            (
                at,
                lemma,
                response.status_code,
            ),
        )

        received.status = response.status_code

        return response

    return FreeDictionaryClient(
        base_url="https://dictionary.test/api/v2/entries/en",
        timeout_seconds=30.0,
        requests_per_second=2.0,
        burst=4,
        client=AsyncClient(
            transport=MockTransport(
                handler=handler,
            ),
        ),
        clock=_now,
    )


async def _run_scenario(
    scenario: _Scenario,
    /,
) -> _Run:
    requests: list[_Request] = []

    sent: list[tuple[float, str, int]] = []

    client = _client_for(
        scenario.answer(),
        requests,
        sent,
    )

    cache = InMemoryDictionaryCache()

    for lemma in scenario.cached:
        await cache.set(
            key=cache_key(
                lemma=lemma,
            ),
            value=_CACHED,
            ttl_seconds=3600,
        )

    metrics = build_metrics()

    caching = CachingDictionary(
        inner=client,
        found_ttl_seconds=60,
        not_found_ttl_seconds=60,
        cache=cache,
        on_lookup=(
            lookup_observer(
                metrics=metrics,
            )
            if scenario.shared
            else None
        ),
    )

    transport = (
        ASGITransport(
            app=build_enrichment_app(
                dependencies=EnrichmentDependencies(
                    dictionary=caching,
                    metrics=metrics,
                    activity=lambda: DictionaryActivity(
                        counts=caching.counts(),
                        provider=client.activity(),
                    ),
                ),
                allowed_hosts=frozenset(
                    ("enrichment.test",),
                ),
            ),
        )
        if scenario.shared
        else None
    )

    tasks: list[Task[None]] = []

    watches: dict[str, _Watch] = {}

    boths: dict[str, _Both] = {}

    ended: dict[str, bool] = {}

    for plan in scenario.plans:
        analysis = requested_analysis()

        analysis.pull_events()

        analysis.start(
            at=EPOCH,
        )

        storage = InMemoryStorage()

        async with storage.new_unit_of_work() as uow:
            await uow.analyses.add(
                analysis=analysis,
            )

            await uow.commit()

        watches[plan.name] = _Watch(
            name=plan.name,
        )

        boths[plan.name] = _Both(
            watch=watches[plan.name],
            storage=storage,
            analysis_id=analysis.id,
            tasks=tasks,
        )

    async def run(
        plan: _Plan,
        /,
    ) -> None:
        if plan.start_at:
            await asyncio_sleep(
                delay=plan.start_at,
            )

        dictionary = (
            caching
            if transport is None
            else RemoteDictionary(
                enrichment_url="http://enrichment.test",
                timeout_seconds=180.0,
                client=AsyncClient(
                    transport=transport,
                ),
                clock=_now,
            )
        )

        both = boths[plan.name]

        pipeline = replace(
            stage_pipeline(),
            dictionary=dictionary,
            dictionary_concurrency=8,
            progress=both,
            dictionary_patience_seconds=PATIENCE_SECONDS,
            clock=_now,
        )

        both.stage_started(
            stage=Stage.DICTIONARY_ENRICHMENT,
            steps=len(
                plan.items,
            ),
        )

        answers = await _look_up_all(
            plan.items,
            tuple(
                range(
                    len(
                        plan.items,
                    ),
                ),
            ),
            pipeline,
        )

        watches[plan.name].ended = _now()

        ended[plan.name] = True

        watches[plan.name].outcomes = tuple(
            (
                plan.name,
                item.lemma,
                "none -" if info is None else f"{info.outcome.name.lower()} {info.failure_reason or '-'}",
            )
            for (
                item,
                info,
            ) in sorted(
                zip(
                    plan.items,
                    answers,
                    strict=True,
                ),
                key=lambda pair: pair[0].lemma,
            )
        )

    done = Event()

    async def every_second() -> None:
        while not done.is_set():
            for (
                name,
                both,
            ) in boths.items():
                if name not in ended:
                    both.sample_second()

                    await both.compare()

            await asyncio_sleep(
                delay=1.0,
            )

    ticking = create_task(
        coro=every_second(),
    )

    await gather(
        *(
            run(
                plan,
            )
            for plan in scenario.plans
        ),
    )

    done.set()

    await ticking

    await gather(
        *tasks,
    )

    request_lines = tuple(
        f"{milliseconds} {lemma} {status}"
        for (
            milliseconds,
            lemma,
            status,
        ) in sorted(
            (
                (
                    round(
                        number=received.at * 1000,
                    ),
                    received.lemma,
                    received.status,
                )
                for received in requests
            ),
            key=lambda received: received[:2],
        )
    )

    outcome_lines = tuple(
        f"{name} {lemma} {outcome}"
        for watch in sorted(
            watches.values(),
            key=lambda watch: watch.name,
        )
        for (
            name,
            lemma,
            outcome,
        ) in watch.outcomes
    )

    return _Run(
        watches=watches,
        requests=sent,
        digest=sha256(
            data="\n".join(
                (
                    "requests",
                    *request_lines,
                    "outcomes",
                    *outcome_lines,
                ),
            ).encode(),
        ).hexdigest(),
    )


_RUNS: Final[dict[tuple[str, int], _Run]] = {}


def _run(
    name: str,
    /,
    *,
    seeded: int = SEED,
) -> _Run:
    """
    The scenario `name` run once under `seeded`, with the random generator put
    back as it was.
    """

    key = (
        name,
        seeded,
    )

    if key in _RUNS:
        return _RUNS[key]

    before = getstate()

    seed(
        a=seeded,
    )

    # NOTE:
    # A tracer provider that another test installed for the whole process draws every span's identifier from the
    # shared generator, and so moves the draws the breaker and the backoff take from the seed.
    #
    # The identifiers draw from a generator of their own while a scenario runs, so the seed reaches only the client.
    try:
        with patch(
            target="opentelemetry.sdk.trace.id_generator.random",
            new=Random(),
        ):
            _RUNS[key] = run_in_virtual_time(
                main=lambda: _run_scenario(
                    _SCENARIOS[name],
                ),
            )

    finally:
        setstate(
            state=before,
        )

    return _RUNS[key]


def _expected_wake(
    sample: _Sample,
    /,
) -> int | None:
    """
    The earliest wake of any sleeping word in the last report before `sample`,
    counted down to the sample's moment.
    """

    fact = sample.fact

    if fact is None or fact.retry_in_seconds is None:
        return None

    return max(
        0,
        ceil(
            fact.at + fact.retry_in_seconds - sample.at,
        ),
    )


def _expected_left(
    sample: _Sample,
    /,
) -> int:
    fact = sample.fact

    assert fact is not None

    return max(
        0,
        ceil(
            fact.at + fact.patience_left_seconds - sample.at,
        ),
    )


def _within_a_second(
    drawn: int | None,
    expected: int | None,
    /,
) -> bool:
    if drawn is None or expected is None:
        return drawn is expected

    return (
        abs(
            drawn - expected,
        )
        <= 1
    )


def _states(
    watch: _Watch,
    /,
) -> tuple[str, ...]:
    """
    The states the line drew, in order, each once for as long as it lasted.
    """

    states: list[str] = []

    for sample in watch.samples:
        if not states or states[-1] != sample.line.state:
            states.append(
                sample.line.state,
            )

    return tuple(
        states,
    )


@final
class TestThePolicyIsUnchanged:
    @mark.parametrize(
        argnames="name",
        argvalues=sorted(
            _POLICY_DIGESTS,
        ),
    )
    def test_the_requests_and_every_outcome_are_what_the_tree_made_before_the_line_changed(
        self,
        *,
        name: str,
    ) -> None:
        assert (
            _run(
                name,
            ).digest
            == _POLICY_DIGESTS[name]
        )


_NAMES_THE_STATE: Final = (
    "not answering",
    "paused",
    "WordNet",
    "next check",
    "resumes",
)


def _names_the_dictionarys_state(
    sample: _Sample,
    /,
) -> bool:
    return any(phrase in sample.line.text for phrase in _NAMES_THE_STATE)


def _first(
    watch: _Watch,
    predicate: Callable[[_Sample], bool],
    /,
) -> int:
    return next(
        index
        for (
            index,
            sample,
        ) in enumerate(
            iterable=watch.samples,
        )
        if predicate(
            sample,
        )
    )


def _position(
    watch: _Watch,
    fact: _Fact | None,
    /,
) -> int:
    """
    How many reports the stage had made when it made `fact`, by identity.
    """

    return next(
        index
        for (
            index,
            made,
        ) in enumerate(
            iterable=watch.facts,
        )
        if made is fact
    )


def _counted(
    sample: _Sample,
    /,
) -> int:
    """
    How many words the last report before `sample` counts as waiting, by the
    reason each was turned away for.
    """

    fact = sample.fact

    assert fact is not None

    return sum(
        held.turned_away
        if held.reason
        in {
            FailureReason.CIRCUIT_OPEN,
            FailureReason.RATE_LIMITED,
        }
        else held.turned_away + held.others_sleeping
        for held in fact.held
    )


def _expected_scope_wake(
    sample: _Sample,
    reason: FailureReason,
    /,
) -> int | None:
    """
    The earliest wake among the turned-away words that sleep for `reason` in
    the last report before `sample`, counted down to the sample's moment.
    """

    fact = sample.fact

    assert fact is not None

    wakes = tuple(
        held.turned_away_wake_in_seconds
        for held in fact.held
        if held.reason == reason and held.turned_away_wake_in_seconds is not None
    )

    if not wakes:
        return None

    return max(
        0,
        ceil(
            fact.at
            + min(
                wakes,
            )
            - sample.at,
        ),
    )


@final
class TestSeveralWordsWaitForTheirOwnReasons:
    def test_words_waiting_while_others_are_asked_say_how_many_wait_and_nothing_of_the_dictionary(
        self,
    ) -> None:
        watch = _run(
            "two_long_waits",
        ).watches["A"]

        first = _first(
            watch,
            lambda sample: sample.line.state != "stage",
        )

        last = _first(
            watch,
            lambda sample: sample.done == 38,
        )

        window = watch.samples[first:last]

        assert window[0].at < 0.5

        assert {sample.line.state for sample in window} == {
            "some words",
        }

        assert {sample.line.words for sample in window} == {
            1,
            2,
        }

        assert {
            sample.line.words
            for sample in window
            if sample.fact is not None
            and _position(
                watch,
                sample.fact,
            )
            >= 1
        } == {
            2,
        }

        assert not any(
            _names_the_dictionarys_state(
                sample,
            )
            for sample in window
        )

    def test_only_waiting_words_left_say_when_the_next_asks_and_how_long_wordnets_meaning_waits(
        self,
    ) -> None:
        watch = _run(
            "two_long_waits",
        ).watches["A"]

        last = _first(
            watch,
            lambda sample: sample.done == 38,
        )

        woke = _first(
            watch,
            lambda sample: sample.at >= 90.0,
        )

        window = watch.samples[last:woke]

        assert {sample.line.state for sample in window} == {
            "waiting words",
        }

        assert all(
            _within_a_second(
                sample.line.next_in,
                _expected_wake(
                    sample,
                ),
            )
            and _within_a_second(
                sample.line.bound,
                _expected_left(
                    sample,
                ),
            )
            for sample in window
        )

    def test_the_count_falls_from_two_to_one_when_the_first_waiting_word_is_answered(
        self,
    ) -> None:
        watch = _run(
            "two_long_waits",
        ).watches["A"]

        answered = _first(
            watch,
            lambda sample: sample.done == 39,
        )

        before = watch.samples[answered - 1]

        after = watch.samples[answered]

        assert (
            before.line.state,
            before.line.words,
            after.line.state,
            after.line.words,
        ) == (
            "waiting words",
            2,
            "waiting words",
            1,
        )

        assert 40.0 < after.at < 40.5


@final
class TestACountdownThatChanges:
    def test_the_earliest_wake_falls_when_a_word_with_an_earlier_wake_begins_to_wait(
        self,
    ) -> None:
        facts = tuple(
            fact
            for fact in _run(
                "a_wait_that_starts_later",
            )
            .watches["A"]
            .facts
            if fact.retry_in_seconds is not None
        )

        assert any(
            81.0 < earlier.at + earlier.retry_in_seconds - later.at < 82.0
            and 44.0 < later.retry_in_seconds < 46.0
            and 8.0 < later.at < 9.2
            for (
                earlier,
                later,
            ) in pairwise(
                facts,
            )
            if earlier.retry_in_seconds is not None and later.retry_in_seconds is not None
        )

    def test_the_countdown_is_the_earliest_wake_at_every_sample_and_rises_when_the_earliest_wakes(
        self,
    ) -> None:
        watch = _run(
            "a_wait_that_starts_later",
        ).watches["A"]

        waiting = tuple(sample for sample in watch.samples if sample.line.state == "waiting words")

        assert waiting

        assert all(
            _within_a_second(
                sample.line.next_in,
                _expected_wake(
                    sample,
                ),
            )
            for sample in waiting
        )

        assert any(
            earlier.line.next_in is not None
            and later.line.next_in is not None
            and later.line.next_in - earlier.line.next_in >= 30
            and 53.0 < later.at < 55.0
            for (
                earlier,
                later,
            ) in pairwise(
                waiting,
            )
        )


@final
class TestWaitsInsideALookupStayUnseen:
    def test_a_wait_of_thirty_seconds_or_less_adds_no_waiting_word_and_no_countdown(
        self,
    ) -> None:
        watch = _run(
            "a_wait_kept_inside_a_lookup",
        ).watches["A"]

        assert max(sum(held.turned_away + held.others_sleeping for held in fact.held) for fact in watch.facts) == 1

        last = _first(
            watch,
            lambda sample: sample.line.state == "waiting words",
        )

        window = tuple(sample for sample in watch.samples[:last] if sample.line.state != "stage")

        assert {sample.line.state for sample in window} == {
            "some words",
        }

        assert {sample.line.words for sample in window} == {
            1,
        }

        assert {sample.line.next_in for sample in window} == {
            None,
        }


@final
class TestAProviderWidePause:
    def test_the_pause_is_named_until_it_ends_with_its_countdown_and_the_words_it_holds(
        self,
    ) -> None:
        watch = _run(
            "a_pause_the_provider_asks_for",
        ).watches["A"]

        first = _first(
            watch,
            lambda sample: sample.line.state == "asked to pause",
        )

        last = max(
            index
            for (
                index,
                sample,
            ) in enumerate(
                iterable=watch.samples,
            )
            if sample.line.state == "asked to pause"
        )

        window = watch.samples[first : last + 1]

        assert {sample.line.state for sample in window} == {
            "asked to pause",
        }

        assert max(sample.line.words or 0 for sample in window) == 37

        assert all(
            _within_a_second(
                sample.line.next_in,
                _expected_scope_wake(
                    sample,
                    FailureReason.RATE_LIMITED,
                ),
            )
            for sample in window
        )

    def test_the_line_is_back_to_the_stage_within_a_second_of_the_end(
        self,
    ) -> None:
        watch = _run(
            "a_pause_the_provider_asks_for",
        ).watches["A"]

        last = max(
            index
            for (
                index,
                sample,
            ) in enumerate(
                iterable=watch.samples,
            )
            if sample.line.state == "asked to pause"
        )

        back = next(sample for sample in watch.samples[last:] if sample.line.state == "stage")

        assert back.at - watch.samples[last].at <= 1.0


@final
class TestTheBreakersRefusal:
    def test_the_circuit_is_named_while_words_are_refused_and_its_check_restarts_after_each_failed_trial(
        self,
    ) -> None:
        watch = _run(
            "an_outage_with_failed_trials",
        ).watches["A"]

        first = _first(
            watch,
            lambda sample: sample.line.state == "not answering",
        )

        back = _first(
            watch,
            lambda sample: sample.line.state == "stage" and sample.done > 0,
        )

        window = watch.samples[first:back]

        assert {sample.line.state for sample in window} <= {
            "not answering",
            "held",
        }

        checks = tuple(sample for sample in window if sample.line.state == "not answering")

        assert all(
            _within_a_second(
                sample.line.next_in,
                _expected_scope_wake(
                    sample,
                    FailureReason.CIRCUIT_OPEN,
                ),
            )
            for sample in checks
        )

        assert (
            sum(
                1
                for (
                    earlier,
                    later,
                ) in pairwise(
                    checks,
                )
                if later.line.next_in is not None
                and earlier.line.next_in is not None
                and later.line.next_in - earlier.line.next_in >= 3
            )
            >= 2
        )

    def test_a_trial_in_flight_draws_the_same_words_without_a_countdown_and_its_answer_ends_them(
        self,
    ) -> None:
        run = _run(
            "an_outage_with_failed_trials",
        )

        watch = run.watches["A"]

        held = tuple(sample for sample in watch.samples if sample.line.state == "held")

        assert held

        assert all(
            sample.line.text.startswith(
                "the dictionary is not answering | ",
            )
            and sample.line.bound is not None
            for sample in held
        )

        opened = _first(
            watch,
            lambda sample: sample.line.state == "not answering",
        )

        trial = next(
            at
            for (
                at,
                _,
                status,
            ) in run.requests
            if status == 200 and at > watch.samples[opened].at
        )

        back = next(sample for sample in watch.samples[opened:] if sample.line.state == "stage")

        assert 0.0 <= back.at - (trial + 0.1) <= 1.0

        assert back.done < 40


@final
class TestMixedScopes:
    def test_a_circuit_that_opens_while_a_word_waits_for_its_own_reason_counts_that_word_too(
        self,
    ) -> None:
        watch = _run(
            "a_long_wait_while_the_circuit_opens",
        ).watches["A"]

        refusing = tuple(
            sample
            for sample in watch.samples
            if sample.line.state
            in {
                "not answering",
                "held",
            }
        )

        assert refusing

        assert all(
            sample.line.words
            == _counted(
                sample,
            )
            for sample in refusing
        )

        assert any(
            held.reason == FailureReason.PROVIDER_ERROR and held.turned_away + held.others_sleeping
            for sample in refusing
            for held in (sample.fact.held if sample.fact else ())
        )

    def test_a_pause_that_ends_while_a_word_still_waits_gives_way_to_the_stage_then_to_the_waiting_word(
        self,
    ) -> None:
        watch = _run(
            "a_long_wait_while_the_client_pauses",
        ).watches["A"]

        states = _states(
            watch,
        )

        assert states[1:6] == (
            "some words",
            "asked to pause",
            "held",
            "some words",
            "waiting words",
        )

        pausing = tuple(sample for sample in watch.samples if sample.line.state == "asked to pause")

        assert all(
            sample.line.words
            == _counted(
                sample,
            )
            for sample in pausing
        )

        assert all(sample.line.bound is not None for sample in watch.samples if sample.line.state == "waiting words")


@final
class TestTheWaitingTimeRunsOut:
    def test_wordnets_meaning_is_bounded_by_the_time_left_which_never_rises_and_the_line_ends_standing_in(
        self,
    ) -> None:
        watch = _run(
            "an_outage_longer_than_the_waiting_time",
        ).watches["A"]

        bounded = tuple(sample for sample in watch.samples if sample.line.bound is not None)

        assert bounded

        assert all(
            _within_a_second(
                sample.line.bound,
                _expected_left(
                    sample,
                ),
            )
            for sample in bounded
        )

        assert all(
            later.line.bound <= earlier.line.bound
            for (
                earlier,
                later,
            ) in pairwise(
                bounded,
            )
            if later.line.bound is not None and earlier.line.bound is not None
        )

        assert watch.samples[-1].line.state == "stands in"

        assert {
            outcome
            for (
                _,
                _,
                outcome,
            ) in watch.outcomes
        } <= {
            "unavailable circuit_open",
            "unavailable provider_error",
        }


@final
class _FixedClock:
    """
    A wall clock that moves only when a test moves it.
    """

    def __init__(
        self,
    ) -> None:
        self.at = EPOCH

    def __call__(
        self,
    ) -> datetime:
        return self.at

    def now(
        self,
    ) -> datetime:
        return self.at

    def advance(
        self,
        *,
        seconds: float,
    ) -> None:
        self.at += timedelta(
            seconds=seconds,
        )


_STAGE_ANSWER: Final = DictionaryLookup(
    outcome=LookupOutcome.NOT_FOUND,
)


def _terminal(
    *,
    width: int = 160,
    clock: Callable[[], datetime],
) -> TerminalProgress:
    return TerminalProgress(
        console=Console(
            file=StringIO(),
            force_terminal=True,
            width=width,
            color_system=None,
        ),
        clock=clock,
    )


def _drawn(
    line: TerminalProgress,
    clock: Callable[[], datetime],
    /,
) -> tuple[str, str]:
    task = line.line.tasks[-1]

    return (
        _description(
            task,
            clock(),
        ).plain,
        _count_and_time(
            task,
            clock(),
        ).plain,
    )


def _wait(
    reason: FailureReason,
    /,
    *,
    words: int,
    wake: float | None,
    wait: float | None = None,
    repeated: bool = False,
) -> HeldWords:
    return held_words(
        reason=reason,
        turned_away=words,
        turned_away_wake_in_seconds=wake,
        longest_wait_seconds=wait,
        repeated=repeated,
    )


def _report(
    line: TerminalProgress,
    held: Sequence[HeldWords],
    /,
    *,
    retry: float | None = None,
    left: float = 180.0,
) -> None:
    line.dictionary_waiting(
        held=held,
        ending=False,
        retry_in_seconds=retry,
        patience_left_seconds=left,
    )


@final
class TestRecovery:
    def test_the_estimate_returns_only_once_nothing_waits_and_the_quiet_is_said_in_two_states_alone(
        self,
    ) -> None:
        clock = _FixedClock()

        line = _terminal(
            clock=clock,
        )

        line.stage_started(
            stage=Stage.DICTIONARY_ENRICHMENT,
            steps=1_000,
        )

        for _ in range(
            30,
        ):
            clock.advance(
                seconds=1.0,
            )

            line.looked_up(
                lookup=_STAGE_ANSWER,
            )

        assert (
            _drawn(
                line,
                clock,
            )[1]
            == "30 of 1,000, about 16 min left"
        )

        # NOTE:
        # One word waits for its own reason while the rest are asked, so the estimate is withheld and the quiet, once
        # five seconds pass with no answer, is said after the count of waiting words.
        _report(
            line,
            (
                _wait(
                    FailureReason.PROVIDER_ERROR,
                    words=1,
                    wake=60.0,
                    wait=60.0,
                ),
            ),
            retry=60.0,
        )

        assert (
            _drawn(
                line,
                clock,
            )[1]
            == "30 of 1,000, 1 waiting"
        )

        clock.advance(
            seconds=6.0,
        )

        assert (
            _drawn(
                line,
                clock,
            )[1]
            == "30 of 1,000, 1 waiting, no answer for 6 s"
        )

        # NOTE:
        # Nothing waits any longer: the quiet is counted again from there, and the estimate waits for the minute to
        # hold answers again.
        _report(
            line,
            (),
        )

        assert (
            _drawn(
                line,
                clock,
            )[1]
            == "30 of 1,000"
        )

        clock.advance(
            seconds=6.0,
        )

        assert (
            _drawn(
                line,
                clock,
            )[1]
            == "30 of 1,000, no answer for 6 s"
        )

        for _ in range(
            12,
        ):
            clock.advance(
                seconds=1.0,
            )

            line.looked_up(
                lookup=_STAGE_ANSWER,
            )

        assert (
            "left"
            in _drawn(
                line,
                clock,
            )[1]
        )

    def test_the_other_states_say_no_quiet_and_no_estimate_whatever_the_time(
        self,
    ) -> None:
        clock = _FixedClock()

        line = _terminal(
            clock=clock,
        )

        line.stage_started(
            stage=Stage.DICTIONARY_ENRICHMENT,
            steps=40,
        )

        for held in (
            (
                _wait(
                    FailureReason.PROVIDER_ERROR,
                    words=40,
                    wake=90.0,
                    wait=90.0,
                ),
            ),
            (
                _wait(
                    FailureReason.CIRCUIT_OPEN,
                    words=40,
                    wake=9.0,
                ),
            ),
            (
                _wait(
                    FailureReason.RATE_LIMITED,
                    words=40,
                    wake=45.0,
                ),
            ),
            (
                _wait(
                    FailureReason.CIRCUIT_OPEN,
                    words=40,
                    wake=None,
                ),
            ),
        ):
            _report(
                line,
                held,
                retry=None,
            )

            clock.advance(
                seconds=30.0,
            )

            assert "no answer" not in " ".join(
                _drawn(
                    line,
                    clock,
                ),
            )

            assert "left" not in " ".join(
                _drawn(
                    line,
                    clock,
                ),
            )


def _same_moment(
    first: _Watch,
    second: _Watch,
    /,
) -> tuple[tuple[_Sample, _Sample], ...]:
    """
    The pairs of whole-second samples the two analyses drew at one moment.
    """

    seconds = {sample.at: sample for sample in second.samples if sample.why == "second"}

    return tuple(
        (
            sample,
            seconds[sample.at],
        )
        for sample in first.samples
        if sample.why == "second" and sample.at in seconds
    )


@final
class TestConcurrentAnalysesOnOneService:
    def test_an_analysis_that_sent_no_request_before_the_circuit_opened_names_it_from_its_first_refusal(
        self,
    ) -> None:
        run = _run(
            "two_analyses_one_outage",
        )

        x = run.watches["X"]

        y = run.watches["Y"]

        first = y.samples[
            _first(
                y,
                lambda sample: sample.line.state != "stage",
            )
        ]

        assert first.line.state == "not answering"

        assert 15.0 <= first.at < 15.5

        pairs = tuple(
            (
                mine,
                theirs,
            )
            for (
                theirs,
                mine,
            ) in _same_moment(
                x,
                y,
            )
            if mine.line.state == "not answering" and theirs.line.state == "not answering"
        )

        assert pairs

        assert all(
            abs(
                (mine.line.next_in or 0) - (theirs.line.next_in or 0),
            )
            <= 1
            for (
                mine,
                theirs,
            ) in pairs
        )

        assert all(
            _within_a_second(
                mine.line.bound,
                _expected_left(
                    mine,
                ),
            )
            for (
                mine,
                _,
            ) in pairs
        )

        assert any(
            mine.line.bound != theirs.line.bound
            for (
                mine,
                theirs,
            ) in pairs
        )

    def test_a_word_that_fails_alone_is_its_own_analysis_wait_and_never_the_circuits(
        self,
    ) -> None:
        x = _run(
            "a_failing_word_beside_another_analysis",
        ).watches["X"]

        assert set(
            _states(
                x,
            ),
        ) == {
            "stage",
            "waiting words",
            "some words",
        }

        waiting = tuple(sample for sample in x.samples if sample.line.state == "waiting words")

        without = _first(
            x,
            lambda sample: sample.line.state == "waiting words" and sample.line.bound is None,
        )

        with_bound = _first(
            x,
            lambda sample: sample.line.state == "waiting words" and sample.line.bound is not None,
        )

        assert without < with_bound

        assert all(
            sample.line.bound is not None
            for sample in waiting
            if x.samples.index(
                sample,
            )
            >= with_bound
        )

    def test_the_other_analysis_names_the_circuit_with_the_moment_the_failing_word_asks_next(
        self,
    ) -> None:
        run = _run(
            "a_failing_word_beside_another_analysis",
        )

        x = run.watches["X"]

        y = run.watches["Y"]

        assert _states(
            y,
        ) == (
            "stage",
            "not answering",
            "held",
            "stage",
        )

        checks = tuple(sample for sample in y.samples if sample.line.state == "not answering")

        for sample in checks:
            asks = tuple(
                (
                    fact.at,
                    fact.retry_in_seconds,
                )
                for fact in x.facts
                if fact.at <= sample.at and fact.retry_in_seconds is not None
            )

            assert asks

            (
                at,
                retry,
            ) = asks[-1]

            assert retry is not None

            assert _within_a_second(
                sample.line.next_in,
                ceil(
                    at + retry - sample.at,
                ),
            )

        held = _first(
            y,
            lambda sample: sample.line.state == "held",
        )

        back = _first(
            y,
            lambda sample: sample.line.state == "stage" and sample.done > 0,
        )

        assert y.samples[back].at - y.samples[held].at <= 1.0

    @mark.parametrize(
        argnames="seeded",
        argvalues=range(
            1,
            9,
        ),
    )
    def test_the_same_states_follow_under_other_seeds_though_the_trial_may_fall_to_another_word(
        self,
        *,
        seeded: int,
    ) -> None:
        run = _run(
            "a_failing_word_beside_another_analysis",
            seeded=seeded,
        )

        assert set(
            _states(
                run.watches["X"],
            ),
        ) <= {
            "stage",
            "waiting words",
            "some words",
        }

        states = _states(
            run.watches["Y"],
        )

        assert states[:2] == (
            "stage",
            "not answering",
        )

        assert states[-1] == "stage"

        assert set(
            states,
        ) <= {
            "stage",
            "not answering",
            "held",
        }

        assert all(
            local.split(
                sep=" | ",
            )[0]
            == stored.split(
                sep=" | ",
            )[0]
            for watch in run.watches.values()
            for (
                _,
                local,
                stored,
            ) in watch.compared
        )


_ESTIMATE_OR_QUIET: Final = compile_pattern(
    pattern=r", (?:about .* left|a few seconds left|no answer for \d+ s)$",
)


def _without_estimate(
    text: str,
    /,
) -> str:
    """
    The line without the estimate or the quiet, which a command reading stored
    reports dates from the reports and not from every answer.
    """

    return _ESTIMATE_OR_QUIET.sub(
        repl="",
        string=text,
    )


@final
class TestTheDistributedModeDrawsAsTheLocalMode:
    @mark.parametrize(
        argnames="name",
        argvalues=sorted(
            _POLICY_DIGESTS,
        ),
    )
    def test_a_line_drawn_from_stored_reports_equals_the_local_line_at_the_same_moment(
        self,
        *,
        name: str,
    ) -> None:
        for watch in _run(
            name,
        ).watches.values():
            assert watch.compared

            assert (
                tuple(
                    (
                        at,
                        local,
                        stored,
                    )
                    for (
                        at,
                        local,
                        stored,
                    ) in watch.compared
                    if _without_estimate(
                        local,
                    )
                    != _without_estimate(
                        stored,
                    )
                )
                == ()
            )

            assert all(
                report.dictionary_waiting_words is not None
                for (
                    _,
                    report,
                ) in watch.stored
            )

    def test_the_stored_reports_keep_the_two_values_a_command_line_of_the_last_release_reads(
        self,
    ) -> None:
        for name in sorted(
            _POLICY_DIGESTS,
        ):
            for watch in _run(
                name,
            ).watches.values():
                for (
                    seen,
                    report,
                ) in watch.stored:
                    written = (report.reported_at - EPOCH).total_seconds()

                    last = watch.facts[seen - 1] if seen else None

                    if last is None or last.retry_in_seconds is None:
                        assert report.dictionary_retry_in_seconds is None

                        assert report.dictionary_patience_left_seconds is None

                        continue

                    assert report.dictionary_retry_in_seconds == approx(
                        expected=max(
                            0.0,
                            last.at + last.retry_in_seconds - written,
                        ),
                        abs=0.001,
                    )

                    assert report.dictionary_patience_left_seconds == approx(
                        expected=max(
                            0.0,
                            last.at + last.patience_left_seconds - written,
                        ),
                        abs=0.001,
                    )


@final
class TestAWordsOwnRetry:
    def test_a_word_that_fails_three_times_waits_for_the_countdown_alone_and_is_then_answered(
        self,
    ) -> None:
        watch = _run(
            "a_word_that_fails_three_times",
        ).watches["A"]

        assert _states(
            watch,
        ) == (
            "stage",
            "waiting words",
            "some words",
            "stage",
        )

        assert all(sample.line.bound is None for sample in watch.samples)

        assert not any(
            _names_the_dictionarys_state(
                sample,
            )
            for sample in watch.samples
        )

        assert all(
            _within_a_second(
                sample.line.next_in,
                _expected_wake(
                    sample,
                ),
            )
            for sample in watch.samples
            if sample.line.state == "waiting words"
        )


@final
class TestAWordThatAlwaysFails:
    def test_the_word_is_its_own_wait_and_never_the_circuits_with_the_bound_from_its_second_wait(
        self,
    ) -> None:
        watch = _run(
            "a_word_that_always_fails",
        ).watches["A"]

        assert set(
            _states(
                watch,
            ),
        ) == {
            "stage",
            "waiting words",
            "some words",
        }

        second = tuple(fact for fact in watch.facts if fact.retry_in_seconds is not None)[1]

        waiting = tuple(sample for sample in watch.samples if sample.line.state == "waiting words")

        assert all((sample.line.bound is not None) is (sample.at >= second.at) for sample in waiting)

        assert all(
            _within_a_second(
                sample.line.bound,
                _expected_left(
                    sample,
                ),
            )
            for sample in waiting
            if sample.line.bound is not None
        )

        bounds = [sample.line.bound for sample in waiting if sample.line.bound is not None]

        assert bounds == sorted(
            bounds,
            reverse=True,
        )

        assert (
            "A",
            "groom",
            "unavailable provider_error",
        ) in watch.outcomes


@final
class TestALongWaitForOneWord:
    def test_the_others_are_asked_and_then_the_waiting_word_draws_the_bound_throughout(
        self,
    ) -> None:
        watch = _run(
            "a_wait_of_two_minutes_for_one_word",
        ).watches["A"]

        assert _states(
            watch,
        )[:3] == (
            "stage",
            "some words",
            "waiting words",
        )

        waiting = tuple(sample for sample in watch.samples if sample.line.state == "waiting words")

        assert waiting

        assert all(
            _within_a_second(
                sample.line.bound,
                _expected_left(
                    sample,
                ),
            )
            for sample in waiting
        )

        assert not any(
            sample.line.state
            in {
                "not answering",
                "held",
                "asked to pause",
            }
            for sample in watch.samples
        )


_BAR_GLYPHS: Final = (
    "━",
    "╸",
    "╺",
)

_TIME: Final = 179.0


def _rows(
    line: TerminalProgress,
    width: int,
    /,
) -> list[str]:
    stream = StringIO()

    Console(
        file=stream,
        width=width,
        color_system=None,
        force_terminal=False,
    ).print(
        line.line.get_renderable(),
    )

    return (
        stream.getvalue()
        .rstrip(
            "\n",
        )
        .splitlines()
    )


@dataclass(
    frozen=True,
    kw_only=True,
    slots=True,
)
@final
class _Case:
    """
    A stage of 1,144 words at one of the widest moments of a state: how many
    words were answered, how many ended unavailable for `reason` after them,
    and the held words reported, with the time to the next ask.
    """

    name: str

    bar: bool

    answered: int

    unavailable_after: int = 0

    reason: FailureReason = FailureReason.TIMED_OUT

    held: tuple[HeldWords, ...] = ()

    retry: float | None = None


def _stage_of_1144(
    case: _Case,
    width: int,
    /,
) -> TerminalProgress:
    clock = _FixedClock()

    line = _terminal(
        width=width,
        clock=clock,
    )

    line.stage_started(
        stage=Stage.DICTIONARY_ENRICHMENT,
        steps=1_144,
    )

    for _ in range(
        case.answered,
    ):
        line.looked_up(
            lookup=_STAGE_ANSWER,
        )

    for _ in range(
        case.unavailable_after,
    ):
        line.looked_up(
            lookup=unavailable(
                reason=case.reason,
            ),
        )

    if case.held:
        _report(
            line,
            case.held,
            retry=case.retry,
            left=_TIME,
        )

    return line


_WIDEST: Final = (
    _Case(
        name="some words",
        bar=True,
        answered=312,
        held=(
            _wait(
                FailureReason.PROVIDER_ERROR,
                words=2,
                wake=_TIME,
                wait=_TIME,
            ),
        ),
        retry=_TIME,
    ),
    _Case(
        name="some words, with words unavailable",
        bar=True,
        answered=300,
        unavailable_after=12,
        held=(
            _wait(
                FailureReason.PROVIDER_ERROR,
                words=2,
                wake=_TIME,
                wait=_TIME,
            ),
        ),
        retry=_TIME,
    ),
    _Case(
        name="some words, the most",
        bar=True,
        answered=312,
        held=(
            _wait(
                FailureReason.PROVIDER_ERROR,
                words=832,
                wake=None,
            ),
        ),
    ),
    _Case(
        name="waiting words",
        bar=False,
        answered=0,
        held=(
            _wait(
                FailureReason.PROVIDER_ERROR,
                words=1_144,
                wake=_TIME,
                wait=_TIME,
            ),
        ),
        retry=_TIME,
    ),
    _Case(
        name="not answering",
        bar=False,
        answered=0,
        held=(
            _wait(
                FailureReason.CIRCUIT_OPEN,
                words=1_144,
                wake=_TIME,
            ),
        ),
        retry=_TIME,
    ),
    _Case(
        name="asked to pause",
        bar=False,
        answered=0,
        held=(
            _wait(
                FailureReason.RATE_LIMITED,
                words=1_144,
                wake=_TIME,
            ),
        ),
        retry=_TIME,
    ),
    _Case(
        name="held after the circuit",
        bar=False,
        answered=0,
        held=(
            _wait(
                FailureReason.CIRCUIT_OPEN,
                words=1_144,
                wake=None,
            ),
        ),
    ),
    _Case(
        name="held after the pause",
        bar=False,
        answered=0,
        held=(
            _wait(
                FailureReason.RATE_LIMITED,
                words=1_144,
                wake=None,
            ),
        ),
    ),
    _Case(
        name="stands in",
        bar=False,
        answered=300,
        unavailable_after=12,
        reason=FailureReason.CIRCUIT_OPEN,
    ),
    _Case(
        name="stands in, the most",
        bar=False,
        answered=0,
        unavailable_after=1_144,
        reason=FailureReason.CIRCUIT_OPEN,
    ),
    _Case(
        name="one waiting word, no bound",
        bar=False,
        answered=1_143,
        held=(
            _wait(
                FailureReason.PROVIDER_ERROR,
                words=1,
                wake=2.0,
                wait=2.0,
            ),
        ),
        retry=2.0,
    ),
    _Case(
        name="one waiting word, with the bound",
        bar=False,
        answered=1_143,
        held=(
            _wait(
                FailureReason.PROVIDER_ERROR,
                words=1,
                wake=119.0,
                wait=119.0,
            ),
        ),
        retry=119.0,
    ),
    _Case(
        name="not answering, a typical count",
        bar=False,
        answered=0,
        held=(
            _wait(
                FailureReason.CIRCUIT_OPEN,
                words=38,
                wake=9.0,
            ),
        ),
        retry=9.0,
    ),
    _Case(
        name="asked to pause, a typical count",
        bar=False,
        answered=0,
        held=(
            _wait(
                FailureReason.RATE_LIMITED,
                words=37,
                wake=45.0,
            ),
        ),
        retry=45.0,
    ),
)


@final
class TestWidth:
    @mark.parametrize(
        argnames="case",
        argvalues=_WIDEST,
        ids=tuple(case.name for case in _WIDEST),
    )
    def test_no_state_draws_more_than_two_rows_at_80_and_100_columns_or_more_than_one_at_120(
        self,
        *,
        case: _Case,
    ) -> None:
        rows = {
            width: _rows(
                _stage_of_1144(
                    case,
                    width,
                ),
                width,
            )
            for width in (
                80,
                100,
                120,
            )
        }

        assert (
            len(
                rows[80],
            ),
            len(
                rows[100],
            ),
            len(
                rows[120],
            ),
        ) <= (
            2,
            2,
            1,
        )

        drawn = "".join(
            rows[80],
        )

        assert any(glyph in drawn for glyph in _BAR_GLYPHS) is case.bar

    def test_the_widest_words_with_the_bar_drawn_would_fail_the_width_and_so_the_measure_can_fail(
        self,
    ) -> None:
        for case in (
            _WIDEST[4],
            _WIDEST[8],
        ):
            line = _stage_of_1144(
                case,
                80,
            )

            columns = line.line.columns

            line.line.columns = (
                columns[0],
                columns[1],
                BarColumn(
                    bar_width=24,
                ),
                *columns[3:],
            )

            assert (
                len(
                    _rows(
                        line,
                        80,
                    ),
                )
                > 2
            )


_IN: Final = MappingProxyType(
    mapping={
        0: "now",
        2: "in 2 s",
        119: "in 1 min 59 s",
    },
)

_WITHIN: Final = MappingProxyType(
    mapping={
        0: "now",
        2: "within 2 s",
        119: "within 1 min 59 s",
    },
)


def _words_drawn(
    *,
    wait: DictionaryWait,
    words: int,
    seconds: int,
) -> tuple[str, str, ProcessingProgress]:
    """
    What a line says for `words` words that wait for `wait` with `seconds`
    left to the next ask or check and to the end of WordNet's meaning's time,
    drawn from the stage's own reports, and the stored report that says the
    same.
    """

    clock = _FixedClock()

    line = _terminal(
        clock=clock,
    )

    line.stage_started(
        stage=Stage.DICTIONARY_ENRICHMENT,
        steps=words if wait is DictionaryWait.WAITING_WORDS else 1_000,
    )

    reason = {
        DictionaryWait.SOME_WORDS: FailureReason.PROVIDER_ERROR,
        DictionaryWait.WAITING_WORDS: FailureReason.PROVIDER_ERROR,
        DictionaryWait.NOT_ANSWERING: FailureReason.CIRCUIT_OPEN,
        DictionaryWait.ASKED_TO_PAUSE: FailureReason.RATE_LIMITED,
    }[wait]

    sleeping = wait is not DictionaryWait.SOME_WORDS

    _report(
        line,
        (
            _wait(
                reason,
                words=words,
                wake=(
                    float(
                        seconds,
                    )
                    if sleeping
                    else None
                ),
                wait=90.0,
            ),
        ),
        retry=(
            float(
                seconds,
            )
            if sleeping
            else None
        ),
        left=float(
            seconds,
        ),
    )

    bound = wait is not DictionaryWait.SOME_WORDS

    stored = ProcessingProgress(
        stage=Stage.DICTIONARY_ENRICHMENT,
        steps=words if wait is DictionaryWait.WAITING_WORDS else 1_000,
        done=0,
        unavailable=0,
        dictionary_paused=False,
        reported_at=EPOCH,
        dictionary_waiting_words=words,
        dictionary_wait=wait,
        dictionary_next_in_seconds=(
            float(
                seconds,
            )
            if sleeping
            else None
        ),
        dictionary_wordnet_within_seconds=(
            float(
                seconds,
            )
            if bound
            else None
        ),
    )

    (
        description,
        count,
    ) = _drawn(
        line,
        clock,
    )

    return (
        description,
        count,
        stored,
    )


@final
class TestTheWords:
    @mark.parametrize(
        argnames="words",
        argvalues=(
            1,
            832,
        ),
    )
    @mark.parametrize(
        argnames="seconds",
        argvalues=(
            0,
            2,
            119,
        ),
    )
    def test_each_state_draws_exactly_its_words_in_singular_and_plural_with_now_at_nought(
        self,
        *,
        words: int,
        seconds: int,
    ) -> None:
        noun = "1 word waits" if words == 1 else "832 words wait"

        expected = {
            DictionaryWait.SOME_WORDS: (
                STAGE_WORDS,
                f"0 of 1,000, {words} waiting",
            ),
            DictionaryWait.WAITING_WORDS: (
                f"{noun} to be asked again; next {_IN[seconds]}",
                f"WordNet's meaning {_WITHIN[seconds]}",
            ),
            DictionaryWait.NOT_ANSWERING: (
                f"the dictionary is not answering; next check {_IN[seconds]}",
                f"{words} waiting, WordNet's meaning {_WITHIN[seconds]}",
            ),
            DictionaryWait.ASKED_TO_PAUSE: (
                f"paused, as the dictionary asked; resumes {_IN[seconds]}",
                f"{words} waiting, WordNet's meaning {_WITHIN[seconds]}",
            ),
        }

        for wait in DictionaryWait:
            (
                description,
                count,
                stored,
            ) = _words_drawn(
                wait=wait,
                words=words,
                seconds=seconds,
            )

            assert (
                description,
                count,
            ) == expected[wait]

            clock = _FixedClock()

            reader = _terminal(
                clock=clock,
            )

            reader.processing(
                progress=stored,
            )

            assert (
                _drawn(
                    reader,
                    clock,
                )
                == expected[wait]
            )

    def test_the_two_states_after_a_check_or_a_pause_say_no_countdown(
        self,
    ) -> None:
        clock = _FixedClock()

        for (
            reason,
            expected,
        ) in (
            (
                FailureReason.CIRCUIT_OPEN,
                "the dictionary is not answering",
            ),
            (
                FailureReason.RATE_LIMITED,
                "paused, as the dictionary asked",
            ),
        ):
            line = _terminal(
                clock=clock,
            )

            line.stage_started(
                stage=Stage.DICTIONARY_ENRICHMENT,
                steps=1_000,
            )

            _report(
                line,
                (
                    _wait(
                        reason,
                        words=832,
                        wake=None,
                    ),
                ),
                left=119.0,
            )

            assert _drawn(
                line,
                clock,
            ) == (
                expected,
                "832 waiting, WordNet's meaning within 1 min 59 s",
            )

    def test_a_dictionary_that_cannot_be_asked_in_the_time_left_says_wordnets_meaning_stands_in(
        self,
    ) -> None:
        clock = _FixedClock()

        line = _terminal(
            clock=clock,
        )

        line.stage_started(
            stage=Stage.DICTIONARY_ENRICHMENT,
            steps=1_144,
        )

        for _ in range(
            12,
        ):
            line.looked_up(
                lookup=unavailable(
                    reason=FailureReason.CIRCUIT_OPEN,
                ),
            )

        assert _drawn(
            line,
            clock,
        ) == (
            "the dictionary is not answering; WordNet's meaning stands in",
            "12 of 1,144, 12 unavailable",
        )

    @mark.parametrize(
        argnames="wait",
        argvalues=tuple(
            DictionaryWait,
        ),
    )
    async def test_a_worker_stores_every_wait_with_its_count_and_its_two_times(
        self,
        *,
        wait: DictionaryWait,
    ) -> None:
        analysis = requested_analysis()

        analysis.pull_events()

        analysis.start(
            at=EPOCH,
        )

        storage = InMemoryStorage()

        async with storage.new_unit_of_work() as uow:
            await uow.analyses.add(
                analysis=analysis,
            )

            await uow.commit()

        clock = _FixedClock()

        stored = StoredProgress(
            analysis_id=analysis.id,
            new_unit_of_work=storage.new_unit_of_work,
            clock=clock,
        )

        stored.stage_started(
            stage=Stage.DICTIONARY_ENRICHMENT,
            steps=2 if wait is DictionaryWait.WAITING_WORDS else 40,
        )

        reason = {
            DictionaryWait.SOME_WORDS: FailureReason.PROVIDER_ERROR,
            DictionaryWait.WAITING_WORDS: FailureReason.PROVIDER_ERROR,
            DictionaryWait.NOT_ANSWERING: FailureReason.CIRCUIT_OPEN,
            DictionaryWait.ASKED_TO_PAUSE: FailureReason.RATE_LIMITED,
        }[wait]

        stored.dictionary_waiting(
            held=(
                _wait(
                    reason,
                    words=2,
                    wake=30.0,
                    wait=90.0,
                ),
            ),
            ending=False,
            retry_in_seconds=30.0,
            patience_left_seconds=150.0,
        )

        # NOTE:
        # The report is written ten seconds after the words were reported, and says ten seconds less of each time.
        clock.advance(
            seconds=10.0,
        )

        await stored.flush()

        report = storage.progress[analysis.id]

        sleeps = wait is not DictionaryWait.SOME_WORDS

        assert (
            report.dictionary_waiting_words,
            report.dictionary_wait,
            report.dictionary_next_in_seconds,
            report.dictionary_wordnet_within_seconds,
        ) == (
            2,
            wait,
            20.0 if sleeps else None,
            140.0 if sleeps else None,
        )

        assert report.dictionary_retry_in_seconds == 20.0

        assert report.dictionary_patience_left_seconds == 140.0


@final
class _ProgressOutOfTheLastRelease(
    BaseModel,
):
    """
    The report the last release reads: the fields it knew then.
    """

    stage: Stage

    steps: int | None

    done: int

    unavailable: int

    dictionary_paused: bool

    reported_at: datetime

    dictionary_retry_in_seconds: float | None = None

    dictionary_patience_left_seconds: float | None = None


def _older(
    *,
    paused: bool = False,
    retry: float | None = None,
    patience: float | None = None,
) -> ProcessingProgress:
    return ProcessingProgress(
        stage=Stage.DICTIONARY_ENRICHMENT,
        steps=156,
        done=40,
        unavailable=0,
        dictionary_paused=paused,
        reported_at=EPOCH,
        dictionary_retry_in_seconds=retry,
        dictionary_patience_left_seconds=patience,
    )


def _read_by_a_new_line(
    progress: ProcessingProgress,
    /,
) -> tuple[str, str, bool]:
    clock = _FixedClock()

    line = _terminal(
        clock=clock,
    )

    line.processing(
        progress=progress,
    )

    (
        description,
        count,
    ) = _drawn(
        line,
        clock,
    )

    return (
        description,
        count,
        any(
            glyph
            in "".join(
                _rows(
                    line,
                    160,
                ),
            )
            for glyph in _BAR_GLYPHS
        ),
    )


@final
class TestAnOlderReport:
    def test_a_report_written_before_the_line_named_the_waits_draws_the_stage_with_the_bar(
        self,
    ) -> None:
        assert _read_by_a_new_line(
            _older(),
        ) == (
            STAGE_WORDS,
            "40 of 156",
            True,
        )

    def test_a_paused_report_of_that_time_draws_wordnets_meaning_standing_in_without_the_bar(
        self,
    ) -> None:
        assert _read_by_a_new_line(
            _older(
                paused=True,
            ),
        ) == (
            "the dictionary is not answering; WordNet's meaning stands in",
            "40 of 156",
            False,
        )

    def test_a_waiting_report_of_that_time_draws_its_old_waiting_text_with_the_bar(
        self,
    ) -> None:
        assert _read_by_a_new_line(
            _older(
                retry=20.0,
                patience=170.0,
            ),
        ) == (
            "waiting for the dictionary: next try in 20 s, WordNet within 2 min 50 s",
            "40 of 156",
            True,
        )


@final
class TestAnOlderCommandLineKeepsWorking:
    def test_the_report_on_the_wire_is_read_by_a_model_that_knows_only_the_old_fields(
        self,
    ) -> None:
        wire = ProgressOut.from_domain(
            progress=replace(
                _older(
                    retry=20.0,
                    patience=170.0,
                ),
                dictionary_waiting_words=3,
                dictionary_wait=DictionaryWait.WAITING_WORDS,
                dictionary_next_in_seconds=20.0,
                dictionary_wordnet_within_seconds=170.0,
            ),
        )

        read = _ProgressOutOfTheLastRelease.model_validate_json(
            json_data=wire.model_dump_json(),
        )

        assert (
            read.dictionary_retry_in_seconds,
            read.dictionary_patience_left_seconds,
        ) == (
            20.0,
            170.0,
        )

        assert (
            _read_by_a_new_line(
                ProcessingProgress(
                    stage=read.stage,
                    steps=read.steps,
                    done=read.done,
                    unavailable=read.unavailable,
                    dictionary_paused=read.dictionary_paused,
                    reported_at=read.reported_at,
                    dictionary_retry_in_seconds=read.dictionary_retry_in_seconds,
                    dictionary_patience_left_seconds=read.dictionary_patience_left_seconds,
                ),
            )[0]
            == "waiting for the dictionary: next try in 20 s, WordNet within 2 min 50 s"
        )

    def test_the_wire_model_ignores_the_fields_it_does_not_know_as_it_always_has(
        self,
    ) -> None:
        assert ProgressOut.model_config.get(
            "extra",
        ) in {
            None,
            "ignore",
        }

        assert (
            ProgressOut.model_validate_json(
                json_data='{"stage": "dictionary_enrichment", "steps": 1, "done": 0, "unavailable": 0, '
                '"dictionary_paused": false, "reported_at": "1970-01-01T00:00:00Z", "unknown_later": 1}',
            ).dictionary_waiting_words
            is None
        )


@final
class _LastReport:
    """
    Keeps the held words of the last wait a `_Patience` reports.
    """

    def __init__(
        self,
    ) -> None:
        self.held: Sequence[HeldWords] = ()

    def stage_started(
        self,
        *,
        stage: Stage,
        steps: int | None,
    ) -> None:
        return

    def looked_up(
        self,
        *,
        lookup: DictionaryLookup,
    ) -> None:
        return

    def dictionary_waiting(
        self,
        *,
        held: Sequence[HeldWords],
        ending: bool,
        retry_in_seconds: float | None,
        patience_left_seconds: float,
    ) -> None:
        self.held = held


async def _sleep_until_cancelled(
    seconds: float,
    /,
) -> None:
    await Event().wait()


def _patience(
    last: _LastReport,
    /,
) -> _Patience:
    return _Patience(
        seconds=PATIENCE_SECONDS,
        clock=lambda: 0.0,
        sleep=_sleep_until_cancelled,
        progress=last,
    )


@final
class TestWhatTheStageReportsOfItsHeldWords:
    async def test_a_word_turned_away_again_is_held_for_the_reason_of_its_last_refusal(
        self,
    ) -> None:
        last = _LastReport()

        patience = _patience(
            last,
        )

        word = patience.hold(
            word=None,
            failure_reason=FailureReason.PROVIDER_ERROR,
        )

        word = patience.hold(
            word=word,
            failure_reason=FailureReason.CIRCUIT_OPEN,
        )

        sleeping = create_task(
            coro=patience.sleep(
                word=word,
                seconds=9.0,
            ),
        )

        await asyncio_sleep(
            delay=0.0,
        )

        assert tuple(held.reason for held in last.held) == (FailureReason.CIRCUIT_OPEN,)

        sleeping.cancel()

        await gather(
            sleeping,
            return_exceptions=True,
        )

    async def test_a_word_turned_away_again_after_an_answer_counts_as_turned_away(
        self,
    ) -> None:
        last = _LastReport()

        patience = _patience(
            last,
        )

        word = patience.hold(
            word=None,
            failure_reason=FailureReason.CIRCUIT_OPEN,
        )

        patience.answered()

        word = patience.hold(
            word=word,
            failure_reason=FailureReason.CIRCUIT_OPEN,
        )

        sleeping = create_task(
            coro=patience.sleep(
                word=word,
                seconds=9.0,
            ),
        )

        await asyncio_sleep(
            delay=0.0,
        )

        (held,) = last.held

        assert (
            held.turned_away,
            held.others_sleeping,
        ) == (
            1,
            0,
        )

        sleeping.cancel()

        await gather(
            sleeping,
            return_exceptions=True,
        )

    async def test_a_report_gives_the_earliest_wake_and_the_longest_wait_of_every_word_held_for_one_reason(
        self,
    ) -> None:
        last = _LastReport()

        patience = _patience(
            last,
        )

        waits = (
            2.0,
            3.0,
            4.0,
            5.0,
            6.0,
            7.0,
            8.0,
            120.0,
        )

        words = tuple(
            patience.hold(
                word=None,
                failure_reason=FailureReason.PROVIDER_ERROR,
            )
            for _ in waits
        )

        sleeping = tuple(
            create_task(
                coro=patience.sleep(
                    word=word,
                    seconds=wait,
                ),
            )
            for (
                word,
                wait,
            ) in zip(
                words,
                waits,
                strict=True,
            )
        )

        await asyncio_sleep(
            delay=0.0,
        )

        (held,) = last.held

        assert (
            held.turned_away_wake_in_seconds,
            held.wake_in_seconds,
            held.longest_wait_seconds,
        ) == (
            2.0,
            2.0,
            120.0,
        )

        for task in sleeping:
            task.cancel()

        await gather(
            *sleeping,
            return_exceptions=True,
        )
