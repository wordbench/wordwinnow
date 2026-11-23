"""
What the operator view's marks look like: the glyphs of its two forms, and the
styles of its four hues.

A hue means one thing each: green, the dictionary or WordNet delivered;
yellow, the provider makes the client wait; red, the provider failed and what
follows from it; magenta, the breaker is testing.

No hue is drawn on bold text, and none as a background.
"""

from collections.abc import (
    Mapping,
)
from dataclasses import (
    dataclass,
)
from enum import (
    Enum,
    auto,
)
from types import (
    MappingProxyType,
)
from typing import (
    Final,
    final,
)

from rich.style import (
    Style,
)

from wordwinnow.infrastructure.dictionary.resilience import (
    BreakerState,
)
from wordwinnow.services.cli.operator_states import (
    State,
)


class Mark(
    Enum,
):
    """
    What a mark on the lane or in a sentence says of a request or a word.
    """

    ANSWERED = auto()

    WORDNET = auto()

    REFUSED = auto()

    FAILED = auto()


class Role(
    Enum,
):
    """
    What a part of the drawing is, which fixes its style.
    """

    STRUCTURE = auto()

    LABEL = auto()

    TEXT = auto()

    BOLD = auto()

    NAME = auto()

    CLOSED = auto()

    HALF = auto()

    OPEN = auto()

    DEAD_OPEN = auto()

    DEAD_HALF = auto()

    PIP = auto()

    TOKEN = auto()

    FORMING = auto()

    PAUSE = auto()

    ASKING = auto()

    SLOW = auto()

    QUEUE = auto()

    BACKOFF = auto()

    BEHIND = auto()

    TRIAL = auto()

    HELD = auto()

    ANSWERED = auto()

    WORDNET = auto()

    REFUSED = auto()

    FAILED = auto()


# NOTE:
# Each hue is the terminal's own, named, so it follows the reader's theme; no blue and no cyan, which run into green
# in some themes.
#
# Under `NO_COLOR` Rich drops a color and keeps bold and dim, so nothing here depends on a hue to be read.
STYLES: Final = MappingProxyType(
    mapping={
        Role.STRUCTURE: Style(
            dim=True,
        ),
        Role.LABEL: Style(
            dim=True,
        ),
        Role.TEXT: Style(),
        Role.BOLD: Style(
            bold=True,
        ),
        Role.NAME: Style(
            bold=True,
        ),
        Role.CLOSED: Style(
            color="green",
        ),
        Role.HALF: Style(
            color="magenta",
        ),
        Role.OPEN: Style(
            color="red",
        ),
        Role.DEAD_OPEN: Style(
            color="red",
            dim=True,
        ),
        Role.DEAD_HALF: Style(
            color="magenta",
            dim=True,
        ),
        Role.PIP: Style(
            color="red",
        ),
        Role.TOKEN: Style(
            bold=True,
        ),
        Role.FORMING: Style(
            dim=True,
        ),
        Role.PAUSE: Style(
            color="yellow",
        ),
        Role.ASKING: Style(
            bold=True,
        ),
        Role.SLOW: Style(
            color="yellow",
        ),
        Role.QUEUE: Style(
            bold=True,
        ),
        Role.BACKOFF: Style(
            bold=True,
        ),
        Role.BEHIND: Style(
            dim=True,
        ),
        Role.TRIAL: Style(
            color="magenta",
        ),
        Role.HELD: Style(
            color="yellow",
        ),
        Role.ANSWERED: Style(
            color="green",
        ),
        Role.WORDNET: Style(
            color="green",
        ),
        Role.REFUSED: Style(
            color="yellow",
        ),
        Role.FAILED: Style(
            color="red",
        ),
    },
)

# NOTE:
# The title's border and badge take the state's hue; `idle` has none, and its badge is reverse alone.
HUES: Final = MappingProxyType(
    mapping={
        State.FAILING: Style(
            color="red",
        ),
        State.ON_TRIAL: Style(
            color="magenta",
        ),
        State.RATE_LIMITED: Style(
            color="yellow",
        ),
        State.IDLE: Style(),
        State.NO_ANSWER: Style(
            color="yellow",
        ),
        State.STRUGGLING: Style(
            color="yellow",
        ),
        State.SLOW: Style(
            color="yellow",
        ),
        State.ANSWERING: Style(
            color="green",
        ),
    },
)

