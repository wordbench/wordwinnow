"""
A dictionary stage recorded as a view of its mechanics would read it, script
by script, on virtual time.

Each named script stages what it is named for, and the recording of every
script agrees with the product that ran it, repeats exactly, and ends as the
watch ends it.
"""

from collections import (
    Counter,
)
from dataclasses import (
    replace,
)
from itertools import (
    pairwise,
)
from random import (
    getstate,
    seed,
    setstate,
)
from types import (
    MappingProxyType,
)
from typing import (
    Final,
    final,
)

from pytest import (
    mark,
    raises,
)

from tests.fakes.dictionary_stage import (
    ANSWERING,
    ANSWERING_SLOWLY,
    DOWN_AND_BACK,
    DOWN_LONGER_THAN_PATIENCE,
    MEASURED_LIMIT,
    NEVER_ANSWERING,
    REFUSING,
    SOME_REQUESTS_FAILING,
    Answering,
    Chapter,
    DictionaryWaiting,
    Down,
    Flaky,
    LookedUp,
    Received,
    Recording,
    Script,
    Settings,
    StageStarted,
    Watched,
    Word,
    Written,
    record,
)
from wordwinnow.domain.analysis import (
    Stage,
)
from wordwinnow.domain.dictionary import (
    LookupOutcome,
)
from wordwinnow.domain.language import (
    PartOfSpeech,
)
from wordwinnow.infrastructure.dictionary.free_dictionary import (
    RequestOutcome,
    WaitCause,
)
from wordwinnow.infrastructure.dictionary.resilience import (
    BreakerState,
)
from wordwinnow.services.progress import (
    ACTIVITY_INTERVAL_SECONDS,
)

_SCRIPTS: Final = MappingProxyType(
    mapping={
        "answering": ANSWERING,
        "some_requests_failing": SOME_REQUESTS_FAILING,
        "refusing": REFUSING,
        "down_and_back": DOWN_AND_BACK,
        "down_longer_than_patience": DOWN_LONGER_THAN_PATIENCE,
        "never_answering": NEVER_ANSWERING,
        "answering_slowly": ANSWERING_SLOWLY,
        "measured_limit": MEASURED_LIMIT,
    },
)

# NOTE:
# One lemma under two parts of speech, as an analysis can hold it, between two words of its own.
_SHARED_LEMMA: Final = Script(
    chapters=(
        Chapter(
            at=0.0,
            behavior=Answering(),
        ),
    ),
    words=(
        Word(
            lemma="photograph",
            part_of_speech=PartOfSpeech.NOUN,
            found=True,
        ),
        Word(
            lemma="smoke",
            part_of_speech=PartOfSpeech.NOUN,
            found=True,
        ),
        Word(
            lemma="smoke",
            part_of_speech=PartOfSpeech.VERB,
            found=True,
        ),
        Word(
            lemma="mask",
            part_of_speech=PartOfSpeech.NOUN,
            found=True,
        ),
    ),
)

_NAMES: Final = tuple(
    _SCRIPTS,
)

# NOTE:
# Two words that share a lemma begin to sleep at different times, the earlier first: the provider is down until the
# 40th second, and the stage asks one lookup at a time.
_STAGGERED: Final = Script(
    chapters=(
        Chapter(
            at=0.0,
            behavior=Down(),
        ),
        Chapter(
            at=40.0,
            behavior=Answering(),
        ),
    ),
    words=(
        Word(
            lemma="smoke",
            part_of_speech=PartOfSpeech.NOUN,
            found=True,
        ),
        Word(
            lemma="mask",
            part_of_speech=PartOfSpeech.NOUN,
            found=True,
        ),
        Word(
            lemma="smoke",
            part_of_speech=PartOfSpeech.VERB,
            found=True,
        ),
    ),
    settings=Settings(
        concurrency=1,
        patience_seconds=120.0,
    ),
)

# NOTE:
# The scripts whose recording the global generator's seed changes, by the breaker's recovery period or a retry's
# backoff; the others draw nothing from it that changes their recording, since the refusing script and the measured
# limit draw once, for a refusal whose named wait the client keeps, so none of them can tell a recording that is
# seeded from one that is not.
_JITTERED: Final = (
    "some_requests_failing",
    "down_and_back",
    "down_longer_than_patience",
    "never_answering",
)

# NOTE:
# The scripts in which a word sleeps more than once, so its sleeps can be told to overlap or not.
_SLEEPING: Final = (
    "down_longer_than_patience",
    "never_answering",
)

