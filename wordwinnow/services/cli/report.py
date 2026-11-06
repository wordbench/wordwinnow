"""
How an analysis is shown to a learner: as tables or cards in the terminal, or
as JSON and CSV for other tools.

The report is built from the domain objects and nothing else, so what the
learner sees is exactly what the analysis holds.
"""

from collections.abc import (
    Mapping,
    Sequence,
)
from csv import (
    writer,
)
from io import (
    StringIO,
)
from types import (
    MappingProxyType,
)
from typing import (
    Final,
)

from rich.console import (
    Console,
)
from rich.padding import (
    Padding,
)
from rich.table import (
    Table,
)
from rich.text import (
    Text,
)

from wordwinnow.application.dto import (
    AnalysisSummary,
    VocabularySummary,
)
from wordwinnow.domain.analysis import (
    Analysis,
    AnalysisStatus,
)
from wordwinnow.domain.cefr import (
    LevelSource,
)
from wordwinnow.domain.dictionary import (
    LookupOutcome,
    Provenance,
)
from wordwinnow.domain.document import (
    Document,
    DocumentOrigin,
)
from wordwinnow.domain.learner import (
    LearnerProfile,
)
from wordwinnow.domain.senses import (
    LexicalSense,
)
from wordwinnow.domain.study import (
    GlossSource,
    StudyItem,
    StudyTier,
)
from wordwinnow.services.intake.schemas import (
    AnalysisOut,
)

# NOTE:
# Rows a terminal can take before the report becomes a scroll; `--rows` overrides it.
DEFAULT_ROW_LIMIT: Final = 40

# NOTE:
# The tier's name heads each group of rows in the table and is printed beside every word on a card, so the style only
# repeats it: the words to study now stand out, the ones within reach are marked, and the ones below the learner's
# level or already known recede.
_TIER_STYLES: Final = MappingProxyType(
    mapping={
        StudyTier.FOCUS: "bold green",
        StudyTier.STRETCH: "yellow",
        StudyTier.REVIEW: "dim",
        StudyTier.KNOWN: "dim strike",
    },
)

# NOTE:
# What a heading says of a tier is what the tier means for this learner, so it names the learner's level and target.
_TIER_HEADINGS: Final = MappingProxyType(
    mapping={
        StudyTier.FOCUS: "FOCUS, from {level} to {target}: study these first",
        StudyTier.STRETCH: "STRETCH, above {target}: study these next",
        StudyTier.REVIEW: "REVIEW, below {level}: revisit any you are unsure of",
        StudyTier.KNOWN: "KNOWN, words you said you know",
    },
)

# NOTE:
# One character after the level says where it came from, and a space keeps the levels of the reference lists in line
# with the estimates.
_LEVEL_MARKS: Final = MappingProxyType(
    mapping={
        LevelSource.REFERENCE_LIST: " ",
        LevelSource.MODEL: "*",
        LevelSource.FREQUENCY_HEURISTIC: "~",
    },
)

_GLOSS_SOURCE_NAMES: Final = MappingProxyType(
    mapping={
        GlossSource.DICTIONARY: "dictionary",
        GlossSource.LEXICAL_NETWORK: "WordNet",
    },
)

# NOTE:
# The title `analyze -` gives a text it read from standard input; a listing line holds an origin and a title and no
# reference, so the two together are the one rule that tells such a text from a file.
_STANDARD_INPUT: Final = "standard input"

_FROM_STANDARD_INPUT: Final = "read from standard input"

# NOTE:
# How many words of a text read from standard input say which text it is.
_OPENING_WORDS: Final = 9

# NOTE:
# The multiplication sign before a word's count, spelled by name because the linter's confusables rule reads the
# character as the letter x, which it is not.
_TIMES: Final = "\N{MULTIPLICATION SIGN}"

# NOTE:
# What the count and the level on a row mean, said once under the tables, since the rows have no header line.
_COLUMNS: Final = f"{_TIMES}n is how often the text uses the word; each level is the word's CEFR level."

# NOTE:
# The reason `process_an_analysis` and `requeue_stale_analyses` store for an analysis whose worker went away, the one
# failure that is the product's own sentence and not an exception's class and message.
_INTERRUPTED: Final = "processing was interrupted"

# NOTE:
# The three control characters that have a name of their own, written as that name.
_NAMED_CONTROLS: Final = MappingProxyType(
    mapping={
        9: "\\t",
        10: "\\n",
        13: "\\r",
    },
)

