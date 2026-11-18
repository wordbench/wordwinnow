"""
A learner's own text: normalization, subtitle extraction, and file reading.
"""

from pathlib import (
    Path,
)
from typing import (
    Final,
    final,
)

from pytest import (
    mark,
    raises,
)

from wordwinnow.application.errors import (
    SourceRejectedError,
)
from wordwinnow.domain.document import (
    DocumentOrigin,
)
from wordwinnow.infrastructure.sources.custom_text import (
    document_from_text,
    extract_caption_prose,
    looks_like_a_web_page,
    looks_like_captions,
    normalize_text,
    read_custom_text,
)

_PAGE: Final = (
    '<html lang="en">\n'
    "<head><title>foo</title></head>\n"
    "<body><p>Irene Adler kept the photograph.</p></body>\n"
    "</html>\n"
)


@final
class TestNormalizeText:
    def test_line_endings_entities_and_spacing_are_unified(
        self,
    ) -> None:
        assert (
            normalize_text(
                text="﻿Holmes &amp; Watson\r\n\r\n\r\n\ttoss   the rocket.  \r\n",
            )
            == "Holmes & Watson\n\ntoss the rocket."
        )


@final
class TestCaptions:
    def test_srt_cues_become_prose(
        self,
    ) -> None:
        srt = (
            "1\n00:00:01,000 --> 00:00:02,000\n- Where is the photograph?\n\n"
            "2\n00:00:03,000 --> 00:00:04,500\n[Fire alarm]\n<i>Behind the sliding panel.</i>\n"
        )

        assert looks_like_captions(
            text=srt,
        )

        assert (
            extract_caption_prose(
                text=srt,
            )
            == "Where is the photograph? Behind the sliding panel."
        )

    def test_webvtt_headers_and_notes_are_dropped(
        self,
    ) -> None:
        vtt = "WEBVTT\n\nNOTE made by hand\n\n00:00:01.000 --> 00:00:02.000\nRaise the cry of fire.\n"

        assert looks_like_captions(
            text=vtt,
        )

        assert (
            extract_caption_prose(
                text=vtt,
            )
            == "Raise the cry of fire."
        )

    def test_prose_is_not_captions(
        self,
    ) -> None:
        assert not looks_like_captions(
            text="At 7:45 the King arrived.",
        )


@final
class TestDocumentFromText:
    def test_a_subtitle_file_is_extracted_and_a_text_kept(
        self,
    ) -> None:
        document = document_from_text(
            text="1\n00:00:01,000 --> 00:00:02,000\nRaise the cry of fire.\n",
            title="a-scandal-in-bohemia",
            reference="a-scandal-in-bohemia.srt",
        )

        assert document.text == "Raise the cry of fire."

        assert document.origin is DocumentOrigin.CUSTOM_TEXT

        assert (
            document_from_text(
                text="Irene Adler kept the photograph.",
                title="a-scandal-in-bohemia",
                reference="a-scandal-in-bohemia.txt",
            ).text
            == "Irene Adler kept the photograph."
        )


@final
class TestReadCustomText:
    def test_a_file_becomes_a_document_named_after_it(
        self,
        *,
        tmp_path: Path,
    ) -> None:
        path = tmp_path / "a-scandal-in-bohemia.txt"

        path.write_text(
            data="Irene Adler kept the photograph.\n",
            encoding="utf-8",
        )

        document = read_custom_text(
            path=path,
        )

        assert document.title == "a-scandal-in-bohemia"

        assert document.reference == "a-scandal-in-bohemia.txt"

        assert document.text == "Irene Adler kept the photograph."

    def test_markdown_keeps_its_prose_and_loses_code_addresses_and_markup(
        self,
        *,
        tmp_path: Path,
    ) -> None:
        source = (
            "# Getting **started**\n\n"
            "Read the [guide](https://example.com/guide) and the [notes][notes] first, then run `pip install "
            "wordwinnow` and enjoy the _story_.\n\n"
            "![A detective](images/holmes.png) <b>Holmes</b> waits at <https://example.org> or "
            "https://example.net/page.\n\n"
            "```sh\nuv run wordwinnow analyze\n```\n\n"
            "<!-- a hidden remark -->\n\n"
            "[notes]: https://example.com/notes\n"
        )

        markdown = tmp_path / "note.md"

        markdown.write_text(
            data=source,
            encoding="utf-8",
        )

        text = read_custom_text(
            path=markdown,
        ).text

        for kept in (
            "Getting",
            "started",
            "guide",
            "notes",
            "story",
            "A detective",
            "Holmes",
        ):
            assert kept in text

        for lost in (
            "http",
            "example",
            "pip",
            "uv run",
            "images",
            "<b>",
            "hidden remark",
            "**",
            "_story_",
        ):
            assert lost not in text

        plain = tmp_path / "note.txt"

        plain.write_text(
            data=source,
            encoding="utf-8",
        )

        assert (
            "https://example.com/guide"
            in read_custom_text(
                path=plain,
            ).text
        )

    def test_a_missing_empty_or_binary_file_is_refused_by_name(
        self,
        *,
        tmp_path: Path,
    ) -> None:
        with raises(
            expected_exception=SourceRejectedError,
            match="no-such-file",
        ):
            read_custom_text(
                path=tmp_path / "no-such-file.txt",
            )

        empty = tmp_path / "empty.txt"

        empty.write_text(
            data="  \n",
            encoding="utf-8",
        )

        with raises(
            expected_exception=SourceRejectedError,
            match=r"empty\.txt",
        ):
            read_custom_text(
                path=empty,
            )

        binary = tmp_path / "binary.txt"

        binary.write_bytes(
            data=b"\xff\xfe\x00\x01",
        )

        with raises(
            expected_exception=SourceRejectedError,
            match="not UTF-8",
        ):
            read_custom_text(
                path=binary,
            )


