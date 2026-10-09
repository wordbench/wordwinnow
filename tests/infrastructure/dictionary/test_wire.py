"""
Tests for the wire format dictionary lookups travel and are cached in.
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
)
from wordwinnow.domain.language import (
    PartOfSpeech,
)
from wordwinnow.infrastructure.dictionary.wire import (
    FailureReason,
    LookupPayload,
    from_wire,
    to_wire,
    unavailable,
)

_FOUND: Final = DictionaryLookup(
    outcome=LookupOutcome.FOUND,
    entries=(
        DictionaryEntry(
            headword="photograph",
            phonetic="/ˈfəʊtəɡrɑːf/",
            meanings=(
                Meaning(
                    word_class="noun",
                    part_of_speech=PartOfSpeech.NOUN,
                    definitions=(
                        Definition(
                            text="A picture made with a camera.",
                            example="The King wanted the photograph back.",
                        ),
                    ),
                ),
                Meaning(
                    word_class="proper noun",
                    part_of_speech=None,
                    definitions=(
                        Definition(
                            text="A river in Idaho that joins the Snake River.",
                        ),
                    ),
                ),
            ),
            provenance=Provenance(
                license=License(
                    name="CC BY-SA 3.0",
                    url="https://creativecommons.org/licenses/by-sa/3.0",
                ),
                source_urls=("https://example.com/photograph",),
            ),
        ),
    ),
)


_NOT_FOUND: Final = DictionaryLookup(
    outcome=LookupOutcome.NOT_FOUND,
)


_UNAVAILABLE: Final = unavailable(
    reason=FailureReason.TIMED_OUT,
)


@final
class TestWire:
    def test_a_lookup_survives_the_round_trip(
        self,
    ) -> None:
        for lookup in (
            _FOUND,
            _NOT_FOUND,
            _UNAVAILABLE,
        ):
            encoded = to_wire(
                lookup=lookup,
            ).model_dump_json()

            decoded = from_wire(
                payload=LookupPayload.model_validate_json(
                    json_data=encoded,
                ),
            )

            assert decoded == lookup

    def test_the_part_of_speech_travels_as_its_value_or_null(
        self,
    ) -> None:
        payload = to_wire(
            lookup=_FOUND,
        ).model_dump(
            mode="json",
        )

        (
            noun,
            proper_noun,
        ) = payload["entries"][0]["meanings"]

        assert noun["part_of_speech"] == "noun"

        assert proper_noun["part_of_speech"] is None

        assert payload["outcome"] == "found"

    def test_an_inconsistent_payload_is_refused(
        self,
    ) -> None:
        with raises(
            expected_exception=InvalidDictionaryDataError,
        ):
            from_wire(
                payload=LookupPayload(
                    outcome=LookupOutcome.FOUND,
                ),
            )