# NOTE:
# Every character whose Unicode general category is `Cc`, U+0000 to U+001F and U+007F to U+009F, and no other, with
# its escape: a named one as `\n` is written, and each other as `\x` and two lower-case hexadecimal digits.
#
# A backslash already in a message stays one, so the form is for reading and is not reversible.
_CONTROLS: Final = MappingProxyType(
    mapping={
        code: _NAMED_CONTROLS.get(
            code,
            f"\\x{code:02x}",
        )
        for code in (
            *range(
                0x20,
            ),
            *range(
                0x7F,
                0xA0,
            ),
        )
    },
)


def visible(
    *,
    message: str,
) -> str:
    """
    `message` with each control character written as a visible escape.

    Text that comes from outside the program reaches a terminal through this,
    so that it can neither break a line nor drive the terminal.
    """

    return message.translate(
        _CONTROLS,
    )


def _plain(
    value: str,
    /,
    *,
    style: str = "",
) -> Text:
    # NOTE:
    # Text from outside the program, a dictionary's definition or a borrowed text's title, is drawn as text and never
    # as markup, so a square bracket or an emoji code in it stays as typed, and its control characters are written
    # out, since an escape character in a `Text` reaches the terminal as it is.
    return Text(
        text=visible(
            message=value,
        ),
        style=style,
    )


def tier_counts(
    *,
    items: Sequence[StudyItem],
) -> dict[StudyTier, int]:
    """
    How many study items landed in each study tier, every tier present.
    """

    counts = dict.fromkeys(
        StudyTier,
        0,
    )

    for study_item in items:
        counts[study_item.tier] += 1

    return counts


def _counted[Name: str](
    counts: Mapping[Name, int],
    /,
) -> str:
    """
    Counts as a learner reads them: each name, its underscores as spaces, then
    its count.
    """

    parts: list[str] = []

    for (
        name,
        count,
    ) in counts.items():
        spaced = name.replace(
            "_",
            " ",
        )

        parts.append(
            f"{spaced} {count}",
        )

    return ", ".join(
        parts,
    )


def level_source_counts(
    *,
    items: Sequence[StudyItem],
) -> dict[LevelSource, int]:
    """
    How many study items took their level from each source, every source
    present, so that a level model the report did not use shows as zero.
    """

    counts = dict.fromkeys(
        LevelSource,
        0,
    )

    for study_item in items:
        counts[study_item.level.source] += 1

    return counts


def lookup_outcome_counts(
    *,
    items: Sequence[StudyItem],
) -> dict[LookupOutcome, int]:
    """
    How the dictionary answered for the study items it was asked about, every
    outcome present.
    """

    counts = dict.fromkeys(
        LookupOutcome,
        0,
    )

    for study_item in items:
        if study_item.dictionary is not None:
            counts[study_item.dictionary.outcome] += 1

    return counts


def select_items(
    *,
    items: Sequence[StudyItem],
    tiers: Sequence[StudyTier] | None,
    limit: int | None,
) -> tuple[StudyItem, ...]:
    """
    The items a report shows: the chosen tiers, up to `limit`.
    """

    chosen = tuple(study_item for study_item in items if tiers is None or study_item.tier in tiers)

    return chosen[:limit] if limit is not None else chosen


def meaning_of(
    *,
    study_item: StudyItem,
    mark_source: bool = True,
) -> str:
    """
    The one line a learner reads where the meaning goes: the gloss with its
    source marked, or, when there is no gloss, why there is none.

    A definition the dictionary could not be asked for is unknown rather than
    missing, and its reason is said in words, never as the meaning itself.

    A table that says once that every meaning is WordNet's passes
    `mark_source` as false, and a WordNet gloss then goes without its source.
    """

    gloss = study_item.gloss

    dictionary = study_item.dictionary

    if gloss is not None:
        marker = "" if gloss.part_of_speech_matched else " (other part of speech)"

        # NOTE:
        # A WordNet gloss standing in for a dictionary that did not answer says so, or the failure would be invisible.
        unavailable = dictionary is not None and dictionary.outcome is LookupOutcome.UNAVAILABLE

        name = _GLOSS_SOURCE_NAMES[gloss.source]

        source = (
            ""
            if gloss.source is GlossSource.DICTIONARY or not mark_source
            else f" ({name}; dictionary unavailable)"
            if unavailable
            else f" ({name})"
        )

        return f"{gloss.text}{marker}{source}"

    # NOTE:
    # WordNet is asked about the word as the analysis tagged it, so the line names that part of speech: a word the
    # tagger misread can have a meaning as another, which `not in WordNet` would deny.
    if dictionary is None:
        return f"no {study_item.item.part_of_speech} meaning in WordNet"

    if dictionary.outcome is LookupOutcome.UNAVAILABLE:
        reason = (dictionary.failure_reason or "").replace(
            "_",
            " ",
        )

        return (
            f"unknown: the dictionary was unavailable ({reason})"
            if reason
            else "unknown: the dictionary was unavailable"
        )

    return "no definition found"


