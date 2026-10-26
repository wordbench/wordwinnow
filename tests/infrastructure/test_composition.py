"""
Tests for the composition root's choice of dictionary: the one place another
implementation of the `Dictionary` port is wired in, and where its cache
lives.
"""

from contextlib import (
    closing,
)
from pathlib import (
    Path,
)
from sqlite3 import (
    connect,
)
from typing import (
    final,
)

from httpx2 import (
    AsyncClient,
    MockTransport,
    Request,
    Response,
)
from pytest import (
    MonkeyPatch,
)

from wordwinnow.infrastructure.composition import (
    build_provider_dictionary,
    local_profile,
)
from wordwinnow.infrastructure.dictionary.cache import (
    CachingDictionary,
    InMemoryDictionaryCache,
)
from wordwinnow.infrastructure.dictionary.resilience import (
    BreakerState,
)
from wordwinnow.infrastructure.dictionary.wordnet_dictionary import (
    WordNetDictionary,
)
from wordwinnow.infrastructure.settings import (
    DictionaryName,
    Settings,
)


def _answer(
    request: Request,
    /,
) -> Response:
    """
    The provider's answer for any word: one entry with one definition.
    """

    return Response(
        status_code=200,
        json=[
            {
                "word": "photograph",
                "meanings": [
                    {
                        "partOfSpeech": "noun",
                        "definitions": [
                            {
                                "definition": "A picture made with a camera.",
                            },
                        ],
                    },
                ],
            },
        ],
    )


@final
class TestBuildProviderDictionary:
    async def test_the_free_dictionary_answers_by_default_behind_a_cache_and_says_what_it_does(
        self,
        *,
        monkeypatch: MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        monkeypatch.chdir(
            path=tmp_path,
        )

        async with AsyncClient(
            transport=MockTransport(
                handler=_answer,
            ),
        ) as client:
            provided = build_provider_dictionary(
                settings=Settings(),
                client=client,
                cache=InMemoryDictionaryCache(),
                on_lookup=None,
                observer=None,
            )

            for _ in range(
                2,
            ):
                await provided.dictionary.look_up(
                    lemma="photograph",
                )

        assert isinstance(
            provided.dictionary,
            CachingDictionary,
        )

        assert provided.activity is not None

        activity = provided.activity()

        assert (
            activity.counts.found,
            activity.counts.cached,
            activity.provider.circuit,
            activity.provider.lookups,
        ) == (
            1,
            1,
            BreakerState.CLOSED,
            (),
        )

    async def test_wordnet_is_chosen_by_name_and_answers_without_the_cache(
        self,
        *,
        monkeypatch: MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        monkeypatch.chdir(
            path=tmp_path,
        )

        async with AsyncClient() as client:
            provided = build_provider_dictionary(
                settings=Settings(
                    dictionary=DictionaryName.WORDNET,
                ),
                client=client,
                cache=InMemoryDictionaryCache(),
                on_lookup=None,
                observer=None,
            )

        assert isinstance(
            provided.dictionary,
            WordNetDictionary,
        )

        assert provided.activity is None


@final
class TestLocalProfile:
    async def test_the_dictionary_answers_are_kept_in_their_file_only_when_the_dictionary_is_asked(
        self,
        *,
        monkeypatch: MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        monkeypatch.chdir(
            path=tmp_path,
        )

        settings = Settings(
            database_url=f"sqlite+aiosqlite:///{tmp_path / 'wordwinnow.db'}",
            reference_lists_dir=Path(
                __file__,
            ).parents[2]
            / "data/reference",
            level_model_path=tmp_path / "nonexistent.joblib",
        )

        async with local_profile(
            settings=settings,
            include_dictionary=False,
        ) as skipped:
            assert skipped.activity is None

        assert not settings.dictionary_cache_path.exists()

        async with local_profile(
            settings=settings,
            include_dictionary=True,
        ) as asked:
            assert asked.activity is not None

        with closing(
            thing=connect(
                database=settings.dictionary_cache_path,
            ),
        ) as connection:
            assert connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'",
            ).fetchall() == [
                ("dictionary_cache",),
            ]