@final
class TestAWholeWebPage:
    @mark.parametrize(
        argnames="text",
        argvalues=(
            "<!DOCTYPE html>\n" + _PAGE,
            "<!doctype html>\n" + _PAGE,
            _PAGE,
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Strict//EN" "foo.dtd">\n' + _PAGE,
            "<!-- foo -->\n<!DOCTYPE html>\n" + _PAGE,
            "\ufeff\r\n\r\n   <!DOCTYPE html>\r\n"
            + _PAGE.replace(
                "\n",
                "\r\n",
            ),
            ("<!DOCTYPE html>\n" + _PAGE).replace(
                "\n",
                "",
            ),
            '<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 4.01//EN" "foo.dtd">\n'
            + _PAGE.replace(
                "html",
                "HTML",
            ),
        ),
        ids=(
            "doctype",
            "doctype in lower case",
            "html start tag and no doctype",
            "xml declaration and an xhtml doctype",
            "comment and a doctype",
            "byte-order mark, blank lines, spaces, and crlf",
            "one line",
            "html 4.01 in upper case",
        ),
    )
    def test_a_text_that_begins_as_a_page_does_is_recognized(
        self,
        *,
        text: str,
    ) -> None:
        assert looks_like_a_web_page(
            text=text,
        )

    @mark.parametrize(
        argnames="text",
        argvalues=(
            "Irene Adler kept the photograph.\n",
            "A page starts with <!DOCTYPE html>, and the <html> element holds the rest.\n",
            '1\n00:00:01,000 --> 00:00:02,000\n<i>[Fire alarm]</i> <font color="#ffffff">Irene Adler.</font>\n',
            "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\n<v Holmes><c.yellow>Irene Adler.</c></v>\n",
            '<p align="center"><img src="foo.png" alt="foo"></p>\n\n# foo\n',
            "<!-- foo -->\n# foo\n\nIrene Adler kept the photograph.\n",
            "<article><p>Irene Adler kept the photograph.</p></article>\n",
            "HTTP/2 200\r\ncontent-type: text/html\r\n\r\n<!DOCTYPE html>\n" + _PAGE,
            "<br />\n<b>Warning</b>: headers already sent<br />\n<!DOCTYPE html>\n" + _PAGE,
            "<head><title>foo</title></head>\n<body><p>Irene Adler kept the photograph.</p></body>\n",
            "<!-- foo\n<!DOCTYPE html>\n" + _PAGE,
            '<?xml version="1.0"\n<!DOCTYPE html>\n' + _PAGE,
            "<html-viewer>Irene Adler kept the photograph.</html-viewer>\n",
            "",
        ),
        ids=(
            "prose",
            "prose that names a doctype and an html tag",
            "srt with tags in its cues",
            "webvtt with tags in its cues",
            "markdown that opens with a block",
            "markdown that opens with a comment",
            "fragment",
            "response head and then a page",
            "warning line and then a page",
            "page that opens with head",
            "comment that does not end",
            "xml declaration that does not end",
            "html-viewer",
            "empty",
        ),
    )
    def test_any_other_text_is_not_recognized(
        self,
        *,
        text: str,
    ) -> None:
        assert not looks_like_a_web_page(
            text=text,
        )

    # TEST:
    # The Markdown page opens with an html start tag and no doctype, so that a check made after `markdown_prose` has
    # taken the tags away would miss it.
    @mark.parametrize(
        argnames=(
            "name",
            "page",
        ),
        argvalues=(
            (
                "page.html",
                "<!DOCTYPE html>\n" + _PAGE,
            ),
            (
                "page.txt",
                "<!DOCTYPE html>\n" + _PAGE,
            ),
            (
                "page.md",
                _PAGE,
            ),
        ),
        ids=(
            "html",
            "text",
            "markdown",
        ),
    )
    def test_a_page_in_a_file_is_refused_by_name(
        self,
        *,
        tmp_path: Path,
        name: str,
        page: str,
    ) -> None:
        path = tmp_path / name

        path.write_text(
            data=page,
            encoding="utf-8",
        )

        with raises(
            expected_exception=SourceRejectedError,
        ) as caught:
            read_custom_text(
                path=path,
            )

        assert (
            str(
                object=caught.value,
            )
            == f"{path} is a web page; extract its article's text first, as the operating guide at "
            "github.com/wordbench/wordwinnow shows"
        )

    def test_markdown_that_opens_with_html_is_read(
        self,
        *,
        tmp_path: Path,
    ) -> None:
        path = tmp_path / "note.md"

        path.write_text(
            data='<p align="center">\n  <img src="foo.png" alt="foo">\n</p>\n\nIrene Adler kept the photograph.\n',
            encoding="utf-8",
        )

        document = read_custom_text(
            path=path,
        )

        assert document.text == "Irene Adler kept the photograph."