def _meaning_text(
    study_item: StudyItem,
    /,
    *,
    mark_source: bool = True,
) -> Text:
    # NOTE:
    # A meaning is lexical text and may hold square brackets, which markup would swallow, so it is drawn as text; a
    # line that says why there is no meaning is set apart in dim italics, so it never reads as the word's meaning.
    return _plain(
        meaning_of(
            study_item=study_item,
            mark_source=mark_source,
        ),
        style="" if study_item.gloss is not None else "dim italic",
    )


def _related_of(
    sense: LexicalSense | None,
    /,
    *,
    broader: bool = True,
) -> str:
    """
    The relations of one sense, as one line: synonyms, broader concepts when
    `broader` asks for them, and opposites.
    """

    if sense is None:
        return ""

    parts: list[str] = []

    if sense.synonyms:
        parts.append(
            "= "
            + ", ".join(
                sense.synonyms[:3],
            ),
        )

    if broader and sense.hypernyms:
        parts.append(
            "< "
            + ", ".join(
                sense.hypernyms[:2],
            ),
        )

    if sense.antonyms:
        parts.append(
            "≠ "
            + ", ".join(
                sense.antonyms[:2],
            ),
        )

    return "; ".join(
        parts,
    )


def _other_meanings(
    study_item: StudyItem,
    /,
) -> tuple[str, ...]:
    """
    The meanings a card lists after the one shown, one line each with its
    source: the dictionary's further definitions, then WordNet's other senses,
    most used first, each with its own synonyms and opposites, and a sense
    that names a particular person, place, or work marked as a name.
    """

    dictionary = study_item.dictionary

    definitions = dictionary.definitions if dictionary is not None else ()

    lines = [
        f"{definition.text}{'' if definition.part_of_speech_matched else ' (other part of speech)'} (dictionary)"
        for definition in definitions[1:]
    ]

    for sense in study_item.other_senses:
        source = "WordNet; a name" if sense.is_instance else "WordNet"

        related = _related_of(
            sense,
            broader=False,
        )

        lines.append(
            f"{sense.gloss} ({source}){f'  {related}' if related else ''}",
        )

    return tuple(
        lines,
    )


def provenance_of(
    *,
    provenance: Provenance,
) -> str:
    """
    Where a definition comes from and the license it is shown under, as one
    line a learner can follow.
    """

    source = ", ".join(
        provenance.source_urls,
    )

    shown_under = (
        f"{value_object.name} ({value_object.url})" if (value_object := provenance.license) is not None else ""
    )

    if source and shown_under:
        return f"from {source} under {shown_under}"

    if source:
        return f"from {source}"

    return f"under {shown_under}"


def _grid() -> Table:
    # NOTE:
    # Two columns, the label and its value, with no border and no edge, so that a value that wraps wraps under its own
    # column; the fold keeps a word longer than its column whole on the screen, where the default would cut it.
    table = Table(
        box=None,
        padding=(
            0,
            1,
        ),
        pad_edge=False,
        show_header=False,
        show_edge=False,
    )

    table.add_column(
        style="bold",
        overflow="fold",
        no_wrap=True,
    )

    table.add_column(
        overflow="fold",
    )

    return table


def _label_of(
    origin: DocumentOrigin,
    title: str,
    /,
) -> str:
    """
    What names a text for the learner: that it was read from standard input,
    or its title.
    """

    if origin is DocumentOrigin.CUSTOM_TEXT and title == _STANDARD_INPUT:
        return _FROM_STANDARD_INPUT

    return title


def _source_of(
    document: Document,
    /,
) -> Text:
    if document.origin is not DocumentOrigin.CUSTOM_TEXT or document.title != _STANDARD_INPUT:
        return _plain(
            document.title,
        )

    words = document.text.split()

    opening = " ".join(
        words[:_OPENING_WORDS],
    )

    spoken = len(
        words,
    )

    beginning = "beginning " if spoken > _OPENING_WORDS else ""

    return _plain(
        f"{_FROM_STANDARD_INPUT}, {beginning}“{opening}”",
    )


def _learner_of(
    profile: LearnerProfile,
    /,
) -> str:
    known = len(
        profile.known_lemmas,
    )

    words = "no known words" if known == 0 else "1 known word" if known == 1 else f"{known:,} known words"

    return f"{profile.level}, working toward {profile.target_level}, {words}"


