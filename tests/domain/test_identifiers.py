"""
An analysis's identity, and its string form on the wire and in the store.
"""

from typing import (
    final,
)
from uuid import (
    uuid4,
)

from pytest import (
    raises,
)

from wordwinnow.domain.identifiers import (
    AnalysisId,
    InvalidIdentifierError,
)


@final
class TestAnalysisId:
    def test_a_new_identifier_round_trips_through_its_string_form(
        self,
    ) -> None:
        analysis_id = AnalysisId.new()

        assert (
            AnalysisId.parse(
                text=str(
                    object=analysis_id,
                ),
            )
            == analysis_id
        )

    def test_two_new_identifiers_differ(
        self,
    ) -> None:
        assert AnalysisId.new() != AnalysisId.new()

    def test_a_non_uuid_string_is_rejected(
        self,
    ) -> None:
        with raises(
            expected_exception=InvalidIdentifierError,
        ):
            AnalysisId.parse(
                text="not-a-uuid",
            )

    def test_a_non_uuid_value_is_rejected(
        self,
    ) -> None:
        # WARN:
        # `AnalysisId` is typed to accept a `UUID`; passing a string is an intentional type violation that proves the
        # runtime guard fires, so the diagnostic is suppressed with `ty`'s own rule name.
        with raises(
            expected_exception=InvalidIdentifierError,
        ):
            AnalysisId(
                value=str(
                    object=uuid4(),
                ),  # ty: ignore[invalid-argument-type]
            )
