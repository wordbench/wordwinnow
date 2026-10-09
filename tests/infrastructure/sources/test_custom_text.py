"""
A learner's own text: normalization, subtitle extraction, and file reading.
"""

from pathlib import (
    Path,
)
from typing import (
    final,
)

from pytest import (
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
    looks_like_captions,
    normalize_text,
    read_custom_text,
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
