"""
The Free Dictionary client against a mock transport.

The provider's shape is reproduced from a real answer, and time is a clock the
test moves, so a retry costs no waiting.
"""

from asyncio import (
    Event,
    create_task,
    gather,
    wait_for,
)
from asyncio import (
    sleep as asyncio_sleep,
)
from collections.abc import (
    Callable,
)
from logging import (
    DEBUG,
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
    LogCaptureFixture,
)

from tests.infrastructure.dictionary.doubles import (
    FakeMonotonicClock,
    RecordingObserver,
    RecordingSleep,
)
from wordwinnow.domain.dictionary import (
    License,
    LookupOutcome,
    Provenance,
)
from wordwinnow.domain.language import (
    PartOfSpeech,
)
from wordwinnow.infrastructure.dictionary.free_dictionary import (
    RETRYABLE_REASONS,
    FreeDictionaryClient,
    ProviderEntry,
    WaitCause,
    translate_entries,
)
from wordwinnow.infrastructure.dictionary.resilience import (
    BreakerState,
)
from wordwinnow.infrastructure.dictionary.wire import (
    FailureReason,
)
from wordwinnow.infrastructure.user_agent import (
    USER_AGENT,
)

type Step = int | Response | Exception

_BASE_URL: Final = "https://dictionary.test/api/v2/entries/en"

# NOTE:
# Words of the story looked up together, as an analysis looks its words up.
_HOLMES_WORDS: Final = (
    "photograph",
    "mask",
    "groom",
    "rocket",
)

# NOTE:
# Real IPA from the provider, so the adapter is shown to carry the modifier letters unchanged; the linter reads them
# as look-alikes of ASCII punctuation, and here they are the point.
_PHONETIC: Final = "/ˈfəʊtəɡrɑːf/"

# NOTE:
# The shape of a real answer, reduced to the fields the adapter reads plus one it ignores; the definitions are written
# for this test rather than copied from the provider.
_PAYLOAD: Final = [
    {
        "word": "photograph",
        "phonetic": _PHONETIC,
        "phonetics": [
            {
                "text": _PHONETIC,
                "audio": "https://example.com/photograph.mp3",
            },
        ],
        "meanings": [
            {
                "partOfSpeech": "noun",
                "definitions": [
                    {
                        "definition": "A picture made with a camera.",
                        "example": "The King wanted the photograph back.",
                        "synonyms": [],
                        "antonyms": [],
                    },
                ],
                "synonyms": [],
                "antonyms": [],
            },
            {
                "partOfSpeech": "proper noun",
                "definitions": [
                    {
                        "definition": "A river in Idaho that joins the Snake River.",
                        "synonyms": [],
                        "antonyms": [],
                    },
                ],
                "synonyms": [],
                "antonyms": [],
            },
        ],
        "license": {
            "name": "CC BY-SA 3.0",
            "url": "https://creativecommons.org/licenses/by-sa/3.0",
        },
        "sourceUrls": [
            "https://example.com/photograph",
        ],
    },
]


def _answering(
    steps: list[Step],
    /,
) -> Callable[[Request], Response]:
    """
    A handler that plays `steps` in order and repeats the last one.

    A status becomes a response, a response is returned as it is, and an
    exception is raised.
    """

    def handler(
        request: Request,
        /,
    ) -> Response:
        step = (
            steps.pop(
                0,
            )
            if len(
                steps,
            )
            > 1
            else steps[0]
        )

        if isinstance(
            step,
            Exception,
        ):
            raise step

        if isinstance(
            step,
            Response,
        ):
            return step

        if step == 200:
            return Response(
                status_code=200,
                json=_PAYLOAD,
            )

        return Response(
            status_code=step,
            json={
                "title": "No Definitions Found",
            },
        )

    return handler


async def _until(
    client: FreeDictionaryClient,
    lemma: str,
    waiting_for: WaitCause,
    /,
) -> None:
    """
    Let the other tasks run until `lemma` is in flight waiting for
    `waiting_for`, and fail when that does not happen.
    """

    for _ in range(
        100,
    ):
        if any(lookup.lemma == lemma and lookup.waiting_for is waiting_for for lookup in client.activity().lookups):
            return

        await asyncio_sleep(
            delay=0,
        )

    raise AssertionError(
        f"{lemma!r} never waited for {waiting_for}",
    )


