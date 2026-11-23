"""
Rows of styled cells, the canvas the operator view draws its loop on.
"""

from typing import (
    Final,
    final,
)

from rich.cells import (
    cell_len,
)
from rich.style import (
    Style,
)
from rich.text import (
    Text,
)


@final
class Canvas:
    """
    Rows of cells, each a character and a style, `width` cells wide.

    Every row is exactly `width` cells, and a character put beyond the width
    is dropped, so a row is never longer than its panel, whatever it holds.
    """

    def __init__(
        self,
        *,
        width: int,
    ) -> None:
        self.width: Final = width

        self.rows: Final[list[list[tuple[str, Style | None]]]] = []

    def row(
        self,
    ) -> int:
        """
        A new row of blank cells, and its index.
        """

        self.rows.append(
            [
                (
                    " ",
                    None,
                ),
            ]
            * self.width,
        )

        return (
            len(
                self.rows,
            )
            - 1
        )

    def put(
        self,
        *,
        y: int,
        x: int,
        text: str,
        style: Style | None = None,
    ) -> int:
        """
        Put `text` on row `y` from column `x`, and return the column after it.

        A character takes the cells a terminal gives it: a wide one takes two,
        and one that combines with the character before it takes none.
        """

        for character in text:
            width = cell_len(
                text=character,
            )

            if width == 0:
                if 0 < x <= self.width:
                    (
                        before,
                        before_style,
                    ) = self.rows[y][x - 1]

                    self.rows[y][x - 1] = (
                        before + character,
                        before_style,
                    )

                continue

            if x >= 0 and x + width <= self.width:
                self._clear(
                    y,
                    x,
                    width,
                )

                self.rows[y][x] = (
                    character,
                    style,
                )

                for beyond in range(
                    x + 1,
                    x + width,
                ):
                    self.rows[y][beyond] = (
                        "",
                        style,
                    )

            x += width

        return x

    def _clear(
        self,
        y: int,
        x: int,
        width: int,
        /,
    ) -> None:
        """
        Blank the half of a wide character that a character about to take
        `width` cells from `x` would leave behind.
        """

        cells = self.rows[y]

        if x > 0 and cells[x][0] == "":
            cells[x - 1] = (
                " ",
                None,
            )

        if x + width < self.width and cells[x + width][0] == "":
            cells[x + width] = (
                " ",
                None,
            )

    def put_right(
        self,
        *,
        y: int,
        right: int,
        text: str,
        style: Style | None = None,
    ) -> int:
        """
        Put `text` so that it ends just before column `right`, and return the
        column it starts at.
        """

        start = right - cell_len(
            text=text,
        )

        self.put(
            y=y,
            x=start,
            text=text,
            style=style,
        )

        return start

    def lines(
        self,
    ) -> tuple[Text, ...]:
        """
        The rows as text that neither wraps nor grows past its width.
        """

        out: list[Text] = []

        for cells in self.rows:
            line = Text(
                overflow="crop",
                no_wrap=True,
            )

            run = ""

            style: Style | None = None

            for (
                character,
                cell_style,
            ) in cells:
                if cell_style != style and run:
                    line.append(
                        text=run,
                        style=style,
                    )

                    run = ""

                style = cell_style

                run += character

            if run:
                line.append(
                    text=run,
                    style=style,
                )

            out.append(
                line,
            )

        return tuple(
            out,
        )
