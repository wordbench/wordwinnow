"""
The panel the operator view draws under the learner's line: the dictionary
client as one loop, from one read of its activity and what the view has kept.

A word travels along one wire: through the breaker's switch, past the bucket
that hands out tokens, to the provider.

Under the wire stand the words at the client, the last minute, one sentence
that says why the state is what it is, and how the words ended.

A position changes only at a read, and a clock counts on between reads.
"""

from collections.abc import (
    Sequence,
)
from dataclasses import (
    dataclass,
)
from types import (
    MappingProxyType,
)
from typing import (
    Final,
    final,
)

from rich.box import (
    ROUNDED,
)
from rich.console import (
    Group,
    RenderableType,
)
from rich.panel import (
    Panel,
)
from rich.style import (
    Style,
)
from rich.text import (
    Text,
)

from wordwinnow.infrastructure.dictionary.free_dictionary import (
    LookupActivity,
    ProviderActivity,
    RequestOutcome,
    WaitCause,
)
from wordwinnow.infrastructure.dictionary.resilience import (
    BreakerState,
)
from wordwinnow.services.cli.operator_canvas import (
    Canvas,
)
from wordwinnow.services.cli.operator_look import (
    ASCII,
    HUES,
    STYLES,
    UNICODE,
    WORDS,
    Glyphs,
    Mark,
    Role,
)
from wordwinnow.services.cli.operator_seen import (
    MINUTE,
    Seen,
    clock_second,
)
from wordwinnow.services.cli.operator_states import (
    SLOW_AFTER,
    State,
    cause,
    closed_lately,
    duration,
    held_of,
    long_wait,
    minute_counts,
    next_clause,
    ordinal,
    pause_of,
    queued,
    state_of,
    tally,
    to_ask,
    trial_name,
)

# NOTE:
# The widest the panel grows: its longest row fits within it, and on a wider terminal the eye still reads each row
# from its label in one sweep.
PANEL_WIDEST: Final = 100

PANEL_LEAST: Final = 40

# NOTE:
# The least panel the loop is drawn in, a terminal of 73 columns; a narrower one keeps the title, the sentences, and
# the count.
LOOP_LEAST: Final = 72

# NOTE:
# The most words drawn a row each, the eight lookups an analysis has at once by default.
WORD_ROWS: Final = 8

# NOTE:
# How long a notice of words let go stays, in seconds.
NOTICE_SECONDS: Final = 3.0

_SWITCH_WIDTH: Final = 7

_ROLES: Final = MappingProxyType(
    mapping={
        BreakerState.CLOSED: Role.CLOSED,
        BreakerState.OPEN: Role.OPEN,
        BreakerState.HALF_OPEN: Role.HALF,
    },
)

_WORDS: Final = MappingProxyType(
    mapping={
        BreakerState.CLOSED: "closed",
        BreakerState.OPEN: "open",
        BreakerState.HALF_OPEN: "half-open",
    },
)

_MARKS: Final = MappingProxyType(
    mapping={
        RequestOutcome.ANSWERED: Mark.ANSWERED,
        RequestOutcome.REFUSED: Mark.REFUSED,
        RequestOutcome.FAILED: Mark.FAILED,
    },
)

_MARK_ROLES: Final = MappingProxyType(
    mapping={
        Mark.ANSWERED: Role.ANSWERED,
        Mark.WORDNET: Role.WORDNET,
        Mark.REFUSED: Role.REFUSED,
        Mark.FAILED: Role.FAILED,
    },
)

# NOTE:
# A second's mark is its most serious: a failure over a refusal, then WordNet's meaning standing in, then the
# dictionary's answer.
_MARK_RANK: Final = MappingProxyType(
    mapping={
        Mark.ANSWERED: 1,
        Mark.WORDNET: 2,
        Mark.REFUSED: 4,
        Mark.FAILED: 5,
    },
)

_CIRCUIT_RANK: Final = MappingProxyType(
    mapping={
        BreakerState.CLOSED: 0,
        BreakerState.HALF_OPEN: 1,
        BreakerState.OPEN: 2,
    },
)

_LANE_ROLES: Final = MappingProxyType(
    mapping={
        BreakerState.CLOSED: Role.STRUCTURE,
        BreakerState.HALF_OPEN: Role.DEAD_HALF,
        BreakerState.OPEN: Role.DEAD_OPEN,
    },
)

_TALLY_MARKS: Final = MappingProxyType(
    mapping={
        "found": Mark.ANSWERED,
        "let go": Mark.WORDNET,
    },
)


