"""
The report a learner reads.
"""

from csv import (
    reader,
)
from datetime import (
    UTC,
    datetime,
    timedelta,
)
from json import (
    loads,
)
from typing import (
    Final,
    final,
)

from rich.console import (
    Console,
)

from wordwinnow.domain.analysis import (
    Analysis,
    AnalysisOptions,
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
from wordwinnow.services.cli.report import (
    meaning_of,
    render_cards,
    render_items,
    render_summary,
    select_items,
    tier_counts,
    to_csv,
    to_json,
)
from wordwinnow.services.intake.schemas import (
    AnalysisOut,
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
# Watson's own sentence from the story, the one place it uses the word.
_TREACHERY_SENTENCE: Final = (
    "And yet it would be the blackest treachery to Holmes to draw back now from the part which he had entrusted to "
    "me."
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


def _completed() -> Analysis:
    analysis = request_an_analysis(
        document=Document(
            title="a-scandal-in-bohemia",
            text="Irene Adler kept the photograph.",
            origin=DocumentOrigin.CUSTOM_TEXT,
            reference="a-scandal-in-bohemia.txt",
        ),
        profile=_PROFILE,
        options=AnalysisOptions(),
        at=_EPOCH,
    )

    analysis.start(
        at=_EPOCH,
    )

    analysis.complete(
        at=_EPOCH,
        items=_ITEMS,
        stage_timings=(
            StageTiming(
                stage=Stage.WINNOWING,
                seconds=0.5,
            ),
        ),
    )

    return analysis


def _render(
    analysis: Analysis,
    /,
) -> str:
    console = Console(
        record=True,
        width=200,
    )

    render_summary(
        analysis=analysis,
        console=console,
    )

    render_items(
        items=analysis.items,
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
        text = _render(
            _completed(),
        )

        assert "A covering that hides the face." in text

        assert "betrayal of a trust (WordNet; dictionary unavailable)" in text

        assert "= perfidy, perfidiousness" in text

        assert "unknown: the dictionary was unavailable (timed out)" in text

        assert "timed_out" not in text

        assert "focus 1, stretch 1, review 1, known 1" in text

        assert "level B1, working toward B2, 0 known words" in text

        assert "winnowing=0.50s" in text

        assert "reference list 4, model 0, frequency heuristic 0" in text

        assert "found 1, not found 0, unavailable 2" in text

        assert (
            "Definitions shown under CC BY-SA 3.0 (https://creativecommons.org/licenses/by-sa/3.0); --detail gives "
            "each one's source."
        ) in text

    def test_a_dictionary_without_a_page_per_word_is_credited_by_its_license_alone(
        self,
    ) -> None:
        console = Console(
            record=True,
            width=200,
        )

        render_items(
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
            render_items,
            render_cards,
        ):
            console = Console(
                record=True,
                width=200,
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

        assert set(
            said,
        ) == set(
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
            "-",
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
            render_items,
            render_cards,
        ):
            console = Console(
                record=True,
                width=200,
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
            record=True,
            width=200,
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

        assert {item["example_sentence"] for item in payload["items"]} == {
            "",
        }

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
