"""
The Free Dictionary client under failures that happen while lookups overlap.

Each scenario runs on a virtual-time loop, so a provider that fails for two
minutes fails for two minutes of the loop's clock while the test takes no real
time, and every request is recorded with the circuit the client showed when it
was sent.
"""

from asyncio import (
    CancelledError,
    create_task,
    gather,
    get_running_loop,
)
from asyncio import (
    sleep as asyncio_sleep,
)
from collections.abc import (
    Callable,
    Sequence,
)
from contextlib import (
    suppress,
)
from dataclasses import (
    dataclass,
)
from itertools import (
    pairwise,
)
from math import (
    ceil,
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

from tests.fakes.virtual_time import (
    run_in_virtual_time,
)
from wordwinnow.domain.dictionary import (
    DictionaryLookup,
    LookupOutcome,
)
from wordwinnow.infrastructure.dictionary.free_dictionary import (
    DEFAULT_BURST,
    DEFAULT_REQUESTS_PER_SECOND,
    FreeDictionaryClient,
    ProviderActivity,
    WaitCause,
)
from wordwinnow.infrastructure.dictionary.resilience import (
    BreakerState,
)
from wordwinnow.infrastructure.dictionary.wire import (
    FailureReason,
)

_BASE_URL: Final = "https://dictionary.test/api/v2/entries/en"

# NOTE:
# Words of the story looked up together, as an analysis looks its words up, one more than the lookups an analysis runs
# at once.
_STORY_WORDS: Final = (
    "photograph",
    "mask",
    "groom",
    "rocket",
    "landau",
    "ulster",
    "villa",
    "carriage",
    "lantern",
)

# NOTE:
# Twenty words of the story, for a limit that refuses some of them in every window.
_MORE_STORY_WORDS: Final = (
    *_STORY_WORDS,
    "violin",
    "cigar",
    "chamber",
    "glove",
    "coachman",
    "monarch",
    "envelope",
    "hansom",
    "sovereign",
    "cab",
    "door",
)

_FOUND_PAYLOAD: Final = [
    {
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
]


@dataclass(
    frozen=True,
    kw_only=True,
    slots=True,
)
@final
class _Sent:
    """
    One request the provider received: when, for which word, and the circuit
    the client showed at that moment.
    """

    at: float

    lemma: str

    circuit: BreakerState


@final
class _Provider:
    """
    A provider whose answer depends on the moment a request arrives, and which
    takes `latency_seconds` of the loop's clock to give it.
    """

    def __init__(
        self,
        *,
        answer: Callable[[float], Response],
        latency_seconds: float,
    ) -> None:
        self._answer: Final = answer

        self._latency: Final = latency_seconds

        self.sent: Final[list[_Sent]] = []

        # NOTE:
        # Each wait the provider asked for, from the moment its refusal reached the client to the moment it ends.
        self.asked: Final[list[tuple[float, float]]] = []

        self.client: FreeDictionaryClient | None = None

    async def receive(
        self,
        *,
        request: Request,
    ) -> Response:
        loop = get_running_loop()

        if self.client is None:
            raise AssertionError(
                "the provider answers a client it was never given",
            )

        self.sent.append(
            _Sent(
                at=loop.time(),
                lemma=request.url.path.rsplit(
                    sep="/",
                    maxsplit=1,
                )[-1],
                circuit=self.client.activity().circuit,
            ),
        )

        response = self._answer(
            loop.time(),
        )

        await asyncio_sleep(
            delay=self._latency,
        )

        # WARN:
        # `Headers.get` declares its key positional-only in the overloads the type checker reads, although the
        # implementation also takes it as a keyword, so the key is passed positionally.
        header = response.headers.get(
            "retry-after",
        )

        if header is not None:
            received = loop.time()

            self.asked.append(
                (
                    received,
                    received
                    + float(
                        header,
                    ),
                ),
            )

        return response


def _client_for(
    provider: _Provider,
    /,
    *,
    requests_per_second: float | None = None,
    burst: int | None = None,
) -> FreeDictionaryClient:
    """
    A client over `provider`, paced as the project paces the real one unless
    given a pace, on the running loop's clock.
    """

    async def handler(
        request: Request,
        /,
    ) -> Response:
        return await provider.receive(
            request=request,
        )

    client = FreeDictionaryClient(
        base_url=_BASE_URL,
        timeout_seconds=30.0,
        requests_per_second=requests_per_second,
        burst=burst,
        client=AsyncClient(
            transport=MockTransport(
                handler=handler,
            ),
        ),
        clock=get_running_loop().time,
    )

    provider.client = client

    return client


def _failing(
    status_code: int,
    /,
) -> Callable[[float], Response]:
    """
    An answer that is always `status_code`.
    """

    def answer(
        arrived: float,
        /,
    ) -> Response:
        return Response(
            status_code=status_code,
        )

    return answer


def _answering(
    arrived: float,
    /,
) -> Response:
    """
    An answer to every request.
    """

    return Response(
        status_code=200,
        json=_FOUND_PAYLOAD,
    )


def _limited(
    *,
    answers_per_window: int,
    window_seconds: float,
    names_the_wait: bool,
) -> Callable[[float], Response]:
    """
    A provider that answers `answers_per_window` requests in each window and
    refuses the rest with 429, naming the seconds left in the window when
    `names_the_wait`, as the Free Dictionary API has been seen to do.
    """

    answered: dict[int, int] = {}

    def answer(
        arrived: float,
        /,
    ) -> Response:
        window = int(
            arrived // window_seconds,
        )

        if (
            answered.get(
                window,
                0,
            )
            < answers_per_window
        ):
            answered[window] = (
                answered.get(
                    window,
                    0,
                )
                + 1
            )

            return Response(
                status_code=200,
                json=_FOUND_PAYLOAD,
            )

        left = ceil(
            (window + 1) * window_seconds - arrived,
        )

        return Response(
            status_code=429,
            headers=(
                {
                    "Retry-After": str(
                        object=left,
                    ),
                }
                if names_the_wait
                else {}
            ),
        )

    return answer


async def _look_up_all(
    client: FreeDictionaryClient,
    words: tuple[str, ...],
    /,
) -> tuple[DictionaryLookup, ...]:
    return tuple(
        await gather(
            *(
                client.look_up(
                    lemma=word,
                )
                for word in words
            ),
        ),
    )


def _offsets(
    provider: _Provider,
    /,
) -> tuple[float, ...]:
    """
    When each request reached `provider`, in seconds after the first, to the
    microsecond.
    """

    return tuple(
        round(
            number=request.at - provider.sent[0].at,
            ndigits=6,
        )
        for request in provider.sent
    )


@final
class TestThePace:
    def test_a_client_given_no_pace_keeps_the_providers_own(
        self,
    ) -> None:
        async def scenario() -> tuple[_Provider, ProviderActivity]:
            provider = _Provider(
                answer=_answering,
                latency_seconds=0.1,
            )

            client = _client_for(
                provider,
            )

            await _look_up_all(
                client,
                _STORY_WORDS,
            )

            return (
                provider,
                client.activity(),
            )

        (
            provider,
            activity,
        ) = run_in_virtual_time(
            main=scenario,
        )

        # NOTE:
        # Two requests a second after a burst of four, the pace the settings set before the adapter kept its own: the
        # burst leaves at once, and the other five words one every half second.
        assert (
            DEFAULT_REQUESTS_PER_SECOND,
            DEFAULT_BURST,
        ) == (
            2.0,
            4,
        )

        assert (
            activity.requests_per_second,
            activity.burst,
        ) == (
            DEFAULT_REQUESTS_PER_SECOND,
            DEFAULT_BURST,
        )

        assert _offsets(
            provider,
        ) == (
            0.0,
            0.0,
            0.0,
            0.0,
            0.5,
            1.0,
            1.5,
            2.0,
            2.5,
        )

    def test_a_pace_given_to_the_client_holds_whether_faster_or_slower_than_the_providers_own(
        self,
    ) -> None:
        # NOTE:
        # The provider's own pace is no ceiling and no floor: twenty a second sends the five words after the burst
        # within a quarter of a second, and one every two seconds after a burst of one takes sixteen.
        def last_request(
            requests_per_second: float,
            burst: int,
            /,
        ) -> float:
            async def scenario() -> _Provider:
                provider = _Provider(
                    answer=_answering,
                    latency_seconds=0.1,
                )

                await _look_up_all(
                    _client_for(
                        provider,
                        requests_per_second=requests_per_second,
                        burst=burst,
                    ),
                    _STORY_WORDS,
                )

                return provider

            return _offsets(
                run_in_virtual_time(
                    main=scenario,
                ),
            )[-1]

        assert (
            last_request(
                20.0,
                4,
            ),
            last_request(
                0.5,
                1,
            ),
        ) == (
            0.25,
            16.0,
        )

    def test_clients_in_two_processes_each_wait_out_only_the_refusals_they_received(
        self,
    ) -> None:
        # NOTE:
        # Two processes behind one address share the provider's window and nothing else: the second starts while the
        # first waits out a refusal and is refused at once, and each client keeps its own pace and its own pauses, so
        # only the provider's refusals hold the two back together.
        async def scenario() -> tuple[_Provider, _Provider, tuple[DictionaryLookup, ...]]:
            answer = _limited(
                answers_per_window=8,
                window_seconds=10.0,
                names_the_wait=True,
            )

            first = _Provider(
                answer=answer,
                latency_seconds=0.1,
            )

            second = _Provider(
                answer=answer,
                latency_seconds=0.1,
            )

            looking = create_task(
                coro=_look_up_all(
                    _client_for(
                        first,
                    ),
                    _STORY_WORDS,
                ),
            )

            await asyncio_sleep(
                delay=5.0,
            )

            later = await _look_up_all(
                _client_for(
                    second,
                ),
                _STORY_WORDS,
            )

            return (
                first,
                second,
                (
                    *await looking,
                    *later,
                ),
            )

        (
            first,
            second,
            lookups,
        ) = run_in_virtual_time(
            main=scenario,
        )

        def inside(
            requests: Sequence[_Sent],
            waits: Sequence[tuple[float, float]],
            /,
        ) -> int:
            return sum(
                any(
                    start < request.at < end
                    for (
                        start,
                        end,
                    ) in waits
                )
                for request in requests
            )

        assert all(lookup.outcome is LookupOutcome.FOUND for lookup in lookups)

        assert (
            inside(
                first.sent,
                first.asked,
            ),
            inside(
                second.sent,
                second.asked,
            ),
        ) == (
            0,
            0,
        )

        assert (
            inside(
                second.sent,
                first.asked,
            )
            > 0
        )


@final
class TestThePauseTheProviderAsksFor:
    def test_a_429_holds_every_lookup_until_the_wait_it_names_and_releases_them_at_the_rate(
        self,
    ) -> None:
        async def scenario() -> tuple[_Provider, tuple[DictionaryLookup, ...]]:
            provider = _Provider(
                answer=_limited(
                    answers_per_window=6,
                    window_seconds=10.0,
                    names_the_wait=True,
                ),
                latency_seconds=0.1,
            )

            client = _client_for(
                provider,
            )

            lookups = await _look_up_all(
                client,
                _MORE_STORY_WORDS,
            )

            return (
                provider,
                lookups,
            )

        (
            provider,
            lookups,
        ) = run_in_virtual_time(
            main=scenario,
        )

        inside = tuple(
            request
            for request in provider.sent
            if any(
                start < request.at < end
                for (
                    start,
                    end,
                ) in provider.asked
            )
        )

        (
            first_refused,
            _,
        ) = provider.asked[0]

        after = tuple(request.at for request in provider.sent if request.at > first_refused)

        # NOTE:
        # Every word is answered, no request reaches the provider inside a wait it asked for, and once the first
        # refusal arrived the requests leave one token apart, so the end of a pause releases no burst.
        assert all(lookup.outcome is LookupOutcome.FOUND for lookup in lookups)

        assert inside == ()

        # WARN:
        # The iterable of consecutive pairs takes its argument positionally only, in C, and a keyword raises.
        assert all(
            later - earlier >= 0.5 - 1e-6
            for (
                earlier,
                later,
            ) in pairwise(
                after,
            )
        )

    def test_a_lookup_waiting_for_a_token_is_shown_waiting_out_the_pause_while_it_lasts_and_for_the_rate_after(
        self,
    ) -> None:
        # NOTE:
        # Two answers in each ten seconds: the burst's third and fourth requests are refused, naming the rest of the
        # window, so the words queued for a token wait for the pause's end at second 10.1, and then for the rate,
        # which is what the activity says at second 3 and at second 10.3.
        async def scenario() -> tuple[ProviderActivity, ProviderActivity, tuple[DictionaryLookup, ...]]:
            provider = _Provider(
                answer=_limited(
                    answers_per_window=2,
                    window_seconds=10.0,
                    names_the_wait=True,
                ),
                latency_seconds=0.1,
            )

            client = _client_for(
                provider,
            )

            looking = create_task(
                coro=_look_up_all(
                    client,
                    _STORY_WORDS,
                ),
            )

            await asyncio_sleep(
                delay=3.0,
            )

            during = client.activity()

            await asyncio_sleep(
                delay=7.3,
            )

            after = client.activity()

            return (
                during,
                after,
                await looking,
            )

        (
            during,
            after,
            lookups,
        ) = run_in_virtual_time(
            main=scenario,
        )

        assert during.cooldown_seconds is not None

        assert all(lookup.waiting_for is not WaitCause.BUDGET for lookup in during.lookups)

        assert any(lookup.waiting_for is WaitCause.COOLDOWN for lookup in during.lookups)

        assert after.cooldown_seconds is None

        assert all(lookup.waiting_for is not WaitCause.COOLDOWN for lookup in after.lookups)

        assert any(lookup.waiting_for is WaitCause.BUDGET for lookup in after.lookups)

        assert all(lookup.outcome is LookupOutcome.FOUND for lookup in lookups)

    def test_a_429_that_names_no_wait_costs_no_word(
        self,
    ) -> None:
        async def scenario() -> tuple[DictionaryLookup, ...]:
            provider = _Provider(
                answer=_limited(
                    answers_per_window=6,
                    window_seconds=10.0,
                    names_the_wait=False,
                ),
                latency_seconds=0.1,
            )

            client = _client_for(
                provider,
            )

            return await _look_up_all(
                client,
                _MORE_STORY_WORDS,
            )

        lookups = run_in_virtual_time(
            main=scenario,
        )

        assert all(lookup.outcome is LookupOutcome.FOUND for lookup in lookups)


def _refused_before(
    moment: float,
    /,
    *,
    retry_after: str | None,
) -> Callable[[float], Response]:
    """
    A provider that refuses every request arriving before `moment` with 429,
    naming `retry_after` when it is given, and answers every later one.
    """

    def answer(
        arrived: float,
        /,
    ) -> Response:
        if arrived >= moment:
            return Response(
                status_code=200,
                json=_FOUND_PAYLOAD,
            )

        return Response(
            status_code=429,
            headers=(
                {
                    "Retry-After": retry_after,
                }
                if retry_after is not None
                else {}
            ),
        )

    return answer


@final
class TestRefusalsAlreadyInFlight:
    def test_refusals_that_name_a_wait_hold_the_client_no_longer_than_the_last_one_names(
        self,
    ) -> None:
        # NOTE:
        # Each request takes two seconds, so the seven requests sent before the provider starts answering are all in
        # flight when the first refusal arrives; their refusals are one refusal, and the client waits as long as the
        # last of them names, not a wait doubled seven times.
        async def scenario() -> tuple[_Provider, tuple[DictionaryLookup, ...]]:
            provider = _Provider(
                answer=_refused_before(
                    2.0,
                    retry_after="2",
                ),
                latency_seconds=2.0,
            )

            lookups = await _look_up_all(
                _client_for(
                    provider,
                ),
                _STORY_WORDS[:8],
            )

            return (
                provider,
                lookups,
            )

        (
            provider,
            lookups,
        ) = run_in_virtual_time(
            main=scenario,
        )

        last_named = max(
            end
            for (
                _,
                end,
            ) in provider.asked
        )

        assert all(lookup.outcome is LookupOutcome.FOUND for lookup in lookups)

        assert min(request.at for request in provider.sent if request.at >= 2.0) <= last_named + 1e-6

    def test_a_burst_refused_without_a_wait_pauses_the_client_as_one_refusal(
        self,
    ) -> None:
        # NOTE:
        # The first four requests leave together and are refused together: one refusal in a row, so the pause is the
        # first doubling wait, a second at most, rather than the fourth.
        async def scenario() -> tuple[_Provider, tuple[DictionaryLookup, ...]]:
            provider = _Provider(
                answer=_refused_before(
                    1.0,
                    retry_after=None,
                ),
                latency_seconds=0.5,
            )

            lookups = await _look_up_all(
                _client_for(
                    provider,
                ),
                _STORY_WORDS[:8],
            )

            return (
                provider,
                lookups,
            )

        (
            provider,
            lookups,
        ) = run_in_virtual_time(
            main=scenario,
        )

        first_refusal = min(request.at for request in provider.sent) + 0.5

        assert all(lookup.outcome is LookupOutcome.FOUND for lookup in lookups)

        assert min(request.at for request in provider.sent if request.at >= 1.0) <= first_refusal + 1.0 + 1e-6

    def test_a_429_starts_the_count_of_failures_in_a_row_again(
        self,
    ) -> None:
        async def scenario() -> tuple[int | None, int | None]:
            async def handler(
                request: Request,
                /,
            ) -> Response:
                if request.url.path.endswith(
                    "/landau",
                ):
                    return Response(
                        status_code=429,
                        headers={
                            "Retry-After": "1",
                        },
                    )

                return Response(
                    status_code=400,
                )

            client = FreeDictionaryClient(
                base_url=_BASE_URL,
                timeout_seconds=30.0,
                requests_per_second=100.0,
                burst=10,
                client=AsyncClient(
                    transport=MockTransport(
                        handler=handler,
                    ),
                ),
                clock=get_running_loop().time,
            )

            for word in _STORY_WORDS[:4]:
                await client.look_up(
                    lemma=word,
                )

            before = client.activity().consecutive_failures

            refused = create_task(
                coro=client.look_up(
                    lemma="landau",
                ),
            )

            await asyncio_sleep(
                delay=0.5,
            )

            after = client.activity().consecutive_failures

            refused.cancel()

            with suppress(
                CancelledError,
            ):
                await refused

            return (
                before,
                after,
            )

        assert run_in_virtual_time(
            main=scenario,
        ) == (
            4,
            0,
        )


@final
class TestTheTrialItself:
    def test_a_former_trial_canceled_later_leaves_the_current_trial_alone(
        self,
    ) -> None:
        # NOTE:
        # `mask` is the trial, draws a 429 that closes the circuit, and asks again as an ordinary lookup whose request
        # takes a minute; meanwhile the circuit opens again and `groom` is the new trial.
        #
        # Canceling `mask` must not end `groom`'s trial, so no other request may reach the provider until `groom` is
        # answered.
        async def scenario() -> tuple[list[tuple[float, str]], float]:
            loop = get_running_loop()

            arrivals: list[tuple[float, str]] = []

            answered: dict[str, float] = {}

            async def handler(
                request: Request,
                /,
            ) -> Response:
                word = request.url.path.rsplit(
                    sep="/",
                    maxsplit=1,
                )[-1]

                arrivals.append(
                    (
                        loop.time(),
                        word,
                    ),
                )

                if word.startswith(
                    "fail",
                ):
                    return Response(
                        status_code=400,
                    )

                if word == "mask" and loop.time() < 40.0:
                    return Response(
                        status_code=429,
                        headers={
                            "Retry-After": "20",
                        },
                    )

                await asyncio_sleep(
                    delay=60.0 if word == "mask" else 20.0 if word == "groom" else 0.0,
                )

                answered[word] = loop.time()

                return Response(
                    status_code=200,
                    json=_FOUND_PAYLOAD,
                )

            client = FreeDictionaryClient(
                base_url=_BASE_URL,
                timeout_seconds=90.0,
                requests_per_second=2.0,
                burst=4,
                client=AsyncClient(
                    transport=MockTransport(
                        handler=handler,
                    ),
                ),
                clock=loop.time,
            )

            await gather(
                *(
                    client.look_up(
                        lemma=f"fail{index}",
                    )
                    for index in range(
                        5,
                    )
                ),
            )

            await asyncio_sleep(
                delay=31.0 - loop.time(),
            )

            former = create_task(
                coro=client.look_up(
                    lemma="mask",
                ),
            )

            await asyncio_sleep(
                delay=52.0 - loop.time(),
            )

            await gather(
                *(
                    client.look_up(
                        lemma=f"fail{index}",
                    )
                    for index in range(
                        5,
                        10,
                    )
                ),
            )

            await asyncio_sleep(
                delay=31.0,
            )

            current = create_task(
                coro=client.look_up(
                    lemma="groom",
                ),
            )

            await asyncio_sleep(
                delay=1.0,
            )

            waiting = gather(
                client.look_up(
                    lemma="rocket",
                ),
                client.look_up(
                    lemma="villa",
                ),
            )

            await asyncio_sleep(
                delay=1.0,
            )

            former.cancel()

            await gather(
                current,
                waiting,
            )

            with suppress(
                CancelledError,
            ):
                await former

            return (
                arrivals,
                answered["groom"],
            )

        (
            arrivals,
            groom_answered,
        ) = run_in_virtual_time(
            main=scenario,
        )

        groom_sent = next(
            at
            for (
                at,
                word,
            ) in arrivals
            if word == "groom"
        )

        assert (
            tuple(
                word
                for (
                    at,
                    word,
                ) in arrivals
                if groom_sent < at < groom_answered
            )
            == ()
        )


@final
class TestTheTrial:
    def test_a_provider_that_stays_down_is_asked_once_per_recovery_period_and_then_by_the_rate(
        self,
    ) -> None:
        # NOTE:
        # The provider fails every request for its first hundred seconds, and a word is asked every five seconds
        # meanwhile, as analyses arriving one after another would ask: once the circuit is open, only the trial asks,
        # one request at a time, a recovery period apart, until one is answered.
        async def scenario() -> tuple[_Provider, tuple[DictionaryLookup, ...]]:
            def answer(
                arrived: float,
                /,
            ) -> Response:
                if arrived < 100.0:
                    return Response(
                        status_code=503,
                    )

                return Response(
                    status_code=200,
                    json=_FOUND_PAYLOAD,
                )

            provider = _Provider(
                answer=answer,
                latency_seconds=0.1,
            )

            client = _client_for(
                provider,
            )

            for word in _MORE_STORY_WORDS:
                await client.look_up(
                    lemma=word,
                )

                await asyncio_sleep(
                    delay=5.0,
                )

            # NOTE:
            # The last failed trial came before the provider recovered, so the next one is due a recovery period
            # later.
            await asyncio_sleep(
                delay=40.0,
            )

            return (
                provider,
                await _look_up_all(
                    client,
                    _STORY_WORDS,
                ),
            )

        (
            provider,
            lookups,
        ) = run_in_virtual_time(
            main=scenario,
        )

        trials = tuple(request.at for request in provider.sent if request.circuit is not BreakerState.CLOSED)

        recovered = tuple(request.at for request in provider.sent if request.at >= 100.0)

        assert all(request.circuit is not BreakerState.OPEN for request in provider.sent)

        # NOTE:
        # After each failed trial the circuit stays open ten seconds less a random quarter at most, so the trials are
        # never closer than seven and a half seconds.
        assert all(
            later - earlier >= 7.5
            for (
                earlier,
                later,
            ) in pairwise(
                trials,
            )
        )

        # NOTE:
        # Once a trial is answered, the words that waited for it leave a token apart rather than together.
        assert all(
            later - earlier >= 0.5 - 1e-6
            for (
                earlier,
                later,
            ) in pairwise(
                recovered[1:],
            )
        )

        assert all(lookup.outcome is LookupOutcome.FOUND for lookup in lookups)


@final
class TestTheCircuitBeforeEveryAttempt:
    def test_lookups_admitted_before_the_circuit_opened_send_nothing_after_it(
        self,
    ) -> None:
        # NOTE:
        # One token every half second for nine lookups, so when the fifth failed lookup opens the circuit, others are
        # still waiting for a token or sleeping before another attempt: neither may reach a provider the client has
        # stopped asking.
        async def scenario() -> tuple[tuple[_Sent, ...], tuple[DictionaryLookup, ...]]:
            provider = _Provider(
                answer=_failing(
                    503,
                ),
                latency_seconds=0.1,
            )

            client = _client_for(
                provider,
                burst=1,
            )

            lookups = await gather(
                *(
                    client.look_up(
                        lemma=word,
                    )
                    for word in _STORY_WORDS
                ),
            )

            return (
                tuple(
                    provider.sent,
                ),
                tuple(
                    lookups,
                ),
            )

        (
            sent,
            lookups,
        ) = run_in_virtual_time(
            main=scenario,
        )

        assert tuple(request for request in sent if request.circuit is not BreakerState.CLOSED) == ()

        assert all(lookup.outcome is LookupOutcome.UNAVAILABLE for lookup in lookups)

        assert sum(1 for lookup in lookups if lookup.failure_reason == FailureReason.CIRCUIT_OPEN) >= 1, (
            "the scenario must open the circuit while lookups are still waiting"
        )


@dataclass(
    frozen=True,
    kw_only=True,
    slots=True,
)
@final
class _Arrival:
    """
    One request as it reached the provider: for which word, and the breaker's
    count of failures in a row and the circuit at that moment.
    """

    lemma: str

    in_a_row: int | None

    circuit: BreakerState


def _counted(
    answer: Callable[[Request], Response],
    /,
    *,
    burst: int = 4,
    hang_seconds: float = 0.0,
) -> tuple[FreeDictionaryClient, list[_Arrival]]:
    """
    A client whose provider answers each request as `answer` does, after
    `hang_seconds` of the loop's clock, and the list of arrivals it fills.
    """

    arrivals: list[_Arrival] = []

    clients: list[FreeDictionaryClient] = []

    async def handler(
        request: Request,
        /,
    ) -> Response:
        activity = clients[0].activity()

        arrivals.append(
            _Arrival(
                lemma=request.url.path.rsplit(
                    sep="/",
                    maxsplit=1,
                )[-1],
                in_a_row=activity.consecutive_failures,
                circuit=activity.circuit,
            ),
        )

        await asyncio_sleep(
            delay=hang_seconds,
        )

        return answer(
            request,
        )

    clients.append(
        FreeDictionaryClient(
            base_url=_BASE_URL,
            timeout_seconds=30.0,
            requests_per_second=2.0,
            burst=burst,
            client=AsyncClient(
                transport=MockTransport(
                    handler=handler,
                ),
            ),
            clock=get_running_loop().time,
        ),
    )

    return (
        clients[0],
        arrivals,
    )


def _server_error(
    request: Request,
    /,
) -> Response:
    return Response(
        status_code=503,
    )


def _timed_out(
    request: Request,
    /,
) -> Response:
    raise ReadTimeout(
        message="the provider took longer than the timeout",
        request=request,
    )


def _refused_connection(
    request: Request,
    /,
) -> Response:
    raise ConnectError(
        message="connection refused",
        request=request,
    )


def _unreadable(
    request: Request,
    /,
) -> Response:
    return Response(
        status_code=200,
        content=b"not json",
    )


def _bad_request(
    request: Request,
    /,
) -> Response:
    return Response(
        status_code=400,
    )


@final
class TestTheFailuresInARow:
    def test_four_failed_requests_leave_the_circuit_closed_and_the_fifth_opens_it_whatever_failed(
        self,
    ) -> None:
        # NOTE:
        # Each kind of failure the breaker counts, one word after another: a server error, a timeout, and a refused
        # connection are asked again up to three times a word, so the fifth request is the second word's second; an
        # unreadable answer and an unexpected status are not asked again, so it is the fifth word's only one.
        async def scenario(
            answer: Callable[[Request], Response],
            /,
        ) -> tuple[tuple[_Arrival, ...], BreakerState, DictionaryLookup, int]:
            (
                client,
                arrivals,
            ) = _counted(
                answer,
            )

            for word in _STORY_WORDS:
                if (
                    len(
                        arrivals,
                    )
                    >= 5
                ):
                    break

                await client.look_up(
                    lemma=word,
                )

            opened = client.activity().circuit

            refused = await client.look_up(
                lemma="lantern",
            )

            return (
                tuple(
                    arrivals,
                ),
                opened,
                refused,
                len(
                    arrivals,
                ),
            )

        for answer in (
            _server_error,
            _timed_out,
            _refused_connection,
            _unreadable,
            _bad_request,
        ):
            (
                arrivals,
                opened,
                refused,
                requests,
            ) = run_in_virtual_time(
                main=lambda answer=answer: scenario(
                    answer,
                ),
            )

            assert tuple(arrival.in_a_row for arrival in arrivals) == (
                0,
                1,
                2,
                3,
                4,
            ), answer.__name__

            assert all(arrival.circuit is BreakerState.CLOSED for arrival in arrivals), answer.__name__

            assert opened is BreakerState.OPEN, answer.__name__

            assert (
                refused.failure_reason,
                requests,
            ) == (
                FailureReason.CIRCUIT_OPEN,
                5,
            ), answer.__name__

    def test_an_answer_starts_the_count_again_so_four_failures_either_side_of_it_never_open_the_circuit(
        self,
    ) -> None:
        # NOTE:
        # Four failures, a word found, four failures, a word the provider does not know, and four failures: twelve
        # failed requests, never five in a row, because a 404 is an answer as much as a 200 is.
        def answer(
            request: Request,
            /,
        ) -> Response:
            if request.url.path.endswith(
                "/photograph",
            ):
                return Response(
                    status_code=200,
                    json=_FOUND_PAYLOAD,
                )

            if request.url.path.endswith(
                "/egria",
            ):
                return Response(
                    status_code=404,
                )

            return Response(
                status_code=400,
            )

        async def scenario() -> tuple[tuple[_Arrival, ...], BreakerState, int | None]:
            (
                client,
                arrivals,
            ) = _counted(
                answer,
            )

            for word in (
                *(("mask",) * 4),
                "photograph",
                *(("mask",) * 4),
                "egria",
                *(("mask",) * 4),
            ):
                await client.look_up(
                    lemma=word,
                )

            return (
                tuple(
                    arrivals,
                ),
                client.activity().circuit,
                client.activity().consecutive_failures,
            )

        (
            arrivals,
            circuit,
            in_a_row,
        ) = run_in_virtual_time(
            main=scenario,
        )

        assert tuple(arrival.in_a_row for arrival in arrivals) == (
            0,
            1,
            2,
            3,
            4,
            0,
            1,
            2,
            3,
            4,
            0,
            1,
            2,
            3,
        )

        assert (
            circuit,
            in_a_row,
        ) == (
            BreakerState.CLOSED,
            4,
        )

    def test_a_shared_lookup_counts_once_and_a_canceled_one_not_at_all(
        self,
    ) -> None:
        # NOTE:
        # Two failures, then two lookups of one word at once, which share one request, then a lookup canceled while
        # its request is on its way: a canceled request has no outcome, and the count is three throughout.
        def answer(
            request: Request,
            /,
        ) -> Response:
            return Response(
                status_code=400,
            )

        async def scenario() -> tuple[list[int | None], int]:
            (
                client,
                arrivals,
            ) = _counted(
                answer,
                hang_seconds=1.0,
            )

            counts: list[int | None] = []

            for word in (
                "mask",
                "groom",
            ):
                await client.look_up(
                    lemma=word,
                )

            await gather(
                client.look_up(
                    lemma="rocket",
                ),
                client.look_up(
                    lemma="rocket",
                ),
            )

            counts.append(
                client.activity().consecutive_failures,
            )

            canceled = create_task(
                coro=client.look_up(
                    lemma="landau",
                ),
            )

            await asyncio_sleep(
                delay=0.5,
            )

            canceled.cancel()

            with suppress(
                CancelledError,
            ):
                await canceled

            await asyncio_sleep(
                delay=1.0,
            )

            counts.append(
                client.activity().consecutive_failures,
            )

            return (
                counts,
                len(
                    arrivals,
                ),
            )

        assert run_in_virtual_time(
            main=scenario,
        ) == (
            [
                3,
                3,
            ],
            4,
        )

    def test_failures_that_arrive_together_open_the_circuit_once_and_nothing_but_the_trial_follows(
        self,
    ) -> None:
        # NOTE:
        # Nine lookups at once against a provider that hangs until the timeout: all nine requests are on their way
        # when the first five time-outs open the circuit, the other four fail while it is open and are not counted,
        # and none of the nine is asked again; the trial comes one recovery period after the opening, unmoved by the
        # four late failures, and is the only request until it is answered.
        async def scenario() -> tuple[tuple[_Arrival, ...], tuple[DictionaryLookup, ...], float | None]:
            (
                client,
                arrivals,
            ) = _counted(
                _timed_out,
                burst=9,
                hang_seconds=30.0,
            )

            lookups = await _look_up_all(
                client,
                _STORY_WORDS,
            )

            retry_in_seconds = client.activity().retry_in_seconds

            return (
                tuple(
                    arrivals,
                ),
                lookups,
                retry_in_seconds,
            )

        (
            arrivals,
            lookups,
            retry_in_seconds,
        ) = run_in_virtual_time(
            main=scenario,
        )

        assert (
            len(
                arrivals,
            )
            == 9
        )

        assert all(arrival.in_a_row == 0 and arrival.circuit is BreakerState.CLOSED for arrival in arrivals)

        assert tuple(
            sum(1 for lookup in lookups if lookup.failure_reason == reason)
            for reason in (
                FailureReason.TIMED_OUT,
                FailureReason.CIRCUIT_OPEN,
            )
        ) == (
            5,
            4,
        )

        # NOTE:
        # The four refused while waiting to ask again ended within half a second of the opening, so the trial is still
        # seven to ten seconds away less that half second.
        assert retry_in_seconds is not None

        assert 7.0 <= retry_in_seconds <= 10.0

    def test_the_trial_waits_for_the_wait_the_provider_named_even_past_the_recovery_period(
        self,
    ) -> None:
        # NOTE:
        # Two words, each asked again a second after every 503, until the fifth 503, three and a half seconds in,
        # names twenty-five seconds: the circuit opens for those rather than its own ten, so a word asked a tenth of a
        # second before they end sends nothing and is told to ask again then, and the trial is sent no sooner.
        asked: dict[str, int] = {}

        recovered: list[bool] = [
            False,
        ]

        def answer(
            request: Request,
            /,
        ) -> Response:
            if recovered[0]:
                return Response(
                    status_code=200,
                    json=_FOUND_PAYLOAD,
                )

            lemma = request.url.path.rsplit(
                sep="/",
                maxsplit=1,
            )[-1]

            asked[lemma] = (
                asked.get(
                    lemma,
                    0,
                )
                + 1
            )

            # NOTE:
            # The second word's second request is the fifth in a row.
            return Response(
                status_code=503,
                headers={
                    "Retry-After": "25" if asked[lemma] == 2 and lemma == "mask" else "1",
                },
            )

        async def scenario() -> tuple[tuple[_Arrival, ...], float, DictionaryLookup, DictionaryLookup]:
            loop = get_running_loop()

            (
                client,
                arrivals,
            ) = _counted(
                answer,
                hang_seconds=0.1,
            )

            for word in (
                "photograph",
                "mask",
            ):
                await client.look_up(
                    lemma=word,
                )

            opened_at = loop.time()

            await asyncio_sleep(
                delay=24.9,
            )

            early = await client.look_up(
                lemma="lantern",
            )

            recovered[0] = True

            await asyncio_sleep(
                delay=0.1,
            )

            trial = await client.look_up(
                lemma="lantern",
            )

            return (
                tuple(
                    arrivals,
                ),
                opened_at,
                early,
                trial,
            )

        (
            arrivals,
            opened_at,
            early,
            trial,
        ) = run_in_virtual_time(
            main=scenario,
        )

        assert (
            round(
                number=opened_at,
                ndigits=6,
            )
            == 3.5
        )

        assert early.failure_reason == FailureReason.CIRCUIT_OPEN

        assert early.retry_after_seconds is not None

        assert (
            abs(
                early.retry_after_seconds - 0.1,
            )
            < 1e-6
        )

        assert tuple(arrival.circuit for arrival in arrivals) == (
            *((BreakerState.CLOSED,) * 5),
            BreakerState.HALF_OPEN,
        )

        assert trial.outcome is LookupOutcome.FOUND

    def test_a_word_alone_in_an_outage_asks_again_until_the_fifth_failure_opens_the_circuit(
        self,
    ) -> None:
        # NOTE:
        # One word against a provider that fails every request: its three attempts leave four short of opening the
        # circuit, so rather than taking WordNet's gloss it names the next backoff; asked again then, its fourth
        # failure leaves the circuit closed and its fifth opens it, and the word names the trial's moment instead.
        async def scenario() -> tuple[DictionaryLookup, BreakerState, DictionaryLookup, tuple[_Arrival, ...]]:
            (
                client,
                arrivals,
            ) = _counted(
                _server_error,
                hang_seconds=0.1,
            )

            spent = await client.look_up(
                lemma="photograph",
            )

            closed = client.activity().circuit

            await asyncio_sleep(
                delay=spent.retry_after_seconds or 0.0,
            )

            opening = await client.look_up(
                lemma="photograph",
            )

            return (
                spent,
                closed,
                opening,
                tuple(
                    arrivals,
                ),
            )

        (
            spent,
            closed,
            opening,
            arrivals,
        ) = run_in_virtual_time(
            main=scenario,
        )

        assert spent.failure_reason == FailureReason.PROVIDER_ERROR

        assert spent.retry_after_seconds is not None

        assert 1.0 <= spent.retry_after_seconds <= 2.0

        assert closed is BreakerState.CLOSED

        assert tuple(arrival.in_a_row for arrival in arrivals) == (
            0,
            1,
            2,
            3,
            4,
        )

        assert opening.failure_reason == FailureReason.PROVIDER_ERROR

        assert opening.retry_after_seconds is not None

        assert 7.5 <= opening.retry_after_seconds <= 10.0

    def test_a_word_whose_failures_an_answer_interrupted_names_no_time(
        self,
    ) -> None:
        # NOTE:
        # The provider fails one word every time and answers another, asked while the first waits to ask again: the
        # answer shows the failures are the word's own, so once its attempts are spent it names no time, and WordNet
        # stands in for it.
        def answer(
            request: Request,
            /,
        ) -> Response:
            if request.url.path.endswith(
                "/photograph",
            ):
                return Response(
                    status_code=503,
                )

            return Response(
                status_code=200,
                json=_FOUND_PAYLOAD,
            )

        async def scenario() -> tuple[DictionaryLookup, DictionaryLookup, BreakerState, int | None]:
            (
                client,
                _,
            ) = _counted(
                answer,
                hang_seconds=0.1,
            )

            failing = create_task(
                coro=client.look_up(
                    lemma="photograph",
                ),
            )

            await asyncio_sleep(
                delay=0.15,
            )

            found = await client.look_up(
                lemma="mask",
            )

            spent = await failing

            return (
                found,
                spent,
                client.activity().circuit,
                client.activity().consecutive_failures,
            )

        (
            found,
            spent,
            circuit,
            in_a_row,
        ) = run_in_virtual_time(
            main=scenario,
        )

        assert found.outcome is LookupOutcome.FOUND

        assert (
            spent.failure_reason,
            spent.retry_after_seconds,
            circuit,
            in_a_row,
        ) == (
            FailureReason.PROVIDER_ERROR,
            None,
            BreakerState.CLOSED,
            2,
        )

    def test_a_wait_one_failure_named_holds_the_trial_back_after_other_failures_open_the_circuit(
        self,
    ) -> None:
        # NOTE:
        # The first word's 503 names sixty seconds, so its lookup ends at once with that wait; four failures of two
        # other words, naming none, then open the circuit a second and a half in, and its own ten seconds would let a
        # trial go eight seconds later, but no trial may come before the sixty seconds the provider named end.
        named: list[bool] = [
            False,
        ]

        recovered: list[bool] = [
            False,
        ]

        def answer(
            request: Request,
            /,
        ) -> Response:
            if recovered[0]:
                return Response(
                    status_code=200,
                    json=_FOUND_PAYLOAD,
                )

            if not named[0]:
                named[0] = True

                return Response(
                    status_code=503,
                    headers={
                        "Retry-After": "60",
                    },
                )

            return Response(
                status_code=503,
            )

        async def scenario() -> tuple[DictionaryLookup, DictionaryLookup, tuple[_Arrival, ...], float]:
            loop = get_running_loop()

            (
                client,
                arrivals,
            ) = _counted(
                answer,
                hang_seconds=0.1,
            )

            first = await client.look_up(
                lemma="photograph",
            )

            for word in (
                "mask",
                "groom",
            ):
                await client.look_up(
                    lemma=word,
                )

            recovered[0] = True

            await asyncio_sleep(
                delay=59.9 - loop.time(),
            )

            early = await client.look_up(
                lemma="lantern",
            )

            await asyncio_sleep(
                delay=early.retry_after_seconds or 0.0,
            )

            await client.look_up(
                lemma="lantern",
            )

            return (
                first,
                early,
                tuple(
                    arrivals,
                ),
                loop.time(),
            )

        (
            first,
            early,
            arrivals,
            ended,
        ) = run_in_virtual_time(
            main=scenario,
        )

        assert (
            first.failure_reason,
            first.retry_after_seconds,
        ) == (
            FailureReason.PROVIDER_ERROR,
            60.0,
        )

        assert early.failure_reason == FailureReason.CIRCUIT_OPEN

        assert early.retry_after_seconds is not None

        assert (
            abs(
                early.retry_after_seconds - 0.2,
            )
            < 1e-6
        )

        assert tuple(arrival.circuit for arrival in arrivals) == (
            *((BreakerState.CLOSED,) * 5),
            BreakerState.HALF_OPEN,
        )

        assert ended >= 60.1
