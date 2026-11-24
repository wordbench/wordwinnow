"""
What the command line writes on a stream that is not UTF-8.
"""

from codecs import (
    lookup_error,
)
from collections.abc import (
    Sequence,
)
from io import (
    BytesIO,
    StringIO,
    TextIOWrapper,
)
from typing import (
    Final,
    final,
)

from pytest import (
    mark,
    raises,
)
from rich.cells import (
    cell_len,
)
from rich.console import (
    Console,
)
from rich.text import (
    Text,
)

from wordwinnow.services.cli.terminal_text import (
    CELLS_HANDLER,
    keep_cells_on,
    writes_utf8,
)

# TEST:
# The nearest characters, written out here and not taken from the product, so that a change to its table fails the
# test that reads them: each character, by name, and what it is written as where the stream cannot encode it.
_NEAREST: Final = (
    (
        "\N{LEFT SINGLE QUOTATION MARK}",
        "'",
    ),
    (
        "\N{RIGHT SINGLE QUOTATION MARK}",
        "'",
    ),
    (
        "\N{SINGLE LOW-9 QUOTATION MARK}",
        "'",
    ),
    (
        "\N{SINGLE HIGH-REVERSED-9 QUOTATION MARK}",
        "'",
    ),
    (
        "\N{PRIME}",
        "'",
    ),
    (
        "\N{LEFT DOUBLE QUOTATION MARK}",
        '"',
    ),
    (
        "\N{RIGHT DOUBLE QUOTATION MARK}",
        '"',
    ),
    (
        "\N{DOUBLE LOW-9 QUOTATION MARK}",
        '"',
    ),
    (
        "\N{DOUBLE HIGH-REVERSED-9 QUOTATION MARK}",
        '"',
    ),
    (
        "\N{DOUBLE PRIME}",
        '"',
    ),
    (
        "\N{HYPHEN}",
        "-",
    ),
    (
        "\N{NON-BREAKING HYPHEN}",
        "-",
    ),
    (
        "\N{FIGURE DASH}",
        "-",
    ),
    (
        "\N{EN DASH}",
        "-",
    ),
    (
        "\N{EM DASH}",
        "-",
    ),
    (
        "\N{HORIZONTAL BAR}",
        "-",
    ),
    (
        "\N{MINUS SIGN}",
        "-",
    ),
    (
        "\N{HORIZONTAL ELLIPSIS}",
        ".",
    ),
    (
        "\N{NO-BREAK SPACE}",
        " ",
    ),
    (
        "\N{FIGURE SPACE}",
        " ",
    ),
    (
        "\N{NARROW NO-BREAK SPACE}",
        " ",
    ),
    (
        "\N{MULTIPLICATION SIGN}",
        "x",
    ),
)

# TEST:
# What Latin-1 has of the table, which a stream of that encoding writes as itself.
_LATIN_1: Final = frozenset(
    (
        "\N{NO-BREAK SPACE}",
        "\N{MULTIPLICATION SIGN}",
    ),
)

# TEST:
# A fullwidth letter, which takes two cells where its base letter takes one, and the sequences Rich lays out as one
# grapheme of two cells: what each is written as where the stream can encode none of its characters but the first
# ones, and the cells it takes whole.
_GRAPHEMES: Final = (
    (
        "\N{FULLWIDTH LATIN CAPITAL LETTER A}",
        "??",
    ),
    (
        "caf\N{FULLWIDTH LATIN SMALL LETTER E}",
        "caf??",
    ),
    (
        "\N{HEAVY BLACK HEART}\N{VARIATION SELECTOR-16}",
        "??",
    ),
    (
        "\N{MAN}\N{ZERO WIDTH JOINER}\N{WOMAN}\N{ZERO WIDTH JOINER}\N{GIRL}",
        "??",
    ),
    (
        "\N{WOMAN}\N{ZERO WIDTH JOINER}\N{PERSONAL COMPUTER}",
        "??",
    ),
    (
        "1\N{VARIATION SELECTOR-16}\N{COMBINING ENCLOSING KEYCAP}",
        "1?",
    ),
    (
        "\N{REGIONAL INDICATOR SYMBOL LETTER G}\N{REGIONAL INDICATOR SYMBOL LETTER B}",
        "??",
    ),
    (
        "\N{THUMBS UP SIGN}\N{EMOJI MODIFIER FITZPATRICK TYPE-4}",
        "??",
    ),
)

