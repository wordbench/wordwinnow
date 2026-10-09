"""
A stand-in for the Free Dictionary API, for benchmarks that must be
reproducible.

It answers the provider's own route with a synthetic entry, does not know a
fixed share of words, and takes a fixed time per request, so an experiment
measures the client rather than the provider's mood that day.
"""

from asyncio import (
    sleep as asyncio_sleep,
)
from hashlib import (
    sha1,
)
from typing import (
    Final,
)

from fastapi import (
    FastAPI,
)
from fastapi.responses import (
    JSONResponse,
)

# NOTE:
# The number of values four bytes can hold, not the largest of them, so a word's position lies in [0, 1) and a
# fraction of 1.0 marks every word.
_HASH_SPACE: Final = 2**32


def is_not_found(
    *,
    word: str,
    fraction: float,
) -> bool:
    """
    Whether the replay provider does not know `word`.

    The answer depends only on the word and the fraction, so every run of a
    benchmark sees the same words missing.
    """

    # NOTE:
    # A stable hash, because `hash()` of a string changes between interpreter runs.
    digest = sha1(
        data=word.encode(),
    ).digest()

    position = (
        int.from_bytes(
            bytes=digest[:4],
        )
        / _HASH_SPACE
    )

    return position < fraction


def build_replay_provider(
    *,
    latency_seconds: float,
    not_found_fraction: float,
) -> FastAPI:
    """
    The replay provider as an ASGI application, counting what it answered
    under `/stats`.
    """

    counts = {
        "requests": 0,
        "found": 0,
        "not_found": 0,
    }

    app = FastAPI(
        title="wordwinnow replay provider",
    )

    @app.get(
        path="/api/v2/entries/en/{word}",
    )
    async def look_up(
        word: str,
    ) -> JSONResponse:
        counts["requests"] += 1

        await asyncio_sleep(
            delay=latency_seconds,
        )

        if is_not_found(
            word=word,
            fraction=not_found_fraction,
        ):
            counts["not_found"] += 1

            return JSONResponse(
                content={
                    "title": "No Definitions Found",
                    "message": "Sorry pal, we couldn't find definitions for the word you were looking for.",
                    "resolution": "You can try the search again at later time or head to the web instead.",
                },
                status_code=404,
            )

        counts["found"] += 1

        return JSONResponse(
            content=_entries_of(
                word,
            ),
        )

    @app.get(
        path="/stats",
    )
    async def stats() -> dict[str, int]:
        return dict(
            counts,
        )

    return app


def _entries_of(
    word: str,
    /,
) -> list[dict[str, object]]:
    return [
        {
            "word": word,
            "phonetic": f"/{word}/",
            "phonetics": [
                {
                    "text": f"/{word}/",
                    "audio": "",
                },
            ],
            "meanings": [
                {
                    "partOfSpeech": "noun",
                    "definitions": [
                        {
                            "definition": f"A synthetic definition of {word}.",
                            "example": f"The benchmark used {word} in a sentence.",
                            "synonyms": [],
                            "antonyms": [],
                        },
                    ],
                    "synonyms": [],
                    "antonyms": [],
                },
            ],
            "sourceUrls": [],
        },
    ]
