"""
The Free Dictionary API as the project's dictionary.

The provider is a free service: an answer can take many seconds, and the
service is sometimes unreachable, so the client spreads its requests over
time, retries the failures a retry can fix, and stops asking while the
provider keeps failing.

Concurrent lookups of one lemma share one request, and every lookup ends in an
outcome rather than an exception.

An observer hears where the lookups' time goes and each retry, for whoever
turns them into metrics, and the client's activity says what it is doing at
any moment, for whoever watches it.
"""

from asyncio import (
    CancelledError,
    Future,
    get_running_loop,
    shield,
)
from asyncio import (
    sleep as asyncio_sleep,
)
from collections import (
    deque,
)
from collections.abc import (
    Awaitable,
    Sequence,
)
from dataclasses import (
    dataclass,
)
from datetime import (
    UTC,
    datetime,
)
from email.utils import (
    parsedate_to_datetime,
)
from enum import (
    auto,
)
from logging import (
    getLogger,
)
from random import (
    random,
)
from time import (
    monotonic,
)
from types import (
    MappingProxyType,
)
from typing import (
    Final,
    Protocol,
    final,
)
from urllib.parse import (
    quote,
)

from httpx2 import (
    AsyncClient,
    HTTPError,
    Response,
    TimeoutException,
)
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
)

from wordwinnow.domain.dictionary import (
    Definition,
    DictionaryEntry,
    DictionaryLookup,
    License,
    LookupOutcome,
    Meaning,
    Provenance,
)
from wordwinnow.domain.enums import (
    UnorderedStrEnum,
)
from wordwinnow.domain.language import (
    PartOfSpeech,
)
from wordwinnow.infrastructure.dictionary.resilience import (
    BreakerState,
    CircuitBreaker,
    MonotonicClock,
    Permit,
    Sleep,
    TokenBucket,
)
from wordwinnow.infrastructure.dictionary.wire import (
    FailureReason,
    unavailable,
)
from wordwinnow.infrastructure.user_agent import (
    IDENTIFYING_HEADERS,
)

# NOTE:
# How hard the client leans on a provider that is failing: three attempts per lookup, and five failed requests in a
# row before it stops asking.
MAX_ATTEMPTS: Final = 3

FAILURE_THRESHOLD: Final = 5

# PERF:
# When it asks again, with one request: ten seconds after it stopped, and ten after each such request that fails, each
# wait up to a quarter shorter at random, or as long as the provider asked when that is longer.
#
# Simulated against outages of one second to ten minutes, with the provider failing at once, the first answer after
# the provider came back arrived 4 to 5 s after it on average, where a fixed half minute took 13 to 25 s and a wait of
# five seconds doubling to fifteen took 5 to 7 s, for 19 trial requests in a three-minute outage rather than 5 or 13;
# when the provider hangs until the timeout instead, the trials take the time and the three differ little.
RECOVERY_SECONDS: Final = 10.0

_RECOVERY_JITTER: Final = 0.25

# NOTE:
# The longest wait between two attempts: the backoff stays far below it, and a provider that asks for a longer one
# ends the word's lookup, with that wait as its time to ask again.
MAX_DELAY_SECONDS: Final = 30.0

_BASE_BACKOFF_SECONDS: Final = 0.5

# NOTE:
# How far back the activity reports the client's own requests: a minute holds six recovery periods, and at most a
# hundred and twenty requests at the budget's pace.
_RECENT_SECONDS: Final = 60.0

# NOTE:
# How long the client leaves the provider alone after a 429 that names no wait it can use: one second, doubling with
# each refusal in a row, to a minute at most, half of each wait fixed and half random so that several clients do not
# come back together.
_BASE_THROTTLE_SECONDS: Final = 1.0

_MAX_THROTTLE_SECONDS: Final = 60.0

# NOTE:
# How many 429s in a row the provider's own Retry-After is trusted for: after that many, every wait it named was
# waited out and the refusals go on, so the doubling wait applies on top of the one it names.
_TRUSTED_THROTTLES: Final = 3

# NOTE:
# How many 429s one word may draw before its lookup ends as rate limited, with the pause's end as its time to ask
# again: each one pauses the whole client, so a word refused that often is one the provider keeps refusing, and five
# bounds how long a lookup can circle.
MAX_REFUSALS: Final = 5

# NOTE:
# The longest Retry-After read as a wait, a day; a longer one is not a wait anyone means, and counts as no header at
# all.
_MAX_RETRY_AFTER_SECONDS: Final = 86_400.0

# NOTE:
# The statuses a retry can fix: the provider is throttling, or something between the client and the provider is
# failing.
#
# A 404 is an answer, and any other 4xx is a request the provider will keep refusing.
_RETRYABLE_STATUSES: Final = frozenset(
    (
        # NOTE:
        # Too Many Requests.
        429,
        # NOTE:
        # Internal Server Error.
        500,
        # NOTE:
        # Bad Gateway.
        502,
        # NOTE:
        # Service Unavailable.
        503,
        # NOTE:
        # Gateway Timeout.
        504,
        # NOTE:
        # Connection Timed Out.
        522,
    ),
)

