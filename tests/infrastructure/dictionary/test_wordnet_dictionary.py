"""
Tests for `WordNetDictionary`: WordNet as a second dictionary behind the same
port, against the real corpus.

These tests need the NLTK data `make nltk-data` installs.
"""

from typing import (
    final,
)

from pytest import (
    fixture,
)

from wordwinnow.domain.dictionary import (
    LookupOutcome,
    Provenance,
)
from wordwinnow.domain.language import (
    PartOfSpeech,
)
from wordwinnow.infrastructure.dictionary.wordnet_dictionary import (
    WORDNET_LICENSE,
    WordNetDictionary,
)


@fixture(
    scope="module",
)
def dictionary() -> WordNetDictionary:
    return WordNetDictionary()


@final
class TestWordNetDictionary:
    async def test_a_word_is_found_with_its_meanings_by_part_of_speech_and_its_license(
        self,
        *,
        dictionary: WordNetDictionary,
    ) -> None:
        lookup = await dictionary.look_up(
            lemma=" Photograph ",
        )

        assert lookup.outcome is LookupOutcome.FOUND

        (entry,) = lookup.entries

        assert entry.headword == "photograph"

        assert entry.phonetic is None

        assert tuple(meaning.part_of_speech for meaning in entry.meanings) == (
            PartOfSpeech.NOUN,
            PartOfSpeech.VERB,
        )

        assert entry.meanings[0].definitions[0].text == (
            "a representation of a person or scene in the form of a print or transparent slide; recorded by a camera "
            "on light-sensitive material"
        )

        assert entry.provenance == Provenance(
            license=WORDNET_LICENSE,
        )

    async def test_a_definition_brings_wordnet_s_example_when_it_has_one(
        self,
        *,
        dictionary: WordNetDictionary,
    ) -> None:
        lookup = await dictionary.look_up(
            lemma="photograph",
        )

        (entry,) = lookup.entries

        verb = next(meaning for meaning in entry.meanings if meaning.part_of_speech is PartOfSpeech.VERB)

        assert verb.definitions[0].text == "record on photographic film"

        assert verb.definitions[0].example == "I photographed the scene of the accident"

    async def test_a_word_wordnet_lacks_is_not_found(
        self,
        *,
        dictionary: WordNetDictionary,
    ) -> None:
        lookup = await dictionary.look_up(
            lemma="egria",
        )

        assert lookup.outcome is LookupOutcome.NOT_FOUND

        assert lookup.entries == ()
