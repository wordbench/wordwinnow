"""
The operator's view the command line shows on standard error: the learner's
line, and under it what the dictionary is doing.
"""

from io import (
    StringIO,
)
from re import (
    sub,
)
from types import (
    MappingProxyType,
)
from typing import (
    Final,
    final,
)

from rich.console import (
    Console,
)

from tests.infrastructure.dictionary.doubles import (
    FakeMonotonicClock,
)
from wordwinnow.domain.analysis import (
    Stage,
)
from wordwinnow.infrastructure.dictionary.activity import (
    DictionaryActivity,
)
from wordwinnow.infrastructure.dictionary.cache import (
    LookupCounts,
)
from wordwinnow.infrastructure.dictionary.free_dictionary import (
    LookupActivity,
    ProviderActivity,
    WaitCause,
)
from wordwinnow.infrastructure.dictionary.resilience import (
    BreakerState,
)
from wordwinnow.infrastructure.dictionary.wire import (
    FailureReason,
)
from wordwinnow.services.cli.operator import (
    OperatorProgress,
)

# NOTE:
# What the dictionary had done before the analysis began, which the view counts from.
_BEFORE: Final = DictionaryActivity(
    counts=LookupCounts(
        cached=20,
        found=30,
        not_found=2,
        unavailable=MappingProxyType(
            mapping={
                "provider_error": 1,
            },
        ),
    ),
    provider=ProviderActivity(
        circuit=BreakerState.CLOSED,
        retry_in_seconds=None,
        tokens=4.0,
        burst=4,
        requests_per_second=2.0,
        waiting_for_budget=0,
        timeout_seconds=30.0,
        lookups=(),
        retries=MappingProxyType(
            mapping={
                FailureReason.TIMED_OUT: 3,
            },
        ),
    ),
)

# NOTE:
# A trial in flight with one lookup waiting for its answer, one pausing before its second attempt, and one waiting for
# a token.
_DURING: Final = DictionaryActivity(
    counts=LookupCounts(
        cached=32,
        found=35,
        not_found=3,
        unavailable=MappingProxyType(
            mapping={
                "provider_error": 1,
                "circuit_open": 2,
            },
        ),
    ),
    provider=ProviderActivity(
        circuit=BreakerState.HALF_OPEN,
        retry_in_seconds=None,
        tokens=1.4,
        burst=4,
        requests_per_second=2.0,
        waiting_for_budget=3,
        timeout_seconds=30.0,
        lookups=(
            LookupActivity(
                lemma="photograph",
                waiting_for=WaitCause.PROVIDER,
                attempt=1,
                seconds=18.2,
                trial=True,
            ),
            LookupActivity(
                lemma="mask",
                waiting_for=WaitCause.TRIAL,
                attempt=1,
                seconds=18.1,
            ),
            LookupActivity(
                lemma="groom",
                waiting_for=WaitCause.RETRY,
                attempt=2,
                seconds=0.9,
                delay_seconds=1.7,
                reason=FailureReason.RATE_LIMITED,
            ),
            LookupActivity(
                lemma="rocket",
                waiting_for=WaitCause.BUDGET,
                attempt=1,
                seconds=0.4,
            ),
        ),
        retries=MappingProxyType(
            mapping={
                FailureReason.TIMED_OUT: 3,
                FailureReason.RATE_LIMITED: 1,
            },
        ),
    ),
)


def _terminal(
    stream: StringIO,
    /,
) -> Console:
    return Console(
        force_terminal=True,
        force_interactive=True,
        file=stream,
        width=110,
    )


def _drawn(
    stream: StringIO,
    /,
) -> str:
    """
    What `stream` shows, without the sequences that style it and move the
    cursor.
    """

    return sub(
        pattern=r"\x1b\[[0-9;?]*[A-Za-z]",
        repl="",
        string=stream.getvalue(),
    )


@final
class TestOperatorProgress:
    def test_a_stream_that_is_not_a_terminal_receives_nothing(
        self,
    ) -> None:
        stream = StringIO()

        with OperatorProgress(
            console=Console(
                file=stream,
            ),
        ) as progress:
            progress.stage_started(
                stage=Stage.DICTIONARY_ENRICHMENT,
                steps=156,
            )

            progress.watched(
                activity=_DURING,
            )

        assert not progress.shown

        assert stream.getvalue() == ""

    def test_a_terminal_sees_the_line_and_under_it_what_each_lookup_waits_for(
        self,
    ) -> None:
        stream = StringIO()

        clock = FakeMonotonicClock()

        with OperatorProgress(
            console=_terminal(
                stream,
            ),
            refresh_per_second=1,
            clock=clock,
        ) as progress:
            progress.stage_started(
                stage=Stage.DICTIONARY_ENRICHMENT,
                steps=156,
            )

            progress.watched(
                activity=_BEFORE,
            )

            progress.watched(
                activity=_DURING,
            )

            # NOTE:
            # A second after the read, each wait has gone on for a second more.
            clock.advance(
                seconds=1.0,
            )

        drawn = _drawn(
            stream,
        )

        for expected in (
            "looking the words up in the dictionary",
            "half-open: one lookup is the trial, and 1 lookup waits for its outcome",
            "1.4 of 4 tokens, refilled at 2 a second; 3 lookups waiting for one",
            "12 from the cache, 5 found and 1 not found by the provider",
            "unavailable  2 (2 circuit open)",
            "retries      1 (1 rate limited)",
            "asking the provider, attempt 1, the trial",
            "19.2 s",
            "waiting for the trial's outcome",
            "pausing 1.7 s before attempt 2 (rate limited)",
            "1.9 s",
            "waiting for a token",
        ):
            assert expected in drawn, expected

    def test_an_open_circuit_counts_down_to_the_next_trial(
        self,
    ) -> None:
        stream = StringIO()

        clock = FakeMonotonicClock()

        with OperatorProgress(
            console=_terminal(
                stream,
            ),
            refresh_per_second=1,
            clock=clock,
        ) as progress:
            progress.watched(
                activity=DictionaryActivity(
                    counts=_BEFORE.counts,
                    provider=ProviderActivity(
                        circuit=BreakerState.OPEN,
                        retry_in_seconds=23.4,
                        tokens=4.0,
                        burst=4,
                        requests_per_second=2.0,
                        waiting_for_budget=0,
                        timeout_seconds=30.0,
                        lookups=(),
                        retries=_BEFORE.provider.retries,
                    ),
                ),
            )

            clock.advance(
                seconds=3.0,
            )

        drawn = _drawn(
            stream,
        )

        assert "open: no new lookup asks the provider for 20 s more" in drawn

        assert "in flight    nothing" in drawn

    def test_a_dictionary_it_cannot_read_is_named_with_the_reason(
        self,
    ) -> None:
        stream = StringIO()

        with OperatorProgress(
            console=_terminal(
                stream,
            ),
            refresh_per_second=1,
        ) as progress:
            progress.unwatched(
                reason="the enrichment service does not answer at http://enrichment.invalid",
            )

        assert "dictionary  the enrichment service does not answer at http://enrichment.invalid" in _drawn(
            stream,
        )