# NOTE:
# The reasons an attempt is retried for, in the order `FailureReason` declares them: a request that timed out, a
# connection that failed, and the two kinds of answer `_RETRYABLE_STATUSES` holds.
#
# A metrics registry starts a retry counter at zero for each, so a dashboard can show that no retry happened.
RETRYABLE_REASONS: Final = (
    FailureReason.TIMED_OUT,
    FailureReason.TRANSPORT_ERROR,
    FailureReason.RATE_LIMITED,
    FailureReason.PROVIDER_ERROR,
)

_logger: Final = getLogger(
    name="wordwinnow.dictionary",
)


class ProviderDefinition(
    BaseModel,
):
    """
    One definition as the provider sends it.
    """

    model_config = ConfigDict(
        extra="ignore",
    )

    definition: str | None = None

    example: str | None = None


class ProviderMeaning(
    BaseModel,
):
    """
    One part of speech of an entry as the provider sends it.
    """

    model_config = ConfigDict(
        extra="ignore",
    )

    part_of_speech: str | None = Field(
        default=None,
        alias="partOfSpeech",
    )

    definitions: list[ProviderDefinition] = []


class ProviderPhonetic(
    BaseModel,
):
    """
    One pronunciation as the provider sends it; the text may be absent when
    only audio is offered.
    """

    model_config = ConfigDict(
        extra="ignore",
    )

    text: str | None = None


class ProviderLicense(
    BaseModel,
):
    """
    The license the provider reports for an entry.
    """

    model_config = ConfigDict(
        extra="ignore",
    )

    name: str | None = None

    url: str | None = None


class ProviderEntry(
    BaseModel,
):
    """
    One entry as the provider sends it.
    """

    model_config = ConfigDict(
        extra="ignore",
    )

    word: str = ""

    phonetic: str | None = None

    phonetics: list[ProviderPhonetic] = []

    meanings: list[ProviderMeaning] = []

    license: ProviderLicense | None = None

    source_urls: list[str] = Field(
        default=[],
        alias="sourceUrls",
    )


_PROVIDER_ENTRIES: Final = TypeAdapter(
    type=list[ProviderEntry],
)


def translate_entries(
    *,
    payload: Sequence[ProviderEntry],
) -> tuple[DictionaryEntry, ...]:
    """
    The project's entries for what the provider sent.

    A meaning without a usable definition and an entry without a usable
    meaning are dropped, because the domain model has no place for them.

    A word class outside the four the project studies keeps the provider's
    label and has no part of speech.
    """

    entries: list[DictionaryEntry] = []

    for provider_entry in payload:
        headword = provider_entry.word.strip()

        meanings = tuple(
            meaning
            for provider_meaning in provider_entry.meanings
            if (
                meaning := _meaning_of(
                    provider_meaning,
                )
            )
            is not None
        )

        if not headword or not meanings:
            continue

        entries.append(
            DictionaryEntry(
                headword=headword,
                phonetic=_phonetic_of(
                    provider_entry,
                ),
                meanings=meanings,
                provenance=_provenance_of(
                    provider_entry,
                ),
            ),
        )

    return tuple(
        entries,
    )


def _meaning_of(
    provider_meaning: ProviderMeaning,
    /,
) -> Meaning | None:
    definitions = tuple(
        Definition(
            text=text,
            example=_text_or_none(
                provider_definition.example,
            ),
        )
        for provider_definition in provider_meaning.definitions
        if (
            text := _text_or_none(
                provider_definition.definition,
            )
        )
        is not None
    )

    if not definitions:
        return None

    word_class = (provider_meaning.part_of_speech or "").strip()

    return Meaning(
        word_class=word_class,
        part_of_speech=_part_of_speech_of(
            word_class,
        ),
        definitions=definitions,
    )


def _part_of_speech_of(
    word_class: str,
    /,
) -> PartOfSpeech | None:
    try:
        return PartOfSpeech(
            value=word_class.lower(),
        )

    except ValueError:
        return None


def _provenance_of(
    provider_entry: ProviderEntry,
    /,
) -> Provenance | None:
    provider_license = provider_entry.license

    license_name = _text_or_none(
        provider_license.name if provider_license is not None else None,
    )

    license_url = _text_or_none(
        provider_license.url if provider_license is not None else None,
    )

    source_urls = tuple(url for raw in provider_entry.source_urls if (url := raw.strip()))

    entry_license = (
        License(
            name=license_name,
            url=license_url,
        )
        if license_name is not None and license_url is not None
        else None
    )

    if entry_license is None and not source_urls:
        return None

    return Provenance(
        license=entry_license,
        source_urls=source_urls,
    )


def _phonetic_of(
    provider_entry: ProviderEntry,
    /,
) -> str | None:
    candidates = (
        provider_entry.phonetic,
        *(phonetic.text for phonetic in provider_entry.phonetics),
    )

    return next(
        (
            text
            for candidate in candidates
            if (
                text := _text_or_none(
                    candidate,
                )
            )
            is not None
        ),
        None,
    )


