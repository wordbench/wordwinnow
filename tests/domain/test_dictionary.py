"""
Tests for dictionary lookups, and for the selection of definitions a learner
is shown.
"""

from typing import (
    Final,
    final,
)

from pytest import (
    raises,
)

from wordwinnow.domain.dictionary import (
    Definition,
    DictionaryEntry,
    DictionaryLookup,
    InvalidDictionaryDataError,
    License,
    LookupOutcome,
    Meaning,
    Provenance,
    SelectedDefinition,
    select_definitions,
)
from wordwinnow.domain.language import (
    PartOfSpeech,
)

_OPEN_LICENSE: Final = Provenance(
    license=License(
        name="CC BY-SA 3.0",
        url="https://creativecommons.org/licenses/by-sa/3.0",
    ),
    source_urls=("https://example.com/mask",),
)

# NOTE:
# Two entries for one headword, the way a dictionary lists words of different origin, and only the first says where it
# comes from; the definitions are written for this test rather than copied from a dictionary.
_MASK: Final = (
    DictionaryEntry(
        headword="mask",
        phonetic="/mɑːsk/",
        meanings=(
            Meaning(
                word_class="noun",
                part_of_speech=PartOfSpeech.NOUN,
                definitions=(
                    Definition(
                        text="A covering that hides the face.",
                        example="The King wore a black mask.",
                    ),
                    Definition(
                        text="A likeness of a face, cast in wax.",
                    ),
                ),
            ),
            Meaning(
                word_class="verb",
                part_of_speech=PartOfSpeech.VERB,
                definitions=(
                    Definition(
                        text="To hide the face with a mask.",
                    ),
                ),
            ),
        ),
        provenance=_OPEN_LICENSE,
    ),
    DictionaryEntry(
        headword="mask",
        phonetic=None,
        meanings=(
            Meaning(
                word_class="noun",
                part_of_speech=PartOfSpeech.NOUN,
                definitions=(
                    Definition(
                        text="A pretense that hides what someone feels.",
                    ),
                ),
            ),
        ),
    ),
)


@final
class TestSelectDefinitions:
    def test_definitions_in_the_words_own_part_of_speech_come_first_across_entries(
        self,
    ) -> None:
        selected = select_definitions(
            entries=_MASK,
            part_of_speech=PartOfSpeech.NOUN,
            limit=5,
        )

        assert tuple(definition.text for definition in selected) == (
            "A covering that hides the face.",
            "A likeness of a face, cast in wax.",
            "A pretense that hides what someone feels.",
        )

        assert all(definition.part_of_speech_matched for definition in selected)

        assert tuple(definition.provenance for definition in selected) == (
            _OPEN_LICENSE,
            _OPEN_LICENSE,
            None,
        )

    def test_the_limit_is_honored(
        self,
    ) -> None:
        selected = select_definitions(
            entries=_MASK,
            part_of_speech=PartOfSpeech.NOUN,
            limit=1,
        )

        assert selected == (
            SelectedDefinition(
                text="A covering that hides the face.",
                example="The King wore a black mask.",
                part_of_speech_matched=True,
                provenance=_OPEN_LICENSE,
            ),
        )

    def test_without_a_match_the_first_definition_is_shown_and_marked(
        self,
    ) -> None:
        selected = select_definitions(
            entries=_MASK,
            part_of_speech=PartOfSpeech.ADVERB,
            limit=5,
        )

        assert selected == (
            SelectedDefinition(
                text="A covering that hides the face.",
                example="The King wore a black mask.",
                part_of_speech_matched=False,
                provenance=_OPEN_LICENSE,
            ),
        )

    def test_no_entries_means_no_definitions(
        self,
    ) -> None:
        assert (
            select_definitions(
                entries=(),
                part_of_speech=PartOfSpeech.NOUN,
                limit=5,
            )
            == ()
        )

    def test_the_limit_must_be_positive(
        self,
    ) -> None:
        with raises(
            expected_exception=ValueError,
            match="limit must be positive",
        ):
            select_definitions(
                entries=_MASK,
                part_of_speech=PartOfSpeech.NOUN,
                limit=0,
            )


@final
class TestDictionaryLookup:
    def test_found_requires_entries(
        self,
    ) -> None:
        with raises(
            expected_exception=InvalidDictionaryDataError,
        ):
            DictionaryLookup(
                outcome=LookupOutcome.FOUND,
            )

    def test_not_found_cannot_carry_entries(
        self,
    ) -> None:
        with raises(
            expected_exception=InvalidDictionaryDataError,
        ):
            DictionaryLookup(
                outcome=LookupOutcome.NOT_FOUND,
                entries=_MASK,
            )

    def test_unavailable_requires_a_reason_and_nothing_else_may_carry_one(
        self,
    ) -> None:
        with raises(
            expected_exception=InvalidDictionaryDataError,
        ):
            DictionaryLookup(
                outcome=LookupOutcome.UNAVAILABLE,
            )

        with raises(
            expected_exception=InvalidDictionaryDataError,
        ):
            DictionaryLookup(
                outcome=LookupOutcome.NOT_FOUND,
                failure_reason="timed_out",
            )

        lookup = DictionaryLookup(
            outcome=LookupOutcome.UNAVAILABLE,
            failure_reason="timed_out",
        )

        assert lookup.entries == ()

    def test_a_meaning_needs_a_definition_and_an_entry_needs_a_meaning(
        self,
    ) -> None:
        with raises(
            expected_exception=InvalidDictionaryDataError,
        ):
            Meaning(
                word_class="noun",
                part_of_speech=PartOfSpeech.NOUN,
                definitions=(),
            )

        with raises(
            expected_exception=InvalidDictionaryDataError,
        ):
            DictionaryEntry(
                headword="mask",
                phonetic=None,
                meanings=(),
            )


@final
class TestProvenance:
    def test_a_license_needs_a_name_and_a_url(
        self,
    ) -> None:
        with raises(
            expected_exception=InvalidDictionaryDataError,
        ):
            License(
                name=" ",
                url="https://creativecommons.org/licenses/by-sa/3.0",
            )

        with raises(
            expected_exception=InvalidDictionaryDataError,
        ):
            License(
                name="CC BY-SA 3.0",
                url="",
            )

    def test_a_provenance_says_at_least_where_or_under_what_terms(
        self,
    ) -> None:
        with raises(
            expected_exception=InvalidDictionaryDataError,
        ):
            Provenance()

        with raises(
            expected_exception=InvalidDictionaryDataError,
        ):
            Provenance(
                source_urls=(" ",),
            )

        assert (
            Provenance(
                source_urls=("https://example.com/mask",),
            ).license
            is None
        )