def _found_of(
    analysis: Analysis,
    /,
) -> str:
    counts = tier_counts(
        items=analysis.items,
    )

    studied = counts[StudyTier.FOCUS] + counts[StudyTier.STRETCH]

    noun = "word" if studied == 1 else "words"

    found = (
        f"{studied:,} {noun} to study: {counts[StudyTier.FOCUS]:,} FOCUS, {counts[StudyTier.STRETCH]:,} STRETCH; "
        f"{counts[StudyTier.REVIEW]:,} REVIEW, below {analysis.profile.level}"
    )

    if not analysis.profile.known_lemmas:
        return found

    return f"{found}; {counts[StudyTier.KNOWN]:,} KNOWN, words you said you know"


def _failed_of(
    analysis: Analysis,
    remote: bool,
    /,
) -> str:
    reason = analysis.failure_reason or ""

    if reason == _INTERRUPTED:
        analyze = "wordwinnow analyze --remote" if remote else "wordwinnow analyze"

        return f"failed: {_INTERRUPTED}; run {analyze} on the text again"

    # NOTE:
    # A stored failure is an exception's class and its message, `RuntimeError: foo`, and the learner is given only the
    # class, as a code to quote, since the message can hold a path or a value that is for the operator.
    (
        code,
        separator,
        _,
    ) = reason.partition(
        ": ",
    )

    named = f", code {code}" if code and separator else ""

    flag = " --remote" if remote else ""

    show = f"wordwinnow show {analysis.id}{flag} --view operator"

    return (
        f"failed: Wordwinnow met an internal error{named}; run wordwinnow doctor, and if it finds nothing missing, "
        f"report it as an issue with what {show} prints"
    )


def _outcome_of(
    analysis: Analysis,
    remote: bool,
    /,
) -> tuple[str, Text]:
    """
    The header row that says what the analysis found, or, when it found
    nothing yet or failed, what stands in its place.
    """

    status = analysis.status

    if status in (
        AnalysisStatus.REQUESTED,
        AnalysisStatus.PROCESSING,
    ):
        return (
            "Status",
            Text(
                text=f"{status}, not finished yet",
            ),
        )

    if status is AnalysisStatus.FAILED:
        return (
            "Status",
            _plain(
                _failed_of(
                    analysis,
                    remote,
                ),
            ),
        )

    if not analysis.items:
        return (
            "Found",
            Text(
                text="no words to study: the text holds only function words, names, and numbers",
            ),
        )

    return (
        "Found",
        Text(
            text=_found_of(
                analysis,
            ),
        ),
    )


def _unanswered_of(
    analysis: Analysis,
    /,
) -> str | None:
    if analysis.status is not AnalysisStatus.COMPLETED or not analysis.options.include_dictionary:
        return None

    unanswered = lookup_outcome_counts(
        items=analysis.items,
    )[LookupOutcome.UNAVAILABLE]

    if not unanswered:
        return None

    noun = "word" if unanswered == 1 else "words"

    return f"did not answer for {unanswered:,} {noun}; WordNet's meaning stands in where WordNet has one"


def render_summary(
    *,
    analysis: Analysis,
    remote: bool,
    console: Console,
) -> None:
    """
    Print the analysis header: which text and which learner this is, and what
    was found, or why nothing was, and then whose text it is when it is
    borrowed.

    `remote` says that the distributed mode's services hold the analysis, so
    that a command the header names is one that reaches them.
    """

    table = _grid()

    (
        label,
        value,
    ) = _outcome_of(
        analysis,
        remote,
    )

    rows = [
        (
            "Text",
            _source_of(
                analysis.document,
            ),
        ),
        (
            "Learner",
            _plain(
                _learner_of(
                    analysis.profile,
                ),
            ),
        ),
        (
            label,
            value,
        ),
    ]

    unanswered = _unanswered_of(
        analysis,
    )

    if unanswered is not None:
        rows.append(
            (
                "Dictionary",
                Text(
                    text=unanswered,
                ),
            ),
        )

    for (
        name,
        cell,
    ) in rows:
        table.add_row(
            name,
            cell,
        )

    console.print(
        table,
    )

    render_attribution(
        document=analysis.document,
        console=console,
    )


