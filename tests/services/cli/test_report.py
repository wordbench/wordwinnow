"""
The report a learner reads.
"""

from collections.abc import (
    Callable,
)
from csv import (
    reader,
)
from dataclasses import (
    replace,
)
from datetime import (
    UTC,
    datetime,
    timedelta,
)
from functools import (
    partial,
)
from json import (
    loads,
)
from pathlib import (
    Path,
)
from re import (
    search,
)
from types import (
    MappingProxyType,
)
from typing import (
    Final,
    final,
)

from pytest import (
    MonkeyPatch,
    fixture,
    mark,
)
from rich.console import (
    Console,
)
from typer.testing import (
    CliRunner,
)

from wordwinnow.application.dto import (
    AnalysisSummary,
)
from wordwinnow.domain.analysis import (
    Analysis,
    AnalysisOptions,
    AnalysisStatus,
    Stage,
    StageTiming,
    reconstitute,
    request_an_analysis,
)
from wordwinnow.domain.cefr import (
    CefrLevel,
    LevelAssessment,
    LevelSource,
)
from wordwinnow.domain.dictionary import (
    License,
    LookupOutcome,
    Provenance,
    SelectedDefinition,
)
from wordwinnow.domain.document import (
    Document,
    DocumentOrigin,
    Reference,
)
from wordwinnow.domain.identifiers import (
    AnalysisId,
)
from wordwinnow.domain.language import (
    PartOfSpeech,
)
from wordwinnow.domain.learner import (
    LearnerProfile,
)
from wordwinnow.domain.senses import (
    LexicalSense,
)
from wordwinnow.domain.study import (
    DictionaryInformation,
    StudyItem,
    StudyTier,
)
from wordwinnow.domain.vocabulary import (
    VocabularyItem,
)
from wordwinnow.infrastructure.dictionary.wire import (
    FailureReason,
)
from wordwinnow.infrastructure.settings import (
    Settings,
)
from wordwinnow.services.cli import (
    commands,
    main,
)
from wordwinnow.services.cli.intake_client import (
    WatchedAnalysis,
)
from wordwinnow.services.cli.main import (
    app,
)
from wordwinnow.services.cli.report import (
    meaning_of,
    render_cards,
    render_figures,
    render_footer,
    render_items,
    render_listing,
    render_summary,
    select_items,
    tier_counts,
    to_csv,
    to_json,
    visible,
)
from wordwinnow.services.intake.schemas import (
    AnalysisOut,
    AnalysisRequest,
)
from wordwinnow.services.progress import (
    UNWATCHED,
    AnalysisProgress,
    DictionaryWatch,
)

_EPOCH: Final = datetime(
    year=1970,
    month=1,
    day=1,
    tzinfo=UTC,
)

_PROFILE: Final = LearnerProfile(
    level=CefrLevel.B1,
    target_level=CefrLevel.B2,
)


# NOTE:
# The lemma, its tier, and the level that put it there are what a case is about; the dictionary and the senses are
# absent unless a case is about them.
def _item(
    lemma: str,
    tier: StudyTier,
    level: CefrLevel,
    /,
    *,
    example_sentence: str,
    dictionary: DictionaryInformation | None = None,
    senses: tuple[LexicalSense, ...] = (),
) -> StudyItem:
    return StudyItem(
        item=VocabularyItem(
            lemma=lemma,
            part_of_speech=PartOfSpeech.NOUN,
            occurrence_count=2,
            example_sentence=example_sentence,
        ),
        zipf_frequency=4.0,
        level=LevelAssessment(
            level=level,
            source=LevelSource.REFERENCE_LIST,
        ),
        tier=tier,
        dictionary=dictionary,
        senses=senses,
    )


_FOUND: Final = DictionaryInformation(
    outcome=LookupOutcome.FOUND,
    failure_reason=None,
    phonetic="/mɑːsk/",
    definitions=(
        SelectedDefinition(
            text="A covering that hides the face.",
            example=None,
            part_of_speech_matched=True,
            provenance=Provenance(
                license=License(
                    name="CC BY-SA 3.0",
                    url="https://creativecommons.org/licenses/by-sa/3.0",
                ),
                source_urls=("https://example.com/mask",),
            ),
        ),
    ),
)

_UNAVAILABLE: Final = DictionaryInformation(
    outcome=LookupOutcome.UNAVAILABLE,
    failure_reason="timed_out",
    phonetic=None,
    definitions=(),
)

# NOTE:
# WordNet 3.0's first sense of `treachery`, with its real relations.
_PERFIDY: Final = LexicalSense(
    key="perfidy.n.01",
    part_of_speech=PartOfSpeech.NOUN,
    gloss="betrayal of a trust",
    example=None,
    synonyms=(
        "perfidy",
        "perfidiousness",
    ),
    hypernyms=("disloyalty",),
    antonyms=(),
    usage_count=1,
    hyponym_count=1,
    category="noun.attribute",
)

# NOTE:
# WordNet 3.0's two senses of `landau`, in its own order: the physicist first, though the story means the carriage.
_LANDAU_THE_PHYSICIST: Final = LexicalSense(
    key="landau.n.01",
    part_of_speech=PartOfSpeech.NOUN,
    gloss="Soviet physicist who worked on low temperature physics (1908-1968)",
    example=None,
    synonyms=("Lev Davidovich Landau",),
    hypernyms=(),
    antonyms=(),
    usage_count=0,
    hyponym_count=0,
    category="noun.person",
    is_instance=True,
)

_LANDAU_THE_CARRIAGE: Final = LexicalSense(
    key="landau.n.02",
    part_of_speech=PartOfSpeech.NOUN,
    gloss=(
        "a four-wheel covered carriage with a roof divided into two parts (front and back) that can be let down "
        "separately"
    ),
    example=None,
    synonyms=(),
    hypernyms=("carriage",),
    antonyms=(),
    usage_count=0,
    hyponym_count=0,
    category="noun.artifact",
)

# NOTE:
# Watson's own sentence from the story, the one place it uses the word.
_TREACHERY_SENTENCE: Final = (
    "And yet it would be the blackest treachery to Holmes to draw back now from the part which he had entrusted to "
    "me."
)

_LANDAU: Final = StudyItem(
    item=VocabularyItem(
        lemma="landau",
        part_of_speech=PartOfSpeech.NOUN,
        occurrence_count=4,
        example_sentence="Holmes saw a neat little landau, the coachman with his coat only half-buttoned.",
    ),
    zipf_frequency=2.1,
    level=LevelAssessment(
        level=CefrLevel.C2,
        source=LevelSource.MODEL,
    ),
    tier=StudyTier.STRETCH,
    dictionary=_UNAVAILABLE,
    senses=(
        _LANDAU_THE_PHYSICIST,
        _LANDAU_THE_CARRIAGE,
    ),
)

_ITEMS: Final = (
    _item(
        "mask",
        StudyTier.FOCUS,
        CefrLevel.B2,
        example_sentence="The King wore a black mask.",
        dictionary=_FOUND,
    ),
    _item(
        "treachery",
        StudyTier.STRETCH,
        CefrLevel.C2,
        example_sentence=_TREACHERY_SENTENCE,
        dictionary=_UNAVAILABLE,
        senses=(_PERFIDY,),
    ),
    _item(
        "window",
        StudyTier.REVIEW,
        CefrLevel.A1,
        example_sentence="Holmes glanced out of the window.",
        dictionary=_UNAVAILABLE,
    ),
    _item(
        "photograph",
        StudyTier.KNOWN,
        CefrLevel.A2,
        example_sentence="Irene Adler kept the photograph.",
    ),
)


_DOCUMENT: Final = Document(
    title="a-scandal-in-bohemia",
    text="Irene Adler kept the photograph.",
    origin=DocumentOrigin.CUSTOM_TEXT,
    reference="a-scandal-in-bohemia.txt",
)


def _completed(
    *,
    document: Document = _DOCUMENT,
    include_dictionary: bool = True,
    items: tuple[StudyItem, ...] = _ITEMS,
    profile: LearnerProfile = _PROFILE,
) -> Analysis:
    analysis = request_an_analysis(
        document=document,
        profile=profile,
        options=AnalysisOptions(
            include_dictionary=include_dictionary,
        ),
        at=_EPOCH,
    )

    analysis.start(
        at=_EPOCH,
    )

    analysis.complete(
        at=_EPOCH,
        items=items,
        stage_timings=(
            StageTiming(
                stage=Stage.WINNOWING,
                seconds=0.5,
            ),
        ),
    )

    return analysis


def _unfinished(
    *,
    status: AnalysisStatus,
    reason: str = "RuntimeError: foo",
) -> Analysis:
    analysis = request_an_analysis(
        document=_DOCUMENT,
        profile=_PROFILE,
        options=AnalysisOptions(),
        at=_EPOCH,
    )

    if status is not AnalysisStatus.REQUESTED:
        analysis.start(
            at=_EPOCH,
        )

    if status is AnalysisStatus.FAILED:
        analysis.fail(
            at=_EPOCH,
            reason=reason,
        )

    return analysis


def _words(
    text: str,
    /,
) -> str:
    return " ".join(
        text.split(),
    )