def _text_or_none(
    value: str | None,
    /,
) -> str | None:
    if value is None:
        return None

    stripped = value.strip()

    return stripped or None


class WaitCause(
    UnorderedStrEnum,
):
    """
    What a lookup is waiting for: the request budget, the pause the provider
    asked the client for, the provider's answer, the pause before another
    attempt, or the outcome of the trial request that tests a provider the
    client had stopped asking.
    """

    BUDGET = auto()

    COOLDOWN = auto()

    PROVIDER = auto()

    RETRY = auto()

    TRIAL = auto()


class RequestOutcome(
    UnorderedStrEnum,
):
    """
    How a request the client sent came back, as the client saw it: answered,
    found or not; refused with a 429; or failed, by a server error, a timeout,
    a connection that failed, or an answer it could not read.
    """

    ANSWERED = auto()

    REFUSED = auto()

    FAILED = auto()


class ProviderObserver(
    Protocol,
):
    """
    Hears where the client's lookups spend their time, and each retry.
    """

    def waited(
        self,
        *,
        cause: WaitCause,
        seconds: float,
    ) -> None:
        """
        A lookup waited `seconds` for `cause`.
        """

        ...

    def retried(
        self,
        *,
        reason: FailureReason,
    ) -> None:
        """
        A lookup is about to ask again after an attempt that failed for
        `reason`.
        """

        ...


@final
class _Unobserved:
    """
    The observer of a client nobody counts.
    """

    def waited(
        self,
        *,
        cause: WaitCause,
        seconds: float,
    ) -> None:
        return

    def retried(
        self,
        *,
        reason: FailureReason,
    ) -> None:
        return


_UNOBSERVED: Final = _Unobserved()


@dataclass(
    frozen=True,
    kw_only=True,
    slots=True,
)
@final
class LookupActivity:
    """
    One lookup in flight: the word, what it is waiting for, for which attempt,
    and for how many seconds so far.

    A pause before another attempt names its length and why the attempt before
    failed, and the lookup that tests the provider after the circuit opened is
    the trial.
    """

    lemma: str

    waiting_for: WaitCause

    attempt: int

    seconds: float

    delay_seconds: float | None = None

    reason: FailureReason | None = None

    trial: bool = False


@dataclass(
    frozen=True,
    kw_only=True,
    slots=True,
)
@final
class RecentRequest:
    """
    A request the client sent whose outcome came back in the last minute: how
    many seconds ago, and how.
    """

    seconds: float

    outcome: RequestOutcome


@dataclass(
    frozen=True,
    kw_only=True,
    slots=True,
)
@final
class ProviderActivity:
    """
    What the client is doing at one moment: the circuit, the request budget,
    each lookup in flight, oldest first, and the retries since it started.

    `retry_in_seconds` is set while the circuit is open, and `timeout_seconds`
    is how long one request may take.

    `cooldown_seconds` is how long the pause the provider asked for has to
    run, for every lookup, and is `None` when no pause is in force.

    `consecutive_failures` is the breaker's own count of requests that failed
    in a row toward `failure_threshold`, unlike any count of unanswered words,
    and both are `None` where the activity does not report them.

    `failed_trials` is how many trials failed since the circuit last opened
    from closed, and `opened_by` why the request that opened or last reopened
    it failed, while it is open or half-open; both are `None` where the
    activity does not report them.

    `recent` is each of the client's own requests whose outcome came back in
    the last minute, oldest first, and `last_answer_seconds` how long ago one
    was last answered, `None` when none has been; both say what the client saw
    of its own requests, and nothing of what the provider did otherwise.
    """

    circuit: BreakerState

    retry_in_seconds: float | None

    cooldown_seconds: float | None

    consecutive_failures: int | None

    failure_threshold: int | None

    tokens: float

    burst: int

    requests_per_second: float

    waiting_for_budget: int

    timeout_seconds: float

    lookups: tuple[LookupActivity, ...]

    retries: MappingProxyType[FailureReason, int]

    failed_trials: int | None = None

    opened_by: FailureReason | None = None

    recent: tuple[RecentRequest, ...] = ()

    last_answer_seconds: float | None = None


@dataclass(
    frozen=True,
    kw_only=True,
    slots=True,
)
@final
class _Phase:
    """
    What one lookup in flight is waiting for, and since when.
    """

    waiting_for: WaitCause

    attempt: int

    since: float

    delay_seconds: float | None

    reason: FailureReason | None

    trial: bool


@dataclass(
    frozen=True,
    kw_only=True,
    slots=True,
)
@final
class _Retry:
    """
    A failed attempt that a later attempt may fix.

    `status` is the HTTP status when the provider answered, and
    `retry_after_seconds` the wait it asked for, as it asked.
    """

    reason: FailureReason

    status: int | None

    retry_after_seconds: float | None


