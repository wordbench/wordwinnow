"""
The words a report shows, written as flashcards for Anki or Quizlet.

Each word is one card, composed once from the analysis, and each tool gets the
text file it imports: Anki a file with its header lines and an HTML back, and
Quizlet plain lines of a term and a definition.
"""

from collections.abc import (
    Sequence,
)
from dataclasses import (
    dataclass,
)
from html import (
    escape,
)
from typing import (
    Final,
    final,
)

from wordwinnow.domain.dictionary import (
    LookupOutcome,
)
from wordwinnow.domain.study import (
    GlossSource,
    StudyItem,
)
from wordwinnow.services.cli.report import (
    meaning_of,
    visible,
)

# NOTE:
# The header lines Anki reads before the notes: the separator, that fields are HTML, the note type, the three columns,
# and which column holds the tags.
_ANKI_HEADER: Final = (
    "#separator:Tab",
    "#html:true",
    "#notetype:Basic",
    "#columns:Front\tBack\tTags",
    "#tags column:3",
)

# NOTE:
# How many meanings besides the one shown a fuller card lists, and how many synonyms it gives.
_OTHER_MEANINGS: Final = 2

_SYNONYMS: Final = 3

# NOTE:
# Spelled by name because the linter's confusables rule reads the character as a full stop, which it is not.
_MIDDLE_DOT: Final = "\N{MIDDLE DOT}"

# WARN:
# A link is made only to a web page: a source address is the dictionary's own text, and a `javascript:` address as an
# `href` would run in the learner's Anki.
_LINKABLE: Final = (
    "http://",
    "https://",
)


@dataclass(
    frozen=True,
    kw_only=True,
    slots=True,
)
@final
class Card:
    """
    One word as a flashcard, before it is written for a tool.

    Every text is on one line, with its white space collapsed and each control
    character written as a visible escape, and an empty text is a part the
    analysis does not have.

    `credit` names the meaning's license or source, and `pages` are the
    addresses of the pages it came from.
    """

    front: str

    pronunciation: str

    meaning: str

    others: tuple[str, ...]

    example: str

    synonyms: str

    sentence: str

    credit: str

    pages: tuple[str, ...]

    level: str

    @property
    def lines(
        self,
    ) -> tuple[str, ...]:
        """
        The back of the card, one part a line and in order, without its
        credit.
        """

        return tuple(
            line
            for line in (
                self.pronunciation,
                self.meaning,
                *(f"Other meaning: {other}" for other in self.others),
                f"Example: {self.example}" if self.example else "",
                f"Synonyms: {self.synonyms}" if self.synonyms else "",
                f"In the text: {self.sentence}" if self.sentence else "",
            )
            if line
        )


def _clean(
    text: str,
    /,
) -> str:
    # NOTE:
    # The white space of a text is the layout of where it came from, and a tab or a line break inside a field would
    # split it, so each run becomes one space; what is left of the controls is written out as the terminal report
    # writes it.
    return visible(
        message=" ".join(
            text.split(),
        ),
    )


def _other_meanings_of(
    *,
    study_item: StudyItem,
    source: GlossSource,
) -> tuple[str, ...]:
    """
    Up to two meanings besides the one shown, from the source the meaning came
    from and never the other.
    """

    if source is GlossSource.DICTIONARY:
        dictionary = study_item.dictionary

        definitions = dictionary.definitions if dictionary is not None else ()

        return tuple(
            f"{definition.text}{'' if definition.part_of_speech_matched else ' (other part of speech)'}"
            for definition in definitions[1 : 1 + _OTHER_MEANINGS]
        )

    return tuple(
        f"{sense.gloss}{' (a name)' if sense.is_instance else ''}"
        for sense in study_item.other_senses[:_OTHER_MEANINGS]
    )


