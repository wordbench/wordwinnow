"""
Tests for the composition root's choice of dictionary: the one place another
implementation of the `Dictionary` port is wired in, and where its cache
lives, and for its choice between the level model and none.
"""

from collections.abc import (
    Callable,
)
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
    fixture,
    mark,
    raises,
)

from wordwinnow.application.errors import (
    LevelModelUnusableError,
)
from wordwinnow.infrastructure.composition import (
    build_level_estimator,
    build_provider_dictionary,
    local_profile,
)
from wordwinnow.infrastructure.dictionary.cache import (
    CachingDictionary,
    InMemoryDictionaryCache,
)
from wordwinnow.infrastructure.dictionary.free_dictionary import (
    DEFAULT_BURST,
    DEFAULT_REQUESTS_PER_SECOND,
)
from wordwinnow.infrastructure.dictionary.resilience import (
    BreakerState,
)
from wordwinnow.infrastructure.dictionary.wordnet_dictionary import (
    WordNetDictionary,
)
from wordwinnow.infrastructure.ml.model import (
    ModelFileError,
    NoLevelEstimator,
    SklearnLevelEstimator,
    installed_sklearn_version,
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

    async def test_the_free_dictionary_keeps_its_own_pace_unless_the_settings_name_another(
        self,
        *,
        monkeypatch: MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        monkeypatch.chdir(
            path=tmp_path,
        )

        def pace(
            settings: Settings,
            client: AsyncClient,
            /,
        ) -> tuple[float, int]:
            provided = build_provider_dictionary(
                settings=settings,
                client=client,
                cache=InMemoryDictionaryCache(),
                on_lookup=None,
                observer=None,
            )

            assert provided.activity is not None

            provider = provided.activity().provider

            return (
                provider.requests_per_second,
                provider.burst,
            )

        async with AsyncClient() as client:
            assert (
                pace(
                    Settings(),
                    client,
                ),
                pace(
                    Settings(
                        dictionary_requests_per_second=0.5,
                        dictionary_burst=1,
                    ),
                    client,
                ),
            ) == (
                (
                    DEFAULT_REQUESTS_PER_SECOND,
                    DEFAULT_BURST,
                ),
                (
                    0.5,
                    1,
                ),
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


@fixture
def settings(
    *,
    tmp_path: Path,
) -> Settings:
    return Settings(
        level_model_path=tmp_path / "model.joblib",
    )


@final
class TestBuildLevelEstimator:
    def test_without_a_model_file_every_word_is_left_to_the_frequency_heuristic(
        self,
        *,
        settings: Settings,
    ) -> None:
        assert isinstance(
            build_level_estimator(
                settings=settings,
            ),
            NoLevelEstimator,
        )

    def test_a_model_this_scikit_learn_trained_is_used(
        self,
        *,
        settings: Settings,
        put_model: Callable[..., None],
    ) -> None:
        put_model(
            path=settings.level_model_path,
            state="current",
        )

        assert isinstance(
            build_level_estimator(
                settings=settings,
            ),
            SklearnLevelEstimator,
        )

    @mark.parametrize(
        argnames="state",
        argvalues=(
            "another scikit-learn",
            "another scikit-learn and unreadable",
        ),
    )
    def test_a_model_another_scikit_learn_trained_stops_the_command_and_is_never_opened(
        self,
        *,
        settings: Settings,
        put_model: Callable[..., None],
        state: str,
    ) -> None:
        put_model(
            path=settings.level_model_path,
            state=state,
        )

        # NOTE:
        # A pickle this scikit-learn cannot read raises `ModuleNotFoundError` when it is opened, which is not a
        # `ModelFileError`: the error below, with the stale message, is what shows the file was never opened.
        with raises(
            expected_exception=LevelModelUnusableError,
        ) as caught:
            build_level_estimator(
                settings=settings,
            )

        assert (
            str(
                object=caught.value,
            )
            == f"the level model at {settings.level_model_path} was trained with scikit-learn 0.0.0, and "
            f"{installed_sklearn_version()} is installed; run `wordwinnow setup`"
        )

    @mark.parametrize(
        argnames="state",
        argvalues=(
            "cut short",
            "without a sidecar",
            "a sidecar that is not JSON",
        ),
    )
    def test_a_model_this_code_cannot_read_stops_the_command_with_the_reason_and_the_remedy(
        self,
        *,
        settings: Settings,
        put_model: Callable[..., None],
        state: str,
    ) -> None:
        put_model(
            path=settings.level_model_path,
            state=state,
        )

        with raises(
            expected_exception=LevelModelUnusableError,
        ) as caught:
            build_level_estimator(
                settings=settings,
            )

        assert isinstance(
            caught.value.__cause__,
            ModelFileError,
        )

        assert (
            str(
                object=caught.value,
            )
            == f"{caught.value.__cause__}; run `wordwinnow setup`"
        )
