"""
An API client over a mock transport, the key it must never leak, and the
synthetic payloads the adapters read.
"""

from collections.abc import (
    Callable,
)
from typing import (
    Final,
)

from httpx2 import (
    AsyncClient,
    MockTransport,
    Request,
    Response,
)
from pydantic import (
    SecretStr,
)

from wordwinnow.infrastructure.sources.nyt.api import (
    NytApi,
)

KEY: Final = "hidden-key"


def mock_client(
    *,
    handler: Callable[[Request], Response],
) -> AsyncClient:
    return AsyncClient(
        transport=MockTransport(
            handler=handler,
        ),
    )


def mock_api(
    *,
    handler: Callable[[Request], Response],
) -> NytApi:
    return NytApi(
        api_key=SecretStr(
            secret_value=KEY,
        ),
        client=mock_client(
            handler=handler,
        ),
    )


STORIES: Final = {
    "status": "OK",
    "copyright": "Copyright (c) 2026 The New York Times Company.",
    "num_results": 3,
    "results": [
        {
            "title": "A cab waits at the door",
            "abstract": "The King wore a black mask",
            "url": "https://example.com/photograph",
        },
        {
            "title": "Holmes waits in Baker Street",
            "abstract": "He studies the note by the lamp.",
        },
        {
            "title": "The King hides behind a mask",
            "abstract": "She keeps the photograph.",
        },
    ],
}


def search_page(
    *,
    count: int,
) -> dict[str, object]:
    return {
        "status": "OK",
        "response": {
            "docs": [
                {
                    "headline": {
                        "main": f"Headline {index}",
                    },
                    "abstract": f"Abstract {index}.",
                    "lead_paragraph": f"Abstract {index}." if index % 2 else f"Lead {index}.",
                    "web_url": f"https://example.com/{index}",
                }
                for index in range(
                    count,
                )
            ],
            "meta": {
                "hits": count,
            },
        },
    }