def render_figures(
    *,
    analysis: Analysis,
    console: Console,
) -> None:
    """
    Print the figures that say how an analysis ran, which are an operator's
    and not the learner's: the reason it failed, where its text came from, its
    status, where its levels came from, what the dictionary answered, and how
    long each stage took.

    A figure the analysis does not have yet is left out and never shown as
    zero.
    """

    console.print()

    console.print(
        Text(
            text=f"Analysis {analysis.id}",
            style="bold",
        ),
    )

    table = _grid()

    if analysis.status is AnalysisStatus.FAILED:
        table.add_row(
            "Failure",
            _plain(
                analysis.failure_reason or "",
            ),
        )

    table.add_row(
        "Document",
        _plain(
            f"{analysis.document.origin}: {analysis.document.reference}",
        ),
    )

    table.add_row(
        "Status",
        Text(
            text=str(
                object=analysis.status,
            ),
        ),
    )

    if analysis.status is AnalysisStatus.COMPLETED:
        table.add_row(
            "Levels",
            Text(
                text=_counted(
                    level_source_counts(
                        items=analysis.items,
                    ),
                ),
            ),
        )

        if analysis.options.include_dictionary:
            outcomes = lookup_outcome_counts(
                items=analysis.items,
            )

            asked = sum(
                outcomes.values(),
            )

            total = len(
                analysis.items,
            )

            outcomes_text = _counted(
                outcomes,
            )

            table.add_row(
                "Dictionary",
                Text(
                    text=f"asked about {asked} of {total} items: {outcomes_text}",
                ),
            )

        wait = analysis.queue_wait_seconds

        stages = " ".join(f"{timing.stage}={timing.seconds:.2f}s" for timing in analysis.stage_timings)

        table.add_row(
            "Timing",
            Text(
                text=f"queue wait {wait:.2f}s; {stages}" if wait is not None else stages,
            ),
        )

    console.print(
        table,
    )


def render_attribution(
    *,
    document: Document,
    console: Console,
) -> None:
    """
    Print whose text this is and where to read each piece in full, for a
    document that belongs to someone else.
    """

    if document.attribution is None and not document.references:
        return

    if document.attribution is not None:
        console.print(
            _plain(
                document.attribution,
                style="bold",
            ),
        )

    for reference in document.references:
        console.print(
            Text.assemble(
                "  ",
                _plain(
                    reference.title,
                ),
                ": ",
                _plain(
                    reference.url,
                ),
            ),
            soft_wrap=True,
        )

    if document.expires:
        console.print(
            "This text may be kept for a day; the analysis is deleted after that.",
            style="dim",
        )


def _legend_of(
    items: Sequence[StudyItem],
    /,
) -> str | None:
    """
    The line that says what each mark on a level means, for the marks on
    screen alone, or nothing when no row is marked.
    """

    sources = frozenset(study_item.level.source for study_item in items)

    modeled = LevelSource.MODEL in sources

    estimated = LevelSource.FREQUENCY_HEURISTIC in sources

    if not modeled and not estimated:
        return None

    marks = (
        "* estimated by the level model, ~ from the word's frequency in English"
        if modeled and estimated
        else "* estimated by the level model"
        if modeled
        else "~ estimated from the word's frequency in English"
    )

    if LevelSource.REFERENCE_LIST in sources:
        return f"{marks}; the other levels are from reference lists."

    return f"{marks}."


def _meaning_note_of(
    items: Sequence[StudyItem],
    asked: bool,
    /,
) -> str | None:
    """
    The note that says once where the meanings in a table come from and that
    they are one for each word, or nothing when there is nothing to say.
    """

    with_others = any(
        _other_meanings(
            study_item,
        )
        for study_item in items
    )

    if asked:
        # NOTE:
        # A table shows one meaning per word, so it says once that the others are on the cards, and only when some
        # word has others.
        return (
            "Each word shows one meaning, which may not be the one the text uses; --detail lists the others."
            if with_others
            else None
        )

    if with_others:
        return (
            "Meanings are WordNet's, one for each word, which may not be the one the text uses; --detail lists the "
            "others, and the dictionary was not asked."
        )

    return (
        "Meanings are WordNet's, one for each word, which may not be the one the text uses, and the dictionary was "
        "not asked."
    )