# TEST:
# What stands after a character the stream cannot encode, and Rich's graphemes join to it: an escape, a tab, a
# carriage return, written out with what each text is written as under `ascii` and under `iso8859-1`, which has `é`.
_BEFORE_A_CONTROL: Final = (
    (
        "\x1b[1mcaf\N{LATIN SMALL LETTER E WITH ACUTE}\x1b[0m done",
        "\x1b[1mcafe\x1b[0m done",
        "\x1b[1mcaf\N{LATIN SMALL LETTER E WITH ACUTE}\x1b[0m done",
    ),
    (
        "\x1b[2m\N{LEFT DOUBLE QUOTATION MARK}quoted\N{RIGHT DOUBLE QUOTATION MARK}\x1b[0m",
        '\x1b[2m"quoted"\x1b[0m',
        '\x1b[2m"quoted"\x1b[0m',
    ),
    (
        "\x1b[2mfoo\N{EM DASH}\x1b[0m",
        "\x1b[2mfoo-\x1b[0m",
        "\x1b[2mfoo-\x1b[0m",
    ),
    (
        "\x1b[2m\N{FULLWIDTH LATIN CAPITAL LETTER A}\x1b[0m",
        "\x1b[2m??\x1b[0m",
        "\x1b[2m??\x1b[0m",
    ),
    (
        "caf\N{LATIN SMALL LETTER E WITH ACUTE}\tnoun",
        "cafe\tnoun",
        "caf\N{LATIN SMALL LETTER E WITH ACUTE}\tnoun",
    ),
    (
        "caf\N{LATIN SMALL LETTER E WITH ACUTE}\rnext",
        "cafe\rnext",
        "caf\N{LATIN SMALL LETTER E WITH ACUTE}\rnext",
    ),
    (
        "\N{EM DASH}\tnext",
        "-\tnext",
        "-\tnext",
    ),
)

# TEST:
# A variation selector 16 after a character the stream can encode, which Rich draws two cells wide: the stream writes
# the character itself, so only the cells left for the selector are kept, and under `iso8859-1` the copyright and
# registered signs are the character's own and one `?`, where under `ascii` they are two.
_AFTER_AN_ENCODABLE: Final = (
    (
        "\N{COPYRIGHT SIGN}\N{VARIATION SELECTOR-16}",
        "??",
        "\N{COPYRIGHT SIGN}?",
    ),
    (
        "\N{REGISTERED SIGN}\N{VARIATION SELECTOR-16}",
        "??",
        "\N{REGISTERED SIGN}?",
    ),
    (
        "1\N{VARIATION SELECTOR-16}",
        "1?",
        "1?",
    ),
)

_ENCODINGS: Final = (
    "ascii",
    "iso8859-1",
)


def _stream(
    encoding: str,
    /,
    *,
    errors: str = "strict",
) -> TextIOWrapper:
    return TextIOWrapper(
        buffer=BytesIO(),
        encoding=encoding,
        errors=errors,
    )


def _written(
    text: str,
    encoding: str,
    /,
) -> str:
    """
    What a stream of `encoding` with the rule set holds after `text` is
    written to it, decoded again.
    """

    stream = _stream(
        encoding,
    )

    keep_cells_on(
        streams=(stream,),
    )

    stream.write(
        text,
    )

    stream.flush()

    assert isinstance(
        stream.buffer,
        BytesIO,
    )

    return stream.buffer.getvalue().decode(
        encoding=encoding,
    )


def _cases(
    pairs: Sequence[tuple[str, str]],
    /,
) -> tuple[tuple[str, str, str], ...]:
    """
    Each pair as a case for each encoding.
    """

    return tuple(
        (
            encoding,
            character,
            written,
        )
        for encoding in _ENCODINGS
        for (
            character,
            written,
        ) in pairs
    )


