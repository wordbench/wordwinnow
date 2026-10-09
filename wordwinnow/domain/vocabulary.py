"""
Which words of a text are vocabulary, and how often they occur.

A vocabulary item is a lemma in one part of speech.

Two requirements decide what counts: a vocabulary item is a word (letters,
possibly hyphenated, never a number or a symbol) in one of the four open word
classes, and it is not a name and not a function word, because neither is
something a learner studies as vocabulary.
"""

from collections.abc import (
    Sequence,
)
from collections.abc import (
    Set as AbstractSet,
)
from dataclasses import (
    dataclass,
)
from re import (
    compile as compile_pattern,
)
from typing import (
    Final,
    final,
)

from wordwinnow.domain.language import (
    PartOfSpeech,
    Sentence,
    Token,
)

# NOTE:
# Letters with optional internal hyphens: `well-being` is a word, `3rd`, `'s` and `--` are not.
#
# Unicode letters are accepted so a loanword with a diacritic is not silently dropped.
_WORD: Final = compile_pattern(
    pattern=r"^[^\W\d_]+(?:-[^\W\d_]+)*$",
)


@final
class InvalidVocabularyItemError(
    ValueError,
):
    """
    Raised when a vocabulary item would contain invalid data.
    """


@dataclass(
    frozen=True,
    kw_only=True,
    slots=True,
)
@final
class VocabularyItem:
    """
    One lemma in one part of speech, with how it appeared in the text.

    `example_sentence` is the first sentence of the text containing the item,
    so a learner sees the word in the context they are studying.
    """

    lemma: str

    part_of_speech: PartOfSpeech

    occurrence_count: int

    example_sentence: str

    def __post_init__(
        self,
    ) -> None:
        if not self.lemma:
            raise InvalidVocabularyItemError(
                "a vocabulary item requires a non-empty lemma",
            )

        if self.occurrence_count < 1:
            raise InvalidVocabularyItemError(
                f"a vocabulary item requires at least one occurrence, got {self.occurrence_count}",
            )

        if not self.example_sentence.strip():
            raise InvalidVocabularyItemError(
                "a vocabulary item requires a non-blank example sentence",
            )


def is_vocabulary(
    *,
    token: Token,
    function_words: AbstractSet[str],
) -> bool:
    """
    Whether a token is something a learner studies as vocabulary.
    """

    if token.part_of_speech is None or token.is_proper_noun:
        return False

    if not _WORD.match(
        string=token.lemma,
    ):
        return False

    return token.lemma.lower() not in function_words


def collect_vocabulary(
    *,
    sentences: Sequence[Sentence],
    function_words: AbstractSet[str],
) -> tuple[VocabularyItem, ...]:
    """
    Group the vocabulary tokens of a text by lemma and part of speech.

    Items come back in order of first appearance, so the result is
    deterministic for a given text.
    """

    counts: dict[tuple[str, PartOfSpeech], int] = {}

    examples: dict[tuple[str, PartOfSpeech], str] = {}

    for sentence in sentences:
        for token in sentence.tokens:
            if not is_vocabulary(
                token=token,
                function_words=function_words,
            ):
                continue

            if token.part_of_speech is None:
                continue

            key = (
                token.lemma.lower(),
                token.part_of_speech,
            )

            counts[key] = (
                counts.get(
                    key,
                    0,
                )
                + 1
            )

            examples.setdefault(
                key,
                sentence.text,
            )

    return tuple(
        VocabularyItem(
            lemma=lemma,
            part_of_speech=part_of_speech,
            occurrence_count=count,
            example_sentence=examples[
                (
                    lemma,
                    part_of_speech,
                )
            ],
        )
        for (
            (
                lemma,
                part_of_speech,
            ),
            count,
        ) in counts.items()
    )