@final
class FreeDictionaryClient:
    """
    Looks words up at the Free Dictionary API.

    Satisfies `Dictionary` structurally.
    """

    def __init__(
        self,
        *,
        base_url: str,
        timeout_seconds: float,
        requests_per_second: float,
        burst: int,
        client: AsyncClient,
        sleep: Sleep = asyncio_sleep,
        clock: MonotonicClock = monotonic,
        observer: ProviderObserver | None = None,
    ) -> None:
        self._base_url: Final = base_url.rstrip(
            "/",
        )

        self._timeout: Final = timeout_seconds

        self._client: Final = client

        self._sleep: Final = sleep

        self._clock: Final = clock

        self._observer: Final = observer if observer is not None else _UNOBSERVED

        self._bucket: Final = TokenBucket(
            rate_per_second=requests_per_second,
            burst=burst,
            clock=clock,
            sleep=sleep,
        )

        self._breaker: Final = CircuitBreaker(
            failure_threshold=FAILURE_THRESHOLD,
            recovery_seconds=RECOVERY_SECONDS,
            jitter=_RECOVERY_JITTER,
            clock=clock,
        )

        self._in_flight: Final[dict[str, Future[DictionaryLookup]]] = {}

        self._phases: Final[dict[str, _Phase]] = {}

        self._retries: Final[dict[FailureReason, int]] = {}

        self._cooldown_until = clock()

        self._throttles = 0

        # NOTE:
        # When the last refusal counted toward the pause arrived, so that the refusals of requests already in flight
        # then are counted once.
        self._throttled_at = float(
            "-inf",
        )

        # NOTE:
        # When each of the last minute's requests came back, and how, and when one was last answered.
        self._requests: Final[deque[tuple[float, RequestOutcome]]] = deque()

        self._answered_at: float | None = None

        # NOTE:
        # Why the request that opened the circuit, or a trial that reopened it, failed, for an operator asking why the
        # client stopped; nothing while the circuit is closed.
        self._opened_by: FailureReason | None = None

    def activity(
        self,
    ) -> ProviderActivity:
        """
        What the client is doing now.
        """

        now = self._clock()

        # NOTE:
        # A lookup waiting for a token waits for the end of the provider's pause while one is in force, because the
        # paused budget issues nothing before it, and for the rate once it is over, whichever it began waiting for.
        paused = self._cooldown_until > now

        return ProviderActivity(
            circuit=self._breaker.state,
            retry_in_seconds=self._breaker.retry_in_seconds,
            cooldown_seconds=(cooldown if (cooldown := self._cooldown_until - now) > 0 else None),
            consecutive_failures=self._breaker.consecutive_failures,
            failure_threshold=self._breaker.failure_threshold,
            tokens=self._bucket.tokens,
            burst=self._bucket.burst,
            requests_per_second=self._bucket.rate_per_second,
            waiting_for_budget=self._bucket.waiting,
            timeout_seconds=self._timeout,
            lookups=tuple(
                LookupActivity(
                    lemma=key,
                    waiting_for=(
                        (WaitCause.COOLDOWN if paused else WaitCause.BUDGET)
                        if phase.waiting_for is WaitCause.BUDGET or phase.waiting_for is WaitCause.COOLDOWN
                        else phase.waiting_for
                    ),
                    attempt=phase.attempt,
                    seconds=now - phase.since,
                    delay_seconds=phase.delay_seconds,
                    reason=phase.reason,
                    trial=phase.trial,
                )
                for (
                    key,
                    phase,
                ) in self._phases.items()
            ),
            retries=MappingProxyType(
                mapping=dict(
                    self._retries,
                ),
            ),
            failed_trials=self._breaker.failed_trials,
            opened_by=None if self._breaker.state is BreakerState.CLOSED else self._opened_by,
            recent=tuple(
                RecentRequest(
                    seconds=now - at,
                    outcome=outcome,
                )
                for (
                    at,
                    outcome,
                ) in self._requests
                if now - at <= _RECENT_SECONDS
            ),
            last_answer_seconds=None if self._answered_at is None else now - self._answered_at,
        )

    async def look_up(
        self,
        *,
        lemma: str,
    ) -> DictionaryLookup:
        key = lemma.strip().lower()

        pending = self._in_flight.get(
            key,
        )

        if pending is not None:
            # NOTE:
            # The shield keeps a follower's cancellation from canceling the leader's future under everyone else.
            return await shield(
                arg=pending,
            )

        future: Future[DictionaryLookup] = get_running_loop().create_future()

        self._in_flight[key] = future

        lookup: DictionaryLookup | None = None

        try:
            lookup = await self._resolve(
                key,
            )

        except Exception as exception:
            # WARN:
            # The port promises an outcome, so a defect on this path is reported as a failed lookup rather than raised
            # into the pipeline, where it would fail the whole analysis.
            lookup = self._unexpected(
                key,
                exception,
                False,
            )

        finally:
            del self._in_flight[key]

            self._phases.pop(
                key,
                None,
            )

            if not future.done():
                future.set_result(
                    lookup
                    if lookup is not None
                    else unavailable(
                        reason=FailureReason.TRANSPORT_ERROR,
                    ),
                )

        return lookup

    async def _resolve(
        self,
        key: str,
        /,
    ) -> DictionaryLookup:
        # NOTE:
        # Every lookup waits for the outcome of a trial in flight, as every lookup waits for the budget, and the wait
        # takes no time when there is none.
        permit = await self._waiting(
            key=key,
            cause=WaitCause.TRIAL,
            attempt=1,
            trial=False,
            pending=self._breaker.permit(),
        )

        if permit is Permit.REFUSED:
            # NOTE:
            # The refusal read the clock once and the breaker reads it again, and the trial can come due in between,
            # so the time to ask again is never less than nought and never missing: the circuit refused, so asking
            # again is right.
            lookup = unavailable(
                reason=FailureReason.CIRCUIT_OPEN,
                retry_after_seconds=max(
                    0.0,
                    self._breaker.retry_in_seconds or 0.0,
                ),
            )

            _log(
                key,
                lookup,
                0,
            )

            return lookup

        trial = permit is Permit.TRIAL

        # NOTE:
        # The trial this lookup holds: a 429 makes the trial an ordinary lookup and closes the circuit, after which a
        # later trial is another lookup's, and this one's cancellation or failure must leave that trial alone.
        held = self._breaker.trial if trial else None

        try:
            if trial:
                _logger.debug(
                    msg="dictionary.circuit.trial",
                    extra={
                        "lemma": key,
                    },
                )

            return await self._ask(
                key,
                trial,
            )

        except CancelledError:
            # WARN:
            # A trial canceled before its outcome would leave every later lookup waiting for it, so the breaker hears
            # that it was abandoned, and the next lookup is the trial.
            if held is not None and self._breaker.trial is held:
                self._breaker.abandon()

            raise

        except Exception as exception:
            # WARN:
            # Only the trial's own outcome ends the trial, so a defect on a permitted call's path is reported here,
            # where it is known whether the call is the trial; reported by `look_up` instead, a failed trial would
            # leave every later lookup waiting for it.
            return self._unexpected(
                key,
                exception,
                held is not None and self._breaker.trial is held,
            )

    async def _ask(
        self,
        key: str,
        trial: bool,
        /,
    ) -> DictionaryLookup:
        attempt = 0

        failures = 0

        run: int | None = None

        refusals = 0

        while True:
            attempt += 1

            # NOTE:
            # The circuit can open while a lookup waits for its token or before another of its attempts, and only the
            # trial asks a provider the breaker has stopped asking; the failures that opened the circuit are already
            # counted, so this refusal is not counted again.
            if not trial and self._breaker.state is not BreakerState.CLOSED:
                return self._refused(
                    key,
                    attempt - 1,
                )

            # NOTE:
            # A lookup waits out a pause of the provider's as long as it waits for a retry at most, so the client
            # never holds one of the pipeline's lookups for longer than that at a time; a longer pause ends the word's
            # lookup as rate limited, with the pause's end as its time to ask again, and the pause stands for every
            # other lookup.
            admitted = self._cooldown_until - self._clock() <= MAX_DELAY_SECONDS and await self._waiting(
                key=key,
                cause=WaitCause.COOLDOWN if self._cooldown_until > self._clock() else WaitCause.BUDGET,
                attempt=attempt,
                trial=trial,
                pending=self._bucket.acquire(
                    max_pause_seconds=MAX_DELAY_SECONDS,
                ),
            )

            if not admitted:
                return self._waited_out(
                    key,
                    attempt - 1,
                    trial,
                )

            if not trial and self._breaker.state is not BreakerState.CLOSED:
                return self._refused(
                    key,
                    attempt - 1,
                )

            sent = self._clock()

            result = await self._waiting(
                key=key,
                cause=WaitCause.PROVIDER,
                attempt=attempt,
                trial=trial,
                pending=self._attempt(
                    key,
                ),
            )

            self._observed(
                result,
            )

            if isinstance(
                result,
                DictionaryLookup,
            ):
                if result.outcome is not LookupOutcome.UNAVAILABLE:
                    self._throttles = 0

                return self._settle(
                    key,
                    result,
                    attempt,
                    trial,
                )

            # NOTE:
            # A 429 is about the client, not the word: the provider asks every lookup to slow down, so the whole
            # client pauses, as long as the provider asked or, without a usable Retry-After, a wait that doubles with
            # each refusal in a row; the refused word is asked again after the pause, without spending one of its
            # attempts, and the refusal is no failure of the provider's.
            if result.reason is FailureReason.RATE_LIMITED:
                refusals += 1

                self._throttle(
                    key,
                    attempt,
                    result,
                    sent,
                )

                # NOTE:
                # A 429 is an answer from a provider that is up: the trial's closes the circuit, and any other starts
                # the breaker's count of failures in a row again, as every other answer does.
                if trial:
                    self._record(
                        True,
                        None,
                        trial,
                        None,
                    )

                    trial = False

                else:
                    self._breaker.record_success(
                        trial=False,
                    )

                if refusals >= MAX_REFUSALS:
                    return self._waited_out(
                        key,
                        attempt,
                        trial,
                    )

                self._observer.retried(
                    reason=result.reason,
                )

                self._retries[result.reason] = (
                    self._retries.get(
                        result.reason,
                        0,
                    )
                    + 1
                )

                continue

            failures += 1

            # NOTE:
            # The run of failures in a row the lookup's first failure joins, noted before the failure is counted.
            if run is None:
                run = self._breaker.failure_run

            asked = result.retry_after_seconds

            self._record(
                False,
                result.reason,
                trial,
                asked,
            )

            # NOTE:
            # The trial is one request rather than a whole lookup: one answer says the provider is back, one failure
            # that it is not, and a lookup that spent its attempts on a provider still down would only keep every
            # other lookup waiting.
            #
            # A failure that opened the circuit, this one or another lookup's, ends the lookup with its own reason
            # rather than waiting out a backoff only to be refused.
            if trial or self._breaker.state is not BreakerState.CLOSED or failures >= MAX_ATTEMPTS:
                hint = self._recovery_hint()

                # NOTE:
                # A word whose attempts are spent while the circuit is still closed, with no answer counted since its
                # first failure, may have failed for the provider rather than for itself, so it names the next backoff
                # as its time to ask again.
                #
                # Asked again, it adds to the run until five failures open the circuit and it waits for the trial as
                # every other word does, or it meets an answer; a word whose failures an answer interrupted names no
                # time, because asking it again would only fail again.
                if hint is None and self._breaker.failure_run == run:
                    hint = _delay_before(
                        failures + 1,
                        result,
                    )

                return self._ended(
                    key,
                    unavailable(
                        reason=result.reason,
                        retry_after_seconds=hint,
                    ),
                    attempt,
                )

            # NOTE:
            # A provider that asks for a longer wait than the client allows is not asked about the word again before
            # it said: the lookup ends now with the time the provider named, rather than holding one of the pipeline's
            # lookups for an attempt the provider has said is too early, and the analysis asks again then if its
            # waiting time covers it, or WordNet stands in.
            #
            # The case is not hypothetical: when the provider's own server is unreachable, its CDN answers every word
            # it has not cached with HTTP 522 after about twenty seconds and asks for two minutes, while it still
            # answers the words it has cached, which is why, while the circuit is closed, such a wait holds back only
            # the word it was asked for; a failure that opens the circuit holds back the trial too, as every wait a
            # failure names does.
            if asked is not None and asked > MAX_DELAY_SECONDS:
                _logger.debug(
                    msg="dictionary.retry.declined",
                    extra={
                        "lemma": key,
                        "attempt": attempt,
                        "reason": result.reason,
                        "status": result.status,
                        "retry_after_seconds": asked,
                    },
                )

                return self._ended(
                    key,
                    unavailable(
                        reason=result.reason,
                        retry_after_seconds=asked,
                    ),
                    attempt,
                )

            delay = _delay_before(
                failures + 1,
                result,
            )

            # NOTE:
            # One line per retry is a debugging aid, not an event a service log at INFO should carry; the delay is the
            # jittered backoff, or the provider's own `Retry-After` when it sent one.
            _logger.debug(
                msg="dictionary.retry",
                extra={
                    "lemma": key,
                    "attempt": attempt + 1,
                    "reason": result.reason,
                    "status": result.status,
                    "delay_seconds": round(
                        delay,
                        3,
                    ),
                    "retry_after_seconds": asked,
                },
            )

            self._observer.retried(
                reason=result.reason,
            )

            self._retries[result.reason] = (
                self._retries.get(
                    result.reason,
                    0,
                )
                + 1
            )

            await self._waiting(
                key=key,
                cause=WaitCause.RETRY,
                attempt=attempt + 1,
                trial=trial,
                pending=self._sleep(
                    delay,
                ),
                delay_seconds=delay,
                reason=result.reason,
            )

    def _observed(
        self,
        result: DictionaryLookup | _Retry,
        /,
    ) -> None:
        now = self._clock()

        if isinstance(
            result,
            DictionaryLookup,
        ):
            answered = result.outcome is not LookupOutcome.UNAVAILABLE

            outcome = RequestOutcome.ANSWERED if answered else RequestOutcome.FAILED

        else:
            refused = result.reason is FailureReason.RATE_LIMITED

            outcome = RequestOutcome.REFUSED if refused else RequestOutcome.FAILED

        if outcome is RequestOutcome.ANSWERED:
            self._answered_at = now

        self._requests.append(
            (
                now,
                outcome,
            ),
        )

        while self._requests and now - self._requests[0][0] > _RECENT_SECONDS:
            self._requests.popleft()

    def _throttle(
        self,
        key: str,
        attempt: int,
        refusal: _Retry,
        sent: float,
        /,
    ) -> None:
        asked = refusal.retry_after_seconds

        # NOTE:
        # Every request in flight when the provider began refusing comes back refused too, and those refusals are one
        # refusal: only a request sent after the last refusal counted is refused in a row, so only it doubles the wait
        # and wears down the trust in the provider's Retry-After.
        #
        # An earlier request's refusal still holds the client for as long as it names, which is the provider's own
        # statement, and adds nothing when it names no wait.
        seconds: float | None = asked

        if sent > self._throttled_at:
            self._throttles += 1

            self._throttled_at = self._clock()

            base = min(
                _BASE_THROTTLE_SECONDS * 2 ** (self._throttles - 1),
                _MAX_THROTTLE_SECONDS,
            )

            doubling = base / 2 + random() * base / 2

            seconds = (
                doubling
                if asked is None
                else max(
                    asked,
                    doubling,
                )
                if self._throttles > _TRUSTED_THROTTLES
                else asked
            )

        if seconds is not None:
            until = self._clock() + seconds

            if until > self._cooldown_until:
                self._cooldown_until = until

                self._bucket.pause(
                    until=until,
                )

        _logger.debug(
            msg="dictionary.cooldown",
            extra={
                "lemma": key,
                "attempt": attempt,
                "retry_after_seconds": asked,
                "cooldown_seconds": None
                if seconds is None
                else round(
                    number=seconds,
                    ndigits=3,
                ),
                "in_a_row": self._throttles,
            },
        )

    def _refused(
        self,
        key: str,
        attempts: int,
        /,
    ) -> DictionaryLookup:
        return self._ended(
            key,
            unavailable(
                reason=FailureReason.CIRCUIT_OPEN,
                retry_after_seconds=self._recovery_hint(),
            ),
            attempts,
        )

    def _recovery_hint(
        self,
    ) -> float | None:
        # NOTE:
        # Every refusal that can pass says when it may: the wait for the trial, or for the provider's pause, or the
        # wait the provider named; while the circuit is closed there is no trial to wait for, and a lookup that spent
        # its attempts decides for itself.
        if self._breaker.state is BreakerState.CLOSED:
            return None

        return max(
            0.0,
            self._breaker.retry_in_seconds or 0.0,
        )

    def _ended(
        self,
        key: str,
        lookup: DictionaryLookup,
        attempts: int,
        /,
    ) -> DictionaryLookup:
        _log(
            key,
            lookup,
            attempts,
        )

        return lookup

    def _waited_out(
        self,
        key: str,
        attempts: int,
        trial: bool,
        /,
    ) -> DictionaryLookup:
        # WARN:
        # A trial that ends without an outcome would leave every later lookup waiting for it, so the breaker hears
        # that it was abandoned, and the next lookup is the trial.
        if trial:
            self._breaker.abandon()

        return self._ended(
            key,
            unavailable(
                reason=FailureReason.RATE_LIMITED,
                retry_after_seconds=max(
                    0.0,
                    self._cooldown_until - self._clock(),
                ),
            ),
            attempts,
        )

    async def _waiting[T](
        self,
        *,
        key: str,
        cause: WaitCause,
        attempt: int,
        trial: bool,
        pending: Awaitable[T],
        delay_seconds: float | None = None,
        reason: FailureReason | None = None,
    ) -> T:
        started = self._clock()

        self._phases[key] = _Phase(
            waiting_for=cause,
            attempt=attempt,
            since=started,
            delay_seconds=delay_seconds,
            reason=reason,
            trial=trial,
        )

        try:
            return await pending

        finally:
            self._observer.waited(
                cause=cause,
                seconds=self._clock() - started,
            )

    async def _attempt(
        self,
        key: str,
        /,
    ) -> DictionaryLookup | _Retry:
        try:
            response = await self._client.get(
                url=f"{self._base_url}/{
                    quote(
                        string=key,
                        safe='',
                    )
                }",
                headers=IDENTIFYING_HEADERS,
                timeout=self._timeout,
            )

        except TimeoutException:
            return _Retry(
                reason=FailureReason.TIMED_OUT,
                status=None,
                retry_after_seconds=None,
            )

        except HTTPError:
            return _Retry(
                reason=FailureReason.TRANSPORT_ERROR,
                status=None,
                retry_after_seconds=None,
            )

        return _outcome_of(
            response,
        )

    def _settle(
        self,
        key: str,
        lookup: DictionaryLookup,
        attempts: int,
        trial: bool,
        /,
    ) -> DictionaryLookup:
        self._record(
            lookup.outcome is not LookupOutcome.UNAVAILABLE,
            lookup.failure_reason,
            trial,
            None,
        )

        _log(
            key,
            lookup,
            attempts,
        )

        return lookup

    def _unexpected(
        self,
        key: str,
        exception: Exception,
        trial: bool,
        /,
    ) -> DictionaryLookup:
        self._record(
            False,
            FailureReason.TRANSPORT_ERROR,
            trial,
            None,
        )

        kind = type(
            exception,
        ).__name__

        _logger.warning(
            msg="dictionary.lookup.failed",
            exc_info=exception,
            extra={
                "lemma": key,
                "kind": kind,
            },
        )

        return unavailable(
            reason=FailureReason.TRANSPORT_ERROR,
        )

    def _record(
        self,
        answered: bool,
        failure_reason: str | None,
        trial: bool,
        retry_after_seconds: float | None,
        /,
    ) -> None:
        before = self._breaker.state

        if answered:
            self._breaker.record_success(
                trial=trial,
            )

        else:
            self._breaker.record_failure(
                trial=trial,
                retry_after_seconds=retry_after_seconds,
            )

        # NOTE:
        # The tokens saved while the circuit was open are not kept: the lookups that waited for the trial go on one
        # token at a time, not together.
        if trial and answered:
            self._bucket.pause(
                until=self._clock(),
            )

        after = self._breaker.state

        if after is BreakerState.OPEN and failure_reason is not None and (before is BreakerState.CLOSED or trial):
            self._opened_by = FailureReason(
                value=failure_reason,
            )

        # NOTE:
        # One warning per outage: the circuit opening from closed is the event an operator needs, while a failed trial
        # reopening it every recovery period and the circuit closing again are the outage's course, at INFO.
        #
        # Only the trial's outcome is that course: a lookup that ends while the circuit is open changes at most when
        # the next trial may start, and once the recovery period is over the state alone cannot tell it from a trial.
        if not trial and after is BreakerState.OPEN and before is BreakerState.CLOSED:
            _logger.warning(
                msg="dictionary.circuit.opened",
                extra={
                    "failure_reason": failure_reason,
                    "recovery_seconds": self._breaker.retry_in_seconds,
                },
            )

        elif trial and not answered:
            _logger.info(
                msg="dictionary.circuit.reopened",
                extra={
                    "failure_reason": failure_reason,
                    "recovery_seconds": self._breaker.retry_in_seconds,
                },
            )

        elif trial:
            _logger.info(
                msg="dictionary.circuit.closed",
            )