# NOTE:
# What the title says of the dictionary: how the provider has been answering the client. The breaker's own words,
# closed, half-open, and open, stand only at the switch, because a dictionary is not open.
WORDS: Final = MappingProxyType(
    mapping={
        State.FAILING: "failing",
        State.ON_TRIAL: "on trial",
        State.RATE_LIMITED: "rate-limited",
        State.IDLE: "idle",
        State.NO_ANSWER: "no answer",
        State.STRUGGLING: "struggling",
        State.SLOW: "slow",
        State.ANSWERING: "answering",
    },
)


@dataclass(
    frozen=True,
    kw_only=True,
    slots=True,
)
@final
class Glyphs:
    """
    The characters of one form of the loop.
    """

    wire: str

    dead: Mapping[BreakerState, str]

    down_right: str

    up_left: str

    tee_left: str

    vertical: str

    switch: Mapping[BreakerState, str]

    bucket_wall: str

    bucket_floor: str

    bucket_corners: str

    token: str

    forming: str

    empty: str

    lid: str

    provider: str

    head: str

    back: str

    marks: Mapping[Mark, str]

    lane: Mapping[BreakerState, str]

    pip_off: str

    ellipsis: str


UNICODE: Final = Glyphs(
    wire="─",
    dead=MappingProxyType(
        mapping={
            BreakerState.OPEN: "╌",
            BreakerState.HALF_OPEN: "┄",
        },
    ),
    down_right="╮",
    up_left="╯",
    tee_left="┤",
    vertical="│",
    switch=MappingProxyType(
        mapping={
            BreakerState.CLOSED: "●━━━━━●",
            BreakerState.OPEN: "●\N{BOX DRAWINGS LIGHT DIAGONAL UPPER RIGHT TO LOWER LEFT}▔▔▔▔●",
            BreakerState.HALF_OPEN: "●┄┄┄┄┄●",
        },
    ),
    bucket_wall="╷",
    bucket_floor="─",
    bucket_corners="╰╯",
    token="●",
    forming="○",
    empty="·",
    lid="▁",
    provider="◎",
    head="●",
    back="◂",
    marks=MappingProxyType(
        mapping={
            Mark.ANSWERED: "✓",
            Mark.WORDNET: "W",
            Mark.REFUSED: "!",
            Mark.FAILED: "✗",
        },
    ),
    lane=MappingProxyType(
        mapping={
            BreakerState.CLOSED: "─",
            BreakerState.OPEN: "╌",
            BreakerState.HALF_OPEN: "┄",
        },
    ),
    pip_off="·",
    ellipsis="…",
)

ASCII: Final = Glyphs(
    wire="-",
    dead=MappingProxyType(
        mapping={
            BreakerState.OPEN: ".",
            BreakerState.HALF_OPEN: ":",
        },
    ),
    down_right="+",
    up_left="+",
    tee_left="+",
    vertical="|",
    switch=MappingProxyType(
        mapping={
            BreakerState.CLOSED: "o=====o",
            BreakerState.OPEN: "o/    o",
            BreakerState.HALF_OPEN: "o-:-:-o",
        },
    ),
    bucket_wall="|",
    bucket_floor="_",
    bucket_corners="\\/",
    token="o",
    forming=":",
    empty=".",
    lid="_",
    provider="@",
    head="o",
    back="<",
    marks=MappingProxyType(
        mapping={
            Mark.ANSWERED: "+",
            Mark.WORDNET: "W",
            Mark.REFUSED: "!",
            Mark.FAILED: "X",
        },
    ),
    lane=MappingProxyType(
        mapping={
            BreakerState.CLOSED: "-",
            BreakerState.OPEN: " ",
            BreakerState.HALF_OPEN: ":",
        },
    ),
    pip_off=".",
    ellipsis="~",
)