def panel_width(
    *,
    columns: int,
) -> int:
    """
    The panel's width in a terminal of `columns`, between 40 and 100.
    """

    # NOTE:
    # A column short of the terminal's edge, because a row that fills the last column makes some terminals wrap before
    # the line ends, and each redraw would then start a line too low; and no wider than a row is read in one sweep.
    return max(
        min(
            columns - 1,
            PANEL_WIDEST,
        ),
        PANEL_LEAST,
    )


@dataclass(
    frozen=True,
    kw_only=True,
    slots=True,
)
@final
class _Geometry:
    """
    The columns of the loop in a panel `inner` cells wide between its borders.
    """

    inner: int

    right: int

    name: int

    line_at: int

    backoff_at: int

    behind_at: int

    switch_at: int

    bucket_at: int

    provider_at: int


def _geometry(
    inner: int,
    /,
) -> _Geometry:
    right = inner - 1

    name = 11 if inner < 90 else 15

    line_at = name + 1 + 5 + 1

    # NOTE:
    # Room before the bucket for a line of eight, and before the provider for a caption, which a wider panel shares
    # out.
    extra = max(
        right - line_at - 56,
        0,
    )

    switch_at = line_at + 8 + extra // 6

    bucket_at = switch_at + _SWITCH_WIDTH + 11 + extra // 6

    return _Geometry(
        inner=inner,
        right=right,
        name=name,
        line_at=line_at,
        backoff_at=line_at + 1,
        behind_at=switch_at - 2,
        switch_at=switch_at,
        bucket_at=bucket_at,
        provider_at=bucket_at + 6 + 6 + extra // 3,
    )


@dataclass(
    frozen=True,
    kw_only=True,
    slots=True,
)
@final
class _Frame:
    """
    Everything a part of the drawing needs for one frame.
    """

    seen: Seen

    now: float

    since: float

    provider: ProviderActivity

    state: State

    at: _Geometry

    glyphs: Glyphs

    canvas: Canvas

    pause: float | None


def _put(
    frame: _Frame,
    y: int,
    x: int,
    text: str,
    role: Role,
    /,
) -> int:
    return frame.canvas.put(
        y=y,
        x=x,
        text=text,
        style=STYLES[role],
    )


def _edge(
    frame: _Frame,
    y: int,
    /,
) -> None:
    _put(
        frame,
        y,
        frame.at.right,
        frame.glyphs.vertical,
        Role.STRUCTURE,
    )


def _clip(
    text: str,
    room: int,
    ellipsis: str,
    /,
) -> str:
    if room <= 0:
        return ""

    return (
        text
        if len(
            text,
        )
        <= room
        else text[: room - 1] + ellipsis
    )


def _fit(
    room: int,
    /,
    *candidates: str,
) -> str:
    return next(
        (
            candidate
            for candidate in candidates
            if len(
                candidate,
            )
            <= room
        ),
        "",
    )


def _tokens(
    frame: _Frame,
    /,
) -> list[tuple[str, Role]]:
    """
    The bucket's cells: each whole token; then, while the bucket is refilling,
    the token that is forming; then nothing.
    """

    # NOTE:
    # The forming token is one steady sign and not a measure of how far it has formed, since in a busy run each token
    # is taken as it forms.
    provider = frame.provider

    whole = min(
        int(
            provider.tokens + 1e-9,
        ),
        provider.burst,
    )

    cells = [
        (
            frame.glyphs.token,
            Role.TOKEN,
        ),
    ] * whole

    if (
        whole < provider.burst
        and pause_of(
            provider=provider,
        )
        is None
    ):
        cells.append(
            (
                frame.glyphs.forming,
                Role.FORMING,
            ),
        )

    cells.extend(
        [
            (
                frame.glyphs.empty,
                Role.STRUCTURE,
            ),
        ]
        * (
            provider.burst
            - len(
                cells,
            )
        ),
    )

    return cells