def _outcome_of(
    response: Response,
    /,
) -> DictionaryLookup | _Retry:
    status = response.status_code

    if status == 200:
        return _translate(
            response,
        )

    if status == 404:
        return DictionaryLookup(
            outcome=LookupOutcome.NOT_FOUND,
        )

    if status == 429:
        return _Retry(
            reason=FailureReason.RATE_LIMITED,
            status=status,
            retry_after_seconds=_retry_after(
                response,
            ),
        )

    if status in _RETRYABLE_STATUSES:
        return _Retry(
            reason=FailureReason.PROVIDER_ERROR,
            status=status,
            retry_after_seconds=_retry_after(
                response,
            ),
        )

    return unavailable(
        reason=FailureReason.PROVIDER_ERROR,
    )


def _translate(
    response: Response,
    /,
) -> DictionaryLookup:
    try:
        payload = _PROVIDER_ENTRIES.validate_json(
            response.content,
        )

    except ValueError:
        return unavailable(
            reason=FailureReason.INVALID_RESPONSE,
        )

    entries = translate_entries(
        payload=payload,
    )

    if not entries:
        return DictionaryLookup(
            outcome=LookupOutcome.NOT_FOUND,
        )

    return DictionaryLookup(
        outcome=LookupOutcome.FOUND,
        entries=entries,
    )


