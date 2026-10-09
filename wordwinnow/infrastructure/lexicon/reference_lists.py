"""
The CEFR reference word lists, read from the CSV files that ship with the
project.

Two lists are combined: CEFR-J for A1 to B2 and the Octanove profile for C1
and C2.

Where a lemma in one part of speech appears more than once, the lowest level
wins, because a word a learner meets at the lower level is already theirs by
the higher one.
"""

from collections.abc import (
    Iterable,
    Mapping,
)
from csv import (
    DictReader,
)
from dataclasses import (
    dataclass,
)
from pathlib import (
    Path,
)
from types import (
    MappingProxyType,
)
from typing import (
    Final,
    final,
)

from wordwinnow.application.errors import (
    LinguisticResourcesMissingError,
)
from wordwinnow.domain.cefr import (
    CefrLevel,
)
from wordwinnow.domain.language import (
    PartOfSpeech,
)

# NOTE:
# The files as published, in level order; see data/reference/ATTRIBUTION.md.
REFERENCE_FILES: Final = (
    "cefrj-vocabulary-profile-1.5.csv",
    "octanove-vocabulary-profile-c1c2-1.0.csv",
)

# NOTE:
# The lists' own word-class labels that map onto the four the project studies.
#
# Everything else is skipped: the function-word classes (`determiner`, `preposition`, `pronoun`, `be-verb`, and the
# like), and two rows whose label is missing or misspelled, `batter` with none and `remonstrate` as `vern`, which are
# vocabulary but dropped rather than guessed at.
_WORD_CLASSES: Final = MappingProxyType(
    mapping={
        "noun": PartOfSpeech.NOUN,
        "verb": PartOfSpeech.VERB,
        "adjective": PartOfSpeech.ADJECTIVE,
        "adverb": PartOfSpeech.ADVERB,
    },
)


@dataclass(
    frozen=True,
    kw_only=True,
    slots=True,
)
@final
class ReferenceEntry:
    """
    One line of a reference list: a lemma, its part of speech, its level, and
    the list it came from.
    """

    lemma: str

    part_of_speech: PartOfSpeech

    level: CefrLevel

    source: str


def parse_reference_rows(
    *,
    rows: Iterable[Mapping[str, str]],
    source: str,
) -> tuple[ReferenceEntry, ...]:
    """
    Turn the rows of one list into entries, one per single-word variant.

    A headword such as `a.m./A.M./am/AM` lists spelling variants separated by
    slashes, and each distinct lowercase variant becomes an entry; multi-word
    headwords are skipped because the pipeline never produces a multi-word
    lemma.
    """

    entries: list[ReferenceEntry] = []

    for row in rows:
        part_of_speech = _WORD_CLASSES.get(
            row["pos"].strip().lower(),
        )

        if part_of_speech is None:
            continue

        level = CefrLevel(
            value=row["CEFR"].strip().upper(),
        )

        seen: set[str] = set()

        for variant in row["headword"].split(
            sep="/",
        ):
            lemma = variant.strip().lower()

            if not lemma or " " in lemma or lemma in seen:
                continue

            seen.add(
                lemma,
            )

            entries.append(
                ReferenceEntry(
                    lemma=lemma,
                    part_of_speech=part_of_speech,
                    level=level,
                    source=source,
                ),
            )

    return tuple(
        entries,
    )


@final
class CsvReferenceLexicon:
    """
    The combined reference lists, indexed by lemma and part of speech.

    Satisfies `ReferenceLexicon` structurally.
    """

    def __init__(
        self,
        *,
        entries: Iterable[ReferenceEntry],
    ) -> None:
        index: dict[tuple[str, PartOfSpeech], ReferenceEntry] = {}

        for entry in entries:
            key = (
                entry.lemma,
                entry.part_of_speech,
            )

            existing = index.get(
                key,
            )

            if existing is None or entry.level.rank < existing.level.rank:
                index[key] = entry

        self._index: Final = MappingProxyType(
            mapping=index,
        )

    @classmethod
    def from_directory(
        cls,
        *,
        directory: Path,
    ) -> CsvReferenceLexicon:
        """
        Load every shipped list from `directory`.
        """

        entries: list[ReferenceEntry] = []

        for name in REFERENCE_FILES:
            path = directory / name

            # NOTE:
            # The directory is relative to where the command runs, so a missing list most often means the wrong
            # directory, and the message says how to fix that rather than showing a traceback.
            try:
                handle = path.open(
                    encoding="utf-8-sig",
                    newline="",
                )

            except FileNotFoundError as exception:
                raise LinguisticResourcesMissingError(
                    f"the reference list {path} does not exist; run from the repository root, or set "
                    "WORDWINNOW_REFERENCE_LISTS_DIR",
                ) from exception

            with handle:
                entries.extend(
                    parse_reference_rows(
                        rows=DictReader(
                            f=handle,
                        ),
                        source=name,
                    ),
                )

        return cls(
            entries=entries,
        )

    def level_of(
        self,
        *,
        lemma: str,
        part_of_speech: PartOfSpeech,
    ) -> CefrLevel | None:
        entry = self._index.get(
            (
                lemma.lower(),
                part_of_speech,
            ),
        )

        return entry.level if entry is not None else None

    def entries(
        self,
    ) -> tuple[ReferenceEntry, ...]:
        """
        Every entry, which is what the level experiment learns from.
        """

        return tuple(
            self._index.values(),
        )
