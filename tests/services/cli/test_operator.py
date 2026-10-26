"""
The operator's view the command line shows on standard error: the learner's
line, and under it what the dictionary is doing.
"""

from dataclasses import (
    replace,
)
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
    RecentRequest,
    RequestOutcome,
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
        cooldown_seconds=None,
        consecutive_failures=1,
        failure_threshold=5,
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
        cooldown_seconds=None,
        consecutive_failures=0,
        failure_threshold=5,
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
                reason=FailureReason.PROVIDER_ERROR,
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
                FailureReason.PROVIDER_ERROR: 1,
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
            "dictionary: failing",
            "circuit      half-open: the trial is in flight, 1 lookup waits for it",
            "next try     when the trial is answered",
            "answered     18: 12 from the cache, 5 found, 1 not found",
            "turned away  2: 2 held back",
            "retries      1: 1 server error",
            "budget       1.4 of 4 tokens, 2 a second",
            "1 asking, 1 retrying, 1 behind the trial, 1 queued for a token",
            "asking, attempt 1, the trial",
            "19.2 s",
            "attempt 2 in 1.7 s, server error",
            "1.9 s",
        ):
            assert expected in drawn, expected

        # NOTE:
        # The lookups waiting for the trial or for a token are counted, not drawn a row each.
        for unexpected in (
            "mask",
            "rocket",
        ):
            assert unexpected not in drawn, unexpected

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
                        cooldown_seconds=None,
                        consecutive_failures=0,
                        failure_threshold=5,
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

        for expected in (
            "dictionary: failing",
            "circuit      open",
            "next try     the trial in 21 s",
            "in flight    nothing",
        ):
            assert expected in drawn, expected

    def test_an_open_circuit_says_what_opened_it_and_which_trial_comes_next(
        self,
    ) -> None:
        def drawn_for(
            failed_trials: int,
            opened_by: FailureReason,
            /,
        ) -> str:
            stream = StringIO()

            with OperatorProgress(
                console=_terminal(
                    stream,
                ),
                refresh_per_second=1,
                clock=FakeMonotonicClock(),
            ) as progress:
                progress.watched(
                    activity=DictionaryActivity(
                        counts=_BEFORE.counts,
                        provider=ProviderActivity(
                            circuit=BreakerState.OPEN,
                            retry_in_seconds=9.2,
                            cooldown_seconds=None,
                            consecutive_failures=0,
                            failure_threshold=5,
                            tokens=4.0,
                            burst=4,
                            requests_per_second=2.0,
                            waiting_for_budget=0,
                            timeout_seconds=30.0,
                            lookups=(),
                            retries=_BEFORE.provider.retries,
                            failed_trials=failed_trials,
                            opened_by=opened_by,
                        ),
                    ),
                )

            return _drawn(
                stream,
            )

        opened = drawn_for(
            0,
            FailureReason.PROVIDER_ERROR,
        )

        reopened = drawn_for(
            2,
            FailureReason.TIMED_OUT,
        )

        for (
            drawn,
            expected,
        ) in (
            (
                opened,
                "circuit      open after 5 failed requests (server error)",
            ),
            (
                opened,
                "next try     trial 1 in 10 s",
            ),
            (
                reopened,
                "circuit      open after trial 2 failed (timed out)",
            ),
            (
                reopened,
                "next try     trial 3 in 10 s",
            ),
        ):
            assert expected in drawn, expected

    def test_a_half_open_circuit_names_the_trial_in_flight(
        self,
    ) -> None:
        stream = StringIO()

        with OperatorProgress(
            console=_terminal(
                stream,
            ),
            refresh_per_second=1,
            clock=FakeMonotonicClock(),
        ) as progress:
            progress.watched(
                activity=DictionaryActivity(
                    counts=_DURING.counts,
                    provider=replace(
                        _DURING.provider,
                        failed_trials=2,
                        opened_by=FailureReason.TIMED_OUT,
                    ),
                ),
            )

        drawn = _drawn(
            stream,
        )

        for expected in (
            "circuit      half-open: trial 3 is in flight, 1 lookup waits for it",
            "next try     when trial 3 is answered",
        ):
            assert expected in drawn, expected

    def test_a_pause_the_provider_asked_for_counts_down_for_every_lookup(
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
                        circuit=BreakerState.CLOSED,
                        retry_in_seconds=None,
                        cooldown_seconds=9.0,
                        consecutive_failures=0,
                        failure_threshold=5,
                        tokens=0.0,
                        burst=4,
                        requests_per_second=2.0,
                        waiting_for_budget=2,
                        timeout_seconds=30.0,
                        lookups=(
                            LookupActivity(
                                lemma="photograph",
                                waiting_for=WaitCause.COOLDOWN,
                                attempt=2,
                                seconds=1.5,
                            ),
                        ),
                        retries=_BEFORE.provider.retries,
                    ),
                ),
            )

            clock.advance(
                seconds=2.0,
            )

        drawn = _drawn(
            stream,
        )

        for expected in (
            "dictionary: rate-limited",
            "next try     in 7 s, when the provider's pause ends",
            "in flight    1 paused",
        ):
            assert expected in drawn, expected

    def test_a_pause_that_outlasts_the_open_circuit_holds_the_trial_until_it_ends(
        self,
    ) -> None:
        stream = StringIO()

        with OperatorProgress(
            console=_terminal(
                stream,
            ),
            refresh_per_second=1,
            clock=FakeMonotonicClock(),
        ) as progress:
            progress.watched(
                activity=replace(
                    _BEFORE,
                    provider=replace(
                        _BEFORE.provider,
                        circuit=BreakerState.OPEN,
                        retry_in_seconds=3.0,
                        cooldown_seconds=18.0,
                        consecutive_failures=0,
                        failed_trials=1,
                        opened_by=FailureReason.TIMED_OUT,
                    ),
                ),
            )

        drawn = _drawn(
            stream,
        )

        for expected in (
            "dictionary: failing",
            "next try     trial 2 in 18 s, when the provider's pause ends",
        ):
            assert expected in drawn, expected

    def test_a_provider_that_answers_has_no_next_try_row(
        self,
    ) -> None:
        stream = StringIO()

        with OperatorProgress(
            console=_terminal(
                stream,
            ),
            refresh_per_second=1,
            clock=FakeMonotonicClock(),
        ) as progress:
            progress.watched(
                activity=replace(
                    _BEFORE,
                    provider=replace(
                        _BEFORE.provider,
                        consecutive_failures=0,
                    ),
                ),
            )

        drawn = _drawn(
            stream,
        )

        for expected in (
            "dictionary: answering",
            "circuit      closed",
        ):
            assert expected in drawn, expected

        assert "next try" not in drawn

    def test_the_breakers_run_of_failures_stands_apart_from_the_unanswered_total(
        self,
    ) -> None:
        stream = StringIO()

        with OperatorProgress(
            console=_terminal(
                stream,
            ),
            refresh_per_second=1,
        ) as progress:
            progress.watched(
                activity=_BEFORE,
            )

            progress.watched(
                activity=DictionaryActivity(
                    counts=replace(
                        _BEFORE.counts,
                        unavailable=MappingProxyType(
                            mapping={
                                "provider_error": 5,
                                "circuit_open": 2,
                            },
                        ),
                    ),
                    provider=replace(
                        _BEFORE.provider,
                        consecutive_failures=3,
                    ),
                ),
            )

        drawn = _drawn(
            stream,
        )

        for expected in (
            "dictionary: failing",
            "circuit      closed, 3 of 5 requests failed in a row; it opens at 5",
            "turned away  6: 4 server error, 2 held back",
        ):
            assert expected in drawn, expected

    def test_while_the_circuit_is_open_no_count_of_failures_in_a_row_is_shown(
        self,
    ) -> None:
        stream = StringIO()

        with OperatorProgress(
            console=_terminal(
                stream,
            ),
            refresh_per_second=1,
        ) as progress:
            progress.watched(
                activity=replace(
                    _BEFORE,
                    provider=replace(
                        _BEFORE.provider,
                        circuit=BreakerState.OPEN,
                        retry_in_seconds=12.0,
                        cooldown_seconds=None,
                        consecutive_failures=0,
                    ),
                ),
            )

        assert "in a row" not in _drawn(
            stream,
        )

    def test_a_dictionary_that_does_not_report_its_run_of_failures_says_so(
        self,
    ) -> None:
        stream = StringIO()

        with OperatorProgress(
            console=_terminal(
                stream,
            ),
            refresh_per_second=1,
        ) as progress:
            progress.watched(
                activity=replace(
                    _BEFORE,
                    provider=replace(
                        _BEFORE.provider,
                        consecutive_failures=None,
                        failure_threshold=None,
                    ),
                ),
            )

        drawn = _drawn(
            stream,
        )

        assert "circuit      closed" in drawn

        assert "in a row" not in drawn

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

        drawn = _drawn(
            stream,
        )

        for expected in (
            "─ dictionary ─",
            "the enrichment service does not answer at http://enrichment.invalid",
        ):
            assert expected in drawn, expected

    def test_on_an_80_column_terminal_every_row_of_the_panel_is_drawn_whole(
        self,
    ) -> None:
        stream = StringIO()

        with OperatorProgress(
            console=Console(
                force_terminal=True,
                force_interactive=True,
                file=stream,
                width=80,
            ),
            refresh_per_second=1,
            clock=FakeMonotonicClock(),
        ) as progress:
            progress.watched(
                activity=replace(
                    _DURING,
                    provider=replace(
                        _DURING.provider,
                        failed_trials=2,
                        opened_by=FailureReason.TIMED_OUT,
                    ),
                ),
            )

        drawn = _drawn(
            stream,
        )

        panel = [
            line
            for line in drawn.splitlines()
            if line.startswith(
                (
                    "╭",
                    "│",
                    "╰",
                ),
            )
        ]

        assert panel

        assert all(
            len(
                line,
            )
            < 80
            for line in panel
        )

        for expected in (
            "half-open: trial 3 is in flight, 1 lookup waits for it",
            "1 asking, 1 retrying, 1 behind the trial, 1 queued for a token",
            "attempt 2 in 1.7 s, server error",
        ):
            assert expected in drawn, expected

    def test_the_last_minute_shows_the_worst_outcome_of_each_second_newest_on_the_right(
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
                activity=replace(
                    _BEFORE,
                    provider=replace(
                        _BEFORE.provider,
                        recent=tuple(
                            RecentRequest(
                                seconds=seconds,
                                outcome=outcome,
                            )
                            for (
                                seconds,
                                outcome,
                            ) in (
                                (
                                    65.0,
                                    RequestOutcome.FAILED,
                                ),
                                (
                                    58.6,
                                    RequestOutcome.ANSWERED,
                                ),
                                (
                                    29.5,
                                    RequestOutcome.REFUSED,
                                ),
                                (
                                    9.8,
                                    RequestOutcome.FAILED,
                                ),
                                (
                                    9.6,
                                    RequestOutcome.ANSWERED,
                                ),
                                (
                                    0.2,
                                    RequestOutcome.ANSWERED,
                                ),
                            )
                        ),
                        last_answer_seconds=0.2,
                    ),
                ),
            )

            # NOTE:
            # Half a second after the read, every request is half a second older.
            clock.advance(
                seconds=0.5,
            )

        drawn = _drawn(
            stream,
        )

        # NOTE:
        # From the oldest second on the left: the answer 59.1 s old, nothing until the refusal 30 s old, nothing until
        # the second 10 to 11 s old, where a failure outweighs an answer, then the answer 0.7 s old; the failure 65.5
        # s old has left the minute.
        strip = "✓" + "·" * 28 + "!" + "·" * 19 + "✗" + "·" * 9 + "✓"

        assert f"last minute  {strip}" in drawn

        assert "last answer" not in drawn

    def test_an_answer_older_than_the_minute_says_how_long_ago_it_came(
        self,
    ) -> None:
        stream = StringIO()

        with OperatorProgress(
            console=_terminal(
                stream,
            ),
            refresh_per_second=1,
            clock=FakeMonotonicClock(),
        ) as progress:
            progress.watched(
                activity=replace(
                    _BEFORE,
                    provider=replace(
                        _BEFORE.provider,
                        circuit=BreakerState.OPEN,
                        retry_in_seconds=4.0,
                        consecutive_failures=0,
                        recent=(
                            RecentRequest(
                                seconds=8.4,
                                outcome=RequestOutcome.FAILED,
                            ),
                        ),
                        last_answer_seconds=130.4,
                    ),
                ),
            )

        drawn = _drawn(
            stream,
        )

        for expected in (
            "last minute  " + "·" * 51 + "✗" + "·" * 8,
            "last answer  2 min 10 s ago",
        ):
            assert expected in drawn, expected
