"""
Rows of styled cells, the canvas the operator view draws its loop on.
"""

from typing import (
    Final,
    final,
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
    is dropped, so a row is never longer than its panel.
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
        """

        for character in text:
            if 0 <= x < self.width:
                self.rows[y][x] = (
                    character,
                    style,
                )

            x += 1

        return x

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

        start = right - len(
            text,
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