# NOTE:
# The scripts whose stage ends, and so hands back the endings that the stage's own reports are compared with; the one
# that never answers is canceled at its limit, which its own test covers.
_ENDED: Final = tuple(name for name in _NAMES if name != "never_answering")

# NOTE:
# The statuses by which the provider answers a word: found, and not found.
_ANSWERS: Final = frozenset(
    (
        200,
        404,
    ),
)

# NOTE:
# How long requests wait for the provider, with none failed, for the stage to count as one whose provider never
# answers.
_WAITING_SECONDS: Final = 5.0

# NOTE:
# How long a request waits for the provider before the slow script's wait counts as slow.
_SLOW_SECONDS: Final = 4.0

# NOTE:
# A recording is made once for each script and read by every test that needs it; the tests that check a recording
# repeats make their own.
_RECORDINGS: Final[dict[Script, Recording]] = {}


def _recorded(
    script: Script,
    /,
) -> Recording:
    if script not in _RECORDINGS:
        _RECORDINGS[script] = record(
            script=script,
        )

    return _RECORDINGS[script]


def _reads(
    recording: Recording,
    /,
) -> tuple[Watched, ...]:
    return tuple(
        entry
        for entry in recording.timeline
        if isinstance(
            entry,
            Watched,
        )
    )


def _requests(
    recording: Recording,
    /,
) -> tuple[Received, ...]:
    by_number = {request.number: request for truth in recording.words for request in truth.requests}

    return tuple(
        by_number[number]
        for number in sorted(
            by_number,
        )
    )


def _circuits(
    recording: Recording,
    /,
) -> tuple[BreakerState, ...]:
    return tuple(
        read.activity.provider.circuit
        for read in _reads(
            recording,
        )
    )


def _outcomes(
    recording: Recording,
    /,
) -> tuple[LookupOutcome | None, ...]:
    return tuple(None if truth.ending is None else truth.ending.outcome for truth in recording.words)


def _expected_outcomes(
    script: Script,
    /,
) -> tuple[LookupOutcome, ...]:
    return tuple(LookupOutcome.FOUND if word.found else LookupOutcome.NOT_FOUND for word in script.words)


def _failed(
    read: Watched,
    /,
) -> bool:
    provider = read.activity.provider

    return bool(
        provider.consecutive_failures,
    ) or any(request.outcome is RequestOutcome.FAILED for request in provider.recent)


def _waiting_for_the_provider(
    read: Watched,
    /,
) -> bool:
    return any(lookup.waiting_for is WaitCause.PROVIDER for lookup in read.activity.provider.lookups)


def _canonical(
    recording: Recording,
    /,
) -> Recording:
    """
    The recording with the words each `dictionary_waiting` holds in one order,
    which the product builds from a set and does not fix.
    """

    return replace(
        recording,
        timeline=tuple(
            (
                replace(
                    entry,
                    held=tuple(
                        sorted(
                            entry.held,
                            key=repr,
                        ),
                    ),
                )
                if isinstance(
                    entry,
                    DictionaryWaiting,
                )
                else entry
            )
            for entry in recording.timeline
        ),
    )