def render_items(
    *,
    analysis: Analysis,
    items: Sequence[StudyItem],
    console: Console,
) -> None:
    """
    Print the study items as one table for each tier on screen, in study
    order, under a heading that says what the tier means for this learner, and
    then what the columns, the marks, and the meanings in the tables mean.

    `analysis` is the one the items belong to, which says who the learner is
    and whether the dictionary was asked.

    Nothing is printed for no items, since there is then no table to explain.
    """

    if not items:
        return

    asked = analysis.options.include_dictionary

    profile = analysis.profile

    for tier in StudyTier:
        rows = tuple(study_item for study_item in items if study_item.tier is tier)

        if not rows:
            continue

        style = _TIER_STYLES[tier]

        console.print()

        console.print(
            Text(
                text=_TIER_HEADINGS[tier].format(
                    level=profile.level,
                    target=profile.target_level,
                ),
                style="bold",
            ),
        )

        console.print()

        # NOTE:
        # The rows need five columns where the grid has two, and the word and the meaning, the two that can run long,
        # fold, while the other three keep to one line, so that no cell ends in an ellipsis.
        table = Table(
            box=None,
            padding=(
                0,
                1,
            ),
            pad_edge=False,
            show_header=False,
            show_edge=False,
        )

        table.add_column(
            overflow="fold",
        )

        table.add_column(
            overflow="fold",
            no_wrap=True,
        )

        table.add_column(
            overflow="fold",
            no_wrap=True,
        )

        table.add_column(
            justify="right",
            overflow="fold",
            no_wrap=True,
        )

        table.add_column(
            overflow="fold",
        )

        for study_item in rows:
            table.add_row(
                _plain(
                    study_item.item.lemma,
                    style=style,
                ),
                Text(
                    text=str(
                        object=study_item.item.part_of_speech,
                    ),
                ),
                Text(
                    text=f"{study_item.level.level}{_LEVEL_MARKS[study_item.level.source]}",
                ),
                Text(
                    text=f"{_TIMES}{study_item.item.occurrence_count}",
                ),
                _meaning_text(
                    study_item,
                    mark_source=asked,
                ),
            )

        console.print(
            Padding(
                renderable=table,
                pad=(
                    0,
                    0,
                    0,
                    1,
                ),
            ),
        )

    provenances = tuple(
        provenance
        for study_item in items
        if (gloss := study_item.gloss) is not None and (provenance := gloss.provenance) is not None
    )

    licenses = tuple(
        dict.fromkeys(value_object for provenance in provenances if (value_object := provenance.license) is not None),
    )

    notes = tuple(
        note
        for note in (
            _COLUMNS,
            _legend_of(
                items,
            ),
            _meaning_note_of(
                items,
                asked,
            ),
        )
        if note is not None
    )

    if notes or licenses:
        console.print()

    for note in notes:
        console.print(
            note,
            style="dim",
            emoji=False,
            markup=False,
            highlight=False,
        )

    for dictionary_license in licenses:
        # NOTE:
        # A dictionary with a page per word, as Wiktionary has, gives each definition its own source; one without it,
        # as WordNet is, has only the license to show.
        sourced = any(
            provenance.source_urls for provenance in provenances if provenance.license == dictionary_license
        )

        console.print(
            Text.assemble(
                "Definitions shown under ",
                _plain(
                    dictionary_license.name,
                ),
                " (",
                _plain(
                    dictionary_license.url,
                ),
                ")",
                "; --detail gives each one's source" if sourced else "",
                ".",
                style="dim",
            ),
            soft_wrap=True,
        )


def _indented(
    renderable: Text,
    indent: int,
    /,
) -> Padding:
    # NOTE:
    # A line of a card that is wider than the terminal wraps inside its own indent, under its first line, and never
    # back at the left margin.
    return Padding(
        renderable=renderable,
        pad=(
            0,
            0,
            0,
            indent,
        ),
    )


