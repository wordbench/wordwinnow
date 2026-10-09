"""
The name Wordwinnow gives itself to providers: the program with its installed
version, and the repository it is published in.
"""

from importlib.metadata import (
    version,
)
from typing import (
    final,
)

from wordwinnow.infrastructure.user_agent import (
    IDENTIFYING_HEADERS,
    USER_AGENT,
)


@final
class TestUserAgent:
    def test_it_names_the_program_its_version_and_its_repository(
        self,
    ) -> None:
        assert (
            f"Wordwinnow/{
                version(
                    distribution_name='wordwinnow',
                )
            } (https://github.com/wordbench/wordwinnow)"
            == USER_AGENT
        )

    def test_it_is_the_only_header_a_provider_request_adds(
        self,
    ) -> None:
        assert IDENTIFYING_HEADERS == {
            "User-Agent": USER_AGENT,
        }
