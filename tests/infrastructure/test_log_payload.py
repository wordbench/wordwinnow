"""
The redaction every event text and span attribute passes through.
"""

from typing import (
    final,
)

from wordwinnow.infrastructure.logging import (
    redact,
)


@final
class TestRedact:
    def test_only_secret_parameters_change(
        self,
    ) -> None:
        assert (
            redact(
                text="https://example.com/a?page=2&API_KEY=x&client_secret=y",
            )
            == "https://example.com/a?page=2&API_KEY=[REDACTED]&client_secret=[REDACTED]"
        )