@final
class Harness:
    """
    A client over a mock transport, with the clock, the sleep, the observer,
    and every request the transport saw.

    Each request takes `latency_seconds` of the test's clock.
    """

    def __init__(
        self,
        *,
        handler: Callable[[Request], Response],
        latency_seconds: float = 0.0,
    ) -> None:
        self.clock: Final = FakeMonotonicClock()

        self.sleep: Final = RecordingSleep(
            clock=self.clock,
        )

        self.observer: Final = RecordingObserver()

        self.requests: Final[list[Request]] = []

        # NOTE:
        # The zero-second sleep suspends every request between sending it and answering it, as a network round trip
        # would.
        #
        # The mock transport answers synchronously, so without the sleep the first of several concurrent lookups of
        # one lemma would finish, and leave the client's in-flight table, before the next one started, and each would
        # send its own request; with it, the later lookups find the first request in flight and wait for its answer,
        # which is the sharing `test_concurrent_lookups_of_one_lemma_share_one_request` checks.
        async def record(
            request: Request,
            /,
        ) -> Response:
            self.requests.append(
                request,
            )

            await asyncio_sleep(
                delay=0,
            )

            self.clock.advance(
                seconds=latency_seconds,
            )

            return handler(
                request,
            )

        self.client: Final = FreeDictionaryClient(
            base_url=_BASE_URL,
            timeout_seconds=5.0,
            requests_per_second=1_000.0,
            burst=100,
            client=AsyncClient(
                transport=MockTransport(
                    handler=record,
                ),
            ),
            sleep=self.sleep,
            clock=self.clock,
            observer=self.observer,
        )


