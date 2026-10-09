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
# How hard the client leans on a provider that is failing: three attempts per lookup, and five failed lookups in a row
# before it stops asking for half a minute.
MAX_ATTEMPTS: Final = 3

FAILURE_THRESHOLD: Final = 5

RECOVERY_SECONDS: Final = 30.0

# NOTE:
# The longest wait between two attempts: the backoff stays far below it, and a provider that asks for a longer one is
# not asked about that word again.
MAX_DELAY_SECONDS: Final = 30.0

_BASE_BACKOFF_SECONDS: Final = 0.5

# NOTE:
# The statuses a retry can fix: the provider is throttling, or something between the client and the provider is
# failing.
#
# A 404 is an answer, and any other 4xx is a request the provider will keep refusing.
_RETRYABLE_STATUSES: Final = frozenset(
    {
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
    },
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
    What a lookup is waiting for: the request budget, the provider's answer,
    the pause before another attempt, or the outcome of the trial request that
    tests a provider the client had stopped asking.
    """

    BUDGET = auto()

    PROVIDER = auto()

    RETRY = auto()

    TRIAL = auto()


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
class ProviderActivity:
    """
    What the client is doing at one moment: the circuit, the request budget,
    each lookup in flight, oldest first, and the retries since it started.

    `retry_in_seconds` is set while the circuit is open, and `timeout_seconds`
    is how long one request may take.
    """

    circuit: BreakerState

    retry_in_seconds: float | None

    tokens: float

    burst: int

    requests_per_second: float

    waiting_for_budget: int

    timeout_seconds: float

    lookups: tuple[LookupActivity, ...]

    retries: MappingProxyType[FailureReason, int]


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
            clock=clock,
        )

        self._in_flight: Final[dict[str, Future[DictionaryLookup]]] = {}

        self._phases: Final[dict[str, _Phase]] = {}

        self._retries: Final[dict[FailureReason, int]] = {}

    def activity(
        self,
    ) -> ProviderActivity:
        """
        What the client is doing now.
        """

        now = self._clock()

        return ProviderActivity(
            circuit=self._breaker.state,
            retry_in_seconds=self._breaker.retry_in_seconds,
            tokens=self._bucket.tokens,
            burst=self._bucket.burst,
            requests_per_second=self._bucket.rate_per_second,
            waiting_for_budget=self._bucket.waiting,
            timeout_seconds=self._timeout,
            lookups=tuple(
                LookupActivity(
                    lemma=key,
                    waiting_for=phase.waiting_for,
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
            lookup = unavailable(
                reason=FailureReason.CIRCUIT_OPEN,
            )

            _log(
                key,
                lookup,
                0,
            )

            return lookup

        trial = permit is Permit.TRIAL

        if trial:
            _logger.debug(
                msg="dictionary.circuit.trial",
                extra={
                    "lemma": key,
                },
            )

        try:
            return await self._ask(
                key,
                trial,
            )

        except CancelledError:
            # WARN:
            # A trial canceled before its outcome would leave every later lookup waiting for it, so the breaker hears
            # that it was abandoned, and the next lookup is the trial.
            if trial:
                self._breaker.abandon()

            raise

    async def _ask(
        self,
        key: str,
        trial: bool,
        /,
    ) -> DictionaryLookup:
        attempt = 0

        while True:
            attempt += 1

            await self._waiting(
                key=key,
                cause=WaitCause.BUDGET,
                attempt=attempt,
                trial=trial,
                pending=self._bucket.acquire(),
            )

            result = await self._waiting(
                key=key,
                cause=WaitCause.PROVIDER,
                attempt=attempt,
                trial=trial,
                pending=self._attempt(
                    key,
                ),
            )

            if isinstance(
                result,
                DictionaryLookup,
            ):
                return self._settle(
                    key,
                    result,
                    attempt,
                )

            if attempt >= MAX_ATTEMPTS:
                return self._settle(
                    key,
                    unavailable(
                        reason=result.reason,
                    ),
                    attempt,
                )

            asked = result.retry_after_seconds

            # NOTE:
            # A provider that asks for a longer wait than the client allows is not asked about the word again before
            # it said: the lookup ends now and WordNet stands in, rather than holding one of the pipeline's lookups
            # for an attempt the provider has said is too early.
            #
            # The case is not hypothetical: when the provider's own server is unreachable, its CDN answers every word
            # it has not cached with HTTP 522 after about twenty seconds and asks for two minutes.
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

                return self._settle(
                    key,
                    unavailable(
                        reason=result.reason,
                    ),
                    attempt,
                )

            delay = _delay_before(
                attempt + 1,
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
        /,
    ) -> DictionaryLookup:
        self._record(
            lookup.outcome is not LookupOutcome.UNAVAILABLE,
            lookup.failure_reason,
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
        /,
    ) -> DictionaryLookup:
        self._record(
            False,
            FailureReason.TRANSPORT_ERROR,
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
        /,
    ) -> None:
        before = self._breaker.state

        if answered:
            self._breaker.record_success()

        else:
            self._breaker.record_failure()

        after = self._breaker.state

        # NOTE:
        # One warning per outage: the circuit opening from closed is the event an operator needs, while a failed trial
        # reopening it every recovery period and the circuit closing again are the outage's course, at INFO.
        if after is BreakerState.OPEN and before is BreakerState.CLOSED:
            _logger.warning(
                msg="dictionary.circuit.opened",
                extra={
                    "failure_reason": failure_reason,
                    "recovery_seconds": RECOVERY_SECONDS,
                },
            )

        elif after is BreakerState.OPEN and before is BreakerState.HALF_OPEN:
            _logger.info(
                msg="dictionary.circuit.reopened",
                extra={
                    "failure_reason": failure_reason,
                    "recovery_seconds": RECOVERY_SECONDS,
                },
            )

        elif after is BreakerState.CLOSED and before is not BreakerState.CLOSED:
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

    try:
        seconds = float(
            header,
        )

    except ValueError:
        try:
            seconds = (
                parsedate_to_datetime(
                    data=header,
                )
                - datetime.now(
                    tz=UTC,
                )
            ).total_seconds()

        except (
            TypeError,
            ValueError,
        ):
            return None

    return max(
        0.0,
        seconds,
    )


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