def _render(
    analysis: Analysis,
    /,
    *,
    remote: bool = False,
    width: int = 200,
) -> str:
    """
    What a learner reads for `analysis` at `width`: the header, the tables,
    and the footer.
    """

    console = Console(
        width=width,
        record=True,
    )

    render_summary(
        analysis=analysis,
        remote=remote,
        console=console,
    )

    render_items(
        analysis=analysis,
        items=analysis.items,
        console=console,
    )

    render_footer(
        analysis=analysis,
        items=analysis.items,
        available=len(
            analysis.items,
        ),
        remote=remote,
        console=console,
    )

    return console.export_text()


def _figures(
    analysis: Analysis,
    /,
) -> str:
    console = Console(
        width=200,
        record=True,
    )

    render_figures(
        analysis=analysis,
        console=console,
    )

    return console.export_text()


@final
class TestSelection:
    def test_tiers_are_counted_and_selected(
        self,
    ) -> None:
        counts = tier_counts(
            items=_ITEMS,
        )

        assert counts[StudyTier.FOCUS] == 1

        assert counts[StudyTier.REVIEW] == 1

        assert tuple(
            study_item.item.lemma
            for study_item in select_items(
                items=_ITEMS,
                tiers=(
                    StudyTier.FOCUS,
                    StudyTier.STRETCH,
                ),
                limit=1,
            )
        ) == ("mask",)


@final
class TestRendering:
    def test_the_report_shows_the_meaning_the_fallback_gloss_and_the_failure(
        self,
    ) -> None:
        analysis = _completed()

        text = _render(
            analysis,
        )

        assert "A covering that hides the face." in text

        assert "betrayal of a trust (WordNet; dictionary unavailable)" in text

        # NOTE:
        # The table has no column of related words, which the cards hold, so a word's relations are not in it.
        assert "perfidiousness" not in text

        assert "unknown: the dictionary was unavailable (timed out)" in text

        assert "timed_out" not in text

        assert "2 words to study: 1 FOCUS, 1 STRETCH; 1 REVIEW, below B1" in _words(
            text,
        )

        assert "B1, working toward B2, no known words" in _words(
            text,
        )

        figures = _figures(
            analysis,
        )

        assert "winnowing=0.50s" in figures

        assert "reference list 4, model 0, frequency heuristic 0" in figures

        assert "asked about 3 of 4 items: found 1, not found 0, unavailable 2" in figures

        assert (
            "Definitions shown under CC BY-SA 3.0 (https://creativecommons.org/licenses/by-sa/3.0); --detail gives "
            "each one's source."
        ) in text

    def test_the_dictionary_line_is_absent_when_the_analysis_did_not_ask_the_dictionary(
        self,
    ) -> None:
        analysis = _completed(
            include_dictionary=False,
        )

        text = _render(
            analysis,
        )

        assert "2 words to study: 1 FOCUS, 1 STRETCH; 1 REVIEW, below B1" in _words(
            text,
        )

        assert "asked about" not in _figures(
            analysis,
        )

        assert "Dictionary" not in text

    def test_a_dictionary_without_a_page_per_word_is_credited_by_its_license_alone(
        self,
    ) -> None:
        console = Console(
            width=200,
            record=True,
        )

        render_items(
            analysis=_completed(),
            items=(
                _item(
                    "mask",
                    StudyTier.FOCUS,
                    CefrLevel.B2,
                    example_sentence="The King wore a black mask.",
                    dictionary=DictionaryInformation(
                        outcome=LookupOutcome.FOUND,
                        failure_reason=None,
                        phonetic=None,
                        definitions=(
                            SelectedDefinition(
                                text="a covering to disguise or conceal the face",
                                example=None,
                                part_of_speech_matched=True,
                                provenance=Provenance(
                                    license=License(
                                        name="WordNet 3.0 license",
                                        url="https://wordnet.princeton.edu/license-and-commercial-use",
                                    ),
                                ),
                            ),
                        ),
                    ),
                ),
            ),
            console=console,
        )

        assert (
            "Definitions shown under WordNet 3.0 license (https://wordnet.princeton.edu/license-and-commercial-use)."
        ) in console.export_text()

    def test_a_refused_lookup_of_a_word_wordnet_lacks_reads_as_unknown_and_never_as_its_meaning(
        self,
    ) -> None:
        # NOTE:
        # The row dogfooding found: a word WordNet does not hold, looked up while the dictionary's circuit was open,
        # so neither the dictionary nor WordNet gives it a meaning.
        refused = _item(
            "extraordinaires",
            StudyTier.FOCUS,
            CefrLevel.C2,
            example_sentence="The scientists were extraordinaires.",
            dictionary=DictionaryInformation(
                outcome=LookupOutcome.UNAVAILABLE,
                failure_reason=FailureReason.CIRCUIT_OPEN,
                phonetic=None,
                definitions=(),
            ),
        )

        for render in (
            partial(
                render_items,
                analysis=_completed(),
            ),
            render_cards,
        ):
            console = Console(
                width=200,
                record=True,
            )

            render(
                items=(refused,),
                console=console,
            )

            text = console.export_text(
                clear=False,
            )

            assert "unknown: the dictionary was unavailable (circuit open)" in text

            assert "circuit_open" not in text

            assert "dictionary unavailable" not in text

            # NOTE:
            # Set apart from any meaning: the line is dim and italic, whatever the highlighter makes of its
            # parentheses.
            assert (
                '<span style="color: #7f7f7f; text-decoration-color: #7f7f7f; font-style: italic">unknown: the '
                "dictionary was unavailable"
            ) in console.export_html(
                inline_styles=True,
            )

    def test_every_reason_a_lookup_went_unanswered_is_said_in_words_as_an_unknown_meaning(
        self,
    ) -> None:
        said = {
            FailureReason.CIRCUIT_OPEN: "circuit open",
            FailureReason.TIMED_OUT: "timed out",
            FailureReason.TRANSPORT_ERROR: "transport error",
            FailureReason.RATE_LIMITED: "rate limited",
            FailureReason.PROVIDER_ERROR: "provider error",
            FailureReason.INVALID_RESPONSE: "invalid response",
        }

        assert said.keys() == frozenset(
            FailureReason,
        )

        for (
            reason,
            words,
        ) in said.items():
            assert (
                meaning_of(
                    study_item=_item(
                        "window",
                        StudyTier.REVIEW,
                        CefrLevel.A1,
                        example_sentence="Holmes glanced out of the window.",
                        dictionary=DictionaryInformation(
                            outcome=LookupOutcome.UNAVAILABLE,
                            failure_reason=reason,
                            phonetic=None,
                            definitions=(),
                        ),
                    ),
                )
                == f"unknown: the dictionary was unavailable ({words})"
            )

    def test_a_missing_entry_an_unavailable_dictionary_and_no_dictionary_each_read_differently(
        self,
    ) -> None:
        def meaning(
            dictionary: DictionaryInformation | None,
            /,
        ) -> str:
            return meaning_of(
                study_item=_item(
                    "window",
                    StudyTier.REVIEW,
                    CefrLevel.A1,
                    example_sentence="Holmes glanced out of the window.",
                    dictionary=dictionary,
                ),
            )

        assert (
            meaning(
                DictionaryInformation(
                    outcome=LookupOutcome.NOT_FOUND,
                    failure_reason=None,
                    phonetic=None,
                    definitions=(),
                ),
            ),
            meaning(
                _UNAVAILABLE,
            ),
            meaning(
                None,
            ),
        ) == (
            "no definition found",
            "unknown: the dictionary was unavailable (timed out)",
            "no noun meaning in WordNet",
        )

    def test_a_definition_keeps_its_square_brackets_in_the_table_and_on_the_card(
        self,
    ) -> None:
        bracketed = _item(
            "mask",
            StudyTier.FOCUS,
            CefrLevel.B2,
            example_sentence="The King wore a black mask.",
            dictionary=DictionaryInformation(
                outcome=LookupOutcome.FOUND,
                failure_reason=None,
                phonetic=None,
                definitions=(
                    SelectedDefinition(
                        text="A covering [chiefly for the face] that hides who wears it.",
                        example=None,
                        part_of_speech_matched=True,
                    ),
                ),
            ),
        )

        for render in (
            partial(
                render_items,
                analysis=_completed(),
            ),
            render_cards,
        ):
            console = Console(
                width=200,
                record=True,
            )

            render(
                items=(bracketed,),
                console=console,
            )

            assert "A covering [chiefly for the face] that hides who wears it." in console.export_text()

    def test_an_unavailable_lookup_exports_an_empty_meaning_and_keeps_its_outcome_typed(
        self,
    ) -> None:
        analysis = _completed()

        window = loads(
            s=to_json(
                analysis=analysis,
            ),
        )["items"][2]

        assert (
            window["lemma"],
            window["gloss"],
            window["dictionary"]["outcome"],
            window["dictionary"]["failure_reason"],
        ) == (
            "window",
            None,
            "unavailable",
            "timed_out",
        )

        (
            header,
            *rows,
        ) = reader(
            to_csv(
                items=analysis.items,
            ).splitlines(),
        )

        row = dict(
            zip(
                header,
                rows[2],
                strict=True,
            ),
        )

        assert (
            row["lemma"],
            row["meaning"],
            row["meaning_source"],
        ) == (
            "window",
            "",
            "",
        )

        assert not any("timed" in cell or "unavailable" in cell for cell in rows[2])

    def test_the_cards_show_the_pronunciation_the_relations_and_the_sentence(
        self,
    ) -> None:
        console = Console(
            width=200,
            record=True,
        )

        render_cards(
            items=_ITEMS,
            console=console,
        )

        text = console.export_text()

        assert "/mɑːsk/" in text

        assert "= perfidy, perfidiousness; < disloyalty" in text

        assert (
            "from https://example.com/mask under CC BY-SA 3.0 (https://creativecommons.org/licenses/by-sa/3.0)"
        ) in text

        assert f"in the text: {_TREACHERY_SENTENCE}" in text

    def test_a_card_leads_with_a_kind_of_thing_and_lists_the_other_meanings_after_the_sentence(
        self,
    ) -> None:
        console = Console(
            width=300,
            record=True,
        )

        render_cards(
            items=(_LANDAU,),
            console=console,
        )

        text = console.export_text()

        assert (
            text.index(
                "a four-wheel covered carriage",
            )
            < text.index(
                "in the text: Holmes saw a neat little landau",
            )
            < text.index(
                "other meanings, which the text may use instead:",
            )
            < text.index(
                "Soviet physicist",
            )
        )

        assert "< carriage" in text

        assert (
            "Soviet physicist who worked on low temperature physics (1908-1968) (WordNet; a name)  "
            "= Lev Davidovich Landau"
        ) in text

    def test_the_dictionary_s_further_definitions_are_listed_with_their_source_and_part_of_speech(
        self,
    ) -> None:
        console = Console(
            width=200,
            record=True,
        )

        render_cards(
            items=(
                _item(
                    "mask",
                    StudyTier.FOCUS,
                    CefrLevel.B2,
                    example_sentence="The King wore a black mask.",
                    dictionary=replace(
                        _FOUND,
                        definitions=(
                            *_FOUND.definitions,
                            SelectedDefinition(
                                text="To hide the face with a covering.",
                                example=None,
                                part_of_speech_matched=False,
                            ),
                        ),
                    ),
                    senses=(_PERFIDY,),
                ),
            ),
            console=console,
        )

        text = console.export_text()

        assert "A covering that hides the face." in text

        assert "    To hide the face with a covering. (other part of speech) (dictionary)" in text

        assert "betrayal of a trust" not in text

    def test_the_table_says_once_that_the_cards_hold_the_other_meanings(
        self,
    ) -> None:
        hint = "Each word shows one meaning, which may not be the one the text uses; --detail lists the others."

        with_others = Console(
            width=200,
            record=True,
        )

        render_items(
            analysis=_completed(),
            items=(
                _LANDAU,
                _LANDAU,
            ),
            console=with_others,
        )

        without_others = Console(
            width=200,
            record=True,
        )

        render_items(
            analysis=_completed(),
            items=_ITEMS,
            console=without_others,
        )

        assert (
            with_others.export_text().count(
                hint,
            )
            == 1
        )

        assert hint not in without_others.export_text()

    def test_a_borrowed_text_is_attributed_linked_and_exported_without_its_sentences(
        self,
    ) -> None:
        analysis = _completed()

        borrowed = reconstitute(
            id=analysis.id,
            document=Document(
                title=analysis.document.title,
                text=analysis.document.text,
                origin=DocumentOrigin.NEW_YORK_TIMES,
                reference="rss/Science",
                attribution="Data provided by The New York Times",
                references=(
                    Reference(
                        title="A cab waits at the door",
                        url="https://example.com/photograph",
                    ),
                ),
                retention=timedelta(
                    hours=24,
                ),
            ),
            profile=analysis.profile,
            options=analysis.options,
            requested_at=analysis.requested_at,
            status=analysis.status,
            started_at=analysis.started_at,
            finished_at=analysis.finished_at,
            failure_reason=None,
            stage_timings=analysis.stage_timings,
            items=analysis.items,
        )

        text = _render(
            borrowed,
        )

        assert "Data provided by The New York Times" in text

        assert "A cab waits at the door: https://example.com/photograph" in text

        assert "kept for a day" in text

        payload = loads(
            s=to_json(
                analysis=borrowed,
            ),
        )

        assert payload["document"]["text"] == ""

        assert payload["document"]["attribution"] == "Data provided by The New York Times"

        assert frozenset(item["example_sentence"] for item in payload["items"]) == frozenset(
            ("",),
        )

        assert tuple(item["lemma"] for item in payload["items"]) == (
            "mask",
            "treachery",
            "window",
            "photograph",
        )

        csv_text = to_csv(
            items=borrowed.items,
            with_sentences=False,
        )

        assert _TREACHERY_SENTENCE not in csv_text

        assert "mask" in csv_text

        assert "Data provided by The New York Times" not in _render(
            analysis,
        )

    def test_json_and_csv_carry_every_item(
        self,
    ) -> None:
        analysis = _completed()

        payload = loads(
            s=to_json(
                analysis=analysis,
            ),
        )

        assert payload["status"] == "completed"

        assert tuple(entry["lemma"] for entry in payload["items"]) == (
            "mask",
            "treachery",
            "window",
            "photograph",
        )

        assert payload["items"][1]["dictionary"]["failure_reason"] == "timed_out"

        assert payload["items"][1]["gloss"]["source"] == "lexical_network"

        assert payload["profile"]["target_level"] == "B2"

        landau = _completed(
            items=(_LANDAU,),
        )

        landau_payload = loads(
            s=to_json(
                analysis=landau,
            ),
        )

        assert tuple(sense["is_instance"] for sense in landau_payload["items"][0]["senses"]) == (
            True,
            False,
        )

        assert (
            AnalysisOut.model_validate(
                obj=landau_payload,
            ).to_domain()
            == landau
        )

        assert (
            AnalysisOut.model_validate(
                obj=payload,
            ).to_domain()
            == analysis
        )

        csv_text = to_csv(
            items=analysis.items,
        )

        lines = csv_text.splitlines()

        assert lines[0].startswith(
            "tier,lemma,",
        )

        assert (
            len(
                lines,
            )
            == 5
        )

        assert "lexical_network" in lines[2]

        assert "meaning,meaning_source,meaning_url,meaning_license,meaning_license_url,phonetic" in lines[0]

        assert lines[1].endswith(
            ",A covering that hides the face.,dictionary,https://example.com/mask,CC BY-SA 3.0,"
            "https://creativecommons.org/licenses/by-sa/3.0,/mɑːsk/,",
        )