def _header(
    frame: _Frame,
    /,
) -> None:
    canvas = frame.canvas

    at = frame.at

    glyphs = frame.glyphs

    provider = frame.provider

    circuit = provider.circuit

    names = canvas.row()

    upper = canvas.row()

    wire = canvas.row()

    after = 0

    asked = to_ask(
        seen=frame.seen,
    )

    if asked is not None:
        after = _put(
            frame,
            names,
            0,
            "to ask ",
            Role.LABEL,
        )

        after = _put(
            frame,
            names,
            after,
            str(
                object=asked,
            ),
            Role.BOLD,
        )

    said = "breaker " + _WORDS[circuit]

    label_at = max(
        at.switch_at
        + _SWITCH_WIDTH // 2
        - len(
            said,
        )
        // 2,
        after + 2,
    )

    label_at = min(
        label_at,
        at.bucket_at
        - len(
            said,
        )
        - 2,
    )

    x = _put(
        frame,
        names,
        label_at,
        "breaker ",
        Role.LABEL,
    )

    _put(
        frame,
        names,
        x,
        _WORDS[circuit],
        _ROLES[circuit],
    )

    middle = at.switch_at + _SWITCH_WIDTH // 2

    failed = provider.consecutive_failures or 0

    threshold = provider.failure_threshold

    busy_until = 0

    if circuit is BreakerState.CLOSED and failed and threshold:
        pips_at = middle - threshold // 2

        _put(
            frame,
            upper,
            pips_at,
            glyphs.marks[Mark.FAILED] * failed,
            Role.PIP,
        )

        _put(
            frame,
            upper,
            pips_at + failed,
            glyphs.pip_off * (threshold - failed),
            Role.STRUCTURE,
        )

        busy_until = pips_at + threshold

    if circuit is not BreakerState.CLOSED:
        trial = trial_name(
            provider=provider,
        )

        if circuit is BreakerState.OPEN:
            due = max(
                (provider.retry_in_seconds or 0.0) - frame.since,
                0.0,
            )

            if frame.pause is not None and frame.pause > due:
                text = f"{trial} after the pause"

            else:
                text = f"{trial} in {
                    duration(
                        seconds=due,
                    )
                }"

        elif any(lookup.trial for lookup in provider.lookups):
            text = f"{trial} decides"

        else:
            text = f"{trial} is next"

        start = max(
            min(
                middle
                - len(
                    text,
                )
                // 2,
                at.bucket_at
                - len(
                    text,
                )
                - 2,
            ),
            0,
        )

        busy_until = _put(
            frame,
            upper,
            start,
            text,
            _ROLES[circuit],
        )

    if queued(
        provider=provider,
    ):
        said_at = (
            at.bucket_at
            - 1
            - len(
                "in line",
            )
        )

        if said_at > busy_until:
            _put(
                frame,
                upper,
                said_at,
                "in line",
                Role.LABEL,
            )

    if (
        pause_of(
            provider=provider,
        )
        is None
    ):
        _put(
            frame,
            names,
            at.bucket_at,
            "bucket",
            Role.LABEL,
        )

    else:
        _put(
            frame,
            names,
            at.bucket_at,
            glyphs.lid * (provider.burst + 2),
            Role.PAUSE,
        )

    _put(
        frame,
        upper,
        at.bucket_at,
        glyphs.bucket_wall,
        Role.STRUCTURE,
    )

    for (
        offset,
        (
            character,
            role,
        ),
    ) in enumerate(
        iterable=_tokens(
            frame,
        ),
    ):
        _put(
            frame,
            upper,
            at.bucket_at + 1 + offset,
            character,
            role,
        )

    _put(
        frame,
        upper,
        at.bucket_at + 1 + provider.burst,
        glyphs.bucket_wall,
        Role.STRUCTURE,
    )

    beside = at.bucket_at + provider.burst + 3

    room = at.right - beside

    if frame.pause is None:
        _put(
            frame,
            upper,
            beside,
            _fit(
                room,
                f"{provider.requests_per_second:g} a second",
            ),
            Role.LABEL,
        )

    else:
        left = duration(
            seconds=frame.pause,
        )

        _put(
            frame,
            upper,
            beside,
            _fit(
                room,
                f"paused {left}",
                left,
            ),
            Role.PAUSE,
        )

    _put(
        frame,
        names,
        min(
            at.provider_at - 3,
            at.right - 8,
        ),
        "provider",
        Role.LABEL,
    )

    _put(
        frame,
        wire,
        0,
        glyphs.wire * at.switch_at,
        Role.STRUCTURE,
    )

    _put(
        frame,
        wire,
        at.switch_at,
        glyphs.switch[circuit],
        _ROLES[circuit],
    )

    past = at.switch_at + _SWITCH_WIDTH

    if circuit is BreakerState.CLOSED:
        _put(
            frame,
            wire,
            past,
            glyphs.wire * (at.right - past),
            Role.STRUCTURE,
        )

    else:
        _put(
            frame,
            wire,
            past,
            glyphs.dead[circuit] * (at.right - past),
            _LANE_ROLES[circuit],
        )

    floor = glyphs.bucket_corners[0] + glyphs.bucket_floor * provider.burst + glyphs.bucket_corners[1]

    _put(
        frame,
        wire,
        at.bucket_at,
        floor,
        Role.STRUCTURE,
    )

    _put(
        frame,
        wire,
        at.provider_at,
        glyphs.provider,
        Role.BOLD,
    )

    _put(
        frame,
        wire,
        at.right,
        glyphs.down_right,
        Role.STRUCTURE,
    )


