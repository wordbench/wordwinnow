"""
WordNet's senses of a word, against the real corpus.

These tests need the NLTK data `make nltk-data` installs.
"""

from typing import (
    final,
)

from nltk.corpus import (
    wordnet as wordnet_corpus,
)
from pytest import (
    fixture,
)

from wordwinnow.domain.language import (
    PartOfSpeech,
)
from wordwinnow.infrastructure.lexicon.wordnet_semantics import (
    EXPECTED_WORDNET_VERSION,
    WordNetLexicalSemantics,
    gloss_of,
)


@fixture(
    scope="module",
)
def wordnet() -> WordNetLexicalSemantics:
    return WordNetLexicalSemantics()


@final
class TestWordNetLexicalSemantics:
    def test_the_corpus_is_the_documented_version(
        self,
        *,
        wordnet: WordNetLexicalSemantics,
    ) -> None:
        assert wordnet.version == EXPECTED_WORDNET_VERSION

    def test_senses_are_constrained_by_part_of_speech_and_ordered_by_usage(
        self,
        *,
        wordnet: WordNetLexicalSemantics,
    ) -> None:
        senses = wordnet.senses_of(
            lemma="rocket",
            part_of_speech=PartOfSpeech.NOUN,
        )

        # NOTE:
        # The most used noun sense of `rocket` is a vehicle, and the signal Watson tosses through the window only
        # comes fourth, which is why the analysis lists a word's senses and never chooses one.
        assert senses[0].key == "rocket.n.01"

        assert senses[3].key == "rocket.n.04"

        assert senses[0].usage_count >= senses[3].usage_count

        assert all(sense.part_of_speech is PartOfSpeech.NOUN for sense in senses)

        assert "projectile" in senses[0].synonyms

        assert "visual signal" in senses[3].hypernyms

        assert senses[3].category == "noun.communication"

        assert senses[0].hyponym_count > 0

    def test_antonyms_come_from_the_words_own_lemma(
        self,
        *,
        wordnet: WordNetLexicalSemantics,
    ) -> None:
        senses = wordnet.senses_of(
            lemma="married",
            part_of_speech=PartOfSpeech.ADJECTIVE,
        )

        assert "unmarried" in senses[0].antonyms

    def test_hypernyms_come_in_the_same_order_in_every_process(
        self,
        *,
        wordnet: WordNetLexicalSemantics,
    ) -> None:
        # NOTE:
        # NLTK keeps a synset's hypernyms in a set, whose order follows each process's hash seed; `atropine` has five,
        # the most of any word in WordNet, so an unsorted order would pass here once in 120 runs.
        (atropine,) = wordnet.senses_of(
            lemma="atropine",
            part_of_speech=PartOfSpeech.NOUN,
        )

        assert atropine.hypernyms == (
            "alkaloid",
            "antidote",
            "antispasmodic",
            "mydriatic",
            "poison",
        )

    def test_an_unknown_word_has_no_senses(
        self,
        *,
        wordnet: WordNetLexicalSemantics,
    ) -> None:
        assert (
            wordnet.senses_of(
                lemma="egria",
                part_of_speech=PartOfSpeech.NOUN,
            )
            == ()
        )

    def test_a_definition_keeps_nothing_its_examples_left_behind(
        self,
        *,
        wordnet: WordNetLexicalSemantics,
    ) -> None:
        # NOTE:
        # WordNet's definition of the story's `immense` ends in nine empty segments and an author's name once NLTK has
        # taken its examples out.
        (
            immense,
            *_,
        ) = wordnet.senses_of(
            lemma="immense",
            part_of_speech=PartOfSpeech.ADJECTIVE,
        )

        assert immense.gloss == "unusually great in size or amount or degree or especially extent or scope"

        assert immense.example is not None

    def test_half_a_quotation_is_dropped_and_a_stray_quote_trimmed(
        self,
    ) -> None:
        assert (
            gloss_of(
                synset=wordnet_corpus.synset(
                    "notice.n.01",
                ),
            )
            == "an announcement containing information about an event"
        )

        assert (
            gloss_of(
                synset=wordnet_corpus.synset(
                    "post_office.n.01",
                ),
            )
            == "a local branch where postal services are available"
        )

    def test_a_definition_in_two_parts_keeps_both(
        self,
    ) -> None:
        assert gloss_of(
            synset=wordnet_corpus.synset(
                "photograph.n.01",
            ),
        ) == (
            "a representation of a person or scene in the form of a print or transparent slide; recorded by a camera "
            "on light-sensitive material"
        )
