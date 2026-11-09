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
    mark,
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

    def test_a_sense_that_names_one_person_place_or_work_is_marked(
        self,
        *,
        wordnet: WordNetLexicalSemantics,
    ) -> None:
        (
            physicist,
            carriage,
        ) = wordnet.senses_of(
            lemma="landau",
            part_of_speech=PartOfSpeech.NOUN,
        )

        assert physicist.is_instance

        assert not carriage.is_instance

        assert "carriage" in carriage.gloss

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

    def test_a_hyphenated_word_has_no_senses_as_spelled_so_the_level_model_sees_none(
        self,
        *,
        wordnet: WordNetLexicalSemantics,
    ) -> None:
        # NOTE:
        # WordNet writes the compound `sitting_room`, and the level model learns from `senses_of` in training and at
        # analysis, so this pins what the model sees: nothing, however many senses the compound route finds.
        assert (
            wordnet.senses_of(
                lemma="sitting-room",
                part_of_speech=PartOfSpeech.NOUN,
            )
            == ()
        )

    def test_a_hyphenated_word_finds_the_compound_under_the_networks_own_spelling(
        self,
        *,
        wordnet: WordNetLexicalSemantics,
    ) -> None:
        (sitting_room,) = wordnet.compound_senses_of(
            lemma="sitting-room",
            part_of_speech=PartOfSpeech.NOUN,
        )

        assert sitting_room.key == "living_room.n.01"

        assert sitting_room.gloss == (
            "a room in a private house or establishment where people can sit and talk and relax"
        )

        assert "sitting room" not in sitting_room.synonyms

    def test_the_compound_senses_come_most_used_first(
        self,
        *,
        wordnet: WordNetLexicalSemantics,
    ) -> None:
        senses = wordnet.compound_senses_of(
            lemma="drawing-room",
            part_of_speech=PartOfSpeech.NOUN,
        )

        assert tuple(sense.key for sense in senses) == (
            "drawing_room.n.01",
            "drawing_room.n.02",
        )

        assert senses[0].usage_count > senses[1].usage_count

    def test_a_plural_compound_finds_its_base_form_and_is_not_its_own_synonym(
        self,
        *,
        wordnet: WordNetLexicalSemantics,
    ) -> None:
        (paper_mill,) = wordnet.compound_senses_of(
            lemma="paper-mills",
            part_of_speech=PartOfSpeech.NOUN,
        )

        assert paper_mill.key == "paper_mill.n.01"

        assert paper_mill.synonyms == ()

    def test_a_compound_is_found_under_the_part_of_speech_it_was_tagged(
        self,
        *,
        wordnet: WordNetLexicalSemantics,
    ) -> None:
        senses = wordnet.compound_senses_of(
            lemma="cross-examined",
            part_of_speech=PartOfSpeech.VERB,
        )

        assert tuple(sense.key for sense in senses) == ("cross_examine.v.01",)

    def test_a_compound_in_capitals_finds_the_same_entry(
        self,
        *,
        wordnet: WordNetLexicalSemantics,
    ) -> None:
        senses = wordnet.compound_senses_of(
            lemma="Sitting-Room",
            part_of_speech=PartOfSpeech.NOUN,
        )

        assert tuple(sense.key for sense in senses) == ("living_room.n.01",)

    def test_every_hyphen_of_a_compound_is_read(
        self,
        *,
        wordnet: WordNetLexicalSemantics,
    ) -> None:
        senses = wordnet.compound_senses_of(
            lemma="man-of-the-world",
            part_of_speech=PartOfSpeech.NOUN,
        )

        assert tuple(sense.key for sense in senses) == ("sophisticate.n.01",)

    @mark.parametrize(
        argnames=(
            "lemma",
            "part_of_speech",
        ),
        argvalues=(
            (
                "room",
                PartOfSpeech.NOUN,
            ),
            (
                "smoke-rocket",
                PartOfSpeech.NOUN,
            ),
            (
                "sitting-room",
                PartOfSpeech.VERB,
            ),
            (
                "re-sign",
                PartOfSpeech.VERB,
            ),
        ),
        ids=(
            "a word without a hyphen",
            "a compound the network lacks",
            "a compound in the wrong part of speech",
            "a joined spelling is not tried",
        ),
    )
    def test_the_compound_route_has_no_senses_without_a_compound_in_that_part_of_speech(
        self,
        *,
        wordnet: WordNetLexicalSemantics,
        lemma: str,
        part_of_speech: PartOfSpeech,
    ) -> None:
        assert (
            wordnet.compound_senses_of(
                lemma=lemma,
                part_of_speech=part_of_speech,
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

    def test_an_example_is_the_first_that_uses_the_word_or_none(
        self,
        *,
        wordnet: WordNetLexicalSemantics,
    ) -> None:
        # NOTE:
        # WordNet's examples belong to a sense: `observe` shares one sense with `detect`, whose examples say only
        # `detected` and `found`, and another with `note`, whose first example says `observed`; `immense` shares its
        # sense with `huge`, whose first four examples say only `huge`.
        observe = {
            sense.key: sense
            for sense in wordnet.senses_of(
                lemma="observe",
                part_of_speech=PartOfSpeech.VERB,
            )
        }

        (
            immense,
            *_,
        ) = wordnet.senses_of(
            lemma="immense",
            part_of_speech=PartOfSpeech.ADJECTIVE,
        )

        assert (
            observe["detect.v.01"].example,
            observe["note.v.01"].example,
            immense.example,
        ) == (
            None,
            "She observed that his presentation took up too much time",
            "the Los Angeles aqueduct winds like an immense snake along the base of the mountains",
        )

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