def _held_row(
    frame: _Frame,
    /,
) -> None:
    """
    The words the analysis holds, which are not at the client: how many, and
    when the first asks again.
    """

    held = held_of(
        seen=frame.seen,
        now=frame.now,
    )

    if not held.count:
        return

    at = frame.at

    y = frame.canvas.row()

    _edge(
        frame,
        y,
    )

    after = _put(
        frame,
        y,
        0,
        "to ask again ",
        Role.LABEL,
    )

    after = _put(
        frame,
        y,
        after,
        str(
            object=held.count,
        ),
        Role.HELD,
    )

    when = (
        "as places free"
        if held.wake_in is None
        else f"next in {
            duration(
                seconds=held.wake_in,
            )
        }"
    )

    frame.canvas.put_right(
        y=y,
        right=at.right - 1,
        text=_fit(
            at.right - after - 3,
            when,
        ),
        style=STYLES[Role.LABEL],
    )


def _notice(
    frame: _Frame,
    /,
) -> None:
    """
    For three seconds after words are let go, how many, and what stands in for
    them.
    """

    words = sum(
        count
        for (
            at,
            count,
        ) in frame.seen.let_go
        if frame.seen.read_at - at < NOTICE_SECONDS
    )

    if not words:
        return

    at = frame.at

    text = f"{words} let go: {frame.glyphs.marks[Mark.WORDNET]} WordNet's meaning stands in where it has one"

    y = frame.canvas.row()

    _put(
        frame,
        y,
        0,
        frame.glyphs.wire * at.right,
        Role.STRUCTURE,
    )

    _put(
        frame,
        y,
        at.right,
        frame.glyphs.tee_left,
        Role.STRUCTURE,
    )

    _put(
        frame,
        y,
        2,
        " "
        + _clip(
            text,
            at.right - 6,
            frame.glyphs.ellipsis,
        )
        + " ",
        Role.WORDNET,
    )


def _queue_row(
    frame: _Frame,
    /,
) -> None:
    """
    The whole line at the bucket in one row: how many, and their names in the
    order the bucket serves them.
    """

    queue = tuple(
        lookup.lemma
        for lookup in queued(
            provider=frame.provider,
        )
    )

    if not queue:
        return

    at = frame.at

    y = frame.canvas.row()

    _edge(
        frame,
        y,
    )

    after = _put(
        frame,
        y,
        0,
        "in line ",
        Role.LABEL,
    )

    _put(
        frame,
        y,
        after,
        str(
            object=len(
                queue,
            ),
        ),
        Role.QUEUE,
    )

    _put(
        frame,
        y,
        at.line_at,
        frame.glyphs.wire
        * max(
            at.bucket_at - 1 - at.line_at,
            0,
        ),
        Role.STRUCTURE,
    )

    _put(
        frame,
        y,
        at.bucket_at - 1,
        "1",
        Role.QUEUE,
    )

    _list(
        frame,
        y,
        at.bucket_at + 1,
        queue,
        Role.NAME,
    )


def _list(
    frame: _Frame,
    y: int,
    start: int,
    names: Sequence[str],
    first: Role,
    /,
) -> None:
    """
    `names` from column `start`, comma between, as many as leave room for how
    many more there are.
    """

    x = start

    room = frame.at.right - 1

    for (
        index,
        name,
    ) in enumerate(
        iterable=names,
    ):
        left = (
            len(
                names,
            )
            - index
        )

        more = f"+{left}"

        need = len(
            name,
        ) + (2 if index else 0)

        if (
            x
            + need
            + (
                len(
                    more,
                )
                + 2
                if left > 1
                else 0
            )
            > room
        ):
            _put(
                frame,
                y,
                x + (2 if index else 0),
                more,
                Role.LABEL,
            )

            return

        if index:
            x = _put(
                frame,
                y,
                x,
                ", ",
                Role.LABEL,
            )

        x = _put(
            frame,
            y,
            x,
            name,
            first if index == 0 else Role.TEXT,
        )


def _behind_row(
    frame: _Frame,
    /,
) -> None:
    """
    The words waiting for the trial's outcome in one row, before the switch.
    """

    behind = tuple(lookup.lemma for lookup in frame.provider.lookups if lookup.waiting_for is WaitCause.TRIAL)

    if not behind:
        return

    at = frame.at

    y = frame.canvas.row()

    _edge(
        frame,
        y,
    )

    after = _put(
        frame,
        y,
        0,
        "behind ",
        Role.LABEL,
    )

    _put(
        frame,
        y,
        after,
        str(
            object=len(
                behind,
            ),
        ),
        Role.BOLD,
    )

    _put(
        frame,
        y,
        at.line_at,
        frame.glyphs.wire
        * max(
            at.behind_at - at.line_at,
            0,
        ),
        Role.STRUCTURE,
    )

    _put(
        frame,
        y,
        at.behind_at,
        frame.glyphs.head,
        Role.BEHIND,
    )

    x = _put(
        frame,
        y,
        at.behind_at + 2,
        "behind the trial: ",
        Role.BEHIND,
    )

    _list(
        frame,
        y,
        x,
        behind,
        Role.TEXT,
    )