# NOTE:
# Words of three letters or fewer, so that a meaning of them can wrap in the narrowest column the tests draw without
# any word of it folding; the lemma is longer than every column of the table but the meaning's.
_LONG_MEANING: Final = " ".join(
    (
        "foo",
        "bar",
        "baz",
        "qux",
    )
    * 12,
)

# NOTE:
# The multiplication sign before a count, spelled by name because the linter's confusables rule reads the character as
# the letter x.
_TIMES: Final = "\N{MULTIPLICATION SIGN}"

_LONG_LEMMA: Final = "foobarbazquxfoobar"

_WIDTHS: Final = (
    60,
    80,
    100,
    120,
)

_PLAIN_KNOWN: Final = LearnerProfile(
    level=CefrLevel.B1,
    target_level=CefrLevel.B2,
    known_lemmas=frozenset(
        ("foo",),
    ),
)


def _sense(
    gloss: str,
    /,
    *,
    example: str | None = None,
) -> LexicalSense:
    return LexicalSense(
        key="foo.n.01",
        part_of_speech=PartOfSpeech.NOUN,
        gloss=gloss,
        example=example,
        synonyms=(),
        hypernyms=(),
        antonyms=(),
        usage_count=1,
        hyponym_count=0,
        category="noun.attribute",
    )


def _from(
    study_item: StudyItem,
    source: LevelSource,
    /,
) -> StudyItem:
    return replace(
        study_item,
        level=LevelAssessment(
            level=study_item.level.level,
            source=source,
        ),
    )


def _defined(
    text: str,
    /,
    *,
    provenance: Provenance | None = None,
) -> DictionaryInformation:
    return DictionaryInformation(
        outcome=LookupOutcome.FOUND,
        failure_reason=None,
        phonetic=None,
        definitions=(
            SelectedDefinition(
                text=text,
                example=None,
                part_of_speech_matched=True,
                provenance=provenance,
            ),
        ),
    )


# NOTE:
# Every tier, every level source, a lemma and a meaning longer than the other columns, a dictionary that answered with
# a link to its source, one that did not, and a word nothing has a meaning for.
_EVERYTHING: Final = (
    _item(
        _LONG_LEMMA,
        StudyTier.FOCUS,
        CefrLevel.B2,
        example_sentence=_LONG_MEANING,
        dictionary=_defined(
            _LONG_MEANING,
            provenance=Provenance(
                license=License(
                    name="foo",
                    url="https://example.com/license",
                ),
                source_urls=("https://example.com/foo",),
            ),
        ),
        senses=(
            _sense(
                _LONG_MEANING,
            ),
            _sense(
                _LONG_MEANING,
            ),
        ),
    ),
    _from(
        _item(
            "bar",
            StudyTier.FOCUS,
            CefrLevel.B2,
            example_sentence=_LONG_MEANING,
            dictionary=_UNAVAILABLE,
            senses=(
                _sense(
                    _LONG_MEANING,
                ),
            ),
        ),
        LevelSource.MODEL,
    ),
    _from(
        _item(
            "baz",
            StudyTier.STRETCH,
            CefrLevel.C1,
            example_sentence="foo bar baz",
            dictionary=_defined(
                "foo bar",
            ),
        ),
        LevelSource.FREQUENCY_HEURISTIC,
    ),
    _item(
        "qux",
        StudyTier.REVIEW,
        CefrLevel.A1,
        example_sentence="foo bar baz",
        senses=(
            _sense(
                "foo bar baz",
            ),
        ),
    ),
    _item(
        "foo",
        StudyTier.KNOWN,
        CefrLevel.A2,
        example_sentence="foo bar baz",
    ),
)