@final
class TestTheNamedScripts:
    def test_the_answering_script_ends_every_word_as_the_script_says_with_the_circuit_closed(
        self,
    ) -> None:
        recording = _recorded(
            ANSWERING,
        )

        assert _outcomes(
            recording,
        ) == _expected_outcomes(
            ANSWERING,
        )

        assert _reads(
            recording,
        )[-1].activity.counts.cached == sum(word.cached for word in ANSWERING.words)

        assert set(
            _circuits(
                recording,
            ),
        ) == {
            BreakerState.CLOSED,
        }

        assert not any(
            read.activity.provider.cooldown_seconds is not None
            for read in _reads(
                recording,
            )
        )

    def test_the_failing_script_counts_failures_toward_opening_and_the_circuit_never_opens(
        self,
    ) -> None:
        recording = _recorded(
            SOME_REQUESTS_FAILING,
        )

        assert any(
            read.activity.provider.consecutive_failures
            for read in _reads(
                recording,
            )
        )

        assert BreakerState.OPEN not in _circuits(
            recording,
        )

    def test_the_refusing_script_pauses_the_client_at_the_providers_request(
        self,
    ) -> None:
        assert any(
            read.activity.provider.cooldown_seconds is not None
            for read in _reads(
                _recorded(
                    REFUSING,
                ),
            )
        )

    def test_the_script_of_an_outage_that_ends_opens_the_circuit_and_a_trial_closes_it_again(
        self,
    ) -> None:
        recording = _recorded(
            DOWN_AND_BACK,
        )

        circuits = _circuits(
            recording,
        )

        assert BreakerState.OPEN in circuits

        after_opening = _reads(
            recording,
        )[
            circuits.index(
                BreakerState.OPEN,
            ) :
        ]

        closed_at = next(
            (
                position
                for (
                    position,
                    read,
                ) in enumerate(
                    iterable=after_opening,
                )
                if read.activity.provider.circuit is BreakerState.CLOSED
            ),
            None,
        )

        assert closed_at is not None

        assert any(lookup.trial for read in after_opening[:closed_at] for lookup in read.activity.provider.lookups)

    def test_the_script_of_an_outage_past_the_patience_lets_a_word_go_before_the_provider_answers_again(
        self,
    ) -> None:
        recording = _recorded(
            DOWN_LONGER_THAN_PATIENCE,
        )

        spent = tuple(
            entry
            for entry in recording.timeline
            if isinstance(
                entry,
                DictionaryWaiting,
            )
            and entry.ending
            and entry.patience_left_seconds == 0.0
        )

        assert spent

        assert spent[0].at < DOWN_LONGER_THAN_PATIENCE.chapters[-1].at

        assert LookupOutcome.UNAVAILABLE in _outcomes(
            recording,
        )

        assert not any(
            request.status in _ANSWERS and request.arrived >= spent[0].at
            for request in _requests(
                recording,
            )
        )

    def test_the_script_that_never_answers_leaves_requests_waiting_for_five_seconds_and_then_times_them_out(
        self,
    ) -> None:
        recording = _recorded(
            NEVER_ANSWERING,
        )

        reads = _reads(
            recording,
        )

        start = next(
            read.at
            for read in reads
            if _waiting_for_the_provider(
                read,
            )
        )

        window = tuple(read for read in reads if start <= read.at <= start + _WAITING_SECONDS)

        assert window[-1].at == start + _WAITING_SECONDS

        assert all(
            _waiting_for_the_provider(
                read,
            )
            and not _failed(
                read,
            )
            for read in window
        )

        assert any(
            request.status is None and request.answered is not None and request.answered > start + _WAITING_SECONDS
            for request in _requests(
                recording,
            )
        )

    def test_the_slow_script_leaves_a_request_waiting_for_four_seconds_with_none_failed(
        self,
    ) -> None:
        reads = _reads(
            _recorded(
                ANSWERING_SLOWLY,
            ),
        )

        assert any(
            lookup.waiting_for is WaitCause.PROVIDER and lookup.seconds >= _SLOW_SECONDS
            for read in reads
            for lookup in read.activity.provider.lookups
        )

        assert not any(
            _failed(
                read,
            )
            for read in reads
        )

    def test_the_script_of_the_measured_limit_is_refused_with_a_wait_and_every_word_is_answered_in_the_end(
        self,
    ) -> None:
        recording = _recorded(
            MEASURED_LIMIT,
        )

        assert any(
            request.status == 429 and request.retry_after is not None
            for request in _requests(
                recording,
            )
        )

        assert any(
            read.activity.provider.cooldown_seconds is not None
            for read in _reads(
                recording,
            )
        )

        assert _outcomes(
            recording,
        ) == _expected_outcomes(
            MEASURED_LIMIT,
        )