@final
class TestFreeDictionaryClient:
    async def test_a_found_word_is_translated(
        self,
    ) -> None:
        harness = Harness(
            handler=_answering(
                [
                    200,
                ],
            ),
        )

        lookup = await harness.client.look_up(
            lemma=" Photograph",
        )

        assert lookup.outcome is LookupOutcome.FOUND

        assert lookup.failure_reason is None

        (entry,) = lookup.entries

        assert entry.headword == "photograph"

        assert entry.phonetic == _PHONETIC

        assert entry.provenance == Provenance(
            license=License(
                name="CC BY-SA 3.0",
                url="https://creativecommons.org/licenses/by-sa/3.0",
            ),
            source_urls=("https://example.com/photograph",),
        )

        (
            noun,
            proper_noun,
        ) = entry.meanings

        assert noun.word_class == "noun"

        assert noun.part_of_speech is PartOfSpeech.NOUN

        assert noun.definitions[0].text == "A picture made with a camera."

        assert noun.definitions[0].example == "The King wanted the photograph back."

        assert proper_noun.word_class == "proper noun"

        assert proper_noun.part_of_speech is None

        assert proper_noun.definitions[0].example is None

        assert harness.requests[0].url.path == "/api/v2/entries/en/photograph"

        assert harness.sleep.delays == []

    async def test_every_request_names_the_program(
        self,
    ) -> None:
        harness = Harness(
            handler=_answering(
                [
                    503,
                    200,
                ],
            ),
        )

        await harness.client.look_up(
            lemma="photograph",
        )

        assert (
            len(
                harness.requests,
            )
            == 2
        )

        assert all(request.headers["User-Agent"] == USER_AGENT for request in harness.requests)

    async def test_an_unknown_word_is_not_found_without_a_retry(
        self,
    ) -> None:
        harness = Harness(
            handler=_answering(
                [
                    404,
                ],
            ),
        )

        lookup = await harness.client.look_up(
            lemma="nonexistent",
        )

        assert lookup.outcome is LookupOutcome.NOT_FOUND

        assert lookup.entries == ()

        assert (
            len(
                harness.requests,
            )
            == 1
        )

    async def test_a_provider_error_is_retried_with_a_jittered_backoff(
        self,
    ) -> None:
        harness = Harness(
            handler=_answering(
                [
                    503,
                    503,
                    200,
                ],
            ),
        )

        lookup = await harness.client.look_up(
            lemma="photograph",
        )

        assert lookup.outcome is LookupOutcome.FOUND

        assert (
            len(
                harness.requests,
            )
            == 3
        )

        (
            first,
            second,
        ) = harness.sleep.delays

        assert 0.25 <= first <= 0.5

        assert 0.5 <= second <= 1.0

    async def test_a_retry_after_header_is_honored(
        self,
    ) -> None:
        harness = Harness(
            handler=_answering(
                [
                    Response(
                        status_code=429,
                        headers={
                            "Retry-After": "2",
                        },
                    ),
                    200,
                ],
            ),
        )

        lookup = await harness.client.look_up(
            lemma="photograph",
        )

        assert lookup.outcome is LookupOutcome.FOUND

        assert harness.sleep.delays == [
            2.0,
        ]

    async def test_a_wait_as_long_as_the_cap_is_still_honored(
        self,
    ) -> None:
        harness = Harness(
            handler=_answering(
                [
                    Response(
                        status_code=503,
                        headers={
                            "Retry-After": "30",
                        },
                    ),
                    200,
                ],
            ),
        )

        lookup = await harness.client.look_up(
            lemma="photograph",
        )

        assert lookup.outcome is LookupOutcome.FOUND

        assert harness.sleep.delays == [
            30.0,
        ]

    async def test_a_longer_wait_ends_the_lookup_without_asking_again(
        self,
        *,
        caplog: LogCaptureFixture,
    ) -> None:
        harness = Harness(
            handler=_answering(
                [
                    Response(
                        status_code=503,
                        headers={
                            "Retry-After": "600",
                        },
                    ),
                    200,
                ],
            ),
        )

        with caplog.at_level(
            level=DEBUG,
            logger="wordwinnow.dictionary",
        ):
            lookup = await harness.client.look_up(
                lemma="photograph",
            )

        assert lookup.failure_reason == "provider_error"

        assert (
            len(
                harness.requests,
            )
            == 1
        )

        assert harness.sleep.delays == []

        (declined,) = (record for record in caplog.records if record.getMessage() == "dictionary.retry.declined")

        details = vars(
            declined,
        )

        assert (
            declined.levelname,
            details["attempt"],
            details["status"],
            details["retry_after_seconds"],
        ) == (
            "DEBUG",
            1,
            503,
            600.0,
        )

    async def test_words_a_cdn_cannot_fetch_cost_one_request_each(
        self,
    ) -> None:
        # NOTE:
        # The provider as it behaves when its own server is unreachable: its CDN still answers the words it has
        # cached, and answers every other word with HTTP 522 and a request to come back in two minutes, so each of
        # those words must cost one request and no wait.
        def handler(
            request: Request,
            /,
        ) -> Response:
            if request.url.path.endswith(
                "/cached",
            ):
                return Response(
                    status_code=200,
                    json=_PAYLOAD,
                )

            return Response(
                status_code=522,
                headers={
                    "Retry-After": "120",
                },
            )

        harness = Harness(
            handler=handler,
        )

        outcomes = [
            (
                await harness.client.look_up(
                    lemma=lemma,
                )
            ).outcome
            for lemma in (
                "cached",
                "uncached",
            )
            * 4
        ]

        assert (
            outcomes
            == [
                LookupOutcome.FOUND,
                LookupOutcome.UNAVAILABLE,
            ]
            * 4
        )

        assert (
            len(
                harness.requests,
            )
            == 8
        )

        assert harness.sleep.delays == []

        assert harness.client.activity().circuit is BreakerState.CLOSED

    async def test_the_observer_hears_where_the_time_goes_and_each_retry(
        self,
    ) -> None:
        harness = Harness(
            handler=_answering(
                [
                    Response(
                        status_code=503,
                        headers={
                            "Retry-After": "2",
                        },
                    ),
                    200,
                ],
            ),
            latency_seconds=20.0,
        )

        await harness.client.look_up(
            lemma="photograph",
        )

        # NOTE:
        # No trial is in flight, so the wait for one takes no time, as the wait for the budget does here.
        assert harness.observer.waits == [
            (
                WaitCause.TRIAL,
                0.0,
            ),
            (
                WaitCause.BUDGET,
                0.0,
            ),
            (
                WaitCause.PROVIDER,
                20.0,
            ),
            (
                WaitCause.RETRY,
                2.0,
            ),
            (
                WaitCause.BUDGET,
                0.0,
            ),
            (
                WaitCause.PROVIDER,
                20.0,
            ),
        ]

        assert harness.observer.retries == [
            "provider_error",
        ]

    async def test_the_reasons_a_lookup_is_retried_for_are_the_retryable_ones(
        self,
    ) -> None:
        steps: list[Step] = [
            ReadTimeout(
                message="timed out",
            ),
            200,
            ConnectError(
                message="connection refused",
            ),
            200,
            429,
            200,
            503,
            200,
        ]

        harness = Harness(
            handler=_answering(
                steps,
            ),
        )

        for _ in range(
            4,
        ):
            await harness.client.look_up(
                lemma="photograph",
            )

        assert (
            tuple(
                harness.observer.retries,
            )
            == RETRYABLE_REASONS
        )

    async def test_the_activity_follows_the_circuit_through_an_outage(
        self,
    ) -> None:
        steps: list[Step] = [
            ConnectError(
                message="connection refused",
            ),
        ]

        harness = Harness(
            handler=_answering(
                steps,
            ),
        )

        for _ in range(
            6,
        ):
            await harness.client.look_up(
                lemma="photograph",
            )

        opened = harness.client.activity()

        harness.clock.advance(
            seconds=31.0,
        )

        # NOTE:
        # Nothing has been looked up since the recovery period ended, and the circuit is half-open all the same,
        # because the next lookup is the trial.
        recovered = harness.client.activity()

        steps[:] = [
            200,
        ]

        await harness.client.look_up(
            lemma="photograph",
        )

        closed = harness.client.activity()

        assert (
            opened.circuit,
            opened.retry_in_seconds,
        ) == (
            BreakerState.OPEN,
            30.0,
        )

        assert (
            recovered.circuit,
            recovered.retry_in_seconds,
        ) == (
            BreakerState.HALF_OPEN,
            None,
        )

        assert closed.circuit is BreakerState.CLOSED

        assert closed.retries == {
            FailureReason.TRANSPORT_ERROR: 10,
        }

        assert (
            len(
                harness.observer.retries,
            )
            == 10
        )

    async def test_lookups_that_arrive_during_the_trial_wait_for_it_and_are_answered_when_it_succeeds(
        self,
    ) -> None:
        # NOTE:
        # The first analysis after an outage asks for its words at once, so all but the first arrive while the first
        # is the trial; refused then, as they used to be, they would go without definitions from a provider that
        # answers.
        steps: list[Step] = [
            ConnectError(
                message="connection refused",
            ),
        ]

        harness = Harness(
            handler=_answering(
                steps,
            ),
        )

        for _ in range(
            5,
        ):
            await harness.client.look_up(
                lemma="egria",
            )

        harness.clock.advance(
            seconds=31.0,
        )

        steps[:] = [
            200,
        ]

        lookups = await gather(
            *(
                harness.client.look_up(
                    lemma=lemma,
                )
                for lemma in _HOLMES_WORDS
            ),
        )

        assert tuple(lookup.outcome for lookup in lookups) == (LookupOutcome.FOUND,) * len(
            _HOLMES_WORDS,
        )

        assert tuple(request.url.path for request in harness.requests[15:]) == tuple(
            f"/api/v2/entries/en/{lemma}" for lemma in _HOLMES_WORDS
        )

    async def test_lookups_waiting_for_a_failed_trial_are_refused_without_a_request(
        self,
    ) -> None:
        harness = Harness(
            handler=_answering(
                [
                    ConnectError(
                        message="connection refused",
                    ),
                ],
            ),
        )

        for _ in range(
            5,
        ):
            await harness.client.look_up(
                lemma="egria",
            )

        harness.clock.advance(
            seconds=31.0,
        )

        lookups = await gather(
            *(
                harness.client.look_up(
                    lemma=lemma,
                )
                for lemma in _HOLMES_WORDS
            ),
        )

        assert tuple(lookup.failure_reason for lookup in lookups) == (
            "transport_error",
            "circuit_open",
            "circuit_open",
            "circuit_open",
        )

        assert (
            len(
                harness.requests,
            )
            == 18
        )

        assert harness.client.activity().circuit is BreakerState.OPEN

    async def test_a_canceled_trial_hands_the_trial_to_the_next_lookup(
        self,
    ) -> None:
        clock = FakeMonotonicClock()

        recovered = Event()

        never = Event()

        async def handler(
            request: Request,
            /,
        ) -> Response:
            if not recovered.is_set():
                raise ConnectError(
                    message="connection refused",
                )

            # NOTE:
            # The trial's request never comes back, and its lookup is canceled instead, as a process that stops does.
            if request.url.path.endswith(
                "/photograph",
            ):
                await never.wait()

            return Response(
                status_code=200,
                json=_PAYLOAD,
            )

        client = FreeDictionaryClient(
            base_url=_BASE_URL,
            timeout_seconds=5.0,
            requests_per_second=1_000.0,
            burst=100,
            client=AsyncClient(
                transport=MockTransport(
                    handler=handler,
                ),
            ),
            sleep=RecordingSleep(
                clock=clock,
            ),
            clock=clock,
        )

        for _ in range(
            5,
        ):
            await client.look_up(
                lemma="egria",
            )

        clock.advance(
            seconds=31.0,
        )

        recovered.set()

        trial = create_task(
            coro=client.look_up(
                lemma="photograph",
            ),
        )

        await _until(
            client,
            "photograph",
            WaitCause.PROVIDER,
        )

        waiting = create_task(
            coro=client.look_up(
                lemma="mask",
            ),
        )

        await _until(
            client,
            "mask",
            WaitCause.TRIAL,
        )

        during = client.activity()

        trial.cancel()

        mask = await wait_for(
            fut=waiting,
            timeout=1.0,
        )

        assert tuple(
            (
                lookup.lemma,
                lookup.waiting_for,
                lookup.trial,
            )
            for lookup in during.lookups
        ) == (
            (
                "photograph",
                WaitCause.PROVIDER,
                True,
            ),
            (
                "mask",
                WaitCause.TRIAL,
                False,
            ),
        )

        assert during.circuit is BreakerState.HALF_OPEN

        assert trial.cancelled()

        assert mask.outcome is LookupOutcome.FOUND

        assert client.activity().circuit is BreakerState.CLOSED

    async def test_the_activity_shows_what_each_lookup_in_flight_waits_for(
        self,
    ) -> None:
        clock = FakeMonotonicClock()

        released = Event()

        # NOTE:
        # Every pause, for the budget as for a retry, holds until the test releases it, so each lookup stays in the
        # phase the test reads.
        async def sleep(
            delay: float,
            /,
        ) -> None:
            await released.wait()

            clock.advance(
                seconds=delay,
            )

        answers: list[Response] = [
            Response(
                status_code=503,
                headers={
                    "Retry-After": "2",
                },
            ),
        ]

        def handler(
            request: Request,
            /,
        ) -> Response:
            return (
                answers.pop(
                    0,
                )
                if answers
                else Response(
                    status_code=200,
                    json=_PAYLOAD,
                )
            )

        client = FreeDictionaryClient(
            base_url=_BASE_URL,
            timeout_seconds=5.0,
            requests_per_second=1.0,
            burst=1,
            client=AsyncClient(
                transport=MockTransport(
                    handler=handler,
                ),
            ),
            sleep=sleep,
            clock=clock,
        )

        retrying = create_task(
            coro=client.look_up(
                lemma="photograph",
            ),
        )

        await _until(
            client,
            "photograph",
            WaitCause.RETRY,
        )

        budgeted = create_task(
            coro=client.look_up(
                lemma="mask",
            ),
        )

        await _until(
            client,
            "mask",
            WaitCause.BUDGET,
        )

        activity = client.activity()

        released.set()

        answered = await gather(
            retrying,
            budgeted,
        )

        assert tuple(
            (
                lookup.lemma,
                lookup.waiting_for,
                lookup.attempt,
                lookup.delay_seconds,
                lookup.reason,
            )
            for lookup in activity.lookups
        ) == (
            (
                "photograph",
                WaitCause.RETRY,
                2,
                2.0,
                FailureReason.PROVIDER_ERROR,
            ),
            (
                "mask",
                WaitCause.BUDGET,
                1,
                None,
                None,
            ),
        )

        assert (
            activity.tokens,
            activity.burst,
            activity.requests_per_second,
            activity.waiting_for_budget,
            activity.timeout_seconds,
        ) == (
            0.0,
            1,
            1.0,
            1,
            5.0,
        )

        assert tuple(lookup.outcome for lookup in answered) == (
            LookupOutcome.FOUND,
            LookupOutcome.FOUND,
        )

        assert client.activity().lookups == ()

    async def test_a_timeout_is_unavailable_after_three_attempts(
        self,
    ) -> None:
        harness = Harness(
            handler=_answering(
                [
                    ReadTimeout(
                        message="read timed out",
                    ),
                ],
            ),
        )

        lookup = await harness.client.look_up(
            lemma="photograph",
        )

        assert lookup.outcome is LookupOutcome.UNAVAILABLE

        assert lookup.failure_reason == "timed_out"

        assert (
            len(
                harness.requests,
            )
            == 3
        )

        assert (
            len(
                harness.sleep.delays,
            )
            == 2
        )

    async def test_a_transport_error_is_unavailable_after_three_attempts(
        self,
    ) -> None:
        harness = Harness(
            handler=_answering(
                [
                    ConnectError(
                        message="connection refused",
                    ),
                ],
            ),
        )

        lookup = await harness.client.look_up(
            lemma="photograph",
        )

        assert lookup.failure_reason == "transport_error"

        assert (
            len(
                harness.requests,
            )
            == 3
        )

    async def test_exhausted_rate_limiting_is_reported_as_such(
        self,
    ) -> None:
        harness = Harness(
            handler=_answering(
                [
                    429,
                ],
            ),
        )

        lookup = await harness.client.look_up(
            lemma="photograph",
        )

        assert lookup.failure_reason == "rate_limited"

        assert (
            len(
                harness.requests,
            )
            == 3
        )

    async def test_other_statuses_are_not_retried(
        self,
    ) -> None:
        for status in (400,):
            harness = Harness(
                handler=_answering(
                    [
                        status,
                    ],
                ),
            )

            lookup = await harness.client.look_up(
                lemma="photograph",
            )

            assert lookup.failure_reason == "provider_error"

            assert (
                len(
                    harness.requests,
                )
                == 1
            )

    async def test_an_unreadable_payload_is_an_invalid_response(
        self,
    ) -> None:
        harness = Harness(
            handler=_answering(
                [
                    Response(
                        status_code=200,
                        content=b"not json",
                    ),
                ],
            ),
        )

        lookup = await harness.client.look_up(
            lemma="photograph",
        )

        assert lookup.failure_reason == "invalid_response"

        assert (
            len(
                harness.requests,
            )
            == 1
        )

    async def test_a_payload_without_usable_meanings_is_not_found(
        self,
    ) -> None:
        harness = Harness(
            handler=_answering(
                [
                    Response(
                        status_code=200,
                        json=[
                            {
                                "word": "photograph",
                                "meanings": [],
                            },
                        ],
                    ),
                ],
            ),
        )

        lookup = await harness.client.look_up(
            lemma="photograph",
        )

        assert lookup.outcome is LookupOutcome.NOT_FOUND

    async def test_the_breaker_opens_after_five_failed_lookups_and_admits_one_trial_after_recovery(
        self,
    ) -> None:
        steps: list[Step] = [
            ConnectError(
                message="connection refused",
            ),
        ]

        harness = Harness(
            handler=_answering(
                steps,
            ),
        )

        for _ in range(
            5,
        ):
            lookup = await harness.client.look_up(
                lemma="photograph",
            )

            assert lookup.failure_reason == "transport_error"

        assert (
            len(
                harness.requests,
            )
            == 15
        )

        refused = await harness.client.look_up(
            lemma="photograph",
        )

        assert refused.failure_reason == "circuit_open"

        assert (
            len(
                harness.requests,
            )
            == 15
        )

        harness.clock.advance(
            seconds=31.0,
        )

        steps[:] = [
            200,
        ]

        trial = await harness.client.look_up(
            lemma="photograph",
        )

        assert trial.outcome is LookupOutcome.FOUND

        assert (
            len(
                harness.requests,
            )
            == 16
        )

        again = await harness.client.look_up(
            lemma="photograph",
        )

        assert again.outcome is LookupOutcome.FOUND

        assert (
            len(
                harness.requests,
            )
            == 17
        )

    async def test_an_outage_is_logged_once_as_a_warning_and_its_course_below(
        self,
        *,
        caplog: LogCaptureFixture,
    ) -> None:
        steps: list[Step] = [
            ConnectError(
                message="connection refused",
            ),
        ]

        harness = Harness(
            handler=_answering(
                steps,
            ),
        )

        with caplog.at_level(
            level=DEBUG,
            logger="wordwinnow.dictionary",
        ):
            for _ in range(
                5,
            ):
                await harness.client.look_up(
                    lemma="photograph",
                )

            harness.clock.advance(
                seconds=31.0,
            )

            await harness.client.look_up(
                lemma="photograph",
            )

            harness.clock.advance(
                seconds=31.0,
            )

            steps[:] = [
                200,
            ]

            await harness.client.look_up(
                lemma="photograph",
            )

        retries = tuple(record for record in caplog.records if record.getMessage() == "dictionary.retry")

        # NOTE:
        # Two retries for each of the five failed lookups and for the failed trial; a first retry waits between a
        # quarter and a half of a second, half of its backoff step fixed and half random.
        assert (
            len(
                retries,
            )
            == 12
        )

        first = vars(
            retries[0],
        )

        assert (
            first["attempt"],
            first["reason"],
            first["status"],
            first["retry_after_seconds"],
        ) == (
            2,
            "transport_error",
            None,
            None,
        )

        assert 0.25 <= first["delay_seconds"] <= 0.5

        assert tuple(
            (
                record.levelname,
                record.getMessage(),
            )
            for record in caplog.records
            if record.getMessage().startswith(
                "dictionary.circuit.",
            )
        ) == (
            (
                "WARNING",
                "dictionary.circuit.opened",
            ),
            (
                "DEBUG",
                "dictionary.circuit.trial",
            ),
            (
                "INFO",
                "dictionary.circuit.reopened",
            ),
            (
                "DEBUG",
                "dictionary.circuit.trial",
            ),
            (
                "INFO",
                "dictionary.circuit.closed",
            ),
        )

    async def test_concurrent_lookups_of_one_lemma_share_one_request(
        self,
    ) -> None:
        harness = Harness(
            handler=_answering(
                [
                    200,
                ],
            ),
        )

        lookups = await gather(
            *(
                harness.client.look_up(
                    lemma="photograph",
                )
                for _ in range(
                    5,
                )
            ),
        )

        assert all(lookup.outcome is LookupOutcome.FOUND for lookup in lookups)

        assert (
            len(
                harness.requests,
            )
            == 1
        )

        after = await harness.client.look_up(
            lemma="photograph",
        )

        assert after.outcome is LookupOutcome.FOUND

        assert (
            len(
                harness.requests,
            )
            == 2
        )

    async def test_a_canceled_lookup_leaves_the_lookups_sharing_its_request_an_answer_and_the_next_one_asks_again(
        self,
    ) -> None:
        clock = FakeMonotonicClock()

        never = Event()

        requests: list[Request] = []

        async def handler(
            request: Request,
            /,
        ) -> Response:
            requests.append(
                request,
            )

            # NOTE:
            # The first request never comes back; its lookup is canceled instead, as a process that stops cancels it.
            if (
                len(
                    requests,
                )
                == 1
            ):
                await never.wait()

            return Response(
                status_code=200,
                json=_PAYLOAD,
            )

        client = FreeDictionaryClient(
            base_url=_BASE_URL,
            timeout_seconds=5.0,
            requests_per_second=1_000.0,
            burst=100,
            client=AsyncClient(
                transport=MockTransport(
                    handler=handler,
                ),
            ),
            sleep=RecordingSleep(
                clock=clock,
            ),
            clock=clock,
        )

        leading = create_task(
            coro=client.look_up(
                lemma="photograph",
            ),
        )

        await _until(
            client,
            "photograph",
            WaitCause.PROVIDER,
        )

        sharing = tuple(
            create_task(
                coro=client.look_up(
                    lemma="photograph",
                ),
            )
            for _ in range(
                3,
            )
        )

        await asyncio_sleep(
            delay=0,
        )

        assert not any(task.done() for task in sharing)

        leading.cancel()

        answers = await wait_for(
            fut=gather(
                *sharing,
            ),
            timeout=1.0,
        )

        after = await client.look_up(
            lemma="photograph",
        )

        assert leading.cancelled()

        assert (
            tuple(
                (
                    answer.outcome,
                    answer.failure_reason,
                )
                for answer in answers
            )
            == (
                (
                    LookupOutcome.UNAVAILABLE,
                    FailureReason.TRANSPORT_ERROR,
                ),
            )
            * 3
        )

        assert after.outcome is LookupOutcome.FOUND

        assert (
            len(
                requests,
            )
            == 2
        )

    async def test_a_defect_on_the_request_path_is_still_an_outcome(
        self,
    ) -> None:
        harness = Harness(
            handler=_answering(
                [
                    RuntimeError(
                        "an unexpected defect",
                    ),
                ],
            ),
        )

        lookup = await harness.client.look_up(
            lemma="photograph",
        )

        assert lookup.outcome is LookupOutcome.UNAVAILABLE

        assert lookup.failure_reason == "transport_error"