def render_cards(
    *,
    items: Sequence[StudyItem],
    console: Console,
) -> None:
    """
    Print one card per study item, with everything the analysis learned: the
    pronunciation when the dictionary had it, the meaning shown with an
    example and its relations, the sentence from the text, and then, set back,
    the other meanings the word can have.

    Every line after a card's first is indented under it, and the other
    meanings under those.
    """

    for study_item in items:
        console.print()

        style = _TIER_STYLES[study_item.tier]

        source = study_item.level.source.replace(
            "_",
            " ",
        )

        console.print(
            Text.assemble(
                (
                    str(
                        object=study_item.tier,
                    ),
                    style,
                ),
                "  ",
                _plain(
                    study_item.item.lemma,
                    style="bold",
                ),
                f"  {study_item.item.part_of_speech}  {study_item.level.level} ({source})  "
                f"x{study_item.item.occurrence_count}",
            ),
        )

        dictionary = study_item.dictionary

        if dictionary is not None and dictionary.phonetic:
            console.print(
                _indented(
                    _plain(
                        dictionary.phonetic,
                    ),
                    2,
                ),
            )

        console.print(
            _indented(
                _meaning_text(
                    study_item,
                ),
                2,
            ),
        )

        gloss = study_item.gloss

        leading = study_item.leading_sense

        if dictionary is not None and dictionary.definitions and dictionary.definitions[0].example:
            console.print(
                _indented(
                    _plain(
                        dictionary.definitions[0].example,
                        style="italic",
                    ),
                    2,
                ),
            )

        # NOTE:
        # WordNet gives the meaning only when the dictionary gave none, so its example stands in the same place.
        elif (
            gloss is not None
            and gloss.source is GlossSource.LEXICAL_NETWORK
            and leading is not None
            and leading.example
        ):
            console.print(
                _indented(
                    _plain(
                        leading.example,
                        style="italic",
                    ),
                    2,
                ),
            )

        if gloss is not None and gloss.provenance is not None:
            # NOTE:
            # The one line of a card that holds a link, left for the terminal to wrap so that the link stays whole to
            # follow or copy.
            console.print(
                _plain(
                    f"  {
                        provenance_of(
                            provenance=gloss.provenance,
                        )
                    }",
                    style="dim",
                ),
                soft_wrap=True,
            )

        related = _related_of(
            leading,
        )

        if related:
            console.print(
                _indented(
                    _plain(
                        related,
                    ),
                    2,
                ),
            )

        # NOTE:
        # A line break, a tab, or a page break inside a sentence is the layout of the text it came from, which a PDF's
        # extracted text has at the end of every line, so the sentence is shown with its whitespace collapsed, as the
        # header shows a text's first words, and only its other control characters are written out.
        sentence = " ".join(
            study_item.item.example_sentence.split(),
        )

        console.print(
            _indented(
                _plain(
                    f"in the text: {sentence}",
                    style="dim",
                ),
                2,
            ),
        )

        others = _other_meanings(
            study_item,
        )

        if others:
            console.print(
                _indented(
                    Text(
                        text="other meanings, which the text may use instead:",
                        style="dim",
                    ),
                    2,
                ),
            )

            for line in others:
                console.print(
                    _indented(
                        _plain(
                            line,
                            style="dim",
                        ),
                        4,
                    ),
                )


def _left_out_of(
    analysis: Analysis,
    items: Sequence[StudyItem],
    available: int,
    /,
) -> str | None:
    """
    The sentence that says how many of the chosen words are on screen and how
    to see the rest, or nothing when every chosen word is.
    """

    on_screen_count = len(
        items,
    )

    if on_screen_count >= available:
        return None

    totals = tier_counts(
        items=analysis.items,
    )

    shown = tier_counts(
        items=items,
    )

    on_screen = tuple(tier for tier in StudyTier if shown[tier])

    if not on_screen:
        return None

    last = on_screen[-1]

    # NOTE:
    # The rows come in study order and the limit falls in the last tier they reach, so every earlier tier is shown
    # whole.
    earlier = tuple(f"all {shown[tier]:,} {tier.upper()} words" for tier in on_screen[:-1])

    # NOTE:
    # The limit can fall at the very end of the last tier too, which is then as whole as the earlier ones.
    final = (
        f"all {shown[last]:,} {last.upper()} words"
        if shown[last] == totals[last]
        else f"{shown[last]:,} of {totals[last]:,} {last.upper()} words"
    )

    match earlier:
        case []:
            counted = final

        case [only]:
            counted = f"{only} and {final}"

        case _:
            listed = ", ".join(
                earlier,
            )

            counted = f"{listed}, and {final}"

    order = tuple(
        StudyTier,
    )

    position = order.index(
        last,
    )

    following = next(
        (tier for tier in order[position + 1 :] if totals[tier]),
        None,
    )

    if following is None:
        return f"Showing {counted}; --rows raises the limit."

    return f"Showing {counted}; --rows raises the limit, and --tier {following} shows the next tier."


def render_footer(
    *,
    analysis: Analysis,
    items: Sequence[StudyItem],
    available: int,
    remote: bool,
    console: Console,
) -> None:
    """
    Print how many of the `available` words the report left out, when it left
    some, and the command that shows the analysis again.

    `items` are the words the report showed, and `remote` says that the
    command needs `--remote` to reach the services that hold the analysis.
    """

    console.print()

    left_out = _left_out_of(
        analysis,
        items,
        available,
    )

    if left_out is not None:
        console.print(
            left_out,
            style="dim",
            emoji=False,
            markup=False,
            highlight=False,
        )

    flag = " --remote" if remote else ""

    console.print(
        f"wordwinnow show {analysis.id}{flag} shows this again.",
        emoji=False,
        markup=False,
        highlight=False,
    )


