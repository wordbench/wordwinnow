"""
A learner's own text, from a file or pasted, made ready for analysis.

Three kinds of text need help before the linguistic adapter sees them:
subtitle files (SRT and WebVTT), whose cue numbers and timestamps are not
prose; Markdown files, whose code, link addresses, and HTML are not prose
either; and any text with Windows line endings, HTML entities, or a
byte-order mark.
"""

from html import (
    unescape,
)
from pathlib import (
    Path,
)
from re import (
    DOTALL,
    IGNORECASE,
    MULTILINE,
)
from re import (
    compile as compile_pattern,
)
from typing import (
    Final,
)

from wordwinnow.application.errors import (
    SourceRejectedError,
)
from wordwinnow.domain.document import (
    Document,
    DocumentOrigin,
    InvalidDocumentError,
)

# NOTE:
# A file is Markdown by its name, because nothing in a text says so reliably.
_MARKDOWN_SUFFIXES: Final = frozenset(
    (
        ".md",
        ".markdown",
    ),
)

_FENCED_CODE: Final = compile_pattern(
    pattern=r"^[ \t]*(```|~~~).*?^[ \t]*\1[^\n]*$",
    flags=MULTILINE | DOTALL,
)

_HTML_COMMENT: Final = compile_pattern(
    pattern=r"<!--.*?-->",
    flags=DOTALL,
)

# NOTE:
# An image or a link keeps its text, the part a reader reads, and loses its address.
_INLINE_LINK: Final = compile_pattern(
    pattern=r"!?\[([^\]]*)\]\([^)]*\)",
)

_REFERENCE_LINK: Final = compile_pattern(
    pattern=r"!?\[([^\]]*)\]\[[^\]]*\]",
)

_LINK_DEFINITION: Final = compile_pattern(
    pattern=r"^[ \t]*\[[^\]]+\]:[ \t]*\S+.*$",
    flags=MULTILINE,
)

_ADDRESS: Final = compile_pattern(
    pattern=r"<[a-z][a-z0-9+.-]*:[^>\s]*>|\b[a-z][a-z0-9+.-]*://\S+",
    flags=IGNORECASE,
)

_CODE_SPAN: Final = compile_pattern(
    pattern=r"(`+).+?\1",
    flags=DOTALL,
)

_HTML_TAG: Final = compile_pattern(
    pattern=r"</?[a-z][^>]*>",
    flags=IGNORECASE,
)

_EMPHASIS: Final = compile_pattern(
    pattern=r"(?<!\w)[*_]+|[*_]+(?!\w)",
)

# NOTE:
# A subtitle timing line: `00:01:02,500 --> 00:01:04,000` in SRT, with a period in WebVTT.
_CUE_TIMING: Final = compile_pattern(
    pattern=r"^\s*\d{1,2}:\d{2}:\d{2}[,.]\d{3}\s*-->\s*\d{1,2}:\d{2}:\d{2}[,.]\d{3}",
    flags=MULTILINE,
)

_CUE_INDEX: Final = compile_pattern(
    pattern=r"^\s*\d+\s*$",
)

_MARKUP: Final = compile_pattern(
    pattern=r"<[^>]+>|\{[^}]+\}",
)

# NOTE:
# A line that is only a sound description, such as `[Music]` or `(laughs)`.
_NON_SPEECH: Final = compile_pattern(
    pattern=r"^\s*[\[(][^\])]*[\])]\s*$",
)

_LEADING_DASH: Final = compile_pattern(
    pattern=r"^\s*-\s*",
)

_HORIZONTAL_SPACE: Final = compile_pattern(
    pattern="[ \\t\\u00a0]+",
)

_BLANK_LINES: Final = compile_pattern(
    pattern=r"\n{3,}",
)