def _card_of(
    *,
    study_item: StudyItem,
    with_sentence: bool,
    detailed: bool,
) -> Card:
    gloss = study_item.gloss

    dictionary = study_item.dictionary

    credit = ""

    pages: tuple[str, ...] = ()

    others: tuple[str, ...] = ()

    example = ""

    synonyms = ""

    if gloss is not None:
        if gloss.source is GlossSource.DICTIONARY:
            if gloss.provenance is not None:
                credit = gloss.provenance.license.name if gloss.provenance.license is not None else ""

                pages = gloss.provenance.source_urls

            definitions = dictionary.definitions if dictionary is not None else ()

            example = (definitions[0].example or "") if detailed and definitions else ""

        else:
            # NOTE:
            # A WordNet gloss standing in for a dictionary that did not answer says so, as the report does, or the
            # failure would be invisible.
            credit = (
                "WordNet; dictionary unavailable"
                if dictionary is not None and dictionary.outcome is LookupOutcome.UNAVAILABLE
                else "WordNet"
            )

            leading = study_item.leading_sense

            # NOTE:
            # The synonyms belong to WordNet's leading sense, so they are given only where that sense is the meaning
            # shown; under a dictionary definition they would describe a different sense.
            synonyms = (
                ", ".join(
                    leading.synonyms[:_SYNONYMS],
                )
                if detailed and leading is not None
                else ""
            )

        others = (
            _other_meanings_of(
                study_item=study_item,
                source=gloss.source,
            )
            if detailed
            else ()
        )

    return Card(
        front=_clean(
            f"{study_item.item.lemma} ({study_item.item.part_of_speech})",
        ),
        pronunciation=_clean(
            (dictionary.phonetic or "") if dictionary is not None else "",
        ),
        meaning=_clean(
            meaning_of(
                study_item=study_item,
                mark_source=False,
            ),
        ),
        others=tuple(
            _clean(
                other,
            )
            for other in others
        ),
        example=_clean(
            example,
        ),
        synonyms=_clean(
            synonyms,
        ),
        sentence=_clean(
            study_item.item.example_sentence if with_sentence else "",
        ),
        credit=_clean(
            credit,
        ),
        pages=tuple(
            _clean(
                page,
            )
            for page in pages
        ),
        level=_clean(
            study_item.level.level,
        ),
    )


def _link_of(
    page: str,
    /,
) -> str:
    escaped = escape(
        s=page,
    )

    if page.startswith(
        _LINKABLE,
    ):
        return f'<a href="{escaped}">{escaped}</a>'

    return escaped


def _credit_in_html(
    card: Card,
    /,
) -> str:
    # WARN:
    # Every value is escaped before the markup around it is added, because a field is rendered as HTML and an
    # unescaped one would run whatever a text or a dictionary put in it.
    parts = (
        *(
            (
                escape(
                    s=card.credit,
                ),
            )
            if card.credit
            else ()
        ),
        *(
            _link_of(
                page,
            )
            for page in card.pages
        ),
    )

    credit = ", ".join(
        parts,
    )

    return f"<small>{credit}</small>" if parts else ""


def to_anki(
    *,
    items: Sequence[StudyItem],
    with_sentences: bool = True,
    detailed: bool = False,
) -> str:
    """
    A file Anki imports into Basic notes: its header lines, then one note a
    line with the front, the back, and the tags `wordwinnow` and the word's
    level, separated by tabs.

    The back's parts are joined by line breaks and its credit is small, with
    the source page as a link.

    Every value is escaped for HTML.
    """

    notes: list[str] = []

    for study_item in items:
        card = _card_of(
            study_item=study_item,
            with_sentence=with_sentences,
            detailed=detailed,
        )

        credit = _credit_in_html(
            card,
        )

        back = "<br>".join(
            (
                *(
                    escape(
                        s=line,
                    )
                    for line in card.lines
                ),
                *((credit,) if credit else ()),
            ),
        )

        notes.append(
            "\t".join(
                (
                    escape(
                        s=card.front,
                    ),
                    back,
                    escape(
                        s=f"wordwinnow cefr::{card.level}",
                    ),
                ),
            ),
        )

    return "".join(
        f"{line}\n"
        for line in (
            *_ANKI_HEADER,
            *notes,
        )
    )


def to_quizlet(
    *,
    items: Sequence[StudyItem],
    with_sentences: bool = True,
    detailed: bool = False,
) -> str:
    """
    A file to paste into Quizlet: one card a line, a term and a definition
    separated by a tab, with no header and no markup.

    The definition's parts are joined by a middle dot, and its credit is the
    license or source and the page's address as plain text.
    """

    lines: list[str] = []

    for study_item in items:
        card = _card_of(
            study_item=study_item,
            with_sentence=with_sentences,
            detailed=detailed,
        )

        credit = ", ".join(
            part
            for part in (
                card.credit,
                *card.pages,
            )
            if part
        )

        lines.append(
            "\t".join(
                (
                    card.front,
                    f" {_MIDDLE_DOT} ".join(
                        (
                            *card.lines,
                            *((credit,) if credit else ()),
                        ),
                    ),
                ),
            ),
        )

    return "".join(f"{line}\n" for line in lines)
