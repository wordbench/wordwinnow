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
from rich.markup import (
    escape,
)
from rich.table import (
    Table,
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
# The tier's name is printed beside every word, so the style only repeats it: the words to study now stand out, the
# ones within reach are marked, and the ones below the learner's level or already known recede.
_TIER_STYLES: Final = MappingProxyType(
    mapping={
        StudyTier.FOCUS: "bold green",
        StudyTier.STRETCH: "yellow",
        StudyTier.REVIEW: "dim",
        StudyTier.KNOWN: "dim strike",
    },
)

_GLOSS_SOURCE_NAMES: Final = MappingProxyType(
    mapping={
        GlossSource.DICTIONARY: "dictionary",
        GlossSource.LEXICAL_NETWORK: "WordNet",
    },
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
) -> str:
    """
    The one line a learner reads as the meaning: the gloss with its source
    marked, or why there is none.
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
            if gloss.source is GlossSource.DICTIONARY
            else f" ({name}; dictionary unavailable)"
            if unavailable
            else f" ({name})"
        )

        return f"{gloss.text}{marker}{source}"

    if dictionary is None:
        return "-"

    if dictionary.outcome is LookupOutcome.UNAVAILABLE:
        return f"dictionary unavailable: {dictionary.failure_reason}"

    return "no definition found"


def _related_of(
    study_item: StudyItem,
    /,
) -> str:
    if not study_item.senses:
        return ""

    sense = study_item.senses[0]

    parts: list[str] = []

    if sense.synonyms:
        parts.append(
            "= "
            + ", ".join(
                sense.synonyms[:3],
            ),
        )

    if sense.hypernyms:
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


def render_summary(
    *,
    analysis: Analysis,
    console: Console,
) -> None:
    """
    Print the analysis header: what was analyzed, for whom, with what result.
    """

    counts = tier_counts(
        items=analysis.items,
    )

    table = Table(
        title=f"Analysis {analysis.id}",
        box=None,
        show_header=False,
    )

    table.add_column(
        header="field",
        style="bold",
    )

    table.add_column(
        header="value",
    )

    table.add_row(
        "Document",
        f"{analysis.document.title} ({analysis.document.origin}: {analysis.document.reference})",
    )

    table.add_row(
        "Learner",
        f"level {analysis.profile.level}, working toward {analysis.profile.target_level}, {
            len(
                analysis.profile.known_lemmas,
            )
        } known words",
    )

    table.add_row(
        "Status",
        analysis.status,
    )

    if analysis.status is AnalysisStatus.FAILED:
        table.add_row(
            "Failure",
            analysis.failure_reason or "",
        )

    if analysis.status is AnalysisStatus.COMPLETED:
        table.add_row(
            "Items",
            ", ".join(
                f"{tier} {count}"
                for (
                    tier,
                    count,
                ) in counts.items()
            ),
        )

        table.add_row(
            "Levels",
            _counted(
                level_source_counts(
                    items=analysis.items,
                ),
            ),
        )

        if analysis.options.include_dictionary:
            table.add_row(
                "Dictionary",
                _counted(
                    lookup_outcome_counts(
                        items=analysis.items,
                    ),
                ),
            )

        wait = analysis.queue_wait_seconds

        stages = " ".join(f"{timing.stage}={timing.seconds:.2f}s" for timing in analysis.stage_timings)

        table.add_row(
            "Timing",
            f"queue wait {wait:.2f}s; {stages}" if wait is not None else stages,
        )

    console.print(
        table,
    )

    render_attribution(
        document=analysis.document,
        console=console,
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
            escape(
                markup=document.attribution,
            ),
            style="bold",
            highlight=False,
        )

    for reference in document.references:
        title = escape(
            markup=reference.title,
        )

        console.print(
            f"  {title}: {reference.url}",
            highlight=False,
            soft_wrap=True,
        )

    if document.expires:
        console.print(
            "This text may be kept for a day; the analysis is deleted after that.",
            style="dim",
        )

    console.print()


def render_items(
    *,
    items: Sequence[StudyItem],
    console: Console,
) -> None:
    """
    Print the study items as one table, in study order.
    """

    table = Table(
        title="Vocabulary",
        show_lines=False,
    )

    for (
        name,
        justify,
    ) in (
        (
            "Tier",
            "left",
        ),
        (
            "Word",
            "left",
        ),
        (
            "POS",
            "left",
        ),
        (
            "Level",
            "left",
        ),
        (
            "Count",
            "right",
        ),
        (
            "Meaning",
            "left",
        ),
        (
            "Related",
            "left",
        ),
    ):
        table.add_column(
            header=name,
            justify=justify,
        )

    for study_item in items:
        style = _TIER_STYLES[study_item.tier]

        source = study_item.level.source.replace(
            "_",
            " ",
        )

        table.add_row(
            f"[{style}]{study_item.tier}[/]",
            f"[{style}]{study_item.item.lemma}[/]",
            study_item.item.part_of_speech,
            f"{study_item.level.level} ({source})",
            str(
                object=study_item.item.occurrence_count,
            ),
            meaning_of(
                study_item=study_item,
            ),
            _related_of(
                study_item,
            ),
        )

    console.print(
        table,
    )

    provenances = tuple(
        provenance
        for study_item in items
        if (gloss := study_item.gloss) is not None and (provenance := gloss.provenance) is not None
    )

    for dictionary_license in dict.fromkeys(
        value_object for provenance in provenances if (value_object := provenance.license) is not None
    ):
        # NOTE:
        # A dictionary with a page per word, as Wiktionary has, gives each definition its own source; one without it,
        # as WordNet is, has only the license to show.
        sourced = any(
            provenance.source_urls for provenance in provenances if provenance.license == dictionary_license
        )

        console.print(
            f"Definitions shown under {
                escape(
                    markup=dictionary_license.name,
                )
            } ({
                escape(
                    markup=dictionary_license.url,
                )
            }){"; --detail gives each one's source" if sourced else ''}.",
            style="dim",
            highlight=False,
            soft_wrap=True,
        )


def render_cards(
    *,
    items: Sequence[StudyItem],
    console: Console,
) -> None:
    """
    Print one card per study item, with everything the analysis learned: the
    pronunciation and the dictionary's example when the dictionary had them,
    the lexical network's relations, and the sentence from the text.
    """

    for study_item in items:
        style = _TIER_STYLES[study_item.tier]

        source = study_item.level.source.replace(
            "_",
            " ",
        )

        lemma = escape(
            markup=study_item.item.lemma,
        )

        console.print(
            f"[{style}]{study_item.tier}[/]  [bold]{lemma}[/]  {study_item.item.part_of_speech}  "
            f"{study_item.level.level} ({source})  x{study_item.item.occurrence_count}",
        )

        dictionary = study_item.dictionary

        if dictionary is not None and dictionary.phonetic:
            phonetic = escape(
                markup=dictionary.phonetic,
            )

            console.print(
                f"  {phonetic}",
            )

        console.print(
            f"  {
                escape(
                    markup=meaning_of(
                        study_item=study_item,
                    ),
                )
            }",
        )

        if dictionary is not None and dictionary.definitions and dictionary.definitions[0].example:
            example = escape(
                markup=dictionary.definitions[0].example,
            )

            console.print(
                f"  [italic]{example}[/]",
            )

        gloss = study_item.gloss

        if gloss is not None and gloss.provenance is not None:
            console.print(
                f"  {
                    escape(
                        markup=provenance_of(
                            provenance=gloss.provenance,
                        ),
                    )
                }",
                style="dim",
                highlight=False,
                soft_wrap=True,
            )

        related = escape(
            markup=_related_of(
                study_item,
            ),
        )

        if related:
            console.print(
                f"  {related}",
            )

        sentence = escape(
            markup=study_item.item.example_sentence,
        )

        console.print(
            f"  [dim]in the text: {sentence}[/]",
        )

        console.print()


def render_listing(
    *,
    summaries: Sequence[AnalysisSummary],
    console: Console,
) -> None:
    """
    Print one line per analysis, the id first.

    The id is what a learner copies into `show`, so it must survive any
    terminal width, and a shell must be able to pick it out.
    """

    # NOTE:
    # A table cannot hold a 36-character id and the other six facts in 80 columns: Rich crops the id with an ellipsis
    # or squeezes the title to nothing, so the listing is plain lines that the terminal wraps as it can.
    if not summaries:
        console.print(
            "No analyses yet.",
        )

        return

    console.print(
        f"{'Id':<36}  {'Requested':<16}  {'Level':<5}  {'Status':<9}  {'Items':>5}  Document",
        style="dim",
        soft_wrap=True,
    )

    for summary in summaries:
        requested = summary.requested_at.strftime(
            format="%Y-%m-%d %H:%M",
        )

        level = summary.learner_level

        status = summary.status

        title = escape(
            markup=summary.title,
        )

        origin = summary.origin

        items = summary.item_count

        console.print(
            f"{summary.analysis_id}  {requested:<16}  {level:<5}  {status:<9}  {items:>5}  {title} ({origin})",
            highlight=False,
            soft_wrap=True,
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
                    value_objects[0].synonyms,
                )
                if (value_objects := study_item.senses)
                else "",
            ),
        )

    return buffer.getvalue()
