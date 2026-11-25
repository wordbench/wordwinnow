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
    Mapping,
    Sequence,
)
from dataclasses import (
    dataclass,
)
from math import (
    ceil,
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
from rich.cells import (
    cell_len,
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
from wordwinnow.services.cli.operator_clock import (
    MINUTE,
    clock_second,
    within,
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
from wordwinnow.services.cli.operator_rows import (
    LINGER_SECONDS,
    OUT_SECONDS,
    WORD_ROWS,
    Ending,
    Followed,
    behind_the_trial,
    folded_away,
    in_line,
    places,
    queued,
)
from wordwinnow.services.cli.operator_seen import (
    Seen,
)
from wordwinnow.services.cli.operator_states import (
    SLOW_AFTER,
    State,
    asking,
    cause,
    closed_lately,
    duration,
    held_of,
    long_wait,
    minute_counts,
    next_clause,
    ordinal,
    pause_of,
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

# NOTE:
# What a lookup that has left says of how it ended, where it has its answer.
_ENDINGS: Final = MappingProxyType(
    mapping={
        Ending.FOUND: "found",
        Ending.NOT_FOUND: "not found",
        Ending.LET_GO: "let go",
    },
)

_PAST_ROLES: Final = MappingProxyType(
    mapping={
        Mark.ANSWERED: Role.PAST_ANSWERED,
        Mark.WORDNET: Role.PAST_WORDNET,
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

    again_at: int

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
        again_at=name + 1,
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

    places: Mapping[str, int]

    state: State

    at: _Geometry

    glyphs: Glyphs

    canvas: Canvas

    pause: float | None

    settled: bool


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
    """
    `text` cut to `room` cells with the ellipsis, where it is longer.
    """

    if room <= 0:
        return ""

    if (
        cell_len(
            text=text,
        )
        <= room
    ):
        return text

    kept = ""

    used = cell_len(
        text=ellipsis,
    )

    for character in text:
        width = cell_len(
            text=character,
        )

        if used + width > room:
            break

        kept += character

        used += width

    return kept + ellipsis


def _fit(
    room: int,
    /,
    *candidates: str,
) -> str:
    return next(
        (
            candidate
            for candidate in candidates
            if cell_len(
                text=candidate,
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

    if not frame.settled and queued(
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

    _out(
        frame,
        wire,
    )


def _out(
    frame: _Frame,
    y: int,
    /,
) -> None:
    """
    On the wire past the provider, the word that last left found, not found,
    or let go: bright for a second after the read it left at, dim until ten,
    then gone, and never in a settled frame.
    """

    seen = frame.seen

    out = seen.out

    at = frame.at

    room = at.right - at.provider_at - 3

    if frame.settled or out is None or out.left_at is None or room < 8:
        return

    if not within(
        since=out.left_at,
        now=seen.read_at,
        seconds=OUT_SECONDS,
    ):
        return

    fresh = within(
        since=out.left_at,
        now=seen.read_at,
        seconds=LINGER_SECONDS,
    )

    mark = Mark.WORDNET if out.ending is Ending.LET_GO else Mark.ANSWERED

    x = _put(
        frame,
        y,
        at.provider_at + 2,
        " ",
        Role.TEXT,
    )

    x = _put(
        frame,
        y,
        x,
        frame.glyphs.marks[mark],
        _MARK_ROLES[mark] if fresh else _PAST_ROLES[mark],
    )

    _put(
        frame,
        y,
        x,
        f" {
            _clip(
                out.lemma,
                room - 4,
                frame.glyphs.ellipsis,
            )
        } ",
        Role.NAME if fresh else Role.LABEL,
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
    shown: int,
    /,
) -> None:
    """
    For three seconds after words are let go, how many of them have no row of
    their own, and what stands in for them, and never in a settled frame.
    """

    if frame.settled:
        return

    words = (
        sum(
            count
            for (
                at,
                count,
            ) in frame.seen.let_go
            if frame.seen.read_at - at < NOTICE_SECONDS
        )
        - shown
    )

    if words <= 0:
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
    order the bucket serves them, and never in a settled frame.
    """

    if frame.settled:
        return

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

        need = cell_len(
            text=name,
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
    The words waiting for the trial's outcome in one row, before the switch,
    and never in a settled frame.
    """

    if frame.settled:
        return

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


def _line(
    frame: _Frame,
    y: int,
    head: int,
    /,
) -> None:
    """
    The word's line, from where lines start to its head.
    """

    _put(
        frame,
        y,
        frame.at.line_at,
        frame.glyphs.wire
        * max(
            head - frame.at.line_at,
            0,
        ),
        Role.STRUCTURE,
    )


def _shown(
    frame: _Frame,
    followed: Followed,
    /,
) -> tuple[RequestOutcome, ...]:
    """
    The marks of the lookup's requests that fit before the provider, the
    newest.
    """

    room = frame.at.provider_at - frame.at.bucket_at - 7

    return followed.marks[-room:] if room > 0 else ()


def _way(
    frame: _Frame,
    y: int,
    followed: Followed,
    head: int,
    /,
) -> None:
    """
    The requests of this lookup that failed or were refused, at the provider,
    where it happened, and the way the word went, dotted, from where its head
    is now.
    """

    marks = _shown(
        frame,
        followed,
    )

    if not marks:
        return

    start = frame.at.provider_at - len(
        marks,
    )

    if head < start - 1:
        _put(
            frame,
            y,
            head + 1,
            frame.glyphs.trace * (start - head - 1),
            Role.STRUCTURE,
        )

    for (
        offset,
        outcome,
    ) in enumerate(
        iterable=marks,
    ):
        mark = _MARKS[outcome]

        _put(
            frame,
            y,
            start + offset,
            frame.glyphs.marks[mark],
            _MARK_ROLES[mark],
        )


def _word_row(
    *,
    frame: _Frame,
    followed: Followed,
    caption: bool,
) -> None:
    """
    A lookup on a row of its own: its name, `again` where the view saw the
    word turned away before, its line as far as it has got, and what it waits
    for at its head.

    `caption` says whether a word behind the trial says so, which only the
    topmost of them does.
    """

    canvas = frame.canvas

    at = frame.at

    glyphs = frame.glyphs

    y = canvas.row()

    _edge(
        frame,
        y,
    )

    ending = followed.ending

    _put(
        frame,
        y,
        0,
        _clip(
            followed.lemma,
            at.name,
            glyphs.ellipsis,
        ),
        Role.NAME if ending is None else Role.LABEL,
    )

    if followed.earlier:
        _put(
            frame,
            y,
            at.again_at,
            "again",
            Role.LABEL,
        )

    if ending is not None:
        _ending(
            frame,
            y,
            followed,
            ending,
        )

        return

    lookup = followed.lookup

    match lookup.waiting_for:
        case WaitCause.RETRY:
            _backing_off(
                frame,
                y,
                followed,
            )

        case WaitCause.TRIAL:
            head = at.behind_at

            _line(
                frame,
                y,
                head,
            )

            _put(
                frame,
                y,
                head,
                glyphs.head,
                Role.BEHIND,
            )

            _way(
                frame,
                y,
                followed,
                head,
            )

            if caption:
                marks = _shown(
                    frame,
                    followed,
                )

                reach = (
                    at.provider_at
                    - len(
                        marks,
                    )
                    if marks
                    else at.right
                )

                _put(
                    frame,
                    y,
                    head + 2,
                    _fit(
                        reach - head - 3,
                        "behind the trial",
                        "waits",
                    ),
                    Role.BEHIND,
                )

        case WaitCause.BUDGET | WaitCause.COOLDOWN:
            place = frame.places[lookup.lemma]

            head = max(
                at.bucket_at - place,
                at.switch_at + _SWITCH_WIDTH + 1,
            )

            _line(
                frame,
                y,
                head,
            )

            _put(
                frame,
                y,
                head,
                str(
                    object=place,
                )
                if place <= 9
                else "+",
                Role.QUEUE,
            )

            _way(
                frame,
                y,
                followed,
                head,
            )

        case WaitCause.PROVIDER:
            _asking(
                frame,
                y,
                followed,
            )


def _backing_off(
    frame: _Frame,
    y: int,
    followed: Followed,
    /,
) -> None:
    """
    A word that waits out a backoff: its head `◂` just after its line starts,
    the way it went, and when it asks again.
    """

    at = frame.at

    lookup = followed.lookup

    head = at.backoff_at

    _line(
        frame,
        y,
        head,
    )

    _put(
        frame,
        y,
        head,
        frame.glyphs.back,
        Role.BACKOFF,
    )

    _way(
        frame,
        y,
        followed,
        head,
    )

    left = max(
        (lookup.delay_seconds or 0.0) - (lookup.seconds + frame.since),
        0.0,
    )

    caption_at = at.provider_at + 2

    # NOTE:
    # No head stands at the provider for a word backing off, so its caption starts a cell earlier than an asking
    # word's, and stops a cell short of the wire as that one does.
    _put(
        frame,
        y,
        caption_at - 1,
        _fit(
            at.right - caption_at,
            f"{
                _try(
                    lookup,
                )
            } in {left:.1f} s",
            f"in {left:.1f} s",
        ),
        Role.BACKOFF,
    )


def _asking(
    frame: _Frame,
    y: int,
    followed: Followed,
    /,
) -> None:
    """
    A word asking the provider: its line to the marks of its earlier requests,
    its head at the provider, and how long it has asked.
    """

    at = frame.at

    lookup = followed.lookup

    head = at.provider_at

    marks = _shown(
        frame,
        followed,
    )

    _line(
        frame,
        y,
        head
        - len(
            marks,
        ),
    )

    for (
        offset,
        outcome,
    ) in enumerate(
        iterable=marks,
        start=-len(
            marks,
        ),
    ):
        mark = _MARKS[outcome]

        _put(
            frame,
            y,
            head + offset,
            frame.glyphs.marks[mark],
            _MARK_ROLES[mark],
        )

    late = lookup.seconds >= SLOW_AFTER

    role = Role.TRIAL if lookup.trial else (Role.SLOW if late else Role.ASKING)

    _put(
        frame,
        y,
        head,
        frame.glyphs.head,
        role,
    )

    asked = f"{lookup.seconds + frame.since:.1f} s"

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


def _ending(
    frame: _Frame,
    y: int,
    followed: Followed,
    ending: Ending,
    /,
) -> None:
    """
    A lookup that has left, for a second: what the counts say of how it ended,
    on its own line, which runs into the right edge when the word has its
    answer, and stops short when it was turned away to be asked again.
    """

    at = frame.at

    glyphs = frame.glyphs

    if ending in (
        Ending.TURNED_AWAY,
        Ending.ENDED,
    ):
        _line(
            frame,
            y,
            at.backoff_at,
        )

        _put(
            frame,
            y,
            at.backoff_at,
            glyphs.back,
            Role.LABEL,
        )

        if ending is Ending.ENDED:
            _put(
                frame,
                y,
                at.backoff_at + 2,
                "ended",
                Role.LABEL,
            )

            return

        marks = _shown(
            frame,
            followed,
        )

        end = _put(
            frame,
            y,
            at.backoff_at + 2,
            _fit(
                at.provider_at
                - len(
                    marks,
                )
                - at.backoff_at
                - 4,
                "turned away",
            ),
            Role.HELD,
        )

        _way(
            frame,
            y,
            followed,
            end,
        )

        return

    mark = Mark.WORDNET if ending is Ending.LET_GO else Mark.ANSWERED

    text = _fit(
        at.right - at.line_at - 8,
        _ENDINGS[ending],
    )

    start = (
        at.right
        - 3
        - cell_len(
            text=text,
        )
        - 2
    )

    _line(
        frame,
        y,
        at.right,
    )

    _put(
        frame,
        y,
        at.right,
        glyphs.tee_left,
        Role.STRUCTURE,
    )

    _put(
        frame,
        y,
        start - 1,
        " ",
        Role.TEXT,
    )

    after = _put(
        frame,
        y,
        start,
        glyphs.marks[mark],
        _MARK_ROLES[mark],
    )

    _put(
        frame,
        y,
        after,
        f" {text} ",
        _MARK_ROLES[mark],
    )


def _body(
    frame: _Frame,
    rows: Sequence[Followed | None],
    fold: bool,
    /,
) -> None:
    """
    A row for each lookup that has one, a free row between them drawn as the
    panel's edge alone so that the rows beneath it do not move, and a row that
    counts the lookups at the client that have none, and nothing in a settled
    frame.
    """

    if frame.settled:
        return

    said = False

    for row in rows:
        if row is None:
            _edge(
                frame,
                frame.canvas.row(),
            )

            continue

        behind = behind_the_trial(
            followed=row,
        )

        _word_row(
            frame=frame,
            followed=row,
            caption=behind and not said,
        )

        said = said or behind

    shown = frozenset(row.number for row in rows if row is not None)

    _more(
        frame,
        tuple(
            each
            for each in frame.seen.followed
            if each.number not in shown
            and not (
                fold
                and folded_away(
                    followed=each,
                )
            )
        ),
    )


def _more(
    frame: _Frame,
    rest: Sequence[Followed],
    /,
) -> None:
    """
    One dim row that counts the lookups at the client that have no row: those
    in line by their places, those behind the trial, and those asking.
    """

    if not rest:
        return

    in_the_line = tuple(
        sorted(
            frame.places[each.lemma]
            for each in rest
            if in_line(
                followed=each,
            )
        ),
    )

    behind = sum(
        1
        for each in rest
        if behind_the_trial(
            followed=each,
        )
    )

    others = (
        len(
            rest,
        )
        - len(
            in_the_line,
        )
        - behind
    )

    parts: list[str] = []

    if in_the_line:
        first = ordinal(
            number=in_the_line[0],
        )

        last = ordinal(
            number=in_the_line[-1],
        )

        parts.append(
            f"{
                len(
                    in_the_line,
                )
            } more in line, {first} to {last}"
            if len(
                in_the_line,
            )
            > 1
            else f"1 more in line, {first}",
        )

    if behind:
        parts.append(
            f"{behind} more behind the trial",
        )

    if others:
        parts.append(
            f"{others} more asking",
        )

    y = frame.canvas.row()

    _edge(
        frame,
        y,
    )

    _put(
        frame,
        y,
        0,
        _clip(
            "and "
            + ", ".join(
                parts,
            ),
            frame.at.right - 2,
            frame.glyphs.ellipsis,
        ),
        Role.LABEL,
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
                settled=frame.settled,
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
    # on its own row; what is left for this row is the time-out of a request that hangs, which a settled frame does
    # not say, for it has no word to hang, and a breaker held open for longer than its own wait, which no part
    # explains.
    if (frame.state is State.NO_ANSWER and not frame.settled) or long_wait(
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
    settled: bool,
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
        places=places(
            provider=provider,
        ),
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
        settled=settled,
    )


def _loop(
    *,
    seen: Seen,
    now: float,
    inner: int,
    glyphs: Glyphs,
    settled: bool,
    folded: int | None,
) -> tuple[Text, ...]:
    """
    The loop's rows, for each lookup that has one of the rows the view kept:
    at the loop's own budget, or, with `folded` the budget of the rows the
    folded body keeps, the line at the bucket and the words behind the trial
    in one row each.

    A `settled` loop has no word in it.
    """

    frame = _frame(
        seen=seen,
        now=now,
        inner=inner,
        glyphs=glyphs,
        settled=settled,
    )

    rows = seen.rows.loop if folded is None else seen.rows.folded[folded]

    _header(
        frame,
    )

    _held_row(
        frame,
    )

    _notice(
        frame,
        sum(1 for row in rows if row is not None and row.ending is Ending.LET_GO),
    )

    if folded is not None:
        _queue_row(
            frame,
        )

        _behind_row(
            frame,
        )

    _body(
        frame,
        rows,
        folded is not None,
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


def _times_out(
    seen: Seen,
    /,
) -> bool:
    """
    Whether what is next is the time-out of the oldest request that hangs.
    """

    provider = seen.activity.provider

    if (
        state_of(
            seen=seen,
        )
        is not State.NO_ANSWER
    ):
        return False

    return (
        ceil(
            provider.timeout_seconds
            - max(
                lookup.seconds
                for lookup in asking(
                    provider=provider,
                )
            ),
        )
        > 0
    )


def _narrow(
    *,
    seen: Seen,
    now: float,
    inner: int,
    glyphs: Glyphs,
    settled: bool,
) -> tuple[Text, ...]:
    """
    The panel where the loop has no room: its state's sentence, what is next,
    the count, and the columns the loop needs.

    A `settled` panel does not say when a request that hangs times out.
    """

    frame = _frame(
        seen=seen,
        now=now,
        inner=inner,
        glyphs=glyphs,
        settled=settled,
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
                settled=settled,
            ),
            inner,
            glyphs.ellipsis,
        ),
        Role.TEXT,
    )

    # NOTE:
    # The time-out is what the clause names while a request hangs, and no word is left in a settled panel to hang;
    # where the clause names something else, such as the first word turned away asking again, it stays.
    due = (
        None
        if settled
        and _times_out(
            seen,
        )
        else next_clause(
            seen=seen,
            now=now,
            width=inner - 6,
        )
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
    settled: bool,
    room: int,
) -> tuple[Text, ...]:
    """
    The loop's rows, folded where the terminal leaves the panel less than
    `room` rows: first the line at the bucket and the words behind the trial
    into one row each, then fewer rows for the words asking or backing off,
    the oldest kept, and one row that counts the rest.

    A `settled` loop has no word to fold, so it is drawn whole.
    """

    lines = _loop(
        seen=seen,
        now=now,
        inner=inner,
        glyphs=glyphs,
        settled=settled,
        folded=None,
    )

    if settled:
        return lines

    if (
        len(
            lines,
        )
        + 2
        <= room
    ):
        return lines

    lines = _loop(
        seen=seen,
        now=now,
        inner=inner,
        glyphs=glyphs,
        settled=settled,
        folded=WORD_ROWS,
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

    own = sum(
        1
        for followed in seen.followed
        if not folded_away(
            followed=followed,
        )
    )

    # NOTE:
    # The words asking or backing off take `rows` rows now, the row that counts the rest included where there are more
    # than eight; `over` rows must go, and the row that counts the rest takes one of the rows left.
    rows = min(
        own,
        WORD_ROWS,
    ) + (1 if own > WORD_ROWS else 0)

    return _loop(
        seen=seen,
        now=now,
        inner=inner,
        glyphs=glyphs,
        settled=settled,
        folded=max(
            rows - over - 1,
            1,
        ),
    )


def loop_panel(
    *,
    seen: Seen,
    now: float,
    columns: int,
    rows: int,
    line_rows: int,
    ascii_only: bool,
    settled: bool = False,
) -> RenderableType:
    """
    The panel for the view as `seen` has it at `now`, in a terminal of
    `columns` by `rows`: the loop where a panel of 72 cells fits, and its
    narrow form where it does not.

    `rows` is what the terminal has altogether, and `line_rows` how many of
    them the learner's line above the panel takes as it is drawn now.

    A `settled` panel keeps the client's state and its history and draws none
    of the words: no row of a word, no notice of words let go, no last word
    out, and no time-out of a request that hangs.
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
            settled=settled,
        )

    else:
        room = rows - line_rows

        lines = _fold(
            seen=seen,
            now=now,
            inner=inner,
            glyphs=glyphs,
            settled=settled,
            room=room,
        )

    hue = HUES[state]

    # WARN:
    # Rich's `Panel` stylizes its title with the border's style underneath, so a bold `dictionary` takes the state's
    # hue unless its own style names the default color, and the guide's rule is that no hue is drawn on bold text.
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