def _try(
    lookup: LookupActivity,
    /,
) -> str:
    return f"{
        ordinal(
            number=lookup.attempt,
        )
    } try"


def _word_row(
    frame: _Frame,
    lookup: LookupActivity,
    /,
) -> None:
    """
    A word asking the provider or backing off, on a row of its own: its line
    as far as it has got, and what it waits for.
    """

    canvas = frame.canvas

    at = frame.at

    glyphs = frame.glyphs

    y = canvas.row()

    _edge(
        frame,
        y,
    )

    _put(
        frame,
        y,
        0,
        _clip(
            lookup.lemma,
            at.name,
            glyphs.ellipsis,
        ),
        Role.NAME,
    )

    seconds = lookup.seconds + frame.since

    caption_at = at.provider_at + 2

    if lookup.waiting_for is WaitCause.RETRY:
        head = at.backoff_at

        _put(
            frame,
            y,
            at.line_at,
            glyphs.wire
            * max(
                head - at.line_at,
                0,
            ),
            Role.STRUCTURE,
        )

        _put(
            frame,
            y,
            head,
            glyphs.back,
            Role.BACKOFF,
        )

        left = max(
            (lookup.delay_seconds or 0.0) - seconds,
            0.0,
        )

        # NOTE:
        # No head stands at the provider for a word backing off, so its caption starts a cell earlier than an asking
        # word's, and stops a cell short of the wire as that one does.
        text = _fit(
            at.right - caption_at,
            f"{
                _try(
                    lookup,
                )
            } in {left:.1f} s",
            f"in {left:.1f} s",
        )

        _put(
            frame,
            y,
            caption_at - 1,
            text,
            Role.BACKOFF,
        )

        return

    head = at.provider_at

    _put(
        frame,
        y,
        at.line_at,
        glyphs.wire
        * max(
            head - at.line_at,
            0,
        ),
        Role.STRUCTURE,
    )

    late = lookup.seconds >= SLOW_AFTER

    role = Role.TRIAL if lookup.trial else (Role.SLOW if late else Role.ASKING)

    _put(
        frame,
        y,
        head,
        glyphs.head,
        role,
    )

    asked = f"{seconds:.1f} s"

    again = (
        f", {
            _try(
                lookup,
            )
        }"
        if lookup.attempt > 1
        else ""
    )

    room = at.right - head - 3

    if lookup.trial:
        name = trial_name(
            provider=frame.provider,
        )

        text = _fit(
            room,
            f"{name}, {asked}",
            f"trial {asked}",
            "trial",
        )

    else:
        text = _fit(
            room,
            f"{asked}{again}",
            asked,
        )

    _put(
        frame,
        y,
        head + 2,
        text,
        role if (late or lookup.trial) else Role.TEXT,
    )


def _body(
    frame: _Frame,
    capacity: int,
    /,
) -> None:
    """
    The line at the bucket and the words behind the trial in a row each, a row
    for each word asking or backing off, at most `capacity`, and a row that
    counts the rest.
    """

    _queue_row(
        frame,
    )

    _behind_row(
        frame,
    )

    own = _own(
        frame.provider,
    )

    chosen = _chosen(
        own,
        capacity,
    )

    for lookup in chosen:
        _word_row(
            frame,
            lookup,
        )

    rest = len(
        own,
    ) - len(
        chosen,
    )

    if rest:
        y = frame.canvas.row()

        _edge(
            frame,
            y,
        )

        text = _clip(
            f"and {rest} more asking",
            frame.at.right - 2,
            frame.glyphs.ellipsis,
        )

        _put(
            frame,
            y,
            0,
            text,
            Role.LABEL,
        )


def _own(
    provider: ProviderActivity,
    /,
) -> tuple[LookupActivity, ...]:
    """
    The lookups that have a row: those asking the provider or backing off.
    """

    return tuple(
        lookup
        for lookup in provider.lookups
        if lookup.waiting_for
        in (
            WaitCause.PROVIDER,
            WaitCause.RETRY,
        )
    )


