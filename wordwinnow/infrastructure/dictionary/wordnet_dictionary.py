"""
Princeton WordNet 3.0 as a dictionary: the definitions and examples of a
word's senses, grouped by part of speech, read from the local corpus.

It is the second implementation of the `Dictionary` port beside the Free
Dictionary API, and the one to read before writing a third: it answers from
data already on the machine, so it needs no cache, no request budget, and no
network, and it says which license its definitions are shown under.
"""

from types import (
    MappingProxyType,
)
from typing import (
    Final,
    final,
)

from nltk.corpus import (
    wordnet,
)

from wordwinnow.domain.dictionary import (
    Definition,
    DictionaryEntry,
    DictionaryLookup,
    License,
    LookupOutcome,
    Meaning,
    Provenance,
)
from wordwinnow.domain.language import (
    PartOfSpeech,
)
from wordwinnow.infrastructure.lexicon.wordnet_semantics import (
    gloss_of,
)
from wordwinnow.infrastructure.linguistics.nltk_resources import (
    REQUIRED_RESOURCES,
    ensure_resources,
)

# NOTE:
# WordNet has no page per word to link to, so the provenance is the license alone; its full text ships with the corpus
# NLTK downloads.
WORDNET_LICENSE: Final = License(
    name="WordNet 3.0 license",
    url="https://wordnet.princeton.edu/license-and-commercial-use",
)

_WORDNET_POS: Final = MappingProxyType(
    mapping={
        PartOfSpeech.NOUN: "n",
        PartOfSpeech.VERB: "v",
        PartOfSpeech.ADJECTIVE: "a",
        PartOfSpeech.ADVERB: "r",
    },
)


@final
class WordNetDictionary:
    """
    Looks a word up in the local WordNet corpus.

    Satisfies `Dictionary` structurally.
    """

    def __init__(
        self,
    ) -> None:
        ensure_resources(
            resources=tuple(resource for resource in REQUIRED_RESOURCES if resource.package == "wordnet"),
        )

    async def look_up(
        self,
        *,
        lemma: str,
    ) -> DictionaryLookup:
        headword = lemma.strip().lower()

        meanings = tuple(
            meaning
            for part_of_speech in PartOfSpeech
            if (
                meaning := _meaning_of(
                    headword,
                    part_of_speech,
                )
            )
            is not None
        )

        if not meanings:
            return DictionaryLookup(
                outcome=LookupOutcome.NOT_FOUND,
            )

        return DictionaryLookup(
            outcome=LookupOutcome.FOUND,
            entries=(
                DictionaryEntry(
                    headword=headword,
                    phonetic=None,
                    meanings=meanings,
                    provenance=Provenance(
                        license=WORDNET_LICENSE,
                    ),
                ),
            ),
        )


def _meaning_of(
    headword: str,
    part_of_speech: PartOfSpeech,
    /,
) -> Meaning | None:
    definitions = tuple(
        Definition(
            text=gloss_of(
                synset=synset,
            ),
            example=next(
                iter(
                    synset.examples(),
                ),
                None,
            ),
        )
        for synset in wordnet.synsets(
            lemma=headword.replace(
                " ",
                "_",
            ),
            pos=_WORDNET_POS[part_of_speech],
        )
    )

    if not definitions:
        return None

    return Meaning(
        word_class=part_of_speech,
        part_of_speech=part_of_speech,
        definitions=definitions,
    )
