"""
The NLTK linguistic adapter against the real corpora.

These tests need the NLTK data `make nltk-data` installs.
"""

from types import (
    MappingProxyType,
)
from typing import (
    final,
)

from pytest import (
    fixture,
)

from wordwinnow.domain.language import (
    PartOfSpeech,
)
from wordwinnow.infrastructure.linguistics.nltk_analyzer import (
    NltkLinguisticAnalyzer,
    part_of_speech_of,
)


@fixture(
    scope="module",
)
def analyzer() -> NltkLinguisticAnalyzer:
    return NltkLinguisticAnalyzer()


@final
class TestPartOfSpeechOf:
    def test_tag_prefixes_map_onto_the_four_classes(
        self,
    ) -> None:
        assert (
            part_of_speech_of(
                tag="NNS",
            )
            is PartOfSpeech.NOUN
        )

        assert (
            part_of_speech_of(
                tag="VBD",
            )
            is PartOfSpeech.VERB
        )

        assert (
            part_of_speech_of(
                tag="JJR",
            )
            is PartOfSpeech.ADJECTIVE
        )

        assert (
            part_of_speech_of(
                tag="RB",
            )
            is PartOfSpeech.ADVERB
        )

    def test_particles_question_words_and_closed_classes_are_not_vocabulary(
        self,
    ) -> None:
        for tag in (
            "RP",
            "WRB",
            "DT",
            "IN",
            "CD",
            ".",
        ):
            assert (
                part_of_speech_of(
                    tag=tag,
                )
                is None
            )


@final
class TestNltkLinguisticAnalyzer:
    def test_sentences_are_segmented_and_tokens_tagged_and_lemmatized(
        self,
        *,
        analyzer: NltkLinguisticAnalyzer,
    ) -> None:
        sentences = analyzer.analyze(
            text="The grooms were rubbing down their horses quickly. Irene Adler watched them.",
        )

        assert tuple(sentence.text for sentence in sentences) == (
            "The grooms were rubbing down their horses quickly.",
            "Irene Adler watched them.",
        )

        first = MappingProxyType(
            mapping={token.surface: token for token in sentences[0].tokens},
        )

        assert first["grooms"].lemma == "groom"

        assert first["grooms"].part_of_speech is PartOfSpeech.NOUN

        assert first["rubbing"].lemma == "rub"

        assert first["rubbing"].part_of_speech is PartOfSpeech.VERB

        assert first["quickly"].part_of_speech is PartOfSpeech.ADVERB

        assert first["The"].part_of_speech is None

        second = MappingProxyType(
            mapping={token.surface: token for token in sentences[1].tokens},
        )

        assert second["Adler"].is_proper_noun

        assert second["watched"].lemma == "watch"

    def test_a_past_tense_that_is_also_a_verb_of_its_own_is_lemmatized_to_its_base(
        self,
        *,
        analyzer: NltkLinguisticAnalyzer,
    ) -> None:
        (sentence,) = analyzer.analyze(
            text="Holmes saw the King, fell silent, and felt uneasy.",
        )

        lemmas = MappingProxyType(
            mapping={token.surface: token.lemma for token in sentence.tokens},
        )

        assert lemmas["saw"] == "see"

        assert lemmas["fell"] == "fall"

        assert lemmas["felt"] == "feel"

    def test_function_words_come_from_the_stopword_list(
        self,
        *,
        analyzer: NltkLinguisticAnalyzer,
    ) -> None:
        assert "the" in analyzer.function_words

        assert "very" in analyzer.function_words

        assert "photograph" not in analyzer.function_words

    def test_a_blank_line_ends_a_sentence_without_punctuation(
        self,
        *,
        analyzer: NltkLinguisticAnalyzer,
    ) -> None:
        sentences = analyzer.analyze(
            text="A cab waits at the door\n\nThe King wore a black mask.",
        )

        assert tuple(sentence.text for sentence in sentences) == (
            "A cab waits at the door",
            "The King wore a black mask.",
        )

    def test_blank_text_has_no_sentences(
        self,
        *,
        analyzer: NltkLinguisticAnalyzer,
    ) -> None:
        assert (
            analyzer.analyze(
                text="   \n  ",
            )
            == ()
        )