def _chosen(
    own: tuple[LookupActivity, ...],
    capacity: int,
    /,
) -> tuple[LookupActivity, ...]:
    """
    The lookups that get a row where there are more than `capacity`: the trial
    first, then the oldest, shown in the order the activity gives them.
    """

    if (
        len(
            own,
        )
        <= capacity
    ):
        return own

    trial = tuple(
        index
        for (
            index,
            lookup,
        ) in enumerate(
            iterable=own,
        )
        if lookup.trial
    )

    rest = tuple(
        index
        for (
            index,
            lookup,
        ) in enumerate(
            iterable=own,
        )
        if not lookup.trial
    )

    picked = frozenset(
        (trial + rest)[:capacity],
    )

    return tuple(
        lookup
        for (
            index,
            lookup,
        ) in enumerate(
            iterable=own,
        )
        if index in picked
    )


def _cells(
    frame: _Frame,
    span: int,
    /,
) -> tuple[tuple[Mark | None, BreakerState | None], ...]:
    """
    The last `span` seconds, the newest last, each a clock second: what came
    back in it, its most serious, or nothing; and the breaker's worst state in
    it, or nothing before the view began.

    A request is placed by the second it came back in, so a mark stays in its
    cell and the lane moves once a second as a whole.
    """

    seen = frame.seen

    marks: list[Mark | None] = [
        None,
    ] * span

    this_second = clock_second(
        moment=seen.read_at,
    )

    def slot_of(
        moment: float,
        /,
    ) -> int | None:
        slot = (
            span
            - 1
            - (
                this_second
                - clock_second(
                    moment=moment,
                )
            )
        )

        return slot if 0 <= slot < span else None

    def note(
        slot: int | None,
        mark: Mark,
        /,
    ) -> None:
        if slot is None:
            return

        current = marks[slot]

        if current is None or _MARK_RANK[mark] > _MARK_RANK[current]:
            marks[slot] = mark

    came_back: dict[int, int] = {}

    for request in frame.provider.recent:
        slot = slot_of(
            seen.read_at - request.seconds,
        )

        note(
            slot,
            _MARKS[request.outcome],
        )

        if slot is not None:
            came_back[slot] = (
                came_back.get(
                    slot,
                    0,
                )
                + 1
            )

    # NOTE:
    # Where more words were let go in a second than requests came back in it, the cell is theirs, so the second an
    # analysis lets seventy words go is not hidden behind the one trial that failed in it.
    let_go: dict[int, int] = {}

    for (
        moment,
        words,
    ) in seen.let_go:
        slot = slot_of(
            moment,
        )

        if slot is not None:
            let_go[slot] = (
                let_go.get(
                    slot,
                    0,
                )
                + words
            )

    for (
        slot,
        words,
    ) in let_go.items():
        if words > came_back.get(
            slot,
            0,
        ):
            marks[slot] = Mark.WORDNET

        else:
            note(
                slot,
                Mark.WORDNET,
            )

    circuits: list[BreakerState | None] = [
        None,
    ] * span

    for (
        second,
        state,
    ) in seen.circuits:
        slot = slot_of(
            float(
                second,
            ),
        )

        if slot is None:
            continue

        worst = circuits[slot]

        if worst is None or _CIRCUIT_RANK[state] > _CIRCUIT_RANK[worst]:
            circuits[slot] = state

    return tuple(
        zip(
            marks,
            circuits,
            strict=True,
        ),
    )


def _lane(
    frame: _Frame,
    /,
) -> None:
    at = frame.at

    glyphs = frame.glyphs

    y = frame.canvas.row()

    span = min(
        MINUTE,
        at.right - 2 - 12,
    )

    _put(
        frame,
        y,
        0,
        f"last {span} s",
        Role.LABEL,
    )

    cells = _cells(
        frame,
        span,
    )

    start = at.right - 2 - span

    for (
        offset,
        (
            mark,
            circuit,
        ),
    ) in enumerate(
        iterable=cells,
    ):
        if mark is not None:
            _put(
                frame,
                y,
                start + offset,
                glyphs.marks[mark],
                _MARK_ROLES[mark],
            )

        elif circuit is not None:
            _put(
                frame,
                y,
                start + offset,
                glyphs.lane[circuit],
                _LANE_ROLES[circuit],
            )

    word = " open "

    begun: int | None = None

    for offset in range(
        len(
            cells,
        )
        + 1,
    ):
        quiet_open = (
            offset
            < len(
                cells,
            )
            and cells[offset][0] is None
            and cells[offset][1] is BreakerState.OPEN
        )

        if quiet_open and begun is None:
            begun = offset

        if not quiet_open and begun is not None:
            length = offset - begun

            if (
                length
                >= len(
                    word,
                )
                + 2
            ):
                _put(
                    frame,
                    y,
                    start
                    + begun
                    + (
                        length
                        - len(
                            word,
                        )
                    )
                    // 2,
                    word,
                    Role.OPEN,
                )

            begun = None

    _put(
        frame,
        y,
        at.right - 2,
        glyphs.wire * 2,
        Role.STRUCTURE,
    )

    _put(
        frame,
        y,
        at.right,
        glyphs.up_left,
        Role.STRUCTURE,
    )


