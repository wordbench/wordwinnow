"""
Type-safe identifiers.
"""

from dataclasses import (
    dataclass,
)
from typing import (
    Self,
    final,
    override,
)
from uuid import (
    UUID,
    uuid4,
)


@final
class InvalidIdentifierError(
    ValueError,
):
    """
    Raised when an identifier has an invalid value.
    """


@dataclass(
    frozen=True,
    kw_only=True,
    slots=True,
)
@final
class AnalysisId:
    """
    Identifies one analysis of one document for one learner profile.
    """

    value: UUID

    def __post_init__(
        self,
    ) -> None:
        if not isinstance(
            self.value,
            UUID,
        ):
            raise InvalidIdentifierError(
                f"AnalysisId requires a UUID, got {
                    type(
                        self.value,
                    ).__name__
                }",
            )

    @classmethod
    def new(
        cls,
    ) -> Self:
        """
        Mint a fresh identifier.
        """

        return cls(
            value=uuid4(),
        )

    @classmethod
    def parse(
        cls,
        *,
        text: str,
    ) -> Self:
        """
        Read an identifier back from its string form.
        """

        try:
            return cls(
                value=UUID(
                    hex=text,
                ),
            )

        except ValueError as exception:
            raise InvalidIdentifierError(
                f"AnalysisId requires a UUID string, got {text!r}",
            ) from exception

    @override
    def __str__(
        self,
    ) -> str:
        return str(
            object=self.value,
        )
