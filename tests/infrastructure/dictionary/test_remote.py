"""
The remote dictionary against a mock enrichment service.
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
    A remote dictionary over a mock transport, with every request it sent.
    """

    def __init__(
        self,
        *,
        handler: Callable[[Request], Response],
    ) -> None:
        self.requests: Final[list[Request]] = []

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

        for (
            handler,
            expected,
        ) in (
            (
                timing_out,
                "timed_out",
            ),
            (
                unreachable,
                "transport_error",
            ),
            (
                failing,
                "provider_error",
            ),
            (
                unreadable,
                "invalid_response",
            ),
            (
                inconsistent,
                "invalid_response",
            ),
        ):
            lookup = await Harness(
                handler=handler,
            ).dictionary.look_up(
                lemma="photograph",
            )

            assert lookup.outcome is LookupOutcome.UNAVAILABLE

            assert lookup.failure_reason == expected
