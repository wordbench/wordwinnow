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

from pytest import (
    MonkeyPatch,
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
