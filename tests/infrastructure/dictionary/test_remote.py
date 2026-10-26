"""
The remote dictionary against a mock enrichment service, on a clock the test
moves.
"""

from collections.abc import (
    Callable,
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

from tests.infrastructure.dictionary.doubles import (
    FakeMonotonicClock,
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
from wordwinnow.infrastructure.dictionary.remote import (
    RemoteDictionary,
)
from wordwinnow.infrastructure.dictionary.wire import (
    FailureReason,
    LookupPayload,
    to_wire,
    unavailable,
)
from wordwinnow.infrastructure.log_payload import (
    bind_correlation_id,
)

_FOUND: Final = DictionaryLookup(
    outcome=LookupOutcome.FOUND,
    entries=(
        DictionaryEntry(
            headword="photograph",
            phonetic=None,
            meanings=(
                Meaning(
                    word_class="noun",
                    part_of_speech=PartOfSpeech.NOUN,
                    definitions=(
                        Definition(
                            text="A picture made with a camera.",
                        ),
                    ),
                ),
            ),
        ),
    ),
)


def _answer(
    lookup: DictionaryLookup,
    /,
) -> Response:
    return Response(
        status_code=200,
        content=to_wire(
            lookup=lookup,
        ).model_dump_json(),
    )


@final
class Harness:
    """
    A remote dictionary over a mock transport, with every request it sent and
    the clock it reads.
    """

    def __init__(
        self,
        *,
        handler: Callable[[Request], Response],
    ) -> None:
        self.requests: Final[list[Request]] = []

        self.clock: Final = FakeMonotonicClock()

        def record(
            request: Request,
            /,
        ) -> Response:
            self.requests.append(
                request,
            )

            return handler(
                request,
            )

        self.dictionary: Final = RemoteDictionary(
            enrichment_url="http://enrichment.test/",
            timeout_seconds=5.0,
            client=AsyncClient(
                transport=MockTransport(
                    handler=record,
                ),
            ),
            clock=self.clock,
        )


@final
class TestRemoteDictionary:
    async def test_a_lookup_is_fetched_with_the_bound_correlation_id(
        self,
    ) -> None:
        harness = Harness(
            handler=lambda request: _answer(
                _FOUND,
            ),
        )

        bind_correlation_id(
            correlation_id="foo",
        )

        try:
            lookup = await harness.dictionary.look_up(
                lemma="photograph",
            )

        finally:
            bind_correlation_id(
                correlation_id=None,
            )

        assert lookup == _FOUND

        assert harness.requests[0].url.path == "/lookups/photograph"

        assert harness.requests[0].headers["x-correlation-id"] == "foo"

    async def test_no_correlation_header_is_sent_when_none_is_bound(
        self,
    ) -> None:
        harness = Harness(
            handler=lambda request: _answer(
                _FOUND,
            ),
        )

        await harness.dictionary.look_up(
            lemma="photograph",
        )

        assert "x-correlation-id" not in harness.requests[0].headers

    async def test_an_unavailable_answer_is_the_service_s_own_outcome(
        self,
    ) -> None:
        harness = Harness(
            handler=lambda request: _answer(
                unavailable(
                    reason=FailureReason.CIRCUIT_OPEN,
                ),
            ),
        )

        lookup = await harness.dictionary.look_up(
            lemma="photograph",
        )

        assert lookup.outcome is LookupOutcome.UNAVAILABLE

        assert lookup.failure_reason == "circuit_open"

    async def test_failures_reaching_the_service_are_mapped(
        self,
    ) -> None:
        def timing_out(
            request: Request,
            /,
        ) -> Response:
            raise ReadTimeout(
                message="slow",
            )

        def unreachable(
            request: Request,
            /,
        ) -> Response:
            raise ConnectError(
                message="connection refused",
            )

        def failing(
            request: Request,
            /,
        ) -> Response:
            return Response(
                status_code=500,
            )

        def unreadable(
            request: Request,
            /,
        ) -> Response:
            return Response(
                status_code=200,
                content=b"not json",
            )

        def inconsistent(
            request: Request,
            /,
        ) -> Response:
            return Response(
                status_code=200,
                content=LookupPayload(
                    outcome=LookupOutcome.FOUND,
                ).model_dump_json(),
            )

        # NOTE:
        # A service that could not be reached, or did not answer in time, is asked again a second later at the first
        # such failure; one that answered something else names no time, because asking again would get the same
        # answer.
        for (
            handler,
            expected,
            retry_after_seconds,
        ) in (
            (
                timing_out,
                "timed_out",
                1.0,
            ),
            (
                unreachable,
                "transport_error",
                1.0,
            ),
            (
                failing,
                "provider_error",
                None,
            ),
            (
                unreadable,
                "invalid_response",
                None,
            ),
            (
                inconsistent,
                "invalid_response",
                None,
            ),
        ):
            lookup = await Harness(
                handler=handler,
            ).dictionary.look_up(
                lemma="photograph",
            )

            assert lookup.outcome is LookupOutcome.UNAVAILABLE

            assert (
                lookup.failure_reason,
                lookup.retry_after_seconds,
            ) == (
                expected,
                retry_after_seconds,
            )

    async def test_the_time_to_ask_again_is_how_long_the_service_has_been_unreachable_until_it_answers(
        self,
    ) -> None:
        reachable = [
            False,
        ]

        def restarting(
            request: Request,
            /,
        ) -> Response:
            if not reachable[0]:
                raise ConnectError(
                    message="connection refused",
                )

            return _answer(
                _FOUND,
            )

        harness = Harness(
            handler=restarting,
        )

        retries: list[float | None] = []

        # NOTE:
        # The service stops answering at nought: a second at the least, then as long as it has been unreachable, and
        # fifteen seconds at the most; once it answers, the next failure starts a new outage at a second.
        for (
            seconds,
            up,
        ) in (
            (
                0.0,
                False,
            ),
            (
                0.5,
                False,
            ),
            (
                3.5,
                False,
            ),
            (
                26.0,
                False,
            ),
            (
                1.0,
                True,
            ),
            (
                10.0,
                False,
            ),
        ):
            harness.clock.advance(
                seconds=seconds,
            )

            reachable[0] = up

            lookup = await harness.dictionary.look_up(
                lemma="photograph",
            )

            retries.append(
                lookup.retry_after_seconds,
            )

        assert retries == [
            1.0,
            1.0,
            4.0,
            15.0,
            None,
            1.0,
        ]
