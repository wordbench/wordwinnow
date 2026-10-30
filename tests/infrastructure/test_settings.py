"""
Settings: read from the environment with the project's prefix, secrets never
printed.
"""

from pathlib import (
    Path,
)
from typing import (
    final,
)

from platformdirs import (
    user_cache_path,
    user_data_path,
)
from pydantic import (
    ValidationError,
)
from pytest import (
    MonkeyPatch,
    raises,
)

from wordwinnow.infrastructure.dictionary.free_dictionary import (
    MAX_ATTEMPTS,
    MAX_DELAY_SECONDS,
)
from wordwinnow.infrastructure.settings import (
    DictionaryName,
    LogFormat,
    Settings,
)


@final
class TestSettings:
    def test_defaults_work_for_the_local_mode(
        self,
        *,
        monkeypatch: MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        monkeypatch.chdir(
            path=tmp_path,
        )

        settings = Settings()

        assert settings.database_url.startswith(
            "sqlite+aiosqlite:///",
        )

        assert settings.redis_url is None

        assert settings.enrichment_url is None

        assert settings.nyt_api_key is None

        assert settings.log_format is LogFormat.CONSOLE

        assert settings.dictionary is DictionaryName.FREE_DICTIONARY

    def test_no_test_sees_the_real_user_directories(
        self,
        *,
        tmp_path: Path,
    ) -> None:
        assert user_data_path(
            appname="wordwinnow",
        ).is_relative_to(
            other=tmp_path,
        )

        assert user_cache_path(
            appname="wordwinnow",
        ).is_relative_to(
            other=tmp_path,
        )

    def test_the_state_defaults_to_the_users_directories_and_resolving_them_creates_none(
        self,
        *,
        monkeypatch: MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        monkeypatch.chdir(
            path=tmp_path,
        )

        data = user_data_path(
            appname="wordwinnow",
        )

        cache = user_cache_path(
            appname="wordwinnow",
        )

        settings = Settings()

        assert (
            settings.database_url,
            settings.level_model_path,
            settings.reference_lists_dir,
            settings.dictionary_cache_path,
            settings.nltk_data,
        ) == (
            f"sqlite+aiosqlite:///{data / 'wordwinnow.db'}",
            data / "cefr_level_model.joblib",
            data / "reference",
            cache / "dictionary-cache.db",
            None,
        )

        assert (
            tuple(
                tmp_path.rglob(
                    pattern="*",
                ),
            )
            == ()
        )

    def test_each_default_gives_way_to_its_variable(
        self,
        *,
        monkeypatch: MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        monkeypatch.setenv(
            name="WORDWINNOW_DATABASE_URL",
            value="sqlite+aiosqlite:///foo.db",
        )

        monkeypatch.setenv(
            name="WORDWINNOW_LEVEL_MODEL_PATH",
            value=str(
                object=tmp_path / "foo.joblib",
            ),
        )

        monkeypatch.setenv(
            name="WORDWINNOW_REFERENCE_LISTS_DIR",
            value=str(
                object=tmp_path / "foo",
            ),
        )

        monkeypatch.setenv(
            name="WORDWINNOW_DICTIONARY_CACHE_PATH",
            value=str(
                object=tmp_path / "foo.db",
            ),
        )

        settings = Settings()

        assert (
            settings.database_url,
            settings.level_model_path,
            settings.reference_lists_dir,
            settings.dictionary_cache_path,
        ) == (
            "sqlite+aiosqlite:///foo.db",
            tmp_path / "foo.joblib",
            tmp_path / "foo",
            tmp_path / "foo.db",
        )

    def test_a_remote_lookup_outlasts_the_enrichment_services_own_retries(
        self,
        *,
        monkeypatch: MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        monkeypatch.chdir(
            path=tmp_path,
        )

        settings = Settings()

        retries = MAX_ATTEMPTS * settings.dictionary_timeout_seconds + (MAX_ATTEMPTS - 1) * MAX_DELAY_SECONDS

        assert settings.enrichment_timeout_seconds > retries

    def test_the_dictionary_pace_is_left_to_the_provider_unless_the_environment_names_one(
        self,
        *,
        monkeypatch: MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        monkeypatch.chdir(
            path=tmp_path,
        )

        unset = Settings()

        monkeypatch.setenv(
            name="WORDWINNOW_DICTIONARY_REQUESTS_PER_SECOND",
            value="0.5",
        )

        monkeypatch.setenv(
            name="WORDWINNOW_DICTIONARY_BURST",
            value="1",
        )

        named = Settings()

        assert (
            unset.dictionary_requests_per_second,
            unset.dictionary_burst,
            named.dictionary_requests_per_second,
            named.dictionary_burst,
        ) == (
            None,
            None,
            0.5,
            1,
        )

    def test_a_pace_that_would_admit_no_request_is_refused(
        self,
        *,
        monkeypatch: MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        monkeypatch.chdir(
            path=tmp_path,
        )

        for name in (
            "WORDWINNOW_DICTIONARY_REQUESTS_PER_SECOND",
            "WORDWINNOW_DICTIONARY_BURST",
        ):
            with monkeypatch.context() as scoped:
                scoped.setenv(
                    name=name,
                    value="0",
                )

                with raises(
                    expected_exception=ValidationError,
                ):
                    Settings()

    def test_an_empty_key_is_no_key(
        self,
        *,
        monkeypatch: MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        monkeypatch.chdir(
            path=tmp_path,
        )

        for value in (
            "",
            "   ",
        ):
            monkeypatch.setenv(
                name="WORDWINNOW_NYT_API_KEY",
                value=value,
            )

            assert Settings().nyt_api_key is None

    def test_the_prefix_is_read_and_a_secret_does_not_print(
        self,
        *,
        monkeypatch: MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        monkeypatch.chdir(
            path=tmp_path,
        )

        monkeypatch.setenv(
            name="WORDWINNOW_NYT_API_KEY",
            value="hidden",
        )

        monkeypatch.setenv(
            name="WORDWINNOW_LOG_FORMAT",
            value="json",
        )

        monkeypatch.setenv(
            name="WORDWINNOW_DICTIONARY",
            value="wordnet",
        )

        monkeypatch.setenv(
            name="OTEL_EXPORTER_OTLP_ENDPOINT",
            value="http://collector.example.com:4318",
        )

        settings = Settings()

        assert settings.nyt_api_key is not None

        assert settings.nyt_api_key.get_secret_value() == "hidden"

        assert settings.dictionary is DictionaryName.WORDNET

        assert "hidden" not in repr(
            settings,
        )

        assert settings.log_format is LogFormat.JSON

        assert settings.otel_exporter_otlp_endpoint == "http://collector.example.com:4318"