def render_listing(
    *,
    summaries: Sequence[AnalysisSummary],
    console: Console,
) -> None:
    """
    Print two lines per analysis: the id and the facts that are not words on
    the first, and the text's label under it.

    The id is what a learner copies into `show`, so it must survive any
    terminal width, and a shell must be able to pick it out.
    """

    # NOTE:
    # A table cannot hold a 36-character id and the other facts in 80 columns: Rich crops the id with an ellipsis or
    # squeezes the label to nothing, so the listing is plain lines, the id and the numbers on one and the label under
    # it, where it wraps inside its own indent.
    if not summaries:
        console.print(
            "No analyses yet.",
        )

        return

    console.print(
        f"{'Id':<36}  {'Requested (UTC)':<16}  {'Level':<5}  {'Status':<9}  {'Items':>5}",
        style="dim",
        soft_wrap=True,
    )

    console.print(
        "    Text",
        style="dim",
    )

    for summary in summaries:
        requested = summary.requested_at.strftime(
            format="%Y-%m-%d %H:%M",
        )

        level = summary.learner_level

        status = summary.status

        items = summary.item_count

        console.print(
            f"{summary.analysis_id}  {requested:<16}  {level:<5}  {status:<9}  {items:>5}",
            highlight=False,
            soft_wrap=True,
        )

        console.print(
            _indented(
                _plain(
                    _label_of(
                        summary.origin,
                        summary.title,
                    ),
                ),
                4,
            ),
        )


def render_vocabulary_summary(
    *,
    summary: VocabularySummary,
    console: Console,
) -> None:
    """
    Print the cross-analysis summary as two tables.
    """

    console.print(
        f"{summary.analysis_count} completed analyses, {summary.fact_count} study items",
    )

    levels = Table(
        title="Items by level",
    )

    levels.add_column(
        header="Level",
    )

    levels.add_column(
        header="Items",
        justify="right",
    )

    for count in summary.levels:
        levels.add_row(
            count.level,
            str(
                object=count.count,
            ),
        )

    console.print(
        levels,
    )

    focus = Table(
        title="Most common focus words",
    )

    for name in (
        "Word",
        "POS",
        "Analyses",
        "Occurrences",
    ):
        focus.add_column(
            header=name,
        )

    for count in summary.focus_lemmas:
        focus.add_row(
            count.lemma,
            count.part_of_speech,
            str(
                object=count.analysis_count,
            ),
            str(
                object=count.occurrence_count,
            ),
        )

    console.print(
        focus,
    )


def to_json(
    *,
    analysis: Analysis,
) -> str:
    """
    The whole analysis as JSON, in the same shape the intake service serves
    it.

    The text and the sentences of a document that may be kept only for a while
    are left out, so the file holds the learner's words, levels, and tiers
    rather than a copy of someone else's text.
    """

    payload = AnalysisOut.from_domain(
        analysis=analysis,
    )

    if analysis.document.expires:
        payload = payload.model_copy(
            update={
                "document": payload.document.model_copy(
                    update={
                        "text": "",
                    },
                ),
                "items": [
                    item.model_copy(
                        update={
                            "example_sentence": "",
                        },
                    )
                    for item in payload.items
                ],
            },
        )

    return payload.model_dump_json(
        indent=2,
    )


def to_csv(
    *,
    items: Sequence[StudyItem],
    with_sentences: bool = True,
) -> str:
    """
    One row per item with the columns a flashcard tool wants.

    Without sentences, the column stays and every cell is empty, which is how
    a document that may be kept only for a while is exported.
    """

    buffer = StringIO()

    rows = writer(
        buffer,
    )

    rows.writerow(
        (
            "tier",
            "lemma",
            "part_of_speech",
            "level",
            "level_source",
            "occurrence_count",
            "example_sentence",
            "meaning",
            "meaning_source",
            "meaning_url",
            "meaning_license",
            "meaning_license_url",
            "phonetic",
            "synonyms",
        ),
    )

    for study_item in items:
        gloss = study_item.gloss

        provenance = gloss.provenance if gloss is not None else None

        dictionary = study_item.dictionary

        rows.writerow(
            (
                study_item.tier,
                study_item.item.lemma,
                study_item.item.part_of_speech,
                study_item.level.level,
                study_item.level.source,
                study_item.item.occurrence_count,
                study_item.item.example_sentence if with_sentences else "",
                gloss.text if gloss is not None else "",
                gloss.source if gloss is not None else "",
                " ".join(
                    provenance.source_urls,
                )
                if provenance is not None
                else "",
                value_object.name
                if provenance is not None and (value_object := provenance.license) is not None
                else "",
                value_object.url
                if provenance is not None and (value_object := provenance.license) is not None
                else "",
                value if dictionary is not None and (value := dictionary.phonetic) else "",
                "; ".join(
                    value_object.synonyms,
                )
                if (value_object := study_item.leading_sense) is not None
                else "",
            ),
        )

    return buffer.getvalue()