@final
class TestTheTruthAgreesWithTheProduct:
    @mark.parametrize(
        argnames="name",
        argvalues=_NAMES,
    )
    def test_each_request_is_one_the_provider_received_for_that_word(
        self,
        *,
        name: str,
    ) -> None:
        script = _SCRIPTS[name]

        recording = _recorded(
            script,
        )

        assert sorted(
            request.number
            for request in _requests(
                recording,
            )
        ) == list(
            range(
                1,
                recording.requests + 1,
            ),
        )

        for (
            word,
            truth,
        ) in zip(
            script.words,
            recording.words,
            strict=True,
        ):
            assert truth.lemma == word.lemma

            assert all(request.lemma == word.lemma.strip().lower() for request in truth.requests)

            if word.cached:
                assert not truth.requests

            assert (404 if word.found else 200) not in {request.status for request in truth.requests}

    @mark.parametrize(
        argnames="name",
        argvalues=_ENDED,
    )
    def test_each_ending_is_what_the_stage_reported_for_a_word_that_ended(
        self,
        *,
        name: str,
    ) -> None:
        recording = _recorded(
            _SCRIPTS[name],
        )

        looked_up = Counter(
            (
                entry.lookup.outcome,
                entry.lookup.failure_reason,
            )
            for entry in recording.timeline
            if isinstance(
                entry,
                LookedUp,
            )
        )

        endings = Counter(
            (
                truth.ending.outcome,
                truth.ending.failure_reason,
            )
            for truth in recording.words
            if truth.ending is not None
        )

        assert endings == looked_up

        assert sum(
            looked_up.values(),
        ) == sum(truth.ending is not None for truth in recording.words)

    def test_a_stage_still_running_at_the_limit_is_canceled_and_the_recording_says_it_did_not_end(
        self,
    ) -> None:
        recording = _recorded(
            NEVER_ANSWERING,
        )

        assert recording.stage_ended_at is None

        assert {truth.ending for truth in recording.words} == {
            None,
        }

        assert not any(
            isinstance(
                entry,
                LookedUp,
            )
            for entry in recording.timeline
        )

    def test_a_request_cut_off_when_the_stage_lets_its_word_go_has_no_answer_and_no_status(
        self,
    ) -> None:
        recording = _recorded(
            DOWN_LONGER_THAN_PATIENCE,
        )

        assert recording.stage_ended_at is not None

        assert any(
            request.answered is None and request.status is None
            for request in _requests(
                recording,
            )
        )

    @mark.parametrize(
        argnames="name",
        argvalues=_NAMES,
    )
    def test_the_stage_reports_once_that_it_started_with_a_step_for_each_word(
        self,
        *,
        name: str,
    ) -> None:
        script = _SCRIPTS[name]

        started = tuple(
            entry
            for entry in _recorded(
                script,
            ).timeline
            if isinstance(
                entry,
                StageStarted,
            )
        )

        assert started == (
            StageStarted(
                at=script.settings.starts_seconds,
                stage=Stage.DICTIONARY_ENRICHMENT,
                steps=len(
                    script.words,
                ),
            ),
        )

    @mark.parametrize(
        argnames="name",
        argvalues=_SLEEPING,
    )
    def test_the_sleeps_of_a_word_never_overlap(
        self,
        *,
        name: str,
    ) -> None:
        words = _recorded(
            _SCRIPTS[name],
        ).words

        assert any(truth.sleeps[1:] for truth in words)

        for truth in words:
            for (
                earlier,
                later,
            ) in pairwise(
                truth.sleeps,
            ):
                assert earlier.ended <= later.began

    def test_the_earlier_of_two_words_that_share_a_lemma_sleeps_first(
        self,
    ) -> None:
        (
            noun,
            _,
            verb,
        ) = _recorded(
            _STAGGERED,
        ).words

        assert noun.sleeps

        assert verb.sleeps

        assert noun.sleeps[0].began < verb.sleeps[0].began

    def test_each_sleep_the_stage_gave_a_word_lasted_as_long_as_it_was_given(
        self,
    ) -> None:
        sleeps = tuple(
            sleep
            for truth in _recorded(
                DOWN_AND_BACK,
            ).words
            for sleep in truth.sleeps
        )

        assert sleeps

        assert all(sleep.ended == sleep.began + sleep.seconds for sleep in sleeps)


@final
class TestALemmaUnderTwoPartsOfSpeech:
    def test_both_words_are_recorded_with_their_own_endings_and_the_requests_for_their_lemma(
        self,
    ) -> None:
        recording = _recorded(
            _SHARED_LEMMA,
        )

        (
            _,
            noun,
            verb,
            _,
        ) = recording.words

        assert noun.requests

        assert noun.requests == verb.requests

        assert {request.lemma for request in noun.requests} == {
            "smoke",
        }

        assert all(truth.ending is not None for truth in recording.words)

        assert (
            sum(
                isinstance(
                    entry,
                    LookedUp,
                )
                for entry in recording.timeline
            )
            == 4
        )

    def test_each_word_has_the_ending_the_stage_gave_its_own_part_of_speech(
        self,
    ) -> None:
        (
            _,
            noun,
            verb,
            _,
        ) = _recorded(
            _SHARED_LEMMA,
        ).words

        assert noun.ending is not None

        assert verb.ending is not None

        assert tuple(definition.part_of_speech_matched for definition in noun.ending.definitions) == (True,)

        assert tuple(definition.part_of_speech_matched for definition in verb.ending.definitions) == (False,)