def _sentence(
    frame: _Frame,
    /,
) -> None:
    seen = frame.seen

    glyphs = frame.glyphs

    y = frame.canvas.row()

    if frame.state is State.ANSWERING and not closed_lately(
        seen=seen,
        now=frame.now,
    ):
        # NOTE:
        # A healthy minute, said with the marks the lane draws it in, so the panel carries its own legend.
        x = _put(
            frame,
            y,
            0,
            "answering: in the last minute",
            Role.TEXT,
        )

        for (
            outcome,
            count,
            word,
        ) in minute_counts(
            provider=frame.provider,
        ):
            mark = _MARKS[outcome]

            x = _put(
                frame,
                y,
                x,
                "  " + glyphs.marks[mark],
                _MARK_ROLES[mark],
            )

            x = _put(
                frame,
                y,
                x,
                f" {count} {word}",
                Role.TEXT,
            )

    else:
        text = _clip(
            cause(
                seen=seen,
                now=frame.now,
            ),
            frame.at.inner,
            glyphs.ellipsis,
        )

        _put(
            frame,
            y,
            0,
            text,
            Role.TEXT,
        )

    # NOTE:
    # The breaker's ordinary wait is at the switch, and the pause is at the bucket, and the first word to ask again is
    # on its own row; what is left for this row is the time-out of a request that hangs, and a breaker held open for
    # longer than its own wait, which no part explains.
    if frame.state is State.NO_ANSWER or long_wait(
        seen=seen,
        now=frame.now,
    ):
        due = next_clause(
            seen=seen,
            now=frame.now,
            width=frame.at.inner - 6,
        )

        if due:
            y = frame.canvas.row()

            _put(
                frame,
                y,
                0,
                "next",
                Role.LABEL,
            )

            _put(
                frame,
                y,
                6,
                _clip(
                    due,
                    frame.at.inner - 6,
                    glyphs.ellipsis,
                ),
                Role.TEXT,
            )


def _resolved(
    frame: _Frame,
    /,
) -> None:
    """
    How many words have their answer, and how they ended.

    A worker's analysis shows the count alone: the activity's counts are the
    service's, for every analysis it serves, and cannot say how this one's
    words ended.
    """

    seen = frame.seen

    canvas = frame.canvas

    y = canvas.row()

    after = _put(
        frame,
        y,
        0,
        "resolved ",
        Role.LABEL,
    )

    after = _put(
        frame,
        y,
        after,
        str(
            object=seen.done,
        ),
        Role.BOLD,
    )

    if seen.steps is None:
        return

    if seen.stored is not None:
        _put(
            frame,
            y,
            after,
            f" of {seen.steps}",
            Role.LABEL,
        )

        return

    after = _put(
        frame,
        y,
        after,
        f" of {seen.steps}:",
        Role.LABEL,
    )

    for (
        words,
        count,
    ) in tally(
        seen=seen,
    ):
        mark = _TALLY_MARKS.get(
            words,
        )

        need = (
            2
            + (2 if mark else 0)
            + len(
                str(
                    object=count,
                ),
            )
            + 1
            + len(
                words,
            )
        )

        if after + need > canvas.width:
            break

        after += 2

        if mark is not None:
            after = _put(
                frame,
                y,
                after,
                frame.glyphs.marks[mark] + " ",
                _MARK_ROLES[mark],
            )

        after = _put(
            frame,
            y,
            after,
            str(
                object=count,
            ),
            Role.BOLD,
        )

        after = _put(
            frame,
            y,
            after,
            f" {words}",
            Role.LABEL,
        )


def _frame(
    *,
    seen: Seen,
    now: float,
    inner: int,
    glyphs: Glyphs,
) -> _Frame:
    provider = seen.activity.provider

    since = max(
        now - seen.read_at,
        0.0,
    )

    raw = pause_of(
        provider=provider,
    )

    return _Frame(
        seen=seen,
        now=now,
        since=since,
        provider=provider,
        state=state_of(
            seen=seen,
        ),
        at=_geometry(
            inner,
        ),
        glyphs=glyphs,
        canvas=Canvas(
            width=inner,
        ),
        pause=(
            None
            if raw is None
            else max(
                raw - since,
                0.0,
            )
        ),
    )


def _loop(
    *,
    seen: Seen,
    now: float,
    inner: int,
    glyphs: Glyphs,
    capacity: int,
) -> tuple[Text, ...]:
    frame = _frame(
        seen=seen,
        now=now,
        inner=inner,
        glyphs=glyphs,
    )

    _header(
        frame,
    )

    _held_row(
        frame,
    )

    _notice(
        frame,
    )

    _body(
        frame,
        capacity,
    )

    _lane(
        frame,
    )

    _sentence(
        frame,
    )

    _resolved(
        frame,
    )

    return frame.canvas.lines()