def normalize_text(
    *,
    text: str,
) -> str:
    """
    Make a text's whitespace and entities uniform without changing its words.
    """

    unified = (
        text.replace(
            "\ufeff",
            "",
        )
        .replace(
            "\r\n",
            "\n",
        )
        .replace(
            "\r",
            "\n",
        )
    )

    unescaped = unescape(
        s=unified,
    )

    spaced = _HORIZONTAL_SPACE.sub(
        repl=" ",
        string=unescaped,
    )

    lines = "\n".join(
        line.strip()
        for line in spaced.split(
            sep="\n",
        )
    )

    return _BLANK_LINES.sub(
        repl="\n\n",
        string=lines,
    ).strip()


def looks_like_captions(
    *,
    text: str,
) -> bool:
    """
    Whether the text is a subtitle file rather than prose.
    """

    return (
        text.lstrip().startswith(
            "WEBVTT",
        )
        or _CUE_TIMING.search(
            string=text,
        )
        is not None
    )


def extract_caption_prose(
    *,
    text: str,
) -> str:
    """
    Keep only the spoken lines of a subtitle file, joined into prose.
    """

    kept: list[str] = []

    for raw in text.split(
        sep="\n",
    ):
        line = raw.strip()

        if (
            not line
            or line.startswith(
                (
                    "WEBVTT",
                    "NOTE",
                    "STYLE",
                    "REGION",
                ),
            )
            or _CUE_INDEX.match(
                string=line,
            )
            or _CUE_TIMING.match(
                string=line,
            )
            or _NON_SPEECH.match(
                string=line,
            )
        ):
            continue

        cleaned = _LEADING_DASH.sub(
            repl="",
            string=_MARKUP.sub(
                repl="",
                string=line,
            ),
        ).strip()

        if cleaned:
            kept.append(
                cleaned,
            )

    return " ".join(
        kept,
    )


def markdown_prose(
    *,
    text: str,
) -> str:
    """
    The prose of a Markdown text: code blocks, code spans, addresses, link
    definitions, HTML, and emphasis markers removed, and every link and image
    reduced to its text.
    """

    prose = _FENCED_CODE.sub(
        repl="",
        string=text,
    )

    for pattern in (
        _HTML_COMMENT,
        _LINK_DEFINITION,
    ):
        prose = pattern.sub(
            repl="",
            string=prose,
        )

    for pattern in (
        _INLINE_LINK,
        _REFERENCE_LINK,
    ):
        prose = pattern.sub(
            repl=r"\1",
            string=prose,
        )

    for pattern in (
        _ADDRESS,
        _CODE_SPAN,
        _HTML_TAG,
        _EMPHASIS,
    ):
        prose = pattern.sub(
            repl=" ",
            string=prose,
        )

    return prose


def document_from_text(
    *,
    text: str,
    title: str,
    reference: str,
) -> Document:
    """
    Build a custom-text document, extracting prose from subtitles if needed.
    """

    normalized = normalize_text(
        text=text,
    )

    if looks_like_captions(
        text=normalized,
    ):
        normalized = extract_caption_prose(
            text=normalized,
        )

    return Document(
        title=title,
        text=normalized,
        origin=DocumentOrigin.CUSTOM_TEXT,
        reference=reference,
    )


def read_custom_text(
    *,
    path: Path,
) -> Document:
    """
    Read a learner's file as a document.

    A missing, unreadable, or empty file is refused with a message that names
    the file.
    """

    try:
        text = path.read_text(
            encoding="utf-8-sig",
        )

    except FileNotFoundError as exception:
        raise SourceRejectedError(
            f"{path} does not exist",
        ) from exception

    except UnicodeDecodeError as exception:
        raise SourceRejectedError(
            f"{path} is not UTF-8 text",
        ) from exception

    except OSError as exception:
        raise SourceRejectedError(
            f"{path} could not be read ({exception.strerror})",
        ) from exception

    if path.suffix.lower() in _MARKDOWN_SUFFIXES:
        text = markdown_prose(
            text=text,
        )

    try:
        return document_from_text(
            text=text,
            title=path.stem,
            reference=path.name,
        )

    except InvalidDocumentError as exception:
        raise SourceRejectedError(
            f"{path} has no text to analyze",
        ) from exception
