"""
The progress line the command line shows on standard error.
"""

from datetime import (
    datetime,
    timedelta,
)
from io import (
    StringIO,
)
from typing import (
    Final,
    final,
)

from pytest import (
    MonkeyPatch,
)
from rich.console import (
    Console,
)

from tests.fakes.ports import (
    EPOCH,
)
from wordwinnow.application.dto import (
    ProcessingProgress,
)
from wordwinnow.domain.analysis import (
    Stage,
)
from wordwinnow.domain.dictionary import (
    DictionaryLookup,
    LookupOutcome,
)
from wordwinnow.infrastructure.dictionary.wire import (
    FailureReason,
    unavailable,
)
from wordwinnow.services.cli.progress import (
    PAUSED_DICTIONARY,
    STAGE_DESCRIPTIONS,
    TerminalProgress,
)

# NOTE:
# A lookup the dictionary answered; not found is an answer as much as found is, and it needs no entries.
_ANSWERED: Final = DictionaryLookup(
    outcome=LookupOutcome.NOT_FOUND,
)


@final
class _SteppedClock:
    """
    A wall clock that moves only when a test moves it.
    """

    def __init__(
        self,
    ) -> None:
        self.now = EPOCH

    def __call__(
        self,
    ) -> datetime:
        return self.now


def _terminal(
    stream: StringIO,
    /,
) -> Console:
    return Console(
        force_terminal=True,
        force_interactive=True,
        file=stream,
        width=160,
    )