def _narrow(
    *,
    seen: Seen,
    now: float,
    inner: int,
    glyphs: Glyphs,
) -> tuple[Text, ...]:
    """
    The panel where the loop has no room: its state's sentence, what is next,
    the count, and the columns the loop needs.
    """

    frame = _frame(
        seen=seen,
        now=now,
        inner=inner,
        glyphs=glyphs,
    )

    canvas = frame.canvas

    _put(
        frame,
        canvas.row(),
        0,
        _clip(
            cause(
                seen=seen,
                now=now,
            ),
            inner,
            glyphs.ellipsis,
        ),
        Role.TEXT,
    )

    due = next_clause(
        seen=seen,
        now=now,
        width=inner - 6,
    )

    if due:
        y = canvas.row()

        _put(
            frame,
            y,
            0,
            "next",
            Role.LABEL,
        )

        _put(
            frame,
            y,
            6,
            _clip(
                due,
                inner - 6,
                glyphs.ellipsis,
            ),
            Role.TEXT,
        )

    y = canvas.row()

    x = _put(
        frame,
        y,
        0,
        "resolved ",
        Role.LABEL,
    )

    x = _put(
        frame,
        y,
        x,
        str(
            object=seen.done,
        ),
        Role.BOLD,
    )

    if seen.steps is not None:
        _put(
            frame,
            y,
            x,
            f" of {seen.steps}",
            Role.LABEL,
        )

    text = _clip(
        f"the loop is drawn from {LOOP_LEAST + 1} columns",
        inner,
        glyphs.ellipsis,
    )

    _put(
        frame,
        canvas.row(),
        0,
        text,
        Role.LABEL,
    )

    return canvas.lines()


def _fold(
    *,
    seen: Seen,
    now: float,
    inner: int,
    glyphs: Glyphs,
    room: int,
) -> tuple[Text, ...]:
    """
    The loop's rows, with fewer rows for the words asking or backing off where
    the terminal leaves the panel less than `room` rows, and one row that
    counts the rest.
    """

    lines = _loop(
        seen=seen,
        now=now,
        inner=inner,
        glyphs=glyphs,
        capacity=WORD_ROWS,
    )

    over = (
        len(
            lines,
        )
        + 2
        - room
    )

    if over <= 0:
        return lines

    own = len(
        _own(
            seen.activity.provider,
        ),
    )

    shown = min(
        own,
        WORD_ROWS,
    )

    base = (
        len(
            lines,
        )
        - shown
        - (1 if own > WORD_ROWS else 0)
    )

    capacity = (
        own
        if base + own + 2 <= room
        else max(
            room - base - 2 - 1,
            1,
        )
    )

    return _loop(
        seen=seen,
        now=now,
        inner=inner,
        glyphs=glyphs,
        capacity=capacity,
    )


def loop_panel(
    *,
    seen: Seen,
    now: float,
    columns: int,
    rows: int,
    line_rows: int,
    ascii_only: bool,
) -> RenderableType:
    """
    The panel for the view as `seen` has it at `now`, in a terminal of
    `columns` by `rows`: the loop where a panel of 72 cells fits, and its
    narrow form where it does not.

    `rows` is what the terminal has altogether, and `line_rows` how many of
    them the learner's line above the panel takes as it is drawn now.
    """

    glyphs = ASCII if ascii_only else UNICODE

    width = panel_width(
        columns=columns,
    )

    inner = width - 4

    state = state_of(
        seen=seen,
    )

    if width < LOOP_LEAST:
        lines = _narrow(
            seen=seen,
            now=now,
            inner=inner,
            glyphs=glyphs,
        )

    else:
        room = rows - line_rows

        lines = _fold(
            seen=seen,
            now=now,
            inner=inner,
            glyphs=glyphs,
            room=room,
        )

    hue = HUES[state]

    # WARN:
    # Rich's `Panel` stylizes its title with the border's style underneath, so a bold `dictionary` takes the state's
    # hue unless its own style names the default color; the design forbids a hue on bold text.
    title = Text(
        no_wrap=True,
    )

    title.append(
        text="dictionary",
        style=Style(
            color="default",
            bold=True,
        ),
    )

    title.append(
        text=" ",
    )

    title.append(
        text=f" {WORDS[state].upper()} ",
        style=hue
        + Style(
            reverse=True,
        ),
    )

    return Panel(
        renderable=Group(
            *lines,
        ),
        box=ROUNDED,
        title=title,
        title_align="left",
        border_style=hue,
        width=width,
        padding=(
            0,
            1,
        ),
    )