@final
class TestTheRecordingRepeats:
    @mark.parametrize(
        argnames="name",
        argvalues=_NAMES,
    )
    def test_the_same_script_recorded_twice_gives_equal_recordings(
        self,
        *,
        name: str,
    ) -> None:
        script = _SCRIPTS[name]

        assert _canonical(
            record(
                script=script,
            ),
        ) == _canonical(
            record(
                script=script,
            ),
        )

    @mark.parametrize(
        argnames="name",
        argvalues=_JITTERED,
    )
    def test_the_recording_does_not_depend_on_where_the_global_random_generator_stood(
        self,
        *,
        name: str,
    ) -> None:
        script = _SCRIPTS[name]

        before = getstate()

        try:
            seed(
                a=1,
            )

            first = record(
                script=script,
            )

            seed(
                a=2,
            )

            second = record(
                script=script,
            )

        finally:
            setstate(
                state=before,
            )

        assert _canonical(
            first,
        ) == _canonical(
            second,
        )

    @mark.parametrize(
        argnames="name",
        argvalues=_NAMES,
    )
    def test_recording_leaves_the_global_random_generator_as_it_was(
        self,
        *,
        name: str,
    ) -> None:
        before = getstate()

        record(
            script=_SCRIPTS[name],
        )

        assert getstate() == before


@final
class TestTheScriptsRefusals:
    @mark.parametrize(
        argnames="second",
        argvalues=(
            "foo",
            " Foo",
        ),
    )
    def test_a_repeated_lemma_and_part_of_speech_is_refused_naming_both(
        self,
        *,
        second: str,
    ) -> None:
        with raises(
            expected_exception=ValueError,
            match=r"'(foo| Foo)' as a noun is repeated",
        ):
            Script(
                chapters=(
                    Chapter(
                        at=0.0,
                        behavior=Answering(),
                    ),
                ),
                words=(
                    Word(
                        lemma="foo",
                        part_of_speech=PartOfSpeech.NOUN,
                        found=True,
                    ),
                    Word(
                        lemma="bar",
                        part_of_speech=PartOfSpeech.NOUN,
                        found=True,
                    ),
                    Word(
                        lemma=second,
                        part_of_speech=PartOfSpeech.NOUN,
                        found=True,
                    ),
                ),
            )

    def test_words_that_share_a_lemma_must_agree_on_how_it_is_answered(
        self,
    ) -> None:
        with raises(
            expected_exception=ValueError,
            match="disagree",
        ):
            Script(
                chapters=(
                    Chapter(
                        at=0.0,
                        behavior=Answering(),
                    ),
                ),
                words=(
                    Word(
                        lemma="foo",
                        part_of_speech=PartOfSpeech.NOUN,
                        found=True,
                    ),
                    Word(
                        lemma="foo",
                        part_of_speech=PartOfSpeech.VERB,
                        found=False,
                    ),
                ),
            )

    def test_a_script_without_a_chapter_is_refused(
        self,
    ) -> None:
        with raises(
            expected_exception=ValueError,
            match="at least one chapter",
        ):
            Script(
                chapters=(),
                words=(),
            )

    @mark.parametrize(
        argnames="pattern",
        argvalues=(
            "",
            "FAX",
        ),
    )
    def test_a_failing_pattern_of_any_other_letter_is_refused(
        self,
        *,
        pattern: str,
    ) -> None:
        with raises(
            expected_exception=ValueError,
            match="letters F and A",
        ):
            Flaky(
                pattern=pattern,
            )

    @mark.parametrize(
        argnames="pattern",
        argvalues=(
            " ",
            "AFX",
        ),
    )
    def test_a_written_pattern_of_any_other_letter_is_refused(
        self,
        *,
        pattern: str,
    ) -> None:
        with raises(
            expected_exception=ValueError,
            match="letters A, F, and R",
        ):
            Written(
                pattern=pattern,
            )


@final
class TestTheReadsComeFromTheWatch:
    @mark.parametrize(
        argnames="name",
        argvalues=_NAMES,
    )
    def test_the_reads_are_a_quarter_second_apart_from_nought(
        self,
        *,
        name: str,
    ) -> None:
        reads = _reads(
            _recorded(
                _SCRIPTS[name],
            ),
        )

        assert tuple(read.at for read in reads) == tuple(
            position * ACTIVITY_INTERVAL_SECONDS
            for position in range(
                len(
                    reads,
                ),
            )
        )

    @mark.parametrize(
        argnames="name",
        argvalues=_NAMES,
    )
    def test_the_last_read_is_the_first_after_the_stage_ended_or_at_the_limit(
        self,
        *,
        name: str,
    ) -> None:
        script = _SCRIPTS[name]

        recording = _recorded(
            script,
        )

        reads = _reads(
            recording,
        )

        def ends(
            read: Watched,
            /,
        ) -> bool:
            return read.at >= script.settings.limit_seconds or (
                recording.stage_ended_at is not None
                and read.at >= recording.stage_ended_at + script.settings.after_seconds
            )

        assert ends(
            reads[-1],
        )

        assert not any(
            ends(
                read,
            )
            for read in reads[:-1]
        )
