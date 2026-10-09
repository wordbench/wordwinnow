"""
The replay provider over an in-process ASGI transport, including through the
real client.
"""

from typing import (
    Final,
    final,
)

from fastapi import (
    FastAPI,
)
from httpx2 import (
    ASGITransport,
    AsyncClient,
    Response,
)
from pytest import (
    MonkeyPatch,
)

from wordwinnow.domain.dictionary import (
    LookupOutcome,
)
from wordwinnow.domain.language import (
    PartOfSpeech,
)
from wordwinnow.infrastructure.dictionary.free_dictionary import (
    FreeDictionaryClient,
)
from wordwinnow.services.bench import (
    replay_provider,
)
from wordwinnow.services.bench.replay_provider import (
    build_replay_provider,
    is_not_found,
)

_BASE_URL: Final = "http://replay.test"

_SHA1_DIGEST_BYTES: Final = 20

_WORDS: Final = tuple(
    f"word{index}"
    for index in range(
        40,
    )
)


def _client(
    app: FastAPI,
    /,
) -> AsyncClient:
    return AsyncClient(
        base_url=_BASE_URL,
        transport=ASGITransport(
            app=app,
        ),
    )


async def _get(
    app: FastAPI,
    path: str,
    /,
) -> Response:
    async with _client(
        app,
    ) as client:
        return await client.get(
            url=path,
        )


@final
class HighestDigest:
    """
    A hash whose digest is the largest one there is, standing in for
    `hashlib.sha1`.
    """

    def __init__(
        self,
        *,
        data: bytes,
    ) -> None:
        self.data: Final = data

    def digest(
        self,
    ) -> bytes:
        return b"\xff" * _SHA1_DIGEST_BYTES


@final
class TestIsNotFound:
    def test_a_fraction_of_one_marks_even_the_word_with_the_highest_hash(
        self,
        *,
        monkeypatch: MonkeyPatch,
    ) -> None:
        # NOTE:
        # No real word is known to hash this high, so the digest is replaced rather than searched for.
        monkeypatch.setattr(
            target=replay_provider,
            name="sha1",
            value=HighestDigest,
        )

        assert is_not_found(
            word="foo",
            fraction=1.0,
        )

    def test_the_answer_depends_only_on_the_word_and_the_fraction(
        self,
    ) -> None:
        assert not any(
            is_not_found(
                word=word,
                fraction=0.0,
            )
            for word in _WORDS
        )

        assert all(
            is_not_found(
                word=word,
                fraction=1.0,
            )
            for word in _WORDS
        )

        answers = tuple(
            is_not_found(
                word=word,
                fraction=0.5,
            )
            for word in _WORDS
        )

        assert answers == tuple(
            is_not_found(
                word=word,
                fraction=0.5,
            )
            for word in _WORDS
        )

        assert any(
            answers,
        )

        assert not all(
            answers,
        )


@final
class TestReplayProvider:
    async def test_a_known_word_is_answered_with_a_payload_the_real_client_reads(
        self,
    ) -> None:
        app = build_replay_provider(
            latency_seconds=0.0,
            not_found_fraction=0.0,
        )

        async with _client(
            app,
        ) as client:
            dictionary = FreeDictionaryClient(
                base_url=f"{_BASE_URL}/api/v2/entries/en",
                timeout_seconds=5.0,
                requests_per_second=1_000.0,
                burst=10,
                client=client,
            )

            lookup = await dictionary.look_up(
                lemma="photograph",
            )

        assert lookup.outcome is LookupOutcome.FOUND

        (entry,) = lookup.entries

        assert entry.headword == "photograph"

        assert entry.phonetic == "/photograph/"

        (meaning,) = entry.meanings

        assert meaning.part_of_speech is PartOfSpeech.NOUN

        assert meaning.definitions[0].example is not None

    async def test_an_unknown_word_is_answered_the_way_the_provider_answers(
        self,
    ) -> None:
        app = build_replay_provider(
            latency_seconds=0.0,
            not_found_fraction=1.0,
        )

        response = await _get(
            app,
            "/api/v2/entries/en/photograph",
        )

        assert response.status_code == 404

        assert response.json()["title"] == "No Definitions Found"

    async def test_the_stats_count_every_kind_of_answer(
        self,
    ) -> None:
        app = build_replay_provider(
            latency_seconds=0.0,
            not_found_fraction=0.5,
        )

        found = next(
            word
            for word in _WORDS
            if not is_not_found(
                word=word,
                fraction=0.5,
            )
        )

        missing = next(
            word
            for word in _WORDS
            if is_not_found(
                word=word,
                fraction=0.5,
            )
        )

        assert (
            await _get(
                app,
                f"/api/v2/entries/en/{found}",
            )
        ).status_code == 200

        assert (
            await _get(
                app,
                f"/api/v2/entries/en/{missing}",
            )
        ).status_code == 404

        stats = await _get(
            app,
            "/stats",
        )

        assert stats.json() == {
            "requests": 2,
            "found": 1,
            "not_found": 1,
        }
