"""
Tests for the composition root's choice of dictionary: the one place another
implementation of the `Dictionary` port is wired in.
"""

from pathlib import (
    Path,
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
