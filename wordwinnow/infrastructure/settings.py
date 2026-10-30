"""
Process configuration, read once at each entry point and never at import.

Every field has a default that works for the local mode, and every secret is a
`SecretStr` so it cannot reach a log or an exception message by being printed.
"""

from enum import (
    auto,
)
from pathlib import (
    Path,
)
from typing import (
    Final,
    final,
)

from platformdirs import (
    user_cache_path,
    user_data_path,
)
from pydantic import (
    Field,
    SecretStr,
    field_validator,
)
from pydantic_settings import (
    BaseSettings,
    SettingsConfigDict,
)

from wordwinnow.domain.enums import (
    UnorderedStrEnum,
)

# NOTE:
# The user's own directories hold what the command keeps between runs, wherever it is run from: the analysis store,
# the level model, and the reference lists in the data directory, and the dictionary cache in the cache directory.
#
# They are resolved when `Settings` is built and never at import, and resolving one creates nothing; the code that
# first writes into a directory creates it.
_APPLICATION: Final = "wordwinnow"


def _default_database_url() -> str:
    path = (
        user_data_path(
            appname=_APPLICATION,
        )
        / "wordwinnow.db"
    )

    return f"sqlite+aiosqlite:///{path}"


def _default_dictionary_cache_path() -> Path:
    return (
        user_cache_path(
            appname=_APPLICATION,
        )
        / "dictionary-cache.db"
    )


def _default_reference_lists_dir() -> Path:
    return (
        user_data_path(
            appname=_APPLICATION,
        )
        / "reference"
    )


def _default_level_model_path() -> Path:
    return (
        user_data_path(
            appname=_APPLICATION,
        )
        / "cefr_level_model.joblib"
    )


class LogFormat(
    UnorderedStrEnum,
):
    """
    How log records are rendered.
    """

    CONSOLE = auto()

    JSON = auto()


class DictionaryName(
    UnorderedStrEnum,
):
    """
    Which dictionary answers lookups: the Free Dictionary API over the
    network, or WordNet from the local corpus.
    """

    FREE_DICTIONARY = auto()

    WORDNET = auto()


@final
class Settings(
    BaseSettings,
):
    """
    Everything a wordwinnow process reads from its environment.

    Variables carry the `WORDWINNOW_` prefix, except the OpenTelemetry
    exporter endpoint, which keeps the name the OpenTelemetry specification
    gives it.
    """

    model_config = SettingsConfigDict(
        extra="ignore",
        env_prefix="WORDWINNOW_",
        env_file=".env",
        env_file_encoding="utf-8",
    )

    # NOTE:
    # Stores, in the order the pipeline reaches them: the analysis store, the queue, the cache, the facts.
    database_url: str = Field(
        default_factory=_default_database_url,
    )

    kafka_bootstrap_servers: str = "127.0.0.1:9092"

    redis_url: str | None = None

    clickhouse_host: str = "127.0.0.1"

    clickhouse_port: int = 8123

    clickhouse_database: str = "wordwinnow"

    # NOTE:
    # The account the Compose stack creates, so a process started outside Compose reaches its fact store with nothing
    # set; like the stack's other passwords, it guards a server that listens on 127.0.0.1 alone.
    clickhouse_user: str = "wordwinnow"

    clickhouse_password: SecretStr = SecretStr(
        secret_value="wordwinnow",
    )

    # NOTE:
    # Service addresses: where the CLI finds intake, and where a worker finds enrichment.
    #
    # An unset enrichment URL means the worker calls the dictionary provider directly.
    intake_url: str = "http://127.0.0.1:8010"

    enrichment_url: str | None = None

    intake_port: int = 8010

    enrichment_port: int = 8100

    metrics_port: int = 9464

    # NOTE:
    # The dictionary, and how hard the process may lean on the Free Dictionary API when that is the one named.
    dictionary: DictionaryName = DictionaryName.FREE_DICTIONARY

    dictionary_base_url: str = "https://api.dictionaryapi.dev/api/v2/entries/en"

    dictionary_timeout_seconds: float = 30.0

    dictionary_concurrency: int = Field(
        default=8,
        ge=1,
    )

    # NOTE:
    # A pace for the provider's requests in place of the one its adapter keeps, which holds while these are unset.
    dictionary_requests_per_second: float | None = Field(
        default=None,
        gt=0,
    )

    dictionary_burst: int | None = Field(
        default=None,
        ge=1,
    )

    # NOTE:
    # A remote lookup waits for the enrichment service, which itself makes up to three provider attempts of up to
    # `dictionary_timeout_seconds` each with up to 30 s between them, 150 s in all with the defaults, so this outlasts
    # them; a wait for the request budget, or for the answer to a trial after an outage, comes on top and can still
    # run past it.
    enrichment_timeout_seconds: float = Field(
        default=180.0,
        gt=0,
    )

    # NOTE:
    # How long one analysis may wait, in all, for a dictionary provider that stopped answering, before WordNet stands
    # in for every word still waiting: three minutes, which covers one wait of the two minutes the provider's network
    # asks for when the provider's own server is unreachable and a trial after it; nought fails fast.
    dictionary_patience_seconds: float = Field(
        default=180.0,
        ge=0,
    )

    dictionary_found_ttl_seconds: int = 30 * 24 * 60 * 60

    dictionary_not_found_ttl_seconds: int = 7 * 24 * 60 * 60

    # NOTE:
    # Where the local mode keeps the dictionary's answers from one command to the next; the distributed mode keeps
    # them in Redis.
    dictionary_cache_path: Path = Field(
        default_factory=_default_dictionary_cache_path,
    )

    # NOTE:
    # The secondary source, optional; an unset key disables the source rather than the process.
    nyt_api_key: SecretStr | None = None

    # NOTE:
    # Linguistic resources and the level model.
    nltk_data: Path | None = None

    reference_lists_dir: Path = Field(
        default_factory=_default_reference_lists_dir,
    )

    level_model_path: Path = Field(
        default_factory=_default_level_model_path,
    )

    # NOTE:
    # Observability.
    log_format: LogFormat = LogFormat.CONSOLE

    # NOTE:
    # Unset, each process chooses: the command line logs warnings and worse, so a report is not preceded by its own
    # start-up events, and a service logs everything from INFO up.
    log_level: str | None = None

    tracing_enabled: bool = False

    otel_exporter_otlp_endpoint: str | None = Field(
        default=None,
        validation_alias="OTEL_EXPORTER_OTLP_ENDPOINT",
    )

    # NOTE:
    # Worker behavior.
    kafka_consumer_group: str = "wordwinnow-analysis-workers"

    stale_after_seconds: float = Field(
        default=600.0,
        gt=0,
    )

    # NOTE:
    # `.env.example` leaves the key empty for a reader to fill in, and an empty key is no key: kept as a secret, it
    # would enable every API source and fail on each request instead.
    @field_validator(
        "nyt_api_key",
        mode="before",
    )
    @classmethod
    def _blank_key_is_no_key(
        cls,
        value: object,
        /,
    ) -> object:
        if (
            isinstance(
                value,
                str,
            )
            and not value.strip()
        ):
            return None

        return value


def load_settings() -> Settings:
    """
    Read the settings from the environment and the `.env` file, once.
    """

    return Settings()