def _summaries(
    *,
    titles: tuple[tuple[str, DocumentOrigin], ...],
) -> tuple[AnalysisSummary, ...]:
    return tuple(
        AnalysisSummary(
            analysis_id=AnalysisId.new(),
            title=title,
            origin=origin,
            learner_level=CefrLevel.A2,
            status=AnalysisStatus.COMPLETED,
            requested_at=_EPOCH,
            item_count=1508,
        )
        for (
            title,
            origin,
        ) in titles
    )


def _widest(
    text: str,
    /,
) -> int:
    """
    How wide the widest line of `text` is, a line that holds a link left out,
    since the report lets the terminal wrap that one.
    """

    return max(
        (
            len(
                line.rstrip(),
            )
            for line in text.splitlines()
            if "://" not in line
        ),
        default=0,
    )


def _first_column(
    text: str,
    /,
) -> str:
    """
    The first column of a table of one word, its pieces joined: the part of
    each line between the table's left edge and the part of speech.
    """

    lines = tuple(
        line
        for line in text.splitlines()
        if line.startswith(
            " ",
        )
    )

    first = next(line for line in lines if " noun " in line)

    width = (
        first.index(
            " noun ",
        )
        - 2
    )

    return "".join(line[1 : 1 + width].strip() for line in lines)


@final
class TestNothingIsCut:
    @mark.parametrize(
        argnames="width",
        argvalues=_WIDTHS,
    )
    @mark.parametrize(
        argnames="render",
        argvalues=(
            partial(
                render_items,
                analysis=_completed(
                    items=_EVERYTHING,
                    profile=_PLAIN_KNOWN,
                ),
            ),
            render_cards,
        ),
        ids=(
            "table",
            "cards",
        ),
    )
    def test_no_cell_ends_in_an_ellipsis_and_no_word_is_lost_at_four_widths(
        self,
        *,
        render: Callable[..., None],
        width: int,
    ) -> None:
        console = Console(
            width=width,
            record=True,
        )

        render(
            items=_EVERYTHING,
            console=console,
        )

        text = console.export_text()

        assert "…" not in text

        collapsed = _words(
            text,
        )

        assert _LONG_LEMMA in collapsed

        assert all(word in collapsed for word in _LONG_MEANING.split())

        assert _LONG_MEANING in collapsed

        if width >= 80:
            assert (
                _widest(
                    text,
                )
                <= width
            )

    def test_a_word_longer_than_the_line_folds_and_keeps_every_character(
        self,
    ) -> None:
        lemma = "foo" * 34

        console = Console(
            width=60,
            record=True,
        )

        render_items(
            analysis=_completed(),
            items=(
                _item(
                    lemma,
                    StudyTier.FOCUS,
                    CefrLevel.B2,
                    example_sentence="foo bar baz",
                    senses=(
                        _sense(
                            _LONG_MEANING,
                        ),
                    ),
                ),
            ),
            console=console,
        )

        text = console.export_text()

        assert "…" not in text

        assert (
            _first_column(
                text,
            )
            == lemma
        )

        assert (
            _widest(
                text,
            )
            <= 60
        )


def _header(
    analysis: Analysis,
    /,
    *,
    remote: bool = False,
) -> str:
    console = Console(
        width=200,
        record=True,
    )

    render_summary(
        analysis=analysis,
        remote=remote,
        console=console,
    )

    return _words(
        console.export_text(),
    )


def _marked_table(
    sources: tuple[LevelSource, ...],
    /,
) -> str:
    """
    A table of one word for each of `sources`, as its level came from it.
    """

    items = tuple(
        _from(
            _item(
                "foo",
                StudyTier.FOCUS,
                CefrLevel.B2,
                example_sentence="foo",
            ),
            source,
        )
        for source in sources
    )

    console = Console(
        width=200,
        record=True,
    )

    render_items(
        analysis=_completed(
            items=items,
        ),
        items=items,
        console=console,
    )

    return console.export_text()


def _read_from_standard_input(
    text: str,
    /,
) -> Document:
    return Document(
        title="standard input",
        text=text,
        origin=DocumentOrigin.CUSTOM_TEXT,
        reference="stdin",
    )


@final
class TestTheHeader:
    def test_a_text_from_standard_input_is_named_by_its_first_nine_words(
        self,
    ) -> None:
        ten = "foo bar baz foo bar baz foo bar baz qux"

        nine = "foo bar baz foo bar baz foo bar baz"

        beginning = _header(
            _completed(
                document=_read_from_standard_input(
                    ten,
                ),
            ),
        )

        whole = _header(
            _completed(
                document=_read_from_standard_input(
                    nine,
                ),
            ),
        )

        short = _header(
            _completed(
                document=_read_from_standard_input(
                    "foo\nbar",
                ),
            ),
        )

        assert beginning.startswith(
            f"Text read from standard input, beginning “{nine}” Learner",
        )

        assert whole.startswith(
            f"Text read from standard input, “{nine}” Learner",
        )

        assert short.startswith(
            "Text read from standard input, “foo bar” Learner",
        )

        assert "custom_text" not in beginning

        assert "stdin" not in beginning

    def test_a_file_is_named_by_its_title_and_never_by_its_origin(
        self,
    ) -> None:
        header = _header(
            _completed(),
        )

        assert header.startswith(
            "Text a-scandal-in-bohemia Learner",
        )

        assert "custom_text" not in header

        assert "a-scandal-in-bohemia.txt" not in header

    def test_a_borrowed_text_keeps_its_attribution_and_links_each_whole_on_a_line(
        self,
    ) -> None:
        link = "https://example.com/" + "foo" * 40

        borrowed = _completed(
            document=Document(
                title="foo",
                text="bar",
                origin=DocumentOrigin.NEW_YORK_TIMES,
                reference="rss/bar",
                attribution="Data provided by The New York Times",
                references=(
                    Reference(
                        title="bar",
                        url=link,
                    ),
                ),
                retention=timedelta(
                    hours=24,
                ),
            ),
        )

        console = Console(
            width=80,
            record=True,
        )

        render_summary(
            analysis=borrowed,
            remote=False,
            console=console,
        )

        lines = tuple(line.rstrip() for line in console.export_text().splitlines())

        assert "Data provided by The New York Times" in lines

        assert f"  bar: {link}" in lines

        assert "This text may be kept for a day; the analysis is deleted after that." in lines

    @mark.parametrize(
        argnames=(
            "known",
            "said",
        ),
        argvalues=(
            (
                (),
                "no known words",
            ),
            (
                ("foo",),
                "1 known word",
            ),
            (
                (
                    "foo",
                    "bar",
                ),
                "2 known words",
            ),
        ),
    )
    def test_the_learner_line_counts_the_known_words(
        self,
        *,
        known: tuple[str, ...],
        said: str,
    ) -> None:
        header = _header(
            _completed(
                profile=LearnerProfile(
                    level=CefrLevel.B1,
                    target_level=CefrLevel.B2,
                    known_lemmas=frozenset(
                        known,
                    ),
                ),
            ),
        )

        assert f"Learner B1, working toward B2, {said}" in header

    def test_found_counts_the_words_to_study_and_names_the_known_words_only_when_there_are_some(
        self,
    ) -> None:
        without = _header(
            _completed(),
        )

        with_known = _header(
            _completed(
                profile=_PLAIN_KNOWN,
            ),
        )

        assert "Found 2 words to study: 1 FOCUS, 1 STRETCH; 1 REVIEW, below B1" in without

        assert "KNOWN" not in without

        assert (
            "Found 2 words to study: 1 FOCUS, 1 STRETCH; 1 REVIEW, below B1; 1 KNOWN, words you said you know"
            in with_known
        )

    def test_found_says_one_word_to_study_and_writes_thousands_with_a_comma(
        self,
    ) -> None:
        one = _header(
            _completed(
                items=(_ITEMS[0],),
            ),
        )

        many = _header(
            _completed(
                items=tuple(
                    _item(
                        "foo",
                        StudyTier.FOCUS,
                        CefrLevel.B2,
                        example_sentence="foo",
                    )
                    for _ in range(
                        1_144,
                    )
                ),
            ),
        )

        assert "Found 1 word to study: 1 FOCUS, 0 STRETCH; 0 REVIEW, below B1" in one

        assert "Found 1,144 words to study: 1,144 FOCUS, 0 STRETCH; 0 REVIEW, below B1" in many

    def test_found_says_so_when_a_text_has_nothing_to_study(
        self,
    ) -> None:
        header = _header(
            _completed(
                items=(),
            ),
        )

        assert "Found no words to study: the text holds only function words, names, and numbers" in header


