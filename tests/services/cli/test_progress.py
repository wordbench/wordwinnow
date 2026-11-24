"""
The progress line the command line shows on standard error.
"""

from datetime import (
    datetime,
    timedelta,
)
from io import (
    BytesIO,
    StringIO,
    TextIOWrapper,
)
from re import (
    findall,
    split,
    sub,
)
from typing import (
    Final,
    final,
)

from pytest import (
    MonkeyPatch,
    mark,
)
from rich.color import (
    ColorSystem,
)
from rich.console import (
    Console,
)
from rich.progress_bar import (
    ProgressBar,
)

from tests.fakes.ports import (
    EPOCH,
)
from tests.fakes.progress import (
    held_words,
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


def _answer_every(
    progress: TerminalProgress,
    clock: _SteppedClock,
    /,
    *,
    seconds: float,
    count: int,
) -> None:
    """
    Answer `count` lookups, one each `seconds` on `clock`.
    """

    for _ in range(
        count,
    ):
        clock.now += timedelta(
            seconds=seconds,
        )

        progress.looked_up(
            lookup=_ANSWERED,
        )


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

    def test_a_phase_whose_steps_are_unknown_pulses_after_a_counted_one(
        self,
    ) -> None:
        stream = StringIO()

        with TerminalProgress(
            console=Console(
                color_system="truecolor",
                force_terminal=True,
                force_interactive=True,
                file=stream,
                width=100,
            ),
        ) as progress:
            progress.begin(
                description="downloading the NLTK packages that are missing",
                steps=4,
            )

            progress.advance()

            progress.begin(
                description="training the level model on 9,520 words",
                steps=None,
            )

            totals = tuple(task.total for task in progress.line.tasks)

            progress.line.refresh()

        assert totals == (None,)

        # NOTE:
        # Rich draws a bar with a total against it, empty at none done, in its one background color, and only a task
        # with no total as the moving pulse, whose cells shade from the bar's color into the background.
        drawn = tuple(
            line
            for line in split(
                pattern=r"[\r\n]",
                string=stream.getvalue(),
            )
            if "training the level model" in line
        )

        colors = frozenset(
            findall(
                pattern=r"\x1b\[38;2;[0-9;]+m(?=━)",
                string=drawn[-1],
            ),
        )

        assert (
            len(
                colors,
            )
            > 1
        )

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
                held,
                retry_in_seconds,
                patience_left_seconds,
            ) in (
                (
                    (
                        held_words(
                            reason=FailureReason.PROVIDER_ERROR,
                            turned_away=4,
                            turned_away_wake_in_seconds=30.0,
                            longest_wait_seconds=90.0,
                        ),
                    ),
                    30.0,
                    180.0,
                ),
                (
                    (),
                    None,
                    150.0,
                ),
            ):
                before = stream.getvalue()

                progress.dictionary_waiting(
                    held=held,
                    ending=False,
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

        assert "4 words wait to be asked again; next in 30 s  WordNet's meaning within 3 min" in waiting

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
        assert STAGE_DESCRIPTIONS.keys() == frozenset(
            Stage,
        )


@final
class TestFinishingEstimate:
    def test_a_steady_pace_gives_one_finishing_time(
        self,
    ) -> None:
        stream = StringIO()

        clock = _SteppedClock()

        with TerminalProgress(
            console=_terminal(
                stream,
            ),
            clock=clock,
        ) as progress:
            progress.stage_started(
                stage=Stage.DICTIONARY_ENRICHMENT,
                steps=1_000,
            )

            _answer_every(
                progress,
                clock,
                seconds=1.0,
                count=31,
            )

        # NOTE:
        # At one answer a second, the 969 words left take 969 seconds from the 31st answer, which comes at second 31,
        # and a time past ten minutes is said to the minute; a lookup redraws the line only when its state changes, so
        # the line is read as it was drawn last.
        assert "31 of 1,000, about 16 min left" in stream.getvalue()

    def test_a_pace_that_changed_within_the_minute_is_taken_over_the_whole_minute(
        self,
    ) -> None:
        stream = StringIO()

        clock = _SteppedClock()

        with TerminalProgress(
            console=_terminal(
                stream,
            ),
            clock=clock,
        ) as progress:
            progress.stage_started(
                stage=Stage.DICTIONARY_ENRICHMENT,
                steps=1_000,
            )

            _answer_every(
                progress,
                clock,
                seconds=0.25,
                count=80,
            )

            _answer_every(
                progress,
                clock,
                seconds=1.0,
                count=20,
            )

        # NOTE:
        # The window holds 80 answers in 20 s and 20 in the next 20 s: from the first answer, at a quarter second, to
        # the last, at second 40, 99 answers in 39.75 s, so the 900 words left take 361 s, said on a step of half a
        # minute.
        assert "100 of 1,000, about 6 min left" in stream.getvalue()

    def test_too_few_answers_give_no_estimate(
        self,
    ) -> None:
        stream = StringIO()

        clock = _SteppedClock()

        with TerminalProgress(
            console=_terminal(
                stream,
            ),
            clock=clock,
        ) as progress:
            progress.stage_started(
                stage=Stage.DICTIONARY_ENRICHMENT,
                steps=1_000,
            )

            _answer_every(
                progress,
                clock,
                seconds=5.0,
                count=9,
            )

        assert "left" not in stream.getvalue()

    def test_a_wait_withholds_the_estimate_and_the_minute_starts_again_after_it(
        self,
    ) -> None:
        stream = StringIO()

        clock = _SteppedClock()

        with TerminalProgress(
            console=_terminal(
                stream,
            ),
            clock=clock,
        ) as progress:
            progress.stage_started(
                stage=Stage.DICTIONARY_ENRICHMENT,
                steps=1_000,
            )

            _answer_every(
                progress,
                clock,
                seconds=1.0,
                count=30,
            )

            before = stream.getvalue()

            progress.dictionary_waiting(
                held=(
                    held_words(
                        reason=FailureReason.PROVIDER_ERROR,
                        turned_away=1,
                        turned_away_wake_in_seconds=30.0,
                    ),
                ),
                ending=False,
                retry_in_seconds=30.0,
                patience_left_seconds=180.0,
            )

            waiting = stream.getvalue().removeprefix(
                before,
            )

            clock.now += timedelta(
                seconds=30,
            )

            progress.dictionary_waiting(
                held=(),
                ending=False,
                retry_in_seconds=None,
                patience_left_seconds=150.0,
            )

            before = stream.getvalue()

            _answer_every(
                progress,
                clock,
                seconds=1.0,
                count=5,
            )

        resumed = stream.getvalue().removeprefix(
            before,
        )

        assert "30 of 1,000, 1 waiting" in waiting

        assert "left" not in waiting

        assert "35 of 1,000" in resumed

        assert "left" not in resumed

    def test_a_workers_reports_give_the_estimate_from_when_they_were_written(
        self,
    ) -> None:
        stream = StringIO()

        # NOTE:
        # The command reads the reports a second after the last of them was written.
        with TerminalProgress(
            console=_terminal(
                stream,
            ),
            clock=lambda: (
                EPOCH
                + timedelta(
                    seconds=31,
                )
            ),
        ) as progress:
            for second in range(
                0,
                31,
                5,
            ):
                progress.processing(
                    progress=ProcessingProgress(
                        stage=Stage.DICTIONARY_ENRICHMENT,
                        steps=1_400,
                        done=2 * second,
                        unavailable=0,
                        dictionary_paused=False,
                        reported_at=EPOCH
                        + timedelta(
                            seconds=second,
                        ),
                    ),
                )

        # NOTE:
        # Two answers a second: the 1,340 words left take 670 seconds from second 30, which is 669 from second 31.
        assert "60 of 1,400, about 11 min left" in stream.getvalue()

    def test_the_time_left_is_said_as_coarsely_as_the_estimate_errs(
        self,
    ) -> None:
        def drawn_with(
            left: int,
            /,
        ) -> str:
            stream = StringIO()

            clock = _SteppedClock()

            with TerminalProgress(
                console=_terminal(
                    stream,
                ),
                clock=clock,
            ) as progress:
                progress.stage_started(
                    stage=Stage.DICTIONARY_ENRICHMENT,
                    steps=31 + left,
                )

                _answer_every(
                    progress,
                    clock,
                    seconds=1.0,
                    count=31,
                )

                progress.line.refresh()

            return stream.getvalue()

        # NOTE:
        # Under ten seconds no figure is given, five-second steps under a minute, ten-second steps under two.
        assert "31 of 39, a few seconds left" in drawn_with(
            8,
        )

        assert "31 of 78, about 45 s left" in drawn_with(
            47,
        )

        assert "31 of 124, about 1 min 30 s left" in drawn_with(
            93,
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
                held=(
                    held_words(
                        reason=FailureReason.CIRCUIT_OPEN,
                        turned_away=3,
                        turned_away_wake_in_seconds=30.0,
                    ),
                ),
                ending=False,
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

        assert (
            "the dictionary is not answering; next check in 18 s  3 waiting, WordNet's meaning within 33 s"
            in counting
        )

        assert "the dictionary is not answering; next check now  3 waiting, WordNet's meaning within 14 s" in passed

    def test_a_stage_without_an_answer_for_a_while_says_so_in_place_of_the_estimate(
        self,
    ) -> None:
        stream = StringIO()

        clock = _SteppedClock()

        with TerminalProgress(
            console=_terminal(
                stream,
            ),
            clock=clock,
        ) as progress:
            progress.stage_started(
                stage=Stage.DICTIONARY_ENRICHMENT,
                steps=1_000,
            )

            _answer_every(
                progress,
                clock,
                seconds=1.0,
                count=31,
            )

            clock.now += timedelta(
                seconds=6,
            )

            before = stream.getvalue()

            progress.line.refresh()

            quiet = stream.getvalue().removeprefix(
                before,
            )

            before = stream.getvalue()

            _answer_every(
                progress,
                clock,
                seconds=1.0,
                count=1,
            )

            progress.line.refresh()

            answered = stream.getvalue().removeprefix(
                before,
            )

        # NOTE:
        # At one answer a second, three gaps are three seconds, below the five the line waits at the least.
        assert "31 of 1,000, no answer for 6 s" in quiet

        assert "left" not in quiet

        assert "32 of 1,000, " in answered

        assert "no answer" not in answered

        assert "left" in answered

    def test_a_workers_reports_that_bring_no_answer_say_how_long_it_has_been(
        self,
    ) -> None:
        stream = StringIO()

        clock = _SteppedClock()

        with TerminalProgress(
            console=_terminal(
                stream,
            ),
            clock=clock,
        ) as progress:
            for (
                second,
                done,
            ) in (
                (
                    0,
                    10,
                ),
                (
                    1,
                    12,
                ),
                (
                    4,
                    12,
                ),
                (
                    8,
                    12,
                ),
            ):
                clock.now = EPOCH + timedelta(
                    seconds=second,
                )

                progress.processing(
                    progress=ProcessingProgress(
                        stage=Stage.DICTIONARY_ENRICHMENT,
                        steps=156,
                        done=done,
                        unavailable=0,
                        dictionary_paused=False,
                        reported_at=clock.now,
                    ),
                )

        # NOTE:
        # The last answer came in the report of second 1, so the report of second 8 brings seven seconds without one.
        assert "12 of 156, no answer for 7 s" in stream.getvalue()


_CHROMATIC: Final = frozenset(
    (
        *range(
            31,
            37,
        ),
        *range(
            41,
            47,
        ),
        *range(
            91,
            97,
        ),
        *range(
            101,
            107,
        ),
    ),
)


def _line(
    *,
    hue: bool | None,
    steps: int | None,
    done: int,
) -> str:
    """
    What a terminal receives for the line after `done` of `steps` steps, in
    true color so that each of Rich's own styles has a sequence of its own.

    With no `hue` the line is built as it is built today, without the keyword.
    """

    stream = StringIO()

    console = Console(
        color_system="truecolor",
        force_terminal=True,
        file=stream,
        width=80,
        highlight=False,
    )

    progress = (
        TerminalProgress(
            console=console,
        )
        if hue is None
        else TerminalProgress(
            console=console,
            hue=hue,
        )
    )

    progress.begin(
        description="looking the words up in the dictionary",
        steps=steps,
    )

    for _ in range(
        done,
    ):
        progress.advance()

    console.print(
        progress.line,
    )

    return stream.getvalue()


def _colors(
    output: str,
    /,
) -> list[str]:
    """
    Each color the escape sequences in `output` set, a standard one by its
    code and a true color by its channels.
    """

    found: list[str] = []

    for sequence in findall(
        pattern=r"\x1b\[([0-9;]*)m",
        string=output,
    ):
        codes = tuple(
            int(
                code,
            )
            for code in sequence.split(
                sep=";",
            )
            if code
        )

        index = 0

        while index < len(
            codes,
        ):
            code = codes[index]

            if code in (
                38,
                48,
            ):
                if codes[index + 1] != 2:
                    raise ValueError(
                        f"a palette color this test does not read: {codes}",
                    )

                found.append(
                    "{};{}".format(
                        code,
                        ";".join(
                            str(
                                object=channel,
                            )
                            for channel in codes[index + 2 : index + 5]
                        ),
                    ),
                )

                index += 5

                continue

            if code in _CHROMATIC or 30 <= code <= 37 or 90 <= code <= 97 or 40 <= code <= 47 or 100 <= code <= 107:
                found.append(
                    str(
                        object=code,
                    ),
                )

            index += 1

    return found


def _is_hue(
    color: str,
    /,
) -> bool:
    """
    Whether `color`, as `_colors` names it, is a hue: a gray, which has none,
    and the terminal's own black and white, are not.
    """

    if ";" in color:
        (
            red,
            green,
            blue,
        ) = (
            int(
                channel,
            )
            for channel in color.split(
                sep=";",
            )[1:]
        )

        return not red == green == blue

    return int(
        color,
    ) % 10 in range(
        1,
        7,
    ) and int(
        color,
    ) not in (
        30,
        37,
        90,
        97,
    )


def _hues(
    output: str,
    /,
) -> tuple[str, ...]:
    return tuple(
        color
        for color in _colors(
            output,
        )
        if _is_hue(
            color,
        )
    )


def _sequence(
    style: str,
    /,
) -> str:
    """
    The escape sequence that starts Rich's own `style` in true color.
    """

    rendered = (
        Console(
            color_system="truecolor",
            force_terminal=True,
        )
        .get_style(
            name=style,
        )
        .render(
            text="x",
            color_system=ColorSystem.TRUECOLOR,
        )
    )

    return rendered[
        : rendered.index(
            "x",
        )
    ]


def _pulse_colors() -> set[str]:
    """
    Every color Rich's own pulse takes in the course of one sweep.
    """

    colors: set[str] = set()

    for step in range(
        80,
    ):
        stream = StringIO()

        Console(
            color_system="truecolor",
            force_terminal=True,
            file=stream,
            width=80,
        ).print(
            ProgressBar(
                total=None,
                width=24,
                pulse=True,
                animation_time=step * 0.05,
            ),
        )

        colors.update(
            _colors(
                stream.getvalue(),
            ),
        )

    return colors


@final
class TestTheLineWithoutHue:
    @mark.parametrize(
        argnames=(
            "steps",
            "done",
        ),
        argvalues=(
            (
                10,
                3,
            ),
            (
                10,
                10,
            ),
            (
                None,
                0,
            ),
        ),
    )
    def test_drawn_without_hue_the_spinner_the_bar_and_the_elapsed_time_carry_none(
        self,
        *,
        steps: int | None,
        done: int,
    ) -> None:
        assert not _hues(
            _line(
                hue=False,
                steps=steps,
                done=done,
            ),
        )

    def test_drawn_without_hue_the_bar_still_shows_what_is_done_and_what_is_left(
        self,
    ) -> None:
        assert "\x1b[2m" in _line(
            hue=False,
            steps=10,
            done=3,
        )

    def test_constructed_as_today_the_line_draws_them_in_richs_own_styles(
        self,
    ) -> None:
        counting = _line(
            hue=None,
            steps=10,
            done=3,
        )

        finished = _line(
            hue=None,
            steps=10,
            done=10,
        )

        for (
            drawn,
            style,
        ) in (
            (
                counting,
                "progress.spinner",
            ),
            (
                counting,
                "bar.complete",
            ),
            (
                counting,
                "progress.elapsed",
            ),
            (
                finished,
                "bar.finished",
            ),
        ):
            assert (
                _sequence(
                    style,
                )
                in drawn
            ), style

        pulsing = _colors(
            _line(
                hue=None,
                steps=None,
                done=0,
            ),
        )

        assert (
            frozenset(
                pulsing,
            )
            & _pulse_colors()
        )

        assert any(
            _is_hue(
                color,
            )
            for color in pulsing
        )


# TEST:
# The frames of Rich's default spinner and of its `line` spinner, one cell each, written out here and not read from
# Rich.
_BRAILLE: Final = (
    "\N{BRAILLE PATTERN DOTS-124}"
    "\N{BRAILLE PATTERN DOTS-145}"
    "\N{BRAILLE PATTERN DOTS-1456}"
    "\N{BRAILLE PATTERN DOTS-456}"
    "\N{BRAILLE PATTERN DOTS-3456}"
    "\N{BRAILLE PATTERN DOTS-356}"
    "\N{BRAILLE PATTERN DOTS-236}"
    "\N{BRAILLE PATTERN DOTS-1236}"
    "\N{BRAILLE PATTERN DOTS-123}"
    "\N{BRAILLE PATTERN DOTS-1234}"
)

_LINE_FRAMES: Final = "-\\|/"


def _first_cells(
    encoding: str,
    errors: str,
    /,
    *,
    hue: bool,
) -> tuple[tuple[str, ...], bytes]:
    """
    The first cell of each line a progress line writes on a terminal whose
    stream has `encoding` and `errors`, over several refreshes, and every byte
    it wrote.
    """

    stream = TextIOWrapper(
        buffer=BytesIO(),
        encoding=encoding,
        errors=errors,
    )

    with TerminalProgress(
        console=Console(
            force_terminal=True,
            force_interactive=True,
            file=stream,
            width=160,
        ),
        hue=hue,
    ) as progress:
        progress.begin(
            description="looking the words up in the dictionary",
            steps=100,
        )

        for _ in range(
            6,
        ):
            progress.advance()

            progress.line.refresh()

    stream.flush()

    assert isinstance(
        stream.buffer,
        BytesIO,
    )

    written = stream.buffer.getvalue()

    lines = tuple(
        line
        for line in split(
            pattern=r"[\r\n]",
            string=sub(
                pattern=r"\x1b\[[0-9;?]*[A-Za-z]",
                repl="",
                string=written.decode(
                    encoding=encoding,
                    errors="replace",
                ),
            ),
        )
        if "looking the words up" in line
    )

    return (
        tuple(line[0] for line in lines),
        written,
    )


@final
class TestTheLineOnATerminalThatIsNotUtf8:
    @mark.parametrize(
        argnames="hue",
        argvalues=(
            True,
            False,
        ),
    )
    @mark.parametrize(
        argnames="encoding",
        argvalues=(
            "ascii",
            "iso8859-1",
        ),
    )
    def test_it_spins_in_ascii_and_writes_nothing_that_a_stream_would_spell_out(
        self,
        *,
        encoding: str,
        hue: bool,
    ) -> None:
        (
            cells,
            written,
        ) = _first_cells(
            encoding,
            "backslashreplace",
            hue=hue,
        )

        assert (
            len(
                cells,
            )
            >= 6
        )

        assert frozenset(
            cells,
        ) <= frozenset(
            _LINE_FRAMES,
        ), cells

        assert b"\\u" not in written

        assert b"\\x" not in written

    @mark.parametrize(
        argnames="hue",
        argvalues=(
            True,
            False,
        ),
    )
    def test_it_draws_the_braille_frames_where_the_console_writes_utf_8_as_it_always_has(
        self,
        *,
        hue: bool,
    ) -> None:
        (
            cells,
            _,
        ) = _first_cells(
            "utf-8",
            "strict",
            hue=hue,
        )

        assert (
            len(
                cells,
            )
            >= 6
        )

        assert frozenset(
            cells,
        ) <= frozenset(
            _BRAILLE,
        ), cells
