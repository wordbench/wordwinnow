"""
Princeton WordNet 3.0, through NLTK, as the source of lexical senses.

The adapter translates NLTK's synsets into the domain's `LexicalSense` and
nothing of NLTK's representation crosses the boundary.

Lookups are constrained by part of speech and ordered by how often each sense
was tagged in the SemCor corpus, which is the most-frequent-sense ordering and
not disambiguation in context.
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
from nltk.corpus.reader.wordnet import (
    Lemma,
    Synset,
)

from wordwinnow.domain.language import (
    PartOfSpeech,
)
from wordwinnow.domain.senses import (
    LexicalSense,
)
from wordwinnow.infrastructure.linguistics.nltk_resources import (
    REQUIRED_RESOURCES,
    ensure_resources,
)

# NOTE:
# The WordNet version this project was built and evaluated against.
#
# What the network says about a word is part of what the level model sees, so a different version would move the
# features.
EXPECTED_WORDNET_VERSION: Final = "3.0"

_WORDNET_POS: Final = MappingProxyType(
    mapping={
        PartOfSpeech.NOUN: "n",
        PartOfSpeech.VERB: "v",
        PartOfSpeech.ADJECTIVE: "a",
        PartOfSpeech.ADVERB: "r",
    },
)


@final
class WordNetLexicalSemantics:
    """
    Lists a word's senses from the local WordNet corpus.

    Satisfies `LexicalSemantics` structurally.
    """

    def __init__(
        self,
    ) -> None:
        ensure_resources(
            resources=tuple(resource for resource in REQUIRED_RESOURCES if resource.package == "wordnet"),
        )

        self.version: Final = wordnet.get_version()

    def senses_of(
        self,
        *,
        lemma: str,
        part_of_speech: PartOfSpeech,
    ) -> tuple[LexicalSense, ...]:
        query = lemma.lower().replace(
            " ",
            "_",
        )

        synsets = wordnet.synsets(
            lemma=query,
            pos=_WORDNET_POS[part_of_speech],
        )

        senses = tuple(
            _sense(
                synset,
                query,
                part_of_speech,
            )
            for synset in synsets
        )

        return tuple(
            sorted(
                senses,
                key=lambda sense: -sense.usage_count,
            ),
        )


def gloss_of(
    *,
    synset: Synset,
) -> str:
    """
    A sense's definition as a learner should read it.
    """

    # NOTE:
    # NLTK takes WordNet's quoted examples out of a definition but leaves what surrounded them, so in about 675 senses
    # the text ends in empty `;` segments, an author's name such as `- W.R.Inge`, or half of a quotation.
    (
        first,
        *rest,
    ) = (
        segment.strip()
        for segment in synset.definition().split(
            sep=";",
        )
    )

    # NOTE:
    # The first segment is the definition itself, so only a stray quote mark is trimmed from it; a later segment is
    # kept only when it is neither empty, an attribution, nor part of a quotation.
    later = tuple(
        segment
        for segment in rest
        if segment
        and not segment.startswith(
            "- ",
        )
        and '"' not in segment
    )

    definition = first.strip(
        '"',
    )

    return "; ".join(
        (
            definition,
            *later,
        ),
    )


def _sense(
    synset: Synset,
    query: str,
    part_of_speech: PartOfSpeech,
    /,
) -> LexicalSense:
    own = tuple(lemma for lemma in synset.lemmas() if lemma.name().lower() == query)

    examples = synset.examples()

    return LexicalSense(
        key=synset.name(),
        part_of_speech=part_of_speech,
        gloss=gloss_of(
            synset=synset,
        ),
        example=examples[0] if examples else None,
        synonyms=tuple(
            _word(
                lemma,
            )
            for lemma in synset.lemmas()
            if lemma.name().lower() != query
        ),
        # NOTE:
        # NLTK keeps a synset's hypernyms in a set, whose order follows each process's hash seed, so they are sorted
        # by name and a report lists them in the same order in every run.
        hypernyms=tuple(
            _word(
                hypernym.lemmas()[0],
            )
            for hypernym in sorted(
                synset.hypernyms(),
            )
        ),
        antonyms=tuple(
            _word(
                antonym,
            )
            for lemma in own
            for antonym in lemma.antonyms()
        ),
        usage_count=sum(lemma.count() for lemma in own),
        hyponym_count=len(
            synset.hyponyms(),
        ),
        category=synset.lexname(),
    )


def _word(
    lemma: Lemma,
    /,
) -> str:
    return lemma.name().replace(
        "_",
        " ",
    )