@final
class TestTheRule:
    @mark.parametrize(
        argnames=(
            "encoding",
            "character",
            "expected",
        ),
        argvalues=tuple(
            (
                encoding,
                character,
                character if encoding == "iso8859-1" and character in _LATIN_1 else written,
            )
            for (
                encoding,
                character,
                written,
            ) in _cases(
                _NEAREST,
            )
        ),
    )
    def test_each_character_of_the_table_is_written_as_the_nearest_one_the_stream_can_encode(
        self,
        *,
        encoding: str,
        character: str,
        expected: str,
    ) -> None:
        assert (
            _written(
                character,
                encoding,
            )
            == expected
        )

    @mark.parametrize(
        argnames=(
            "character",
            "expected",
        ),
        argvalues=(
            (
                "\N{LATIN SMALL LETTER E WITH ACUTE}",
                "e",
            ),
            (
                "\N{LATIN SMALL LETTER I WITH DIAERESIS}",
                "i",
            ),
            (
                "\N{LATIN SMALL LETTER N WITH TILDE}",
                "n",
            ),
            (
                "\N{LATIN CAPITAL LETTER C WITH CEDILLA}",
                "C",
            ),
        ),
    )
    def test_a_letter_with_a_mark_is_its_base_letter_where_the_stream_cannot_encode_it_and_itself_where_it_can(
        self,
        *,
        character: str,
        expected: str,
    ) -> None:
        assert (
            _written(
                character,
                "ascii",
            )
            == expected
        )

        assert (
            _written(
                character,
                "iso8859-1",
            )
            == character
        )

    @mark.parametrize(
        argnames=(
            "character",
            "ascii_form",
            "latin_1_form",
        ),
        argvalues=(
            (
                "\N{LATIN SMALL LETTER O WITH STROKE}",
                "?",
                "\N{LATIN SMALL LETTER O WITH STROKE}",
            ),
            (
                "\N{LATIN SMALL LETTER SHARP S}",
                "?",
                "\N{LATIN SMALL LETTER SHARP S}",
            ),
            (
                "\N{LATIN SMALL LIGATURE FI}",
                "?",
                "?",
            ),
            (
                "\N{NOT EQUAL TO}",
                "?",
                "?",
            ),
            (
                "\N{VULGAR FRACTION ONE HALF}",
                "?",
                "\N{VULGAR FRACTION ONE HALF}",
            ),
            (
                "\N{TRADE MARK SIGN}",
                "?",
                "?",
            ),
        ),
    )
    def test_a_letter_with_no_base_letter_and_a_symbol_are_a_question_mark_and_never_their_decomposition(
        self,
        *,
        character: str,
        ascii_form: str,
        latin_1_form: str,
    ) -> None:
        assert (
            _written(
                character,
                "ascii",
            )
            == ascii_form
        )

        assert (
            _written(
                character,
                "iso8859-1",
            )
            == latin_1_form
        )

    @mark.parametrize(
        argnames="encoding",
        argvalues=_ENCODINGS,
    )
    @mark.parametrize(
        argnames=(
            "character",
            "expected",
        ),
        argvalues=(
            (
                "\N{CJK UNIFIED IDEOGRAPH-4E2D}",
                "??",
            ),
            (
                "\N{GRINNING FACE}",
                "??",
            ),
            (
                "\N{COMBINING ACUTE ACCENT}",
                "",
            ),
            (
                "e\N{COMBINING ACUTE ACCENT}",
                "e",
            ),
        ),
    )
    def test_a_character_is_a_question_mark_for_each_cell_it_takes_and_nothing_where_it_takes_none(
        self,
        *,
        encoding: str,
        character: str,
        expected: str,
    ) -> None:
        assert (
            _written(
                character,
                encoding,
            )
            == expected
        )

    @mark.parametrize(
        argnames="encoding",
        argvalues=_ENCODINGS,
    )
    @mark.parametrize(
        argnames="character",
        argvalues=(
            *(
                character
                for (
                    character,
                    _,
                ) in _NEAREST
            ),
            "\N{LATIN SMALL LETTER E WITH ACUTE}",
            "\N{LATIN SMALL LETTER I WITH DIAERESIS}",
            "\N{LATIN SMALL LETTER N WITH TILDE}",
            "\N{LATIN CAPITAL LETTER C WITH CEDILLA}",
            "\N{LATIN SMALL LETTER O WITH STROKE}",
            "\N{LATIN SMALL LETTER SHARP S}",
            "\N{LATIN SMALL LIGATURE FI}",
            "\N{NOT EQUAL TO}",
            "\N{VULGAR FRACTION ONE HALF}",
            "\N{TRADE MARK SIGN}",
            "\N{CJK UNIFIED IDEOGRAPH-4E2D}",
            "\N{GRINNING FACE}",
            "\N{COMBINING ACUTE ACCENT}",
            "e\N{COMBINING ACUTE ACCENT}",
            *(
                text
                for (
                    text,
                    _,
                ) in _GRAPHEMES
            ),
        ),
    )
    def test_what_is_written_takes_exactly_the_cells_the_original_took(
        self,
        *,
        encoding: str,
        character: str,
    ) -> None:
        assert cell_len(
            text=_written(
                character,
                encoding,
            ),
        ) == cell_len(
            text=character,
        )

    @mark.parametrize(
        argnames="encoding",
        argvalues=_ENCODINGS,
    )
    @mark.parametrize(
        argnames=(
            "text",
            "expected",
        ),
        argvalues=_GRAPHEMES,
    )
    def test_text_is_written_by_grapheme_as_rich_lays_it_out_with_a_question_mark_for_each_cell_left(
        self,
        *,
        encoding: str,
        text: str,
        expected: str,
    ) -> None:
        written = _written(
            text,
            encoding,
        )

        assert written == expected

        assert cell_len(
            text=written,
        ) == cell_len(
            text=text,
        )

    @mark.parametrize(
        argnames=(
            "encoding",
            "text",
            "expected",
        ),
        argvalues=tuple(
            (
                encoding,
                text,
                ascii_form if encoding == "ascii" else latin_1_form,
            )
            for (
                text,
                ascii_form,
                latin_1_form,
            ) in _BEFORE_A_CONTROL
            for encoding in _ENCODINGS
        ),
    )
    def test_a_control_character_after_a_character_the_stream_cannot_encode_is_kept_whole(
        self,
        *,
        encoding: str,
        text: str,
        expected: str,
    ) -> None:
        written = _written(
            text,
            encoding,
        )

        assert written == expected

        assert cell_len(
            text=written,
        ) == cell_len(
            text=text,
        )

    @mark.parametrize(
        argnames=(
            "encoding",
            "text",
            "expected",
        ),
        argvalues=tuple(
            (
                encoding,
                text,
                ascii_form if encoding == "ascii" else latin_1_form,
            )
            for (
                text,
                ascii_form,
                latin_1_form,
            ) in _AFTER_AN_ENCODABLE
            for encoding in _ENCODINGS
        ),
    )
    def test_a_variation_selector_after_a_character_the_stream_can_encode_keeps_the_cell_it_adds(
        self,
        *,
        encoding: str,
        text: str,
        expected: str,
    ) -> None:
        written = _written(
            text,
            encoding,
        )

        assert written == expected

        assert cell_len(
            text=written,
        ) == cell_len(
            text=text,
        )

    @mark.parametrize(
        argnames="encoding",
        argvalues=_ENCODINGS,
    )
    def test_a_letter_after_a_joiner_is_kept_though_rich_joins_it_to_the_grapheme_before(
        self,
        *,
        encoding: str,
    ) -> None:
        assert (
            _written(
                "in\N{ZERO WIDTH JOINER}line",
                encoding,
            )
            == "inline"
        )

    def test_a_styled_word_ending_in_a_character_the_stream_cannot_encode_keeps_the_escape_that_ends_it(
        self,
    ) -> None:
        stream = TextIOWrapper(
            buffer=BytesIO(),
            encoding="ascii",
        )

        keep_cells_on(
            streams=(stream,),
        )

        Console(
            color_system="standard",
            force_terminal=True,
            file=stream,
            width=80,
        ).print(
            Text(
                text="caf\N{LATIN SMALL LETTER E WITH ACUTE}",
                style="bold",
            ),
        )

        stream.flush()

        assert isinstance(
            stream.buffer,
            BytesIO,
        )

        written = stream.buffer.getvalue()

        assert b"\x1b[1mcafe\x1b[0m" in written

        assert written.count(
            b"[0m",
        ) == written.count(
            b"\x1b[0m",
        )

    def test_what_a_stream_can_encode_is_written_as_it_is_around_what_it_cannot(
        self,
    ) -> None:
        assert (
            _written(
                "caf\N{LATIN SMALL LETTER E WITH ACUTE} "
                "\N{LEFT DOUBLE QUOTATION MARK}na\N{LATIN SMALL LETTER I WITH DIAERESIS}ve"
                "\N{RIGHT DOUBLE QUOTATION MARK} \N{NOT EQUAL TO}\N{CJK UNIFIED IDEOGRAPH-4E2D}",
                "ascii",
            )
            == 'cafe "naive" ???'
        )

    def test_a_handler_asked_to_decode_refuses_rather_than_guess(
        self,
    ) -> None:
        with raises(
            expected_exception=UnicodeDecodeError,
        ):
            lookup_error(
                CELLS_HANDLER,
            )(
                UnicodeDecodeError(
                    "ascii",
                    b"\xff",
                    0,
                    1,
                    "ordinal not in range(128)",
                ),
            )


