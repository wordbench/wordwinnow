"""
A dictionary stage recorded as a view of its mechanics would read it, against
a provider that misbehaves on cue.

The stage is the product's own: the Free Dictionary client behind the caching
dictionary, run by the pipeline's dictionary stage on a virtual-time loop, and
watched by the product's own `watching` every quarter second.

Only the provider is invented, a script of chapters behind a mock transport.

A recording holds what a listener heard, with the virtual time of each, and
beside it what only the recorder knows: which requests, sleeps, and ending
belong to which word.
"""

from asyncio import (
    Event,
    Task,
    create_task,
    current_task,
    get_running_loop,
    wait,
)
from asyncio import (
    sleep as asyncio_sleep,
)
from collections.abc import (
    Sequence,
)
from collections.abc import (
    Set as AbstractSet,
)
from dataclasses import (
    dataclass,
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
from types import (
    MappingProxyType,
)
from typing import (
    Any,
    Final,
    assert_never,
    final,
)
from unittest.mock import (
    patch,
)

from httpx2 import (
    AsyncClient,
    MockTransport,
    ReadTimeout,
    Request,
    Response,
)

from tests.fakes.ports import (
    FakeLevelEstimator,
    FakeLexicalSemantics,
    FakeLinguisticAnalyzer,
    FakeReferenceLexicon,
    FakeWordFrequency,
)
from tests.fakes.virtual_time import (
    run_in_virtual_time,
)
from wordwinnow.application.ports.pipeline_progress import (
    HeldWords,
)
from wordwinnow.application.use_cases.process_an_analysis import (
    Pipeline,
    _look_up_all,
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
from wordwinnow.domain.language import (
    PartOfSpeech,
)
from wordwinnow.domain.study import (
    DictionaryInformation,
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
from wordwinnow.infrastructure.dictionary.wire import (
    to_wire,
)
from wordwinnow.services.progress import (
    ACTIVITY_INTERVAL_SECONDS,
    watching,
)

_BASE_URL: Final = "https://provider.test/api/v2/entries/en"

_FOUND_TTL_SECONDS: Final = 30 * 24 * 60 * 60

_NOT_FOUND_TTL_SECONDS: Final = 7 * 24 * 60 * 60

# NOTE:
# Longer than any script runs, so a word put in the cache before a stage is still there at its end.
_LIFETIME_SECONDS: Final = 10**9

# NOTE:
# A chapter that starts at a request's own instant covers it, whatever rounding put the two a hair apart.
_CHAPTER_EPSILON_SECONDS: Final = 1e-9

_FAILED: Final = "F"

_ANSWERED: Final = "A"

_REFUSED: Final = "R"

_NO_SUCH_WORD: Final = 404

_FOUND_STATUS: Final = 200

_REFUSED_STATUS: Final = 429

_FAILED_STATUS: Final = 503

_HANGING_STATUS: Final = 522


@dataclass(
    frozen=True,
    kw_only=True,
    slots=True,
)
@final
class Answering:
    """
    The provider answers every request after `least_seconds` and a random
    share of `spread_seconds`: found for a word the script calls found, not
    found for any other.
    """

    least_seconds: float = 0.6

    spread_seconds: float = 1.4


@dataclass(
    frozen=True,
    kw_only=True,
    slots=True,
)
@final
class Flaky:
    """
    The provider fails or answers each request after `latency_seconds`, by
    `pattern`, which is `F` for a failure with `status` and `A` for an answer,
    one letter for each request in the order they arrive, cycling.
    """

    pattern: str = "FFFAFFFFAFFA"

    latency_seconds: float = 0.3

    status: int = _FAILED_STATUS

    def __post_init__(
        self,
    ) -> None:
        if not self.pattern or set(
            self.pattern,
        ) - {
            _FAILED,
            _ANSWERED,
        }:
            raise ValueError(
                f"a flaky pattern is letters F and A and at least one, not {self.pattern!r}",
            )


@dataclass(
    frozen=True,
    kw_only=True,
    slots=True,
)
@final
class Down:
    """
    The provider fails every request with `status` after `latency_seconds`.
    """

    latency_seconds: float = 2.0

    status: int = _FAILED_STATUS


@dataclass(
    frozen=True,
    kw_only=True,
    slots=True,
)
@final
class Refusing:
    """
    The provider refuses every request with 429 after `latency_seconds`,
    asking for `retry_after_seconds` in `Retry-After` when it names them.
    """

    latency_seconds: float = 0.2

    retry_after_seconds: float | None = None


@dataclass(
    frozen=True,
    kw_only=True,
    slots=True,
)
@final
class Window:
    """
    The provider answers `answers` requests in each window of
    `window_seconds`, after `least_seconds` and a random share of
    `spread_seconds`, and refuses the rest with 429, naming the window's
    seconds left, rounded up and at least one, in `Retry-After`.

    A request counts its window's answers before it waits and adds its own
    after, so requests that overlap can share one count, and a window can
    answer more than `answers`.
    """

    answers: int = 20

    window_seconds: float = 20.0

    least_seconds: float = 0.15

    spread_seconds: float = 0.35


@dataclass(
    frozen=True,
    kw_only=True,
    slots=True,
)
@final
class Hanging:
    """
    The provider holds every request for `latency_seconds` and then fails it
    with 522, asking for `retry_after_seconds` in `Retry-After` when it names
    them.
    """

    latency_seconds: float = 20.0

    retry_after_seconds: float | None = 120


@dataclass(
    frozen=True,
    kw_only=True,
    slots=True,
)
@final
class Silent:
    """
    The provider never answers: each request waits the client's timeout and
    then times out.
    """


@dataclass(
    frozen=True,
    kw_only=True,
    slots=True,
)
@final
class Written:
    """
    The provider answers each request after `latency_seconds` by `pattern`,
    one letter for each request in the order they arrive, with the last letter
    repeating: `A` answers, `F` fails with 503, and `R` refuses with 429,
    asking for `retry_after_seconds` in `Retry-After` when it names them.

    Spaces in the pattern are ignored.
    """

    pattern: str

    latency_seconds: float = 0.3

    retry_after_seconds: float | None = None

    def __post_init__(
        self,
    ) -> None:
        letters = self.pattern.replace(
            " ",
            "",
        )

        if not letters or set(
            letters,
        ) - {
            _ANSWERED,
            _FAILED,
            _REFUSED,
        }:
            raise ValueError(
                f"a written pattern is letters A, F, and R and at least one, not {self.pattern!r}",
            )


type Behavior = Answering | Flaky | Down | Refusing | Window | Hanging | Silent | Written


@dataclass(
    frozen=True,
    kw_only=True,
    slots=True,
)
@final
class Chapter:
    """
    What the provider does from the moment `at` on, until the next chapter
    that has begun by the time a request arrives.
    """

    at: float

    behavior: Behavior


@dataclass(
    frozen=True,
    kw_only=True,
    slots=True,
)
@final
class Word:
    """
    A word the stage looks up, with whether the provider finds it and whether
    the cache already holds the answer, found or not, before the stage begins.
    """

    lemma: str

    part_of_speech: PartOfSpeech

    found: bool

    cached: bool = False


@dataclass(
    frozen=True,
    kw_only=True,
    slots=True,
)
@final
class Settings:
    """
    How the stage runs: `concurrency` lookups at once, waiting
    `patience_seconds` in all for a dictionary that asks to be asked again,
    the client's `rate_per_second` and `burst` or its own where they are
    `None`, and its `timeout_seconds`.

    The stage begins `starts_seconds` after the watch, the recording ends the
    first read at or after `after_seconds` past the stage's end or at or after
    `limit_seconds`, and the client's jitter draws from the generator `seed`
    seeds.
    """

    concurrency: int = 8

    patience_seconds: float = 180.0

    rate_per_second: float | None = None

    burst: int | None = None

    timeout_seconds: float = 30.0

    starts_seconds: float = 0.5

    after_seconds: float = 3.0

    limit_seconds: float = 200.0

    seed: int = 0


@dataclass(
    frozen=True,
    kw_only=True,
    slots=True,
)
@final
class Script:
    """
    A provider script, the words the stage looks up in the order it asks them,
    and the settings it runs with.

    A lemma may repeat under different parts of speech, as an analysis can
    hold it, and a pair of lemma and part of speech may not.

    The provider and the cache answer a lemma, so the words that share one
    agree on whether it is found and whether the cache holds it.
    """

    chapters: tuple[Chapter, ...]

    words: tuple[Word, ...]

    settings: Settings = Settings()

    def __post_init__(
        self,
    ) -> None:
        if not self.chapters:
            raise ValueError(
                "a script needs at least one chapter",
            )

        seen: dict[tuple[str, PartOfSpeech], Word] = {}

        answers: dict[str, tuple[bool, bool]] = {}

        for word in self.words:
            lemma = _key_of(
                word.lemma,
            )

            if (
                lemma,
                word.part_of_speech,
            ) in seen:
                raise ValueError(
                    f"a script's words are unique by lemma and part of speech, and {word.lemma!r} as a {
                        word.part_of_speech
                    } is repeated",
                )

            seen[
                (
                    lemma,
                    word.part_of_speech,
                )
            ] = word

            if answers.setdefault(
                lemma,
                (
                    word.found,
                    word.cached,
                ),
            ) != (
                word.found,
                word.cached,
            ):
                raise ValueError(
                    f"the words with the lemma {word.lemma!r} disagree on whether it is found or cached",
                )


@dataclass(
    frozen=True,
    kw_only=True,
    slots=True,
)
@final
class Watched:
    """
    The watch read `activity` at the virtual time `at`.
    """

    at: float

    activity: DictionaryActivity


@dataclass(
    frozen=True,
    kw_only=True,
    slots=True,
)
@final
class StageStarted:
    """
    The pipeline reported at `at` that `stage` began, taking `steps` steps.
    """

    at: float

    stage: Stage

    steps: int | None


@dataclass(
    frozen=True,
    kw_only=True,
    slots=True,
)
@final
class LookedUp:
    """
    One word of the stage had `lookup` for its answer at `at`.
    """

    at: float

    lookup: DictionaryLookup


@dataclass(
    frozen=True,
    kw_only=True,
    slots=True,
)
@final
class DictionaryWaiting:
    """
    The stage reported at `at` the words it held, as the progress port's
    `dictionary_waiting` takes them.
    """

    at: float

    held: tuple[HeldWords, ...]

    ending: bool

    retry_in_seconds: float | None

    patience_left_seconds: float


type Heard = Watched | StageStarted | LookedUp | DictionaryWaiting


@dataclass(
    frozen=True,
    kw_only=True,
    slots=True,
)
@final
class Received:
    """
    One request the provider received for `lemma`, the `number`th to arrive,
    at `arrived`.

    It was answered at `answered` with `status` and the `Retry-After` header
    the provider sent, `None` where it sent none.

    A request that timed out has an `answered` and no `status`.

    A request that never came back has neither, because the stage let its word
    go, or the recording ended, before the provider answered.
    """

    number: int

    lemma: str

    arrived: float

    answered: float | None

    status: int | None

    retry_after: str | None


@dataclass(
    frozen=True,
    kw_only=True,
    slots=True,
)
@final
class Slept:
    """
    One sleep the stage gave a word: it began at `began`, was given `seconds`,
    and ended at `ended`, which is earlier when the stage cut it short.
    """

    began: float

    seconds: float

    ended: float


@dataclass(
    frozen=True,
    kw_only=True,
    slots=True,
)
@final
class WordTruth:
    """
    What only the recorder knows about one word, the one at its position in
    the script: the requests the provider received for its lemma, in the order
    they arrived, the sleeps the stage gave it, and its ending.

    A lemma's requests belong to every word with that lemma, since the client
    looks a lemma up once for all of them.

    The ending is the answer the stage handed back for the word, and `None`
    when the stage was canceled first, because a canceled stage hands back
    no answer for any word, one that had ended before the cancellation
    included.
    """

    lemma: str

    requests: tuple[Received, ...]

    sleeps: tuple[Slept, ...]

    ending: DictionaryInformation | None


@dataclass(
    frozen=True,
    kw_only=True,
    slots=True,
)
@final
class Recording:
    """
    One recorded dictionary stage.

    `timeline` is everything a listener heard, in the order it heard it.

    `words` holds the truth about each word, in the script's order.

    `stage_ended_at` is when the stage ended, `None` when the recording ended
    first and the stage was canceled, and `requests` is how many requests the
    provider received.
    """

    timeline: tuple[Heard, ...]

    words: tuple[WordTruth, ...]

    stage_ended_at: float | None

    requests: int


def _key_of(
    lemma: str,
    /,
) -> str:
    return lemma.strip().lower()


def _now() -> float:
    return get_running_loop().time()


@dataclass(
    frozen=True,
    kw_only=True,
    slots=True,
)
@final
class _Reply:
    """
    What the provider says to one request: a status, and the `Retry-After`
    header when it sends one.
    """

    status: int

    retry_after: str | None = None


def _asked_in(
    seconds: float | None,
    /,
) -> str | None:
    return (
        None
        if seconds is None
        else str(
            object=seconds,
        )
    )


def _lemma_of(
    request: Request,
    /,
) -> str:
    return request.url.path.rsplit(
        sep="/",
        maxsplit=1,
    )[-1]


def _chapter_at(
    chapters: Sequence[Chapter],
    now: float,
    /,
) -> Behavior:
    current = chapters[0]

    for chapter in chapters:
        if chapter.at <= now + _CHAPTER_EPSILON_SECONDS:
            current = chapter

    return current.behavior


@final
class _Provider:
    """
    The scripted provider: what it does with a request depends on the chapter
    the request arrives in, and it keeps what it received.

    The waits that draw at random draw from a generator of their own, one draw
    for each request that needs one, in the order the requests arrive.
    """

    def __init__(
        self,
        *,
        script: Script,
    ) -> None:
        self._chapters: Final = script.chapters

        self._found: Final = MappingProxyType(
            mapping={
                _key_of(
                    word.lemma,
                ): word.found
                for word in script.words
            },
        )

        self._random: Final = Random(
            x=script.settings.seed + 1,
        )

        self._timeout: Final = script.settings.timeout_seconds

        self._flaky = 0

        self._written = 0

        # NOTE:
        # The window the last answer or refusal was counted in, and how many answers it had counted then.
        #
        # The count is read before a request waits and written after, which is this provider's own model of a window,
        # so requests that overlap in a window count one answer between them.
        self._window: tuple[int, int] = (
            -1,
            0,
        )

        self.count = 0

        self._taken: Final[dict[int, tuple[str, Received]]] = {}

    def taken(
        self,
    ) -> MappingProxyType[str, tuple[Received, ...]]:
        """
        The requests received for each lemma, in the order they arrived.
        """

        by_lemma: dict[str, list[Received]] = {}

        for number in sorted(
            self._taken,
        ):
            (
                lemma,
                received,
            ) = self._taken[number]

            by_lemma.setdefault(
                lemma,
                [],
            ).append(
                received,
            )

        return MappingProxyType(
            mapping={
                lemma: tuple(
                    requests,
                )
                for (
                    lemma,
                    requests,
                ) in by_lemma.items()
            },
        )

    async def receive(
        self,
        *,
        request: Request,
    ) -> Response:
        arrived = _now()

        lemma = _lemma_of(
            request,
        )

        self.count += 1

        number = self.count

        behavior = _chapter_at(
            self._chapters,
            arrived,
        )

        reply: _Reply | None = None

        answered: float | None = None

        try:
            reply = await self._reply(
                behavior,
                lemma,
                arrived,
            )

            answered = _now()

        finally:
            self._taken[number] = (
                lemma,
                Received(
                    number=number,
                    lemma=lemma,
                    arrived=arrived,
                    answered=answered,
                    status=None if reply is None else reply.status,
                    retry_after=None if reply is None else reply.retry_after,
                ),
            )

        if reply is None:
            raise ReadTimeout(
                message="the scripted provider never answered",
                request=request,
            )

        if reply.status == _FOUND_STATUS:
            return Response(
                status_code=reply.status,
                json=[
                    {
                        "word": lemma,
                        "meanings": [
                            {
                                "partOfSpeech": "noun",
                                "definitions": [
                                    {
                                        "definition": "A scripted definition.",
                                    },
                                ],
                            },
                        ],
                    },
                ],
            )

        return Response(
            status_code=reply.status,
            headers=(
                {}
                if reply.retry_after is None
                else {
                    "retry-after": reply.retry_after,
                }
            ),
        )

    def _answer(
        self,
        lemma: str,
        /,
    ) -> _Reply:
        return _Reply(
            status=_FOUND_STATUS if self._found[lemma] else _NO_SUCH_WORD,
        )

    async def _reply(
        self,
        behavior: Behavior,
        lemma: str,
        arrived: float,
        /,
    ) -> _Reply | None:
        match behavior:
            case Answering(
                least_seconds=least,
                spread_seconds=spread,
            ):
                await asyncio_sleep(
                    delay=least + spread * self._random.random(),
                )

                return self._answer(
                    lemma,
                )

            case Flaky(
                pattern=pattern,
                latency_seconds=latency,
                status=status,
            ):
                failed = (
                    pattern[
                        self._flaky
                        % len(
                            pattern,
                        )
                    ]
                    == _FAILED
                )

                self._flaky += 1

                await asyncio_sleep(
                    delay=latency,
                )

                return (
                    _Reply(
                        status=status,
                    )
                    if failed
                    else self._answer(
                        lemma,
                    )
                )

            case Down(
                latency_seconds=latency,
                status=status,
            ):
                await asyncio_sleep(
                    delay=latency,
                )

                return _Reply(
                    status=status,
                )

            case Refusing(
                latency_seconds=latency,
                retry_after_seconds=wait,
            ):
                await asyncio_sleep(
                    delay=latency,
                )

                return _Reply(
                    status=_REFUSED_STATUS,
                    retry_after=_asked_in(
                        wait,
                    ),
                )

            case Window(
                answers=answers,
                window_seconds=size,
                least_seconds=least,
                spread_seconds=spread,
            ):
                index = int(
                    arrived // size,
                )

                (
                    seen,
                    used,
                ) = self._window

                if seen != index:
                    used = 0

                await asyncio_sleep(
                    delay=least + spread * self._random.random(),
                )

                if used < answers:
                    self._window = (
                        index,
                        used + 1,
                    )

                    return self._answer(
                        lemma,
                    )

                self._window = (
                    index,
                    used,
                )

                return _Reply(
                    status=_REFUSED_STATUS,
                    retry_after=str(
                        object=max(
                            ceil(
                                (index + 1) * size - _now(),
                            ),
                            1,
                        ),
                    ),
                )

            case Hanging(
                latency_seconds=latency,
                retry_after_seconds=wait,
            ):
                await asyncio_sleep(
                    delay=latency,
                )

                return _Reply(
                    status=_HANGING_STATUS,
                    retry_after=_asked_in(
                        wait,
                    ),
                )

            case Silent():
                await asyncio_sleep(
                    delay=self._timeout,
                )

                return None

            case Written(
                pattern=pattern,
                latency_seconds=latency,
                retry_after_seconds=wait,
            ):
                letters = pattern.replace(
                    " ",
                    "",
                )

                letter = letters[
                    min(
                        self._written,
                        len(
                            letters,
                        )
                        - 1,
                    )
                ]

                self._written += 1

                await asyncio_sleep(
                    delay=latency,
                )

                if letter == _ANSWERED:
                    return self._answer(
                        lemma,
                    )

                if letter == _FAILED:
                    return _Reply(
                        status=_FAILED_STATUS,
                    )

                return _Reply(
                    status=_REFUSED_STATUS,
                    retry_after=_asked_in(
                        wait,
                    ),
                )

            case _:
                assert_never(
                    behavior,
                )


@final
class _Names:
    """
    Which word each task of the stage is looking up, by its position in the
    script, which no part of the product is told.
    """

    def __init__(
        self,
        *,
        words: Sequence[Word],
    ) -> None:
        self._words: Final = tuple(
            _key_of(
                word.lemma,
            )
            for word in words
        )

        self._positions: Final[dict[Task[Any], int]] = {}

    def tag(
        self,
        *,
        lemma: str,
    ) -> None:
        """
        The current task is looking up `lemma`, as it first says, and is the
        earliest word with that lemma that no task has been given.
        """

        task = current_task()

        # NOTE:
        # The stage starts one task for each word in the script's order, and the semaphore serves the tasks that wait
        # in the order they began to wait, so the tasks first ask in the words' order and the earliest unmapped word
        # with a lemma is the one whose task asks.
        if task is not None and task not in self._positions:
            self._positions[task] = next(
                position
                for (
                    position,
                    key,
                ) in enumerate(
                    iterable=self._words,
                )
                if key
                == _key_of(
                    lemma,
                )
                and position not in self._positions.values()
            )

    def current(
        self,
    ) -> int | None:
        """
        The position of the word the current task is looking up, once it has
        asked.
        """

        task = current_task()

        return (
            None
            if task is None
            else self._positions.get(
                task,
            )
        )


@final
class _Naming:
    """
    The dictionary the pipeline is given: the caching dictionary, with the
    current task named for the word it asks about, and every call passed
    through.
    """

    def __init__(
        self,
        *,
        inner: CachingDictionary,
        names: _Names,
    ) -> None:
        self._inner: Final = inner

        self._names: Final = names

    async def look_up(
        self,
        *,
        lemma: str,
    ) -> DictionaryLookup:
        self._names.tag(
            lemma=lemma,
        )

        return await self._inner.look_up(
            lemma=lemma,
        )


@final
class _Listener:
    """
    The watch and the progress the stage reports to: it keeps what it hears in
    order, and ends the recording at the first read that says so.

    Once the recording has ended it hears nothing more, so the reports a
    canceled stage makes as it unwinds are not part of what a view read.
    """

    def __init__(
        self,
        *,
        settings: Settings,
    ) -> None:
        self._settings: Final = settings

        self.timeline: Final[list[Heard]] = []

        self.finished: Final = Event()

        self.stage_ended_at: float | None = None

    def stage_ended(
        self,
    ) -> None:
        """
        The stage has handed back its answers.
        """

        self.stage_ended_at = _now()

    def watched(
        self,
        *,
        activity: DictionaryActivity,
    ) -> None:
        if self.finished.is_set():
            return

        now = _now()

        self.timeline.append(
            Watched(
                at=now,
                activity=activity,
            ),
        )

        settings = self._settings

        if (
            self.stage_ended_at is not None and now >= self.stage_ended_at + settings.after_seconds
        ) or now >= settings.limit_seconds:
            self.finished.set()

    def unwatched(
        self,
        *,
        reason: str,
    ) -> None:
        # NOTE:
        # The recorder's read cannot fail, so there is never a reason to hear.
        return

    def stage_started(
        self,
        *,
        stage: Stage,
        steps: int | None,
    ) -> None:
        if self.finished.is_set():
            return

        self.timeline.append(
            StageStarted(
                at=_now(),
                stage=stage,
                steps=steps,
            ),
        )

    def looked_up(
        self,
        *,
        lookup: DictionaryLookup,
    ) -> None:
        if self.finished.is_set():
            return

        self.timeline.append(
            LookedUp(
                at=_now(),
                lookup=lookup,
            ),
        )

    def dictionary_waiting(
        self,
        *,
        held: Sequence[HeldWords],
        ending: bool,
        retry_in_seconds: float | None,
        patience_left_seconds: float,
    ) -> None:
        if self.finished.is_set():
            return

        self.timeline.append(
            DictionaryWaiting(
                at=_now(),
                held=tuple(
                    held,
                ),
                ending=ending,
                retry_in_seconds=retry_in_seconds,
                patience_left_seconds=patience_left_seconds,
            ),
        )


def _cached_lookup(
    word: Word,
    /,
) -> DictionaryLookup:
    if not word.found:
        return DictionaryLookup(
            outcome=LookupOutcome.NOT_FOUND,
        )

    return DictionaryLookup(
        outcome=LookupOutcome.FOUND,
        entries=(
            DictionaryEntry(
                headword=word.lemma,
                phonetic=None,
                meanings=(
                    Meaning(
                        word_class="noun",
                        part_of_speech=PartOfSpeech.NOUN,
                        definitions=(
                            Definition(
                                text="A scripted definition.",
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )


async def _record(
    script: Script,
    /,
) -> Recording:
    loop = get_running_loop()

    settings = script.settings

    cache = InMemoryDictionaryCache(
        clock=loop.time,
    )

    for word in script.words:
        if word.cached:
            await cache.set(
                key=cache_key(
                    lemma=word.lemma,
                ),
                value=to_wire(
                    lookup=_cached_lookup(
                        word,
                    ),
                ).model_dump_json(),
                ttl_seconds=_LIFETIME_SECONDS,
            )

    provider = _Provider(
        script=script,
    )

    async def handler(
        request: Request,
        /,
    ) -> Response:
        return await provider.receive(
            request=request,
        )

    slept: dict[int, list[Slept]] = {}

    names = _Names(
        words=script.words,
    )

    async def sleep(
        seconds: float,
        /,
    ) -> None:
        began = loop.time()

        try:
            await asyncio_sleep(
                delay=seconds,
            )

        finally:
            position = names.current()

            if position is not None:
                slept.setdefault(
                    position,
                    [],
                ).append(
                    Slept(
                        began=began,
                        seconds=seconds,
                        ended=loop.time(),
                    ),
                )

    listener = _Listener(
        settings=settings,
    )

    async with AsyncClient(
        transport=MockTransport(
            handler=handler,
        ),
    ) as http:
        client = FreeDictionaryClient(
            base_url=_BASE_URL,
            timeout_seconds=settings.timeout_seconds,
            requests_per_second=settings.rate_per_second,
            burst=settings.burst,
            client=http,
            clock=loop.time,
        )

        caching = CachingDictionary(
            inner=client,
            found_ttl_seconds=_FOUND_TTL_SECONDS,
            not_found_ttl_seconds=_NOT_FOUND_TTL_SECONDS,
            cache=cache,
        )

        pipeline = Pipeline(
            linguistics=FakeLinguisticAnalyzer(),
            frequency=FakeWordFrequency(),
            reference_lexicon=FakeReferenceLexicon(),
            level_estimator=FakeLevelEstimator(),
            dictionary=_Naming(
                inner=caching,
                names=names,
            ),
            lexical_semantics=FakeLexicalSemantics(),
            dictionary_concurrency=settings.concurrency,
            progress=listener,
            dictionary_patience_seconds=settings.patience_seconds,
            clock=loop.time,
            sleep=sleep,
        )

        items = tuple(
            VocabularyItem(
                lemma=word.lemma,
                part_of_speech=word.part_of_speech,
                occurrence_count=1,
                example_sentence="A sentence.",
            )
            for word in script.words
        )

        async def read() -> DictionaryActivity:
            return DictionaryActivity(
                counts=caching.counts(),
                provider=client.activity(),
            )

        async def run_stage() -> tuple[DictionaryInformation | None, ...]:
            await asyncio_sleep(
                delay=settings.starts_seconds,
            )

            listener.stage_started(
                stage=Stage.DICTIONARY_ENRICHMENT,
                steps=len(
                    items,
                ),
            )

            answers = await _look_up_all(
                items,
                range(
                    len(
                        items,
                    ),
                ),
                pipeline,
            )

            listener.stage_ended()

            return answers

        # NOTE:
        # The watch is entered before the stage begins, as the command line enters it.
        async with watching(
            read=read,
            watch=listener,
            interval_seconds=ACTIVITY_INTERVAL_SECONDS,
        ):
            stage = create_task(
                coro=run_stage(),
            )

            await listener.finished.wait()

            stage.cancel()

            await wait(
                fs=(stage,),
            )

    answers = None if stage.cancelled() else stage.result()

    taken = provider.taken()

    return Recording(
        timeline=tuple(
            listener.timeline,
        ),
        words=tuple(
            WordTruth(
                lemma=word.lemma,
                requests=taken.get(
                    _key_of(
                        word.lemma,
                    ),
                    (),
                ),
                sleeps=tuple(
                    slept.get(
                        index,
                        (),
                    ),
                ),
                ending=None if answers is None else answers[index],
            )
            for (
                index,
                word,
            ) in enumerate(
                iterable=script.words,
            )
        ),
        stage_ended_at=listener.stage_ended_at,
        requests=provider.count,
    )


def record(
    *,
    script: Script,
) -> Recording:
    """
    Run the dictionary stage `script` describes on virtual time, and record
    it.

    The global random generator is put back as it was.
    """

    before = getstate()

    # NOTE:
    # The client's jitter draws from the global generator, which the script's seed sets, and the provider's waits draw
    # from a generator of their own, so the same script gives the same recording.
    seed(
        a=script.settings.seed,
    )

    # NOTE:
    # A tracer provider that another test installed for the whole process draws every span's identifier from the
    # shared generator, and so moves the draws the breaker and the backoff take from the seed.
    #
    # The identifiers draw from a generator of their own while the stage runs, so the seed reaches only the client.
    try:
        with patch(
            target="opentelemetry.sdk.trace.id_generator.random",
            new=Random(),
        ):
            return run_in_virtual_time(
                main=lambda: _record(
                    script,
                ),
            )

    finally:
        setstate(
            state=before,
        )


# NOTE:
# Lemmas that occur in the sample story, the project's canonical example, whether or not an analysis would study them.
_STORY_WORDS: Final = (
    (
        "photograph",
        PartOfSpeech.NOUN,
    ),
    (
        "mask",
        PartOfSpeech.NOUN,
    ),
    (
        "groom",
        PartOfSpeech.NOUN,
    ),
    (
        "rocket",
        PartOfSpeech.NOUN,
    ),
    (
        "landau",
        PartOfSpeech.NOUN,
    ),
    (
        "ulster",
        PartOfSpeech.NOUN,
    ),
    (
        "villa",
        PartOfSpeech.NOUN,
    ),
    (
        "carriage",
        PartOfSpeech.NOUN,
    ),
    (
        "coachman",
        PartOfSpeech.NOUN,
    ),
    (
        "smoke",
        PartOfSpeech.VERB,
    ),
    (
        "remarkable",
        PartOfSpeech.ADJECTIVE,
    ),
    (
        "sovereign",
        PartOfSpeech.NOUN,
    ),
    (
        "door",
        PartOfSpeech.NOUN,
    ),
    (
        "king",
        PartOfSpeech.NOUN,
    ),
    (
        "lady",
        PartOfSpeech.NOUN,
    ),
    (
        "street",
        PartOfSpeech.NOUN,
    ),
    (
        "window",
        PartOfSpeech.NOUN,
    ),
    (
        "letter",
        PartOfSpeech.NOUN,
    ),
    (
        "chamber",
        PartOfSpeech.NOUN,
    ),
    (
        "cigar",
        PartOfSpeech.NOUN,
    ),
    (
        "hansom",
        PartOfSpeech.NOUN,
    ),
    (
        "woman",
        PartOfSpeech.NOUN,
    ),
    (
        "friend",
        PartOfSpeech.NOUN,
    ),
    (
        "room",
        PartOfSpeech.NOUN,
    ),
    (
        "house",
        PartOfSpeech.NOUN,
    ),
    (
        "night",
        PartOfSpeech.NOUN,
    ),
    (
        "morning",
        PartOfSpeech.NOUN,
    ),
    (
        "case",
        PartOfSpeech.NOUN,
    ),
    (
        "client",
        PartOfSpeech.NOUN,
    ),
    (
        "gentleman",
        PartOfSpeech.NOUN,
    ),
)


def _story_words(
    *,
    count: int,
    not_found: AbstractSet[str] = frozenset(),
    cached: AbstractSet[str] = frozenset(),
) -> tuple[Word, ...]:
    """
    `count` lemmas that occur in the sample story, taken in the order
    `_STORY_WORDS` lists them, with the lemmas in `not_found` unknown to the
    provider and those in `cached` already in the cache.
    """

    chosen = _STORY_WORDS[:count]

    unknown = (not_found | cached) - {
        lemma
        for (
            lemma,
            _,
        ) in chosen
    }

    if unknown:
        raise ValueError(
            f"the first {count} story words do not include {
                sorted(
                    unknown,
                )
            }",
        )

    return tuple(
        Word(
            lemma=lemma,
            part_of_speech=part_of_speech,
            found=lemma not in not_found,
            cached=lemma in cached,
        )
        for (
            lemma,
            part_of_speech,
        ) in chosen
    )


ANSWERING: Final = Script(
    chapters=(
        Chapter(
            at=0.0,
            behavior=Answering(),
        ),
    ),
    words=_story_words(
        count=12,
        not_found=frozenset(
            (
                "landau",
                "ulster",
            ),
        ),
        cached=frozenset(
            (
                "mask",
                "ulster",
            ),
        ),
    ),
)

SOME_REQUESTS_FAILING: Final = Script(
    chapters=(
        Chapter(
            at=0.0,
            behavior=Flaky(
                pattern="FFFA",
            ),
        ),
    ),
    words=_story_words(
        count=12,
        not_found=frozenset(
            ("landau",),
        ),
    ),
)

REFUSING: Final = Script(
    chapters=(
        Chapter(
            at=0.0,
            behavior=Answering(),
        ),
        Chapter(
            at=2.0,
            behavior=Refusing(
                retry_after_seconds=6,
            ),
        ),
        Chapter(
            at=3.0,
            behavior=Answering(),
        ),
    ),
    words=_story_words(
        count=12,
        not_found=frozenset(
            ("landau",),
        ),
    ),
)

DOWN_AND_BACK: Final = Script(
    chapters=(
        Chapter(
            at=0.0,
            behavior=Answering(),
        ),
        Chapter(
            at=2.0,
            behavior=Down(),
        ),
        Chapter(
            at=14.0,
            behavior=Answering(),
        ),
    ),
    words=_story_words(
        count=12,
        not_found=frozenset(
            ("landau",),
        ),
    ),
)

DOWN_LONGER_THAN_PATIENCE: Final = Script(
    chapters=(
        Chapter(
            at=0.0,
            behavior=Answering(),
        ),
        Chapter(
            at=2.0,
            behavior=Down(),
        ),
        Chapter(
            at=40.0,
            behavior=Answering(),
        ),
    ),
    words=_story_words(
        count=12,
        not_found=frozenset(
            ("landau",),
        ),
    ),
    settings=Settings(
        patience_seconds=20.0,
        limit_seconds=100.0,
    ),
)

NEVER_ANSWERING: Final = Script(
    chapters=(
        Chapter(
            at=0.0,
            behavior=Silent(),
        ),
    ),
    words=_story_words(
        count=12,
        not_found=frozenset(
            ("landau",),
        ),
    ),
    settings=Settings(
        timeout_seconds=10.0,
        limit_seconds=60.0,
    ),
)

ANSWERING_SLOWLY: Final = Script(
    chapters=(
        Chapter(
            at=0.0,
            behavior=Answering(
                least_seconds=5.0,
                spread_seconds=3.0,
            ),
        ),
    ),
    words=_story_words(
        count=12,
        not_found=frozenset(
            ("landau",),
        ),
    ),
)

# NOTE:
# The provider as it was measured once: 20 answers in each 20 s window, the rest refused until the window ends.
#
# Requests that overlap in a window can share one count, so a window can answer a few more than 20, and the script
# claims no more than that the provider refuses some requests with a wait.
MEASURED_LIMIT: Final = Script(
    chapters=(
        Chapter(
            at=0.0,
            behavior=Window(
                answers=20,
                window_seconds=20.0,
            ),
        ),
    ),
    words=_story_words(
        count=30,
        not_found=frozenset(
            (
                "landau",
                "hansom",
            ),
        ),
    ),
)
