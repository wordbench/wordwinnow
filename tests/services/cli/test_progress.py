"""
The progress line the command line shows on standard error.
"""

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

    def test_every_stage_has_a_description(
        self,
    ) -> None:
        assert set(
            STAGE_DESCRIPTIONS,
        ) == set(
            Stage,
        )