@final
class TestWhichStreamsGetTheRule:
    @mark.parametrize(
        argnames="encoding",
        argvalues=_ENCODINGS,
    )
    def test_a_stream_that_is_not_utf_8_takes_the_rule_and_keeps_its_encoding(
        self,
        *,
        encoding: str,
    ) -> None:
        stream = _stream(
            encoding,
        )

        keep_cells_on(
            streams=(stream,),
        )

        assert stream.errors == CELLS_HANDLER

        assert stream.encoding == encoding

    @mark.parametrize(
        argnames="encoding",
        argvalues=(
            "utf-8",
            "UTF8",
            "utf_8",
        ),
    )
    def test_a_utf_8_stream_is_left_as_it_is_its_error_handler_included(
        self,
        *,
        encoding: str,
    ) -> None:
        stream = _stream(
            encoding,
            errors="surrogateescape",
        )

        keep_cells_on(
            streams=(stream,),
        )

        assert stream.errors == "surrogateescape"

    def test_each_stream_is_judged_by_its_own_encoding(
        self,
    ) -> None:
        utf_8 = _stream(
            "utf-8",
        )

        latin_1 = _stream(
            "iso8859-1",
        )

        keep_cells_on(
            streams=(
                utf_8,
                latin_1,
            ),
        )

        assert (
            utf_8.errors,
            latin_1.errors,
        ) == (
            "strict",
            CELLS_HANDLER,
        )

    def test_a_stream_that_cannot_take_an_error_handler_is_left_as_it_is(
        self,
    ) -> None:
        stream = StringIO()

        keep_cells_on(
            streams=(stream,),
        )

        stream.write(
            "\N{NOT EQUAL TO}",
        )

        assert stream.getvalue() == "\N{NOT EQUAL TO}"

    @mark.parametrize(
        argnames=(
            "encoding",
            "expected",
        ),
        argvalues=(
            (
                "utf-8",
                True,
            ),
            (
                "UTF8",
                True,
            ),
            (
                "ascii",
                False,
            ),
            (
                "iso8859-1",
                False,
            ),
            (
                "utf-16",
                False,
            ),
        ),
    )
    def test_a_stream_writes_utf_8_by_its_codec_and_not_by_its_spelling(
        self,
        *,
        encoding: str,
        expected: bool,
    ) -> None:
        assert (
            writes_utf8(
                stream=_stream(
                    encoding,
                ),
            )
            is expected
        )

    def test_a_stream_that_names_no_encoding_is_read_as_rich_reads_it_as_utf_8(
        self,
    ) -> None:
        assert writes_utf8(
            stream=StringIO(),
        )
