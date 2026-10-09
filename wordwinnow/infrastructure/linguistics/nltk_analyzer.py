"""
Sentence segmentation, tokenization, tagging, and lemmatization with NLTK.

This is the one place that knows the Penn Treebank tagset: it maps a tag onto
the domain's four parts of speech, marks proper nouns, and chooses the WordNet
part of speech the lemmatizer needs.
"""

from types import (
    MappingProxyType,
)
from typing import (
    Final,
    final,
)

from nltk import (
    pos_tag,
    sent_tokenize,
    word_tokenize,
)
from nltk.corpus import (
    stopwords,
)
from nltk.stem import (
    WordNetLemmatizer,
)

from wordwinnow.domain.language import (
    PartOfSpeech,
    Sentence,
    Token,
)
from wordwinnow.infrastructure.linguistics.nltk_resources import (
    ensure_resources,
)

# NOTE:
# The first letter of a Penn Treebank tag names the word class the domain cares about.
_TAG_PREFIXES: Final = MappingProxyType(
    mapping={
        "N": PartOfSpeech.NOUN,
        "V": PartOfSpeech.VERB,
        "J": PartOfSpeech.ADJECTIVE,
        "R": PartOfSpeech.ADVERB,
    },
)

# NOTE:
# WordNet's own part-of-speech letters, which the lemmatizer takes.
_WORDNET_POS: Final = MappingProxyType(
    mapping={
        PartOfSpeech.NOUN: "n",
        PartOfSpeech.VERB: "v",
        PartOfSpeech.ADJECTIVE: "a",
        PartOfSpeech.ADVERB: "r",
    },
)

_PROPER_NOUN_TAGS: Final = frozenset(
    {
        "NNP",
        "NNPS",
    },
)

# NOTE:
# The past tense and the past participle: a verb carrying either tag is not in its base form.
_PAST_TAGS: Final = frozenset(
    {
        "VBD",
        "VBN",
    },
)

# NOTE:
# Adverb tags that are not adverbs a learner studies: `RP` is a particle (`up` in `give up`) and `WRB` a question word
# (`where`).
_NOT_ADVERB_TAGS: Final = frozenset(
    {
        "RP",
        "WRB",
    },
)


def part_of_speech_of(
    *,
    tag: str,
) -> PartOfSpeech | None:
    """
    The domain part of speech for a Penn Treebank tag, or `None`.
    """

    if tag in _NOT_ADVERB_TAGS:
        return None

    return _TAG_PREFIXES.get(
        tag[:1],
    )


@final
class NltkLinguisticAnalyzer:
    """
    Analyzes English text with NLTK's Punkt segmenter, Treebank tokenizer,
    averaged perceptron tagger, and WordNet lemmatizer.

    Satisfies `LinguisticAnalyzer` structurally.
    """

    def __init__(
        self,
    ) -> None:
        ensure_resources()

        self._lemmatizer: Final = WordNetLemmatizer()

        self._function_words: Final = frozenset(
            stopwords.words(
                fileids="english",
            ),
        )

    @property
    def function_words(
        self,
    ) -> frozenset[str]:
        return self._function_words

    def analyze(
        self,
        *,
        text: str,
    ) -> tuple[Sentence, ...]:
        sentences: list[Sentence] = []

        # NOTE:
        # A blank line ends a sentence whatever punctuation precedes it, so a headline or a title that stands as its
        # own paragraph is never run together with the paragraph after it.
        for raw in (
            sentence
            for paragraph in text.split(
                sep="\n\n",
            )
            for sentence in sent_tokenize(
                text=paragraph,
            )
        ):
            stripped = raw.strip()

            if not stripped:
                continue

            words = word_tokenize(
                text=stripped,
            )

            tokens = tuple(
                self._token(
                    surface,
                    tag,
                )
                for (
                    surface,
                    tag,
                ) in pos_tag(
                    tokens=words,
                )
            )

            sentences.append(
                Sentence(
                    index=len(
                        sentences,
                    ),
                    text=stripped,
                    tokens=tokens,
                ),
            )

        return tuple(
            sentences,
        )

    def _token(
        self,
        surface: str,
        tag: str,
        /,
    ) -> Token:
        part_of_speech = part_of_speech_of(
            tag=tag,
        )

        lowered = surface.lower()

        return Token(
            surface=surface,
            lemma=self._lemma(
                lowered,
                part_of_speech,
                tag,
            ),
            part_of_speech=part_of_speech,
            is_proper_noun=tag in _PROPER_NOUN_TAGS,
        )

    def _lemma(
        self,
        lowered: str,
        part_of_speech: PartOfSpeech | None,
        tag: str,
        /,
    ) -> str:
        if part_of_speech is None:
            return lowered

        wordnet_pos = _WORDNET_POS[part_of_speech]

        # HACK:
        # The lemmatizer keeps the shortest candidate and, on a tie, the first, which is the word itself, so `saw`,
        # `fell`, and `felt` in the past tense would stay verbs of their own; a past tag says the word is not the base
        # form, so another candidate wins until the lemmatizer takes the tag into account itself.
        if tag in _PAST_TAGS:
            others = tuple(
                candidate
                for candidate in self._lemmatizer._morphy(
                    form=lowered,
                    pos=wordnet_pos,
                )
                if candidate != lowered
            )

            if others:
                return min(
                    others,
                    key=len,
                )

        return self._lemmatizer.lemmatize(
            word=lowered,
            pos=wordnet_pos,
        )