@final
class TestTranslateEntries:
    def test_an_unknown_word_class_keeps_its_label_without_a_part_of_speech(
        self,
    ) -> None:
        entries = translate_entries(
            payload=[
                ProviderEntry.model_validate(
                    obj={
                        "word": "through",
                        "meanings": [
                            {
                                "partOfSpeech": "Preposition",
                                "definitions": [
                                    {
                                        "definition": "From one side of something to the other.",
                                    },
                                ],
                            },
                            {
                                "partOfSpeech": "Adjective",
                                "definitions": [
                                    {
                                        "definition": "Finished.",
                                    },
                                ],
                            },
                        ],
                    },
                ),
            ],
        )

        (
            preposition,
            adjective,
        ) = entries[0].meanings

        assert preposition.word_class == "Preposition"

        assert preposition.part_of_speech is None

        assert adjective.part_of_speech is PartOfSpeech.ADJECTIVE

    def test_blank_definitions_and_empty_meanings_and_entries_are_dropped(
        self,
    ) -> None:
        entries = translate_entries(
            payload=[
                ProviderEntry.model_validate(
                    obj={
                        "word": "mask",
                        "meanings": [
                            {
                                "partOfSpeech": "noun",
                                "definitions": [
                                    {
                                        "definition": "   ",
                                    },
                                    {
                                        "definition": None,
                                    },
                                ],
                            },
                            {
                                "partOfSpeech": "verb",
                                "definitions": [],
                            },
                        ],
                    },
                ),
                ProviderEntry.model_validate(
                    obj={
                        "word": "photograph",
                        "meanings": [
                            {
                                "partOfSpeech": "noun",
                                "definitions": [
                                    {
                                        "definition": " A picture. ",
                                        "example": "   ",
                                    },
                                ],
                            },
                        ],
                    },
                ),
                ProviderEntry.model_validate(
                    obj={
                        "word": "   ",
                        "meanings": [
                            {
                                "partOfSpeech": "noun",
                                "definitions": [
                                    {
                                        "definition": "A covering that hides the face.",
                                    },
                                ],
                            },
                        ],
                    },
                ),
            ],
        )

        (entry,) = entries

        assert entry.headword == "photograph"

        assert entry.meanings[0].definitions[0].text == "A picture."

        assert entry.meanings[0].definitions[0].example is None

        assert entry.provenance is None

    def test_the_phonetic_falls_back_to_the_first_pronunciation_with_text(
        self,
    ) -> None:
        with_fallback = ProviderEntry.model_validate(
            obj={
                "word": "mask",
                "phonetics": [
                    {
                        "audio": "https://example.com/mask.mp3",
                    },
                    {
                        "text": "/mɑːsk/",
                    },
                ],
                "meanings": [
                    {
                        "partOfSpeech": "noun",
                        "definitions": [
                            {
                                "definition": "A covering that hides the face.",
                            },
                        ],
                    },
                ],
            },
        )

        without = ProviderEntry.model_validate(
            obj={
                "word": "photograph",
                "meanings": [
                    {
                        "partOfSpeech": "noun",
                        "definitions": [
                            {
                                "definition": "A picture made with a camera.",
                            },
                        ],
                    },
                ],
            },
        )

        (
            first,
            second,
        ) = translate_entries(
            payload=[
                with_fallback,
                without,
            ],
        )

        assert first.phonetic == "/mɑːsk/"

        assert second.phonetic is None