@final
class TestTerminalProgress:
    def test_a_stream_that_is_not_a_terminal_receives_nothing(
        self,
    ) -> None:
        stream = StringIO()

        with TerminalProgress(
            console=Console(
                file=stream,
            ),
        ) as progress:
            progress.begin(
                description="looking the words up in the dictionary",
                steps=3,
            )

            progress.advance()

        assert not progress.shown

        assert stream.getvalue() == ""

    def test_a_dumb_terminal_receives_nothing(
        self,
        *,
        monkeypatch: MonkeyPatch,
    ) -> None:
        stream = StringIO()

        # NOTE:
        # A dumb terminal cannot move its cursor, so a line redrawn in place would pile up instead.
        monkeypatch.setenv(
            name="TERM",
            value="dumb",
        )

        with TerminalProgress(
            console=Console(
                force_terminal=True,
                force_interactive=True,
                file=stream,
            ),
        ) as progress:
            progress.begin(
                description="building the dataset from the reference lists",
                steps=None,
            )

        assert not progress.shown

        assert stream.getvalue() == ""

    def test_a_terminal_sees_each_phase_and_the_count_of_a_counted_one(
        self,
    ) -> None:
        stream = StringIO()

        with TerminalProgress(
            console=Console(
                force_terminal=True,
                force_interactive=True,
                file=stream,
                width=100,
            ),
        ) as progress:
            progress.stage_started(
                stage=Stage.LINGUISTIC_ANALYSIS,
                steps=None,
            )

            progress.stage_started(
                stage=Stage.DICTIONARY_ENRICHMENT,
                steps=1_509,
            )

            for _ in range(
                3,
            ):
                progress.looked_up(
                    lookup=_ANSWERED,
                )

        assert progress.shown

        written = stream.getvalue()

        assert "looking the words up in the dictionary" in written

        assert "3 of 1,509" in written

    def test_unavailable_answers_are_counted_and_a_paused_dictionary_is_named(
        self,
    ) -> None:
        stream = StringIO()

        with TerminalProgress(
            console=Console(
                force_terminal=True,
                force_interactive=True,
                file=stream,
                width=120,
            ),
        ) as progress:
            progress.stage_started(
                stage=Stage.DICTIONARY_ENRICHMENT,
                steps=8,
            )

            for lookup in (
                _ANSWERED,
                unavailable(
                    reason=FailureReason.TIMED_OUT,
                ),
                unavailable(
                    reason=FailureReason.CIRCUIT_OPEN,
                ),
            ):
                progress.looked_up(
                    lookup=lookup,
                )

            paused = stream.getvalue()

            progress.looked_up(
                lookup=_ANSWERED,
            )

        answered = stream.getvalue().removeprefix(
            paused,
        )

        assert PAUSED_DICTIONARY in paused

        assert "3 of 8, 2 unavailable" in paused

        assert "4 of 8, 2 unavailable" in answered

        assert "looking the words up in the dictionary" in answered

    def test_a_word_left_unanswered_for_another_reason_does_not_end_the_pause(
        self,
    ) -> None:
        stream = StringIO()

        with TerminalProgress(
            console=Console(
                force_terminal=True,
                force_interactive=True,
                file=stream,
                width=120,
            ),
        ) as progress:
            progress.stage_started(
                stage=Stage.DICTIONARY_ENRICHMENT,
                steps=8,
            )

            for reason in (
                FailureReason.CIRCUIT_OPEN,
                FailureReason.TIMED_OUT,
            ):
                progress.looked_up(
                    lookup=unavailable(
                        reason=reason,
                    ),
                )

        # NOTE:
        # The line's last drawing, before it is cleared, shows the state after the second word.
        last = [
            drawing
            for drawing in stream.getvalue().split(
                sep="\r",
            )
            if " of 8" in drawing
        ][-1]

        assert PAUSED_DICTIONARY in last

        assert "2 of 8, 2 unavailable" in last

    def test_a_workers_report_shows_its_stage_its_count_and_a_pause(
        self,
    ) -> None:
        stream = StringIO()

        reports = (
            ProcessingProgress(
                stage=Stage.DICTIONARY_ENRICHMENT,
                steps=156,
                done=63,
                unavailable=20,
                dictionary_paused=False,
                reported_at=EPOCH,
            ),
            ProcessingProgress(
                stage=Stage.DICTIONARY_ENRICHMENT,
                steps=156,
                done=64,
                unavailable=21,
                dictionary_paused=True,
                reported_at=EPOCH,
            ),
            ProcessingProgress(
                stage=Stage.WINNOWING,
                steps=None,
                done=0,
                unavailable=0,
                dictionary_paused=False,
                reported_at=EPOCH,
            ),
        )

        drawn: list[str] = []

        with TerminalProgress(
            console=Console(
                force_terminal=True,
                force_interactive=True,
                file=stream,
                width=120,
            ),
        ) as progress:
            for report in reports:
                before = stream.getvalue()

                progress.processing(
                    progress=report,
                )

                drawn.append(
                    stream.getvalue().removeprefix(
                        before,
                    ),
                )

        (
            counting,
            pausing,
            placing,
        ) = drawn

        assert "looking the words up in the dictionary" in counting

        assert "63 of 156, 20 unavailable" in counting

        assert PAUSED_DICTIONARY in pausing

        assert "64 of 156, 21 unavailable" in pausing

        assert "placing the words in tiers" in placing

    def test_lookups_waiting_for_the_dictionary_name_the_next_try_and_when_wordnet_takes_over(
        self,
    ) -> None:
        stream = StringIO()

        drawn: list[str] = []

        with TerminalProgress(
            console=Console(
                force_terminal=True,
                force_interactive=True,
                file=stream,
                width=140,
            ),
            clock=lambda: EPOCH,
        ) as progress:
            progress.stage_started(
                stage=Stage.DICTIONARY_ENRICHMENT,
                steps=4,
            )

            for (
                retry_in_seconds,
                patience_left_seconds,
            ) in (
                (
                    30.0,
                    180.0,
                ),
                (
                    None,
                    150.0,
                ),
            ):
                before = stream.getvalue()

                progress.dictionary_waiting(
                    retry_in_seconds=retry_in_seconds,
                    patience_left_seconds=patience_left_seconds,
                )

                drawn.append(
                    stream.getvalue().removeprefix(
                        before,
                    ),
                )

        (
            waiting,
            resumed,
        ) = drawn

        assert "waiting for the dictionary: next try in 30 s, WordNet within 3 min" in waiting

        assert STAGE_DESCRIPTIONS[Stage.DICTIONARY_ENRICHMENT] in resumed

    def test_a_workers_report_of_waiting_lookups_names_the_times_from_when_it_was_written(
        self,
    ) -> None:
        stream = StringIO()

        with TerminalProgress(
            console=Console(
                force_terminal=True,
                force_interactive=True,
                file=stream,
                width=140,
            ),
            clock=lambda: EPOCH,
        ) as progress:
            progress.processing(
                progress=ProcessingProgress(
                    stage=Stage.DICTIONARY_ENRICHMENT,
                    steps=156,
                    done=40,
                    unavailable=0,
                    dictionary_paused=False,
                    reported_at=EPOCH,
                    dictionary_retry_in_seconds=20.0,
                    dictionary_patience_left_seconds=170.0,
                ),
            )

        assert "waiting for the dictionary: next try in 20 s, WordNet within 2 min 50 s" in stream.getvalue()

    def test_every_stage_has_a_description(
        self,
    ) -> None:
        assert set(
            STAGE_DESCRIPTIONS,
        ) == set(
            Stage,
        )


@final
class TestTimesCountedFromNow:
    def test_a_wait_counts_down_between_two_reports_and_says_now_once_its_moment_passed(
        self,
    ) -> None:
        stream = StringIO()

        clock = _SteppedClock()

        drawn: list[str] = []

        with TerminalProgress(
            console=_terminal(
                stream,
            ),
            clock=clock,
        ) as progress:
            progress.stage_started(
                stage=Stage.DICTIONARY_ENRICHMENT,
                steps=40,
            )

            progress.dictionary_waiting(
                retry_in_seconds=30.0,
                patience_left_seconds=45.0,
            )

            for second in (
                12,
                31,
            ):
                clock.now = EPOCH + timedelta(
                    seconds=second,
                )

                before = stream.getvalue()

                progress.line.refresh()

                drawn.append(
                    stream.getvalue().removeprefix(
                        before,
                    ),
                )

        (
            counting,
            passed,
        ) = drawn

        assert "waiting for the dictionary: next try in 18 s, WordNet within 33 s" in counting

        assert "waiting for the dictionary: next try now, WordNet within 14 s" in passed
