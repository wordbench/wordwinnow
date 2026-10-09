"""
The reference word lists: how their rows become entries, and the shipped lists
themselves.
"""

from pathlib import (
    Path,
)
from typing import (
    Final,
    final,
)

from pytest import (
    raises,
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
from wordwinnow.infrastructure.lexicon.reference_lists import (
    REFERENCE_FILES,
    CsvReferenceLexicon,
    parse_reference_rows,
)

_REFERENCE_DIR: Final = (
    Path(
        __file__,
    ).parents[3]
    / "data/reference"
)


@final
class TestParseReferenceRows:
    def test_variants_split_multiword_skipped_and_other_classes_ignored(
        self,
    ) -> None:
        entries = parse_reference_rows(
            rows=[
                {
                    "headword": "a.m./A.M./am",
                    "pos": "adverb",
                    "CEFR": "A1",
                },
                {
                    "headword": "ice cream",
                    "pos": "noun",
                    "CEFR": "A1",
                },
                {
                    "headword": "the",
                    "pos": "determiner",
                    "CEFR": "A1",
                },
                {
                    "headword": "Timid",
                    "pos": "adjective",
                    "CEFR": "c1",
                },
            ],
            source="cefrj-vocabulary-profile-1.5",
        )

        assert tuple(
            (
                entry.lemma,
                entry.part_of_speech,
                entry.level,
            )
            for entry in entries
        ) == (
            (
                "a.m.",
                PartOfSpeech.ADVERB,
                CefrLevel.A1,
            ),
            (
                "am",
                PartOfSpeech.ADVERB,
                CefrLevel.A1,
            ),
            (
                "timid",
                PartOfSpeech.ADJECTIVE,
                CefrLevel.C1,
            ),
        )


@final
class TestCsvReferenceLexicon:
    def test_the_lowest_level_wins_for_a_repeated_entry(
        self,
    ) -> None:
        # NOTE:
        # The real rows: Octanove lists the noun at C2 as a run-down bar and CEFR-J at B1 without naming a meaning,
        # and one level per lemma and part of speech keeps the lower, so the bar's level is not what a learner is
        # shown.
        lexicon = CsvReferenceLexicon(
            entries=parse_reference_rows(
                rows=[
                    {
                        "headword": "dive",
                        "pos": "noun",
                        "CEFR": "C2",
                    },
                    {
                        "headword": "dive",
                        "pos": "noun",
                        "CEFR": "B1",
                    },
                    {
                        "headword": "dive",
                        "pos": "verb",
                        "CEFR": "B1",
                    },
                ],
                source="octanove-vocabulary-profile-c1c2-1.0",
            ),
        )

        assert (
            lexicon.level_of(
                lemma="Dive",
                part_of_speech=PartOfSpeech.NOUN,
            )
            is CefrLevel.B1
        )

        assert (
            lexicon.level_of(
                lemma="dive",
                part_of_speech=PartOfSpeech.VERB,
            )
            is CefrLevel.B1
        )

        assert (
            lexicon.level_of(
                lemma="dive",
                part_of_speech=PartOfSpeech.ADVERB,
            )
            is None
        )

    def test_the_shipped_lists_load_and_cover_every_level(
        self,
    ) -> None:
        lexicon = CsvReferenceLexicon.from_directory(
            directory=_REFERENCE_DIR,
        )

        entries = lexicon.entries()

        assert (
            len(
                entries,
            )
            > 9000
        )

        assert {entry.level for entry in entries} == set(
            CefrLevel,
        )

        assert {entry.source for entry in entries} == set(
            REFERENCE_FILES,
        )

        assert (
            lexicon.level_of(
                lemma="photograph",
                part_of_speech=PartOfSpeech.NOUN,
            )
            is CefrLevel.A2
        )


@final
class TestMissingLists:
    def test_a_missing_list_names_the_setting_that_finds_it(
        self,
        *,
        tmp_path: Path,
    ) -> None:
        with raises(
            expected_exception=LinguisticResourcesMissingError,
            match="WORDWINNOW_REFERENCE_LISTS_DIR",
        ):
            CsvReferenceLexicon.from_directory(
                directory=tmp_path,
            )