def _retry_after(
    response: Response,
    /,
) -> float | None:
    # WARN:
    # `Headers.get` declares its key positional-only in the overloads the type checker reads, although the
    # implementation also takes it as a keyword, so the key is passed positionally.
    header = response.headers.get(
        "retry-after",
    )

    if header is None:
        return None

    value = header.strip()

    # NOTE:
    # HTTP defines the header as whole seconds or a date, and anything else, a fraction, a sign, or a word, is no wait
    # the provider named, so the client decides the wait itself.
    if value.isascii() and value.isdigit():
        seconds = float(
            value,
        )

    else:
        try:
            seconds = max(
                0.0,
                (
                    parsedate_to_datetime(
                        data=value,
                    )
                    - datetime.now(
                        tz=UTC,
                    )
                ).total_seconds(),
            )

        except (
            TypeError,
            ValueError,
        ):
            return None

    if seconds > _MAX_RETRY_AFTER_SECONDS:
        return None

    return seconds


def _delay_before(
    attempt: int,
    retry: _Retry,
    /,
) -> float:
    if retry.retry_after_seconds is not None:
        return retry.retry_after_seconds

    # NOTE:
    # Equal jitter: half the exponential step is fixed and half is random, so retries from many workers spread out
    # without any of them coming back at once.
    base = min(
        _BASE_BACKOFF_SECONDS * 2 ** (attempt - 2),
        MAX_DELAY_SECONDS,
    )

    return base / 2 + random() * base / 2


def _log(
    key: str,
    lookup: DictionaryLookup,
    attempts: int,
    /,
) -> None:
    # NOTE:
    # One line per word is too many for a service log at INFO; the lookup counter carries the outcomes.
    _logger.debug(
        msg="dictionary.lookup",
        extra={
            "lemma": key,
            "outcome": lookup.outcome,
            "failure_reason": lookup.failure_reason,
            "attempts": attempts,
        },
    )