@final
class TestTheStatuses:
    @mark.parametrize(
        argnames="remote",
        argvalues=(
            False,
            True,
        ),
        ids=(
            "local",
            "remote",
        ),
    )
    @mark.parametrize(
        argnames=(
            "status",
            "said",
        ),
        argvalues=(
            (
                AnalysisStatus.REQUESTED,
                "requested, not finished yet",
            ),
            (
                AnalysisStatus.PROCESSING,
                "processing, not finished yet",
            ),
        ),
    )
    def test_an_unfinished_analysis_says_it_is_not_finished_yet_in_place_of_found(
        self,
        *,
        status: AnalysisStatus,
        said: str,
        remote: bool,
    ) -> None:
        header = _header(
            _unfinished(
                status=status,
            ),
            remote=remote,
        )

        assert f"Status {said}" in header

        assert "Found" not in header

    @mark.parametrize(
        argnames="remote",
        argvalues=(
            False,
            True,
        ),
        ids=(
            "local",
            "remote",
        ),
    )
    def test_an_interrupted_analysis_says_how_to_run_it_again(
        self,
        *,
        remote: bool,
    ) -> None:
        analysis = _unfinished(
            status=AnalysisStatus.FAILED,
            reason="processing was interrupted",
        )

        header = _header(
            analysis,
            remote=remote,
        )

        command = "wordwinnow analyze --remote" if remote else "wordwinnow analyze"

        assert f"Status failed: processing was interrupted; run {command} on the text again" in header

        assert "Found" not in header

    @mark.parametrize(
        argnames="remote",
        argvalues=(
            False,
            True,
        ),
        ids=(
            "local",
            "remote",
        ),
    )
    @mark.parametrize(
        argnames=(
            "reason",
            "code",
        ),
        argvalues=(
            (
                "RuntimeError: foo",
                ", code RuntimeError",
            ),
            (
                "foo",
                "",
            ),
        ),
        ids=(
            "with a code",
            "without one",
        ),
    )
    def test_any_other_failure_names_only_its_code_and_where_the_whole_reason_is(
        self,
        *,
        reason: str,
        code: str,
        remote: bool,
    ) -> None:
        analysis = _unfinished(
            status=AnalysisStatus.FAILED,
            reason=reason,
        )

        header = _header(
            analysis,
            remote=remote,
        )

        flag = " --remote" if remote else ""

        assert (
            f"Status failed: Wordwinnow met an internal error{code}; run wordwinnow doctor, and if it finds nothing "
            f"missing, report it as an issue with what wordwinnow show {analysis.id}{flag} --view operator prints"
        ) in header

        assert "Found" not in header

        assert ": foo" not in header


@final
class TestTheDictionaryLine:
    def test_the_header_counts_the_words_the_dictionary_did_not_answer_for(
        self,
    ) -> None:
        many = _header(
            _completed(),
        )

        one = _header(
            _completed(
                items=(
                    _ITEMS[0],
                    _ITEMS[1],
                ),
            ),
        )

        assert "Dictionary did not answer for 2 words; WordNet's meaning stands in where WordNet has one" in many

        assert "Dictionary did not answer for 1 word; WordNet's meaning stands in where WordNet has one" in one

    def test_the_header_has_no_dictionary_line_when_it_answered_for_every_word_or_was_not_asked(
        self,
    ) -> None:
        answered = _header(
            _completed(
                items=(_ITEMS[0],),
            ),
        )

        not_asked = _header(
            _completed(
                include_dictionary=False,
            ),
        )

        assert "Dictionary" not in answered

        assert "Dictionary" not in not_asked


@final
class TestTheTiers:
    def test_a_heading_names_each_tier_on_screen_and_what_it_means_for_the_learner(
        self,
    ) -> None:
        text = _render(
            _completed(
                profile=_PLAIN_KNOWN,
            ),
        )

        assert "FOCUS, from B1 to B2: study these first" in text

        assert "STRETCH, above B2: study these next" in text

        assert "REVIEW, below B1: revisit any you are unsure of" in text

        assert "KNOWN, words you said you know" in text

        headings = tuple(
            text.index(
                heading,
            )
            for heading in (
                "\nFOCUS, from B1 to B2: study these first",
                "\nSTRETCH, above B2: study these next",
                "\nREVIEW, below B1: revisit any you are unsure of",
                "\nKNOWN, words you said you know",
            )
        )

        assert headings == tuple(
            sorted(
                headings,
            ),
        )

    def test_a_tier_off_screen_has_no_heading_and_the_table_has_no_tier_or_related_column(
        self,
    ) -> None:
        analysis = _completed()

        console = Console(
            width=200,
            record=True,
        )

        render_items(
            analysis=analysis,
            items=select_items(
                items=analysis.items,
                tiers=(StudyTier.FOCUS,),
                limit=None,
            ),
            console=console,
        )

        text = console.export_text()

        assert "FOCUS, from B1 to B2" in text

        for heading in (
            "STRETCH, above",
            "REVIEW, below",
            "KNOWN, words",
        ):
            assert heading not in text

        for column in (
            "Tier",
            "Related",
        ):
            assert column not in text

        assert "┃" not in text


@final
class TestTheMarksAndTheirLegend:
    def test_each_mark_has_its_own_level_cell_and_a_reference_list_has_a_space(
        self,
    ) -> None:
        text = _marked_table(
            (
                LevelSource.REFERENCE_LIST,
                LevelSource.MODEL,
                LevelSource.FREQUENCY_HEURISTIC,
            ),
        )

        assert f" B2   {_TIMES}2 " in text

        assert f" B2*  {_TIMES}2 " in text

        assert f" B2~  {_TIMES}2 " in text

    @mark.parametrize(
        argnames=(
            "sources",
            "legend",
        ),
        argvalues=(
            (
                (
                    LevelSource.MODEL,
                    LevelSource.REFERENCE_LIST,
                ),
                "* estimated by the level model; the other levels are from reference lists.",
            ),
            (
                (
                    LevelSource.FREQUENCY_HEURISTIC,
                    LevelSource.REFERENCE_LIST,
                ),
                "~ estimated from the word's frequency in English; the other levels are from reference lists.",
            ),
            (
                (
                    LevelSource.MODEL,
                    LevelSource.FREQUENCY_HEURISTIC,
                    LevelSource.REFERENCE_LIST,
                ),
                "* estimated by the level model, ~ from the word's frequency in English; the other levels are from "
                "reference lists.",
            ),
            (
                (LevelSource.MODEL,),
                "* estimated by the level model.",
            ),
            (
                (LevelSource.FREQUENCY_HEURISTIC,),
                "~ estimated from the word's frequency in English.",
            ),
            (
                (
                    LevelSource.MODEL,
                    LevelSource.FREQUENCY_HEURISTIC,
                ),
                "* estimated by the level model, ~ from the word's frequency in English.",
            ),
        ),
        ids=(
            "model",
            "heuristic",
            "both",
            "only the model",
            "only the heuristic",
            "both, every row marked",
        ),
    )
    def test_the_legend_says_what_the_marks_on_screen_mean(
        self,
        *,
        sources: tuple[LevelSource, ...],
        legend: str,
    ) -> None:
        assert legend in _words(
            _marked_table(
                sources,
            ),
        )

    def test_no_legend_line_is_printed_for_a_mark_off_screen_though_the_analysis_has_one(
        self,
    ) -> None:
        marked = _from(
            _ITEMS[0],
            LevelSource.MODEL,
        )

        analysis = _completed(
            items=(
                _ITEMS[1],
                marked,
            ),
        )

        console = Console(
            width=200,
            record=True,
        )

        render_items(
            analysis=analysis,
            items=(_ITEMS[1],),
            console=console,
        )

        text = console.export_text()

        assert "estimated" not in text

        assert "the other levels" not in text

    @mark.parametrize(
        argnames="sources",
        argvalues=(
            (LevelSource.REFERENCE_LIST,),
            (LevelSource.MODEL,),
            (
                LevelSource.REFERENCE_LIST,
                LevelSource.FREQUENCY_HEURISTIC,
            ),
        ),
        ids=(
            "no mark",
            "every row marked",
            "some rows marked",
        ),
    )
    def test_a_line_under_the_tables_says_what_the_count_and_the_level_mean_and_comes_first(
        self,
        *,
        sources: tuple[LevelSource, ...],
    ) -> None:
        lines = tuple(
            line.rstrip()
            for line in _marked_table(
                sources,
            ).splitlines()
        )

        said = "\N{MULTIPLICATION SIGN}n is how often the text uses the word; each level is the word's CEFR level."

        assert (
            lines.count(
                said,
            )
            == 1
        )

        assert (
            lines[
                lines.index(
                    said,
                )
                - 1
            ]
            == ""
        )

    def test_the_cards_have_no_such_line(
        self,
    ) -> None:
        console = Console(
            width=200,
            record=True,
        )

        render_cards(
            items=_ITEMS,
            console=console,
        )

        assert "how often the text uses the word" not in console.export_text()


