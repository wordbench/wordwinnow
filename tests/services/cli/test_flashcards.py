"""
The flashcards a learner imports into Anki or pastes into Quizlet.
"""

from csv import (
    reader,
)
from io import (
    StringIO,
)
from typing import (
    Final,
    final,
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
from wordwinnow.domain.language import (
    PartOfSpeech,
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
from wordwinnow.services.cli.flashcards import (
    to_anki,
    to_quizlet,
)
from wordwinnow.services.cli.report import (
    to_csv,
)

_PAGE: Final = "https://example.com/wiki/foo"

_PROVENANCE: Final = Provenance(
    license=License(
        name="CC BY-SA 3.0",
        url="https://example.com/licenses/by-sa",
    ),
    source_urls=(_PAGE,),
)

_SENTENCE: Final = "It was not that foo came before baz."


def _sense(
    gloss: str,
    /,
    *,
    synonyms: tuple[str, ...] = (),
    example: str | None = None,
    is_instance: bool = False,
) -> LexicalSense:
    return LexicalSense(
        key="foo.n.01",
        part_of_speech=PartOfSpeech.NOUN,
        gloss=gloss,
        example=example,
        synonyms=synonyms,
        hypernyms=(),
        antonyms=(),
        usage_count=1,
        hyponym_count=0,
        category="noun.attribute",
        is_instance=is_instance,
    )


def _definition(
    text: str,
    /,
    *,
    example: str | None = None,
    matched: bool = True,
) -> SelectedDefinition:
    return SelectedDefinition(
        text=text,
        example=example,
        part_of_speech_matched=matched,
        provenance=_PROVENANCE,
    )


def _item(
    lemma: str,
    /,
    *,
    sentence: str = _SENTENCE,
    level: CefrLevel = CefrLevel.B1,
    dictionary: DictionaryInformation | None = None,
    senses: tuple[LexicalSense, ...] = (),
) -> StudyItem:
    return StudyItem(
        item=VocabularyItem(
            lemma=lemma,
            part_of_speech=PartOfSpeech.NOUN,
            occurrence_count=2,
            example_sentence=sentence,
        ),
        zipf_frequency=4.0,
        level=LevelAssessment(
            level=level,
            source=LevelSource.REFERENCE_LIST,
        ),
        tier=StudyTier.FOCUS,
        dictionary=dictionary,
        senses=senses,
    )


def _found(
    *definitions: SelectedDefinition,
    phonetic: str | None = "/fuː/",
) -> DictionaryInformation:
    return DictionaryInformation(
        outcome=LookupOutcome.FOUND,
        failure_reason=None,
        phonetic=phonetic,
        definitions=definitions,
    )


# NOTE:
# A word the dictionary defined four times, with an example and a pronunciation, and WordNet's sense with a synonym
# that must never sit under a dictionary definition.
_DICTIONARY_WORD: Final = _item(
    "foo",
    dictionary=_found(
        _definition(
            "A first definition.",
            example="An example of foo.",
        ),
        _definition(
            "A second definition.",
        ),
        _definition(
            "A third definition.",
            matched=False,
        ),
        _definition(
            "A fourth definition.",
        ),
    ),
    senses=(
        _sense(
            "a wordnet sense of foo",
            synonyms=("qux",),
        ),
        _sense(
            "another wordnet sense of foo",
        ),
    ),
)

# NOTE:
# A word with no dictionary asked and so no pronunciation: WordNet's leading sense, with four synonyms and an example,
# then three other senses of which the first names a particular thing.
_WORDNET_WORD: Final = _item(
    "baz",
    level=CefrLevel.B2,
    senses=(
        _sense(
            "a first sense",
            synonyms=(
                "qux",
                "quux",
                "corge",
                "grault",
            ),
            example="a wordnet example",
        ),
        _sense(
            "a name sense",
            is_instance=True,
        ),
        _sense(
            "a third sense",
        ),
        _sense(
            "a fourth sense",
        ),
    ),
)

_UNAVAILABLE_WORD: Final = _item(
    "bar",
    dictionary=DictionaryInformation(
        outcome=LookupOutcome.UNAVAILABLE,
        failure_reason="timed_out",
        phonetic=None,
        definitions=(),
    ),
    senses=(
        _sense(
            "a stand-in sense",
        ),
    ),
)

_NO_MEANING_WORD: Final = _item(
    "qux",
    dictionary=DictionaryInformation(
        outcome=LookupOutcome.NOT_FOUND,
        failure_reason=None,
        phonetic=None,
        definitions=(),
    ),
)

_HOSTILE: Final = _item(
    "foo",
    sentence=('<script>alert(1)</script> & "quoted"<img src=x onerror=alert(1)>\tTab\rCR\nLF \x1b[2J\u202e end'),
    dictionary=_found(
        _definition(
            "A\tdefinition\r\nwith <b>white</b> space.",
        ),
        phonetic=None,
    ),
)


def _anki_notes(
    text: str,
    /,
) -> tuple[str, ...]:
    return tuple(
        text.split(
            sep="\n",
        )[5:-1],
    )


def _quizlet_cards(
    text: str,
    /,
) -> tuple[str, ...]:
    return tuple(
        text.split(
            sep="\n",
        )[:-1],
    )


@final
class TestTheShapeOfEachFile:
    def test_anki_has_its_five_header_lines_then_one_note_of_three_fields_a_line(
        self,
    ) -> None:
        text = to_anki(
            items=(
                _DICTIONARY_WORD,
                _WORDNET_WORD,
                _UNAVAILABLE_WORD,
                _NO_MEANING_WORD,
                _HOSTILE,
            ),
        )

        lines = text.split(
            sep="\n",
        )

        assert lines[:5] == [
            "#separator:Tab",
            "#html:true",
            "#notetype:Basic",
            "#columns:Front\tBack\tTags",
            "#tags column:3",
        ]

        assert lines[-1] == ""

        assert (
            len(
                lines,
            )
            == 11
        )

        assert not text.startswith(
            "\ufeff",
        )

        assert "\r" not in text

        assert frozenset(
            note.count(
                "\t",
            )
            for note in _anki_notes(
                text,
            )
        ) == frozenset(
            (2,),
        )

    def test_quizlet_has_no_header_and_one_card_of_two_fields_a_line(
        self,
    ) -> None:
        text = to_quizlet(
            items=(
                _DICTIONARY_WORD,
                _WORDNET_WORD,
                _UNAVAILABLE_WORD,
                _NO_MEANING_WORD,
                _HOSTILE,
            ),
        )

        cards = _quizlet_cards(
            text,
        )

        assert (
            len(
                cards,
            )
            == 5
        )

        assert text.endswith(
            "\n",
        )

        assert not text.endswith(
            "\n\n",
        )

        assert not text.startswith(
            "\ufeff",
        )

        assert "\r" not in text

        assert frozenset(
            card.count(
                "\t",
            )
            for card in cards
        ) == frozenset(
            (1,),
        )

        assert cards[0].startswith(
            "foo (noun)\t",
        )

    def test_no_word_is_a_file_of_its_header_alone_for_anki_and_an_empty_one_for_quizlet(
        self,
    ) -> None:
        assert (
            len(
                to_anki(
                    items=(),
                ).splitlines(),
            )
            == 5
        )

        assert (
            to_quizlet(
                items=(),
            )
            == ""
        )


@final
class TestTheStandardCard:
    def test_a_dictionary_word_has_its_pronunciation_meaning_sentence_and_credit(
        self,
    ) -> None:
        assert _anki_notes(
            to_anki(
                items=(_DICTIONARY_WORD,),
            ),
        ) == (
            "foo (noun)\t"
            "/fuː/<br>A first definition.<br>In the text: It was not that foo came before baz.<br>"
            f'<small>CC BY-SA 3.0, <a href="{_PAGE}">{_PAGE}</a></small>\t'
            "wordwinnow cefr::B1",
        )

        assert _quizlet_cards(
            to_quizlet(
                items=(_DICTIONARY_WORD,),
            ),
        ) == (
            f"foo (noun)\t/fuː/ \u00b7 A first definition. \u00b7 In the text: {_SENTENCE} "
            f"\u00b7 CC BY-SA 3.0, {_PAGE}",
        )

    def test_a_wordnet_word_has_no_pronunciation_and_is_credited_to_wordnet_without_marking_the_meaning(
        self,
    ) -> None:
        assert _anki_notes(
            to_anki(
                items=(_WORDNET_WORD,),
            ),
        ) == (
            "baz (noun)\ta first sense<br>In the text: It was not that foo came before baz.<br>"
            "<small>WordNet</small>\twordwinnow cefr::B2",
        )

        assert _quizlet_cards(
            to_quizlet(
                items=(_WORDNET_WORD,),
            ),
        ) == (f"baz (noun)\ta first sense \u00b7 In the text: {_SENTENCE} \u00b7 WordNet",)

    def test_a_wordnet_gloss_that_stands_in_for_an_unavailable_dictionary_says_so(
        self,
    ) -> None:
        assert _quizlet_cards(
            to_quizlet(
                items=(_UNAVAILABLE_WORD,),
            ),
        ) == (f"bar (noun)\ta stand-in sense \u00b7 In the text: {_SENTENCE} \u00b7 WordNet; dictionary unavailable",)

    def test_a_word_with_no_meaning_has_the_reports_reason_on_the_back_and_no_credit(
        self,
    ) -> None:
        assert _anki_notes(
            to_anki(
                items=(_NO_MEANING_WORD,),
            ),
        ) == (
            "qux (noun)\tno definition found<br>In the text: It was not that foo came before baz.\t"
            "wordwinnow cefr::B1",
        )

        assert _quizlet_cards(
            to_quizlet(
                items=(_NO_MEANING_WORD,),
            ),
        ) == (f"qux (noun)\tno definition found \u00b7 In the text: {_SENTENCE}",)

    def test_a_word_with_no_dictionary_and_no_sense_says_what_the_table_says(
        self,
    ) -> None:
        assert _quizlet_cards(
            to_quizlet(
                items=(
                    _item(
                        "quux",
                    ),
                ),
            ),
        ) == (f"quux (noun)\tno noun meaning in WordNet \u00b7 In the text: {_SENTENCE}",)

    def test_the_level_is_a_tag_in_anki_alone(
        self,
    ) -> None:
        anki = to_anki(
            items=(
                _DICTIONARY_WORD,
                _WORDNET_WORD,
            ),
        )

        assert tuple(
            note.rsplit(
                sep="\t",
                maxsplit=1,
            )[1]
            for note in _anki_notes(
                anki,
            )
        ) == (
            "wordwinnow cefr::B1",
            "wordwinnow cefr::B2",
        )

        quizlet = to_quizlet(
            items=(
                _DICTIONARY_WORD,
                _WORDNET_WORD,
            ),
        )

        assert "cefr" not in quizlet

        assert "wordwinnow" not in quizlet

        assert "B1" not in quizlet


@final
class TestTheFullerCard:
    def test_a_dictionary_meaning_adds_two_more_definitions_and_its_example_and_no_wordnet(
        self,
    ) -> None:
        assert _quizlet_cards(
            to_quizlet(
                items=(_DICTIONARY_WORD,),
                detailed=True,
            ),
        ) == (
            "foo (noun)\t/fuː/ \u00b7 A first definition. \u00b7 Other meaning: A second definition. "
            "\u00b7 Other meaning: A third definition. (other part of speech) \u00b7 Example: An example of foo. "
            f"\u00b7 In the text: {_SENTENCE} \u00b7 CC BY-SA 3.0, {_PAGE}",
        )

    def test_a_dictionary_meaning_never_carries_wordnet_s_synonyms_or_senses(
        self,
    ) -> None:
        for text in (
            to_anki(
                items=(_DICTIONARY_WORD,),
                detailed=True,
            ),
            to_quizlet(
                items=(_DICTIONARY_WORD,),
                detailed=True,
            ),
        ):
            assert "qux" not in text

            assert "wordnet sense" not in text

            assert "Synonyms" not in text

            assert "fourth" not in text

    def test_a_wordnet_meaning_adds_two_more_senses_and_its_synonyms_and_no_example(
        self,
    ) -> None:
        assert _anki_notes(
            to_anki(
                items=(_WORDNET_WORD,),
                detailed=True,
            ),
        ) == (
            "baz (noun)\ta first sense<br>Other meaning: a name sense (a name)<br>Other meaning: a third sense<br>"
            "Synonyms: qux, quux, corge<br>In the text: It was not that foo came before baz.<br>"
            "<small>WordNet</small>\twordwinnow cefr::B2",
        )

        text = to_quizlet(
            items=(_WORDNET_WORD,),
            detailed=True,
        )

        assert "wordnet example" not in text

        assert "grault" not in text

        assert "fourth" not in text

    def test_the_standard_card_has_neither_other_meanings_nor_example_nor_synonyms(
        self,
    ) -> None:
        for item in (
            _DICTIONARY_WORD,
            _WORDNET_WORD,
        ):
            text = to_anki(
                items=(item,),
            )

            assert "Other meaning" not in text

            assert "Example:" not in text

            assert "Synonyms" not in text


@final
class TestHostileText:
    def test_anki_escapes_every_value_and_keeps_each_note_on_one_line(
        self,
    ) -> None:
        text = to_anki(
            items=(_HOSTILE,),
        )

        assert "<script" not in text

        assert "<img" not in text

        assert "<b>" not in text

        assert _anki_notes(
            text,
        ) == (
            "foo (noun)\t"
            "A definition with &lt;b&gt;white&lt;/b&gt; space.<br>"
            "In the text: &lt;script&gt;alert(1)&lt;/script&gt; &amp; &quot;quoted&quot;"
            "&lt;img src=x onerror=alert(1)&gt; Tab CR LF \\x1b[2J\\u202e end<br>"
            f'<small>CC BY-SA 3.0, <a href="{_PAGE}">{_PAGE}</a></small>\t'
            "wordwinnow cefr::B1",
        )

    def test_quizlet_keeps_the_text_as_it_is_but_for_white_space_and_controls(
        self,
    ) -> None:
        assert _quizlet_cards(
            to_quizlet(
                items=(_HOSTILE,),
            ),
        ) == (
            "foo (noun)\tA definition with <b>white</b> space. \u00b7 "
            'In the text: <script>alert(1)</script> & "quoted"<img src=x onerror=alert(1)> Tab CR LF '
            "\\x1b[2J\\u202e end "
            f"\u00b7 CC BY-SA 3.0, {_PAGE}",
        )

    def test_a_control_character_in_a_lemma_a_meaning_or_a_source_address_is_written_out(
        self,
    ) -> None:
        item = _item(
            "fo\x1bo",
            dictionary=_found(
                SelectedDefinition(
                    text="A\x07 definition.",
                    example=None,
                    part_of_speech_matched=True,
                    provenance=Provenance(
                        license=License(
                            name="CC\x00BY",
                            url="https://example.com/licenses/by-sa",
                        ),
                        source_urls=("https://example.com/a\x1b",),
                    ),
                ),
                phonetic="/f\x1bu/",
            ),
        )

        for text in (
            to_anki(
                items=(item,),
            ),
            to_quizlet(
                items=(item,),
            ),
        ):
            assert not any(
                ord(
                    character,
                )
                < 0x20
                and character
                not in (
                    "\t",
                    "\n",
                )
                for character in text
            )

            assert "\\x1b" in text

            assert "\\x07" in text

            assert "\\x00" in text

    def test_a_link_is_made_only_to_a_web_page_and_its_address_cannot_leave_the_attribute(
        self,
    ) -> None:
        item = _item(
            "foo",
            dictionary=_found(
                SelectedDefinition(
                    text="A definition.",
                    example=None,
                    part_of_speech_matched=True,
                    provenance=Provenance(
                        license=None,
                        source_urls=(
                            'https://example.com/a"onmouseover="x',
                            "javascript:alert(1)",
                        ),
                    ),
                ),
            ),
        )

        text = to_anki(
            items=(item,),
        )

        assert (
            '<small><a href="https://example.com/a&quot;onmouseover=&quot;x">'
            "https://example.com/a&quot;onmouseover=&quot;x</a>, javascript:alert(1)</small>"
        ) in text

        assert 'href="javascript' not in text


@final
class TestADocumentKeptOnlyForAWhile:
    def test_neither_file_has_a_sentence_on_any_back(
        self,
    ) -> None:
        anki = to_anki(
            items=(
                _DICTIONARY_WORD,
                _WORDNET_WORD,
            ),
            with_sentences=False,
        )

        quizlet = to_quizlet(
            items=(
                _DICTIONARY_WORD,
                _WORDNET_WORD,
            ),
            with_sentences=False,
            detailed=True,
        )

        assert "In the text" not in anki

        assert "In the text" not in quizlet

        assert _SENTENCE not in anki

        assert _SENTENCE not in quizlet

        assert _anki_notes(
            anki,
        )[1] == ("baz (noun)\ta first sense<br><small>WordNet</small>\twordwinnow cefr::B2")


@final
class TestTheCsvIsUnchanged:
    def test_the_csv_of_a_fixed_analysis_is_what_the_base_wrote(
        self,
    ) -> None:
        text = to_csv(
            items=(
                _DICTIONARY_WORD,
                _WORDNET_WORD,
                _UNAVAILABLE_WORD,
                _NO_MEANING_WORD,
            ),
        )

        assert text == (
            "tier,lemma,part_of_speech,level,level_source,occurrence_count,example_sentence,meaning,meaning_source,"
            "meaning_url,meaning_license,meaning_license_url,phonetic,synonyms\r\n"
            "focus,foo,noun,B1,reference_list,2,It was not that foo came before baz.,A first definition.,dictionary,"
            "https://example.com/wiki/foo,CC BY-SA 3.0,https://example.com/licenses/by-sa,/fuː/,qux\r\n"
            "focus,baz,noun,B2,reference_list,2,It was not that foo came before baz.,a first sense,lexical_network,"
            ",,,,qux; quux; corge; grault\r\n"
            "focus,bar,noun,B1,reference_list,2,It was not that foo came before baz.,a stand-in sense,"
            "lexical_network,,,,,\r\n"
            "focus,qux,noun,B1,reference_list,2,It was not that foo came before baz.,,,,,,,\r\n"
        )

        assert (
            len(
                tuple(
                    reader(
                        StringIO(
                            initial_value=text,
                        ),
                    ),
                ),
            )
            == 5
        )
