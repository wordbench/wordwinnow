"""
How Wordwinnow names itself to the providers it calls: the program, its
version, and where it is published.
"""

from types import (
    MappingProxyType,
)
from typing import (
    Final,
)

from wordwinnow.infrastructure.distribution import (
    VERSION,
)

# NOTE:
# The value names the program and where it is published, as RFC 9110 intends the field, and never whoever runs it:
# every installation sends the same value, so it claims neither that the project's author operates an installation nor
# which installation is asking.
USER_AGENT: Final = f"Wordwinnow/{VERSION} (https://github.com/wordbench/wordwinnow)"

IDENTIFYING_HEADERS: Final = MappingProxyType(
    mapping={
        "User-Agent": USER_AGENT,
    },
)