@final
class TestTheMeanings:
    def test_no_note_about_the_meanings_is_printed_when_no_row_is_on_screen(
        self,
    ) -> None:
        for analysis in (
            _completed(
                include_dictionary=False,
                items=(),
            ),
            _unfinished(
                status=AnalysisStatus.FAILED,
            ),
        ):
            text = _render(
                analysis,
            )

            assert "Meanings are" not in text

            assert "the dictionary was not asked" not in text

    def test_a_table_without_the_dictionary_leaves_out_the_source_and_says_once_that_every_meaning_is_wordnets(
        self,
    ) -> None:
        analysis = _completed(
            include_dictionary=False,
            items=(
                _ITEMS[1],
                _ITEMS[2],
            ),
        )

        console = Console(
            width=200,
            record=True,
        )

        render_items(
            analysis=analysis,
            items=analysis.items,
            console=console,
        )

        text = console.export_text()

        assert "betrayal of a trust" in text

        assert "(WordNet" not in text

        assert (
            "Meanings are WordNet's, one for each word, which may not be the one the text uses, and the dictionary "
            "was not asked."
        ) in text

    def test_the_note_without_the_dictionary_names_the_cards_when_a_word_has_other_meanings(
        self,
    ) -> None:
        analysis = _completed(
            include_dictionary=False,
            items=(_LANDAU,),
        )

        console = Console(
            width=200,
            record=True,
        )

        render_items(
            analysis=analysis,
            items=analysis.items,
            console=console,
        )

        assert (
            "Meanings are WordNet's, one for each word, which may not be the one the text uses; --detail lists the "
            "others, and the dictionary was not asked."
        ) in console.export_text()

    def test_a_table_with_the_dictionary_keeps_the_source_of_a_wordnet_meaning(
        self,
    ) -> None:
        text = _render(
            _completed(),
        )

        assert "betrayal of a trust (WordNet; dictionary unavailable)" in text

        assert "Meanings are WordNet's" not in text

        analysis = _completed(
            items=(
                _item(
                    "foo",
                    StudyTier.FOCUS,
                    CefrLevel.B2,
                    example_sentence="foo",
                    dictionary=DictionaryInformation(
                        outcome=LookupOutcome.NOT_FOUND,
                        failure_reason=None,
                        phonetic=None,
                        definitions=(),
                    ),
                    senses=(
                        _sense(
                            "bar",
                        ),
                    ),
                ),
            ),
        )

        assert "bar (WordNet)" in _render(
            analysis,
        )

    def test_a_word_with_no_meaning_and_no_dictionary_asked_names_its_part_of_speech_on_a_row_and_on_a_card(
        self,
    ) -> None:
        bare = _item(
            "foo",
            StudyTier.FOCUS,
            CefrLevel.B2,
            example_sentence="foo",
        )

        analysis = _completed(
            include_dictionary=False,
            items=(bare,),
        )

        row = Console(
            width=200,
            record=True,
        )

        render_items(
            analysis=analysis,
            items=analysis.items,
            console=row,
        )

        card = Console(
            width=200,
            record=True,
        )

        render_cards(
            items=analysis.items,
            console=card,
        )

        for console in (
            row,
            card,
        ):
            assert "no noun meaning in WordNet" in console.export_text(
                clear=False,
            )

            assert "no definition found" not in console.export_text(
                clear=False,
            )

            # NOTE:
            # Set apart from a meaning in dim italics, as every other reason is.
            assert (
                '<span style="color: #7f7f7f; text-decoration-color: #7f7f7f; font-style: italic">no noun meaning in '
                "WordNet"
            ) in console.export_html(
                inline_styles=True,
            )

    @mark.parametrize(
        argnames=(
            "part_of_speech",
            "said",
        ),
        argvalues=(
            (
                PartOfSpeech.NOUN,
                "no noun meaning in WordNet",
            ),
            (
                PartOfSpeech.VERB,
                "no verb meaning in WordNet",
            ),
            (
                PartOfSpeech.ADJECTIVE,
                "no adjective meaning in WordNet",
            ),
            (
                PartOfSpeech.ADVERB,
                "no adverb meaning in WordNet",
            ),
        ),
    )
    def test_the_line_names_the_part_of_speech_wordnet_was_asked_about(
        self,
        *,
        part_of_speech: PartOfSpeech,
        said: str,
    ) -> None:
        bare = _item(
            "foo",
            StudyTier.FOCUS,
            CefrLevel.B2,
            example_sentence="foo",
        )

        assert (
            meaning_of(
                study_item=replace(
                    bare,
                    item=replace(
                        bare.item,
                        part_of_speech=part_of_speech,
                    ),
                ),
            )
            == said
        )


def _tiered() -> Analysis:
    """
    An analysis with three FOCUS words, two STRETCH, two REVIEW, and two
    KNOWN.
    """

    def tier(
        name: StudyTier,
        count: int,
        /,
    ) -> tuple[StudyItem, ...]:
        return tuple(
            _item(
                "foo",
                name,
                CefrLevel.B2,
                example_sentence="foo",
            )
            for _ in range(
                count,
            )
        )

    return _completed(
        items=(
            *tier(
                StudyTier.FOCUS,
                3,
            ),
            *tier(
                StudyTier.STRETCH,
                2,
            ),
            *tier(
                StudyTier.REVIEW,
                2,
            ),
            *tier(
                StudyTier.KNOWN,
                2,
            ),
        ),
    )


_TIERED: Final = _tiered()


@final
class TestTheFooter:
    def _footer(
        self,
        *,
        tiers: tuple[StudyTier, ...] | None = None,
        limit: int | None,
        remote: bool = False,
    ) -> tuple[str, ...]:
        analysis = _TIERED

        shown = select_items(
            items=analysis.items,
            tiers=tiers,
            limit=limit,
        )

        console = Console(
            width=300,
            record=True,
        )

        render_footer(
            analysis=analysis,
            items=shown,
            available=len(
                select_items(
                    items=analysis.items,
                    tiers=tiers,
                    limit=None,
                ),
            ),
            remote=remote,
            console=console,
        )

        return tuple(
            console.export_text().splitlines(),
        )

    def test_one_tier_cut_says_how_many_of_its_words_are_shown_and_names_the_next_tier(
        self,
    ) -> None:
        assert self._footer(
            limit=2,
        )[1].rstrip() == (
            "Showing 2 of 3 FOCUS words; --rows raises the limit, and --tier stretch shows the next tier."
        )

    @mark.parametrize(
        argnames=(
            "limit",
            "said",
        ),
        argvalues=(
            (
                4,
                "Showing all 3 FOCUS words and 1 of 2 STRETCH words; --rows raises the limit, and --tier review "
                "shows the next tier.",
            ),
            (
                6,
                "Showing all 3 FOCUS words, all 2 STRETCH words, and 1 of 2 REVIEW words; --rows raises the limit, "
                "and --tier known shows the next tier.",
            ),
        ),
        ids=(
            "two tiers",
            "three tiers",
        ),
    )
    def test_rows_that_span_tiers_say_that_the_earlier_ones_are_whole(
        self,
        *,
        limit: int,
        said: str,
    ) -> None:
        assert (
            self._footer(
                limit=limit,
            )[1].rstrip()
            == said
        )

    @mark.parametrize(
        argnames=(
            "limit",
            "said",
        ),
        argvalues=(
            (
                3,
                "Showing all 3 FOCUS words; --rows raises the limit, and --tier stretch shows the next tier.",
            ),
            (
                5,
                "Showing all 3 FOCUS words and all 2 STRETCH words; --rows raises the limit, and --tier review shows "
                "the next tier.",
            ),
        ),
        ids=(
            "one tier",
            "two tiers",
        ),
    )
    def test_a_limit_at_the_end_of_a_tier_says_that_tier_is_shown_whole(
        self,
        *,
        limit: int,
        said: str,
    ) -> None:
        assert (
            self._footer(
                limit=limit,
            )[1].rstrip()
            == said
        )

    def test_the_footer_ends_the_line_without_a_next_tier_when_no_later_tier_has_words(
        self,
    ) -> None:
        assert (
            self._footer(
                tiers=(StudyTier.KNOWN,),
                limit=1,
            )[1].rstrip()
            == "Showing 1 of 2 KNOWN words; --rows raises the limit."
        )

    def test_a_tier_chosen_with_the_tier_option_names_the_next_tier_whether_or_not_it_was_chosen(
        self,
    ) -> None:
        assert (
            self._footer(
                tiers=(StudyTier.FOCUS,),
                limit=1,
            )[1].rstrip()
            == "Showing 1 of 3 FOCUS words; --rows raises the limit, and --tier stretch shows the next tier."
        )

    def test_when_every_chosen_word_is_shown_no_count_is_printed(
        self,
    ) -> None:
        analysis = _TIERED

        lines = self._footer(
            limit=None,
        )

        assert lines == (
            "",
            f"wordwinnow show {analysis.id} shows this again.",
        )

    @mark.parametrize(
        argnames=(
            "remote",
            "flag",
        ),
        argvalues=(
            (
                False,
                "",
            ),
            (
                True,
                " --remote",
            ),
        ),
        ids=(
            "local",
            "remote",
        ),
    )
    def test_the_command_that_shows_the_analysis_again_needs_remote_for_a_remote_one(
        self,
        *,
        remote: bool,
        flag: str,
    ) -> None:
        analysis = _TIERED

        assert self._footer(
            limit=1,
            remote=remote,
        )[-1].rstrip() == (f"wordwinnow show {analysis.id}{flag} shows this again.")

    def test_the_command_is_printed_for_an_analysis_with_nothing_to_show(
        self,
    ) -> None:
        analysis = _unfinished(
            status=AnalysisStatus.FAILED,
        )

        console = Console(
            width=80,
            record=True,
        )

        render_footer(
            analysis=analysis,
            items=(),
            available=0,
            remote=False,
            console=console,
        )

        assert console.export_text().splitlines() == [
            "",
            f"wordwinnow show {analysis.id} shows this again.",
        ]


def _forms(
    analysis: Analysis,
    /,
) -> tuple[str, ...]:
    """
    The learner's report for `analysis` as a table and as cards.
    """

    table = _render(
        analysis,
    )

    console = Console(
        width=200,
        record=True,
    )

    render_summary(
        analysis=analysis,
        remote=False,
        console=console,
    )

    render_cards(
        items=analysis.items,
        console=console,
    )

    render_footer(
        analysis=analysis,
        items=analysis.items,
        available=len(
            analysis.items,
        ),
        remote=False,
        console=console,
    )

    return (
        table,
        console.export_text(),
    )


@final
class TestNoFigureOfTheOperatorsIsInTheReport:
    @mark.parametrize(
        argnames="analysis",
        argvalues=(
            _completed(),
            _unfinished(
                status=AnalysisStatus.FAILED,
            ),
            _unfinished(
                status=AnalysisStatus.PROCESSING,
            ),
        ),
        ids=(
            "completed",
            "failed",
            "running",
        ),
    )
    def test_none_of_them_is_in_the_table_or_on_a_card(
        self,
        *,
        analysis: Analysis,
    ) -> None:
        for text in _forms(
            analysis,
        ):
            collapsed = _words(
                text,
            )

            for figure in (
                f"Analysis {analysis.id}",
                "custom_text",
                "Status completed",
                "asked about",
                "queue wait",
                "winnowing=",
                "linguistic_analysis=",
            ):
                assert figure not in collapsed

            assert (
                search(
                    pattern=r"reference list \d+, model \d+, frequency heuristic \d+",
                    string=collapsed,
                )
                is None
            )


@final
class TestTheOperatorsFigures:
    def test_a_completed_analysis_has_every_figure_under_a_heading_that_names_it(
        self,
    ) -> None:
        analysis = _completed()

        lines = _figures(
            analysis,
        ).splitlines()

        assert lines[0] == ""

        assert lines[1] == f"Analysis {analysis.id}"

        rows = _words(
            "\n".join(
                lines[2:],
            ),
        )

        assert rows == (
            "Document custom_text: a-scandal-in-bohemia.txt Status completed Levels reference list 4, model 0, "
            "frequency heuristic 0 Dictionary asked about 3 of 4 items: found 1, not found 0, unavailable 2 Timing "
            "queue wait 0.00s; winnowing=0.50s"
        )

    def test_a_failed_analysis_has_its_whole_reason_first_and_no_figure_it_cannot_have(
        self,
    ) -> None:
        analysis = _unfinished(
            status=AnalysisStatus.FAILED,
            reason="RuntimeError: foo",
        )

        rows = _words(
            _figures(
                analysis,
            ),
        )

        assert rows == (
            f"Analysis {analysis.id} Failure RuntimeError: foo Document custom_text: a-scandal-in-bohemia.txt Status "
            "failed"
        )

    def test_a_running_analysis_has_no_levels_and_no_timing_and_no_figure_is_shown_as_zero(
        self,
    ) -> None:
        analysis = _unfinished(
            status=AnalysisStatus.PROCESSING,
        )

        rows = _words(
            _figures(
                analysis,
            ),
        )

        assert rows == (f"Analysis {analysis.id} Document custom_text: a-scandal-in-bohemia.txt Status processing")

        assert "Levels" not in rows

        assert "Timing" not in rows

        assert "0.00s" not in rows


@final
class TestTheCards:
    def test_every_line_after_a_cards_first_is_indented_and_a_wrapped_line_continues_at_its_own_indent(
        self,
    ) -> None:
        console = Console(
            width=80,
            record=True,
        )

        render_cards(
            items=_EVERYTHING,
            console=console,
        )

        lines = tuple(line.rstrip() for line in console.export_text().splitlines() if line.strip())

        firsts = tuple(
            line
            for line in lines
            if not line.startswith(
                " ",
            )
        )

        assert (
            len(
                firsts,
            )
            == 5
        )

        assert frozenset(line.split()[0] for line in firsts) == frozenset(
            StudyTier,
        )

        assert (
            _widest(
                "\n".join(
                    lines,
                ),
            )
            <= 80
        )

        # NOTE:
        # The other meanings are set back by four, and a line of one that wraps continues at four.
        position = lines.index(
            "  other meanings, which the text may use instead:",
        )

        others = lines[position + 1 :]

        assert others[0].startswith(
            "    foo bar",
        )

        assert all(
            line.startswith(
                "    ",
            )
            for line in others[:2]
        )

    def test_a_card_holds_its_lines_in_order_under_its_first(
        self,
    ) -> None:
        console = Console(
            width=80,
            record=True,
        )

        render_cards(
            items=(_ITEMS[0],),
            console=console,
        )

        lines = console.export_text().splitlines()

        assert lines[0] == ""

        assert lines[1].rstrip() == "focus  mask  noun  B2 (reference list)  x2"

        assert lines[2].rstrip() == "  /mɑːsk/"

        assert lines[3].rstrip() == "  A covering that hides the face."

    def test_a_sentence_broken_across_lines_reads_as_one_line_and_keeps_its_other_controls_written_out(
        self,
    ) -> None:
        console = Console(
            width=80,
            record=True,
        )

        render_cards(
            items=(
                _item(
                    "foo",
                    StudyTier.FOCUS,
                    CefrLevel.B2,
                    example_sentence="foo\nbar\tbaz\x0cqux\r\n quux \x1b[31mcorge",
                ),
            ),
            console=console,
        )

        text = console.export_text()

        lines = tuple(line.rstrip() for line in text.splitlines())

        assert "  in the text: foo bar baz qux quux \\x1b[31mcorge" in lines

        assert "\x1b" not in text


@final
class TestTheListing:
    def test_each_analysis_takes_two_lines_under_a_heading_that_labels_the_time_utc(
        self,
    ) -> None:
        summaries = _summaries(
            titles=(
                (
                    "standard input",
                    DocumentOrigin.CUSTOM_TEXT,
                ),
                (
                    "foo",
                    DocumentOrigin.CUSTOM_TEXT,
                ),
                (
                    "bar",
                    DocumentOrigin.NEW_YORK_TIMES,
                ),
            ),
        )

        console = Console(
            width=80,
            record=True,
        )

        render_listing(
            summaries=summaries,
            console=console,
        )

        lines = tuple(line.rstrip() for line in console.export_text().splitlines())

        assert lines[0] == "Id                                    Requested (UTC)   Level  Status     Items"

        assert lines[1] == "    Text"

        assert len(
            lines,
        ) == 2 + 2 * len(
            summaries,
        )

        for (
            index,
            (
                summary,
                label,
            ),
        ) in enumerate(
            iterable=zip(
                summaries,
                (
                    "read from standard input",
                    "foo",
                    "bar",
                ),
                strict=True,
            ),
        ):
            assert lines[2 + 2 * index] == f"{summary.analysis_id}  1970-01-01 00:00  A2     completed   1508"

            assert lines[3 + 2 * index] == f"    {label}"

        assert "custom_text" not in "\n".join(
            lines,
        )

        assert "new_york_times" not in "\n".join(
            lines,
        )

        assert (
            _widest(
                "\n".join(
                    lines,
                ),
            )
            <= 80
        )

    def test_a_title_longer_than_the_line_wraps_inside_its_own_indent(
        self,
    ) -> None:
        console = Console(
            width=80,
            record=True,
        )

        render_listing(
            summaries=_summaries(
                titles=(
                    (
                        _LONG_MEANING,
                        DocumentOrigin.CUSTOM_TEXT,
                    ),
                ),
            ),
            console=console,
        )

        lines = tuple(line.rstrip() for line in console.export_text().splitlines())

        label = lines[3:]

        assert (
            len(
                label,
            )
            > 1
        )

        assert all(
            line.startswith(
                "    ",
            )
            for line in label
        )

        assert (
            _widest(
                "\n".join(
                    lines,
                ),
            )
            <= 80
        )

        assert _LONG_MEANING in _words(
            "\n".join(
                label,
            ),
        )

    def test_an_empty_listing_says_so(
        self,
    ) -> None:
        console = Console(
            width=80,
            record=True,
        )

        render_listing(
            summaries=(),
            console=console,
        )

        assert console.export_text() == "No analyses yet.\n"


# NOTE:
# A value that comes from outside the program: markup, an emoji code, and a control sequence that would move a cursor.
_HOSTILE: Final = "[bold]foo[/bold] :smile: \x1b[31mbar"

# NOTE:
# A failure's code is the text before the first colon and a space, so the emoji code in this one is followed by an
# escape character and not by a space, which would end the code inside it.
_HOSTILE_CODE: Final = "Foo[bold]:smile:\x1b[31m"


@final
class TestTheEscape:
    def test_a_control_character_is_written_as_the_error_line_writes_it_and_a_backslash_stays(
        self,
    ) -> None:
        assert (
            visible(
                message="foo\x1b[2Jbar\nbaz\rqux\t\x00\x7f\x9b",
            )
            == "foo\\x1b[2Jbar\\nbaz\\rqux\\t\\x00\\x7f\\x9b"
        )

        assert (
            visible(
                message="foo\\",
            )
            == "foo\\"
        )

    def test_a_title_a_meaning_an_attribution_and_a_failure_reason_are_drawn_as_typed(
        self,
    ) -> None:
        borrowed = _completed(
            document=Document(
                title=_HOSTILE,
                text="foo",
                origin=DocumentOrigin.NEW_YORK_TIMES,
                reference="rss/foo",
                attribution=_HOSTILE,
                references=(
                    Reference(
                        title=_HOSTILE,
                        url="https://example.com/foo",
                    ),
                ),
                retention=timedelta(
                    hours=24,
                ),
            ),
            items=(
                _item(
                    "foo",
                    StudyTier.FOCUS,
                    CefrLevel.B2,
                    example_sentence=_HOSTILE,
                    dictionary=_defined(
                        _HOSTILE,
                    ),
                ),
            ),
        )

        failed = _unfinished(
            status=AnalysisStatus.FAILED,
            reason=f"{_HOSTILE_CODE}: bar",
        )

        written = "[bold]foo[/bold] :smile: \\x1b[31mbar"

        written_code = "Foo[bold]:smile:\\x1b[31m"

        for text in (
            *_forms(
                borrowed,
            ),
            _header(
                failed,
            ),
            _figures(
                failed,
            ),
        ):
            assert "\x1b" not in text

            assert "😄" not in text

        for text in _forms(
            borrowed,
        ):
            assert (
                text.count(
                    written,
                )
                >= 3
            )

        assert f"Failure {written_code}: bar" in _words(
            _figures(
                failed,
            ),
        )

        assert f"code {written_code};" in _header(
            failed,
        )

    def test_the_json_of_the_same_analysis_keeps_every_value_as_it_is(
        self,
    ) -> None:
        analysis = _completed(
            document=Document(
                title=_HOSTILE,
                text="foo",
                origin=DocumentOrigin.NEW_YORK_TIMES,
                reference="rss/foo",
                attribution=_HOSTILE,
                retention=timedelta(
                    hours=24,
                ),
            ),
            items=(
                _item(
                    "foo",
                    StudyTier.FOCUS,
                    CefrLevel.B2,
                    example_sentence=_HOSTILE,
                    dictionary=_defined(
                        _HOSTILE,
                    ),
                ),
            ),
        )

        payload = loads(
            s=to_json(
                analysis=analysis,
            ),
        )

        assert payload["document"]["title"] == _HOSTILE

        assert payload["document"]["attribution"] == _HOSTILE

        assert payload["items"][0]["gloss"]["text"] == _HOSTILE

        failed = _unfinished(
            status=AnalysisStatus.FAILED,
            reason=_HOSTILE,
        )

        assert (
            loads(
                s=to_json(
                    analysis=failed,
                ),
            )["failure_reason"]
            == _HOSTILE
        )


_runner: Final = CliRunner()


@fixture
def inside(
    *,
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> Path:
    monkeypatch.chdir(
        path=tmp_path,
    )

    return tmp_path


def _serving(
    analysis: Analysis,
    /,
    *,
    monkeypatch: MonkeyPatch,
) -> None:
    """
    Make every command that reaches an analysis answer with `analysis`.
    """

    async def analyze_locally(
        *,
        request: AnalysisRequest,
        settings: Settings,
        progress: AnalysisProgress = UNWATCHED,
        watch: DictionaryWatch | None = None,
    ) -> Analysis:
        return analysis

    async def analyze_remotely(
        *,
        request: AnalysisRequest,
        settings: Settings,
        wait: bool,
        timeout_seconds: float,
        progress: AnalysisProgress = UNWATCHED,
        watch: DictionaryWatch | None = None,
    ) -> Analysis:
        return analysis

    async def show(
        *,
        analysis_id: AnalysisId,
        settings: Settings,
        local: bool,
    ) -> WatchedAnalysis:
        return WatchedAnalysis(
            analysis=analysis,
            progress=None,
        )

    for (
        name,
        double,
    ) in (
        (
            "analyze_locally",
            analyze_locally,
        ),
        (
            "analyze_remotely",
            analyze_remotely,
        ),
        (
            "show",
            show,
        ),
    ):
        monkeypatch.setattr(
            target=commands,
            name=name,
            value=double,
        )


def _consoles(
    *,
    monkeypatch: MonkeyPatch,
) -> Console:
    """
    Give the command consoles a width of their own, and return the one for
    standard error, which records what it prints.
    """

    monkeypatch.setattr(
        target=main,
        name="_console",
        value=Console(
            width=100,
        ),
    )

    errors = Console(
        stderr=True,
        width=100,
        record=True,
    )

    monkeypatch.setattr(
        target=main,
        name="_errors",
        value=errors,
    )

    return errors


def _figures_of(
    analysis: Analysis,
    /,
) -> str:
    console = Console(
        width=100,
        record=True,
    )

    render_figures(
        analysis=analysis,
        console=console,
    )

    return console.export_text()


_FORMS: Final = MappingProxyType(
    mapping={
        "table": (),
        "detail": ("--detail",),
        "json": ("--json",),
        "csv": (
            "--csv",
            "out.csv",
        ),
    },
)

_COMMANDS: Final = MappingProxyType(
    mapping={
        "analyze local": (
            "analyze",
            "foo.txt",
            "--level",
            "B1",
            "--no-dictionary",
            "--local",
        ),
        "analyze remote": (
            "analyze",
            "foo.txt",
            "--level",
            "B1",
            "--no-dictionary",
            "--remote",
        ),
        "show local": (
            "show",
            str(
                object=AnalysisId.new(),
            ),
            "--local",
        ),
        "show remote": (
            "show",
            str(
                object=AnalysisId.new(),
            ),
            "--remote",
        ),
    },
)


@final
class TestTheOperatorsFiguresThroughTheCommands:
    @mark.parametrize(
        argnames="form",
        argvalues=tuple(
            _FORMS,
        ),
    )
    @mark.parametrize(
        argnames="command",
        argvalues=tuple(
            _COMMANDS,
        ),
    )
    def test_the_figures_follow_whatever_the_command_prints_on_standard_error_and_change_nothing_on_standard_output(
        self,
        *,
        inside: Path,
        monkeypatch: MonkeyPatch,
        command: str,
        form: str,
    ) -> None:
        (inside / "foo.txt").write_text(
            data="foo",
            encoding="utf-8",
        )

        analysis = _completed()

        _serving(
            analysis,
            monkeypatch=monkeypatch,
        )

        _consoles(
            monkeypatch=monkeypatch,
        )

        arguments = (
            *_COMMANDS[command],
            *_FORMS[form],
        )

        learner = _runner.invoke(
            app=app,
            args=arguments,
        )

        operator = _runner.invoke(
            app=app,
            args=[
                *arguments,
                "--view",
                "operator",
            ],
        )

        assert learner.exit_code == 0, learner.output

        assert operator.exit_code == 0, operator.output

        assert operator.stdout == learner.stdout

        assert learner.stderr == ""

        assert operator.stderr.endswith(
            _figures_of(
                analysis,
            ),
        )

    @mark.parametrize(
        argnames="command",
        argvalues=(
            "analyze local",
            "analyze remote",
        ),
    )
    def test_a_failed_analysis_prints_its_whole_reason_first_and_analyze_still_exits_one(
        self,
        *,
        inside: Path,
        monkeypatch: MonkeyPatch,
        command: str,
    ) -> None:
        (inside / "foo.txt").write_text(
            data="foo",
            encoding="utf-8",
        )

        analysis = _unfinished(
            status=AnalysisStatus.FAILED,
        )

        _serving(
            analysis,
            monkeypatch=monkeypatch,
        )

        _consoles(
            monkeypatch=monkeypatch,
        )

        result = _runner.invoke(
            app=app,
            args=[
                *_COMMANDS[command],
                "--view",
                "operator",
            ],
        )

        assert result.exit_code == 1

        figures = _figures_of(
            analysis,
        )

        assert result.stderr.endswith(
            figures,
        )

        assert figures.splitlines()[2].split() == [
            "Failure",
            "RuntimeError:",
            "foo",
        ]

        shown = _runner.invoke(
            app=app,
            args=[
                *_COMMANDS["show local"],
                "--view",
                "operator",
            ],
        )

        assert shown.exit_code == 0, shown.output

        assert shown.stderr.endswith(
            figures,
        )

    def test_a_running_analysis_has_no_levels_and_no_timing_and_the_figures_follow_the_note(
        self,
        *,
        inside: Path,
        monkeypatch: MonkeyPatch,
    ) -> None:
        analysis = _unfinished(
            status=AnalysisStatus.PROCESSING,
        )

        _serving(
            analysis,
            monkeypatch=monkeypatch,
        )

        _consoles(
            monkeypatch=monkeypatch,
        )

        result = _runner.invoke(
            app=app,
            args=[
                "show",
                str(
                    object=analysis.id,
                ),
                "--view",
                "operator",
            ],
        )

        assert result.exit_code == 0, result.output

        figures = _figures_of(
            analysis,
        )

        assert "Levels" not in figures

        assert "Timing" not in figures

        output = _words(
            result.output,
        )

        assert output.endswith(
            _words(
                figures,
            ),
        )

        note = output.index(
            "a local analysis goes on only while the command that runs it is running",
        )

        assert note < output.index(
            f"Analysis {analysis.id}",
        )

    def test_no_wait_shows_no_analysis_and_so_prints_no_figures(
        self,
        *,
        inside: Path,
        monkeypatch: MonkeyPatch,
    ) -> None:
        (inside / "foo.txt").write_text(
            data="foo",
            encoding="utf-8",
        )

        _serving(
            _unfinished(
                status=AnalysisStatus.REQUESTED,
            ),
            monkeypatch=monkeypatch,
        )

        _consoles(
            monkeypatch=monkeypatch,
        )

        result = _runner.invoke(
            app=app,
            args=[
                "analyze",
                "foo.txt",
                "--level",
                "B1",
                "--remote",
                "--no-wait",
                "--view",
                "operator",
            ],
        )

        assert result.exit_code == 0, result.output

        assert "requested analysis" in result.stdout

        assert "Analysis " not in result.stderr
