"""
The composition root: where settings become adapters and adapters become the
use cases' arguments.

It lives in the infrastructure because it knows every adapter, and the
services above it only ask it for what they need: a profile, or, for the
aggregation worker, the fact store alone.

A profile is an object of already constructed adapters, built inside an
`AsyncExitStack` that closes every client and connection when the process
ends.

Nothing below this module knows which profile it runs in.
"""

from asyncio import (
    to_thread,
)
from collections.abc import (
    AsyncIterator,
    Callable,
    Coroutine,
    Mapping,
)
from contextlib import (
    AsyncExitStack,
    asynccontextmanager,
)
from dataclasses import (
    dataclass,
)
from logging import (
    getLogger,
)
from types import (
    MappingProxyType,
)
from typing import (
    Any,
    Final,
    final,
)

from httpx2 import (
    AsyncClient,
    Timeout,
)
from redis.asyncio import (
    Redis,
)

from wordwinnow.application.ports.analysis_event_publisher import (
    AnalysisEventPublisher,
)
from wordwinnow.application.ports.clock import (
    Clock,
)
from wordwinnow.application.ports.dictionary import (
    Dictionary,
)
from wordwinnow.application.ports.document_source import (
    DocumentSource,
)
from wordwinnow.application.ports.level_estimator import (
    LevelEstimator,
)
from wordwinnow.application.ports.uow import (
    UnitOfWorkFactory,
)
from wordwinnow.application.use_cases.process_an_analysis import (
    Pipeline,
)
from wordwinnow.application.use_cases.purge_expired_analyses import (
    purge_expired_analyses,
)
from wordwinnow.infrastructure.analytics.clickhouse import (
    ClickHouseClient,
    ClickHouseVocabularyFacts,
    build_client,
)
from wordwinnow.infrastructure.clock import (
    SystemClock,
)
from wordwinnow.infrastructure.dictionary.activity import (
    DictionaryActivity,
)
from wordwinnow.infrastructure.dictionary.cache import (
    CachingDictionary,
    DictionaryCache,
    InMemoryDictionaryCache,
    LookupObserver,
    RedisDictionaryCache,
)
from wordwinnow.infrastructure.dictionary.disabled import (
    DisabledDictionary,
)
from wordwinnow.infrastructure.dictionary.free_dictionary import (
    FreeDictionaryClient,
    ProviderObserver,
)
from wordwinnow.infrastructure.dictionary.remote import (
    RemoteDictionary,
)
from wordwinnow.infrastructure.dictionary.wordnet_dictionary import (
    WordNetDictionary,
)
from wordwinnow.infrastructure.lexicon.reference_lists import (
    CsvReferenceLexicon,
)
from wordwinnow.infrastructure.lexicon.wordnet_semantics import (
    WordNetLexicalSemantics,
)
from wordwinnow.infrastructure.linguistics.nltk_analyzer import (
    NltkLinguisticAnalyzer,
)
from wordwinnow.infrastructure.linguistics.nltk_resources import (
    add_data_path,
)
from wordwinnow.infrastructure.linguistics.wordfreq_frequency import (
    WordfreqFrequency,
)
from wordwinnow.infrastructure.logging import (
    configure_logging,
)
from wordwinnow.infrastructure.messaging.null_publisher import (
    NullPublisher,
)
from wordwinnow.infrastructure.messaging.publisher import (
    KafkaAnalysisEventPublisher,
    build_producer,
)
from wordwinnow.infrastructure.ml.model import (
    NoLevelEstimator,
    load,
)
from wordwinnow.infrastructure.observability.metrics import (
    Metrics,
    build_metrics,
)
from wordwinnow.infrastructure.observability.tracing import (
    Tracing,
    configure_tracing,
    instrument_client,
)
from wordwinnow.infrastructure.persistence.engine import (
    build_engine,
    is_sqlite,
)
from wordwinnow.infrastructure.persistence.migrations import (
    upgrade_to_head,
)
from wordwinnow.infrastructure.persistence.uow import (
    new_unit_of_work_factory,
)
from wordwinnow.infrastructure.settings import (
    DictionaryName,
    Settings,
)
from wordwinnow.infrastructure.sources.nyt.api import (
    NytApi,
)
from wordwinnow.infrastructure.sources.nyt.archive import (
    NytArchiveSource,
)
from wordwinnow.infrastructure.sources.nyt.article_search import (
    NytArticleSearchSource,
)
from wordwinnow.infrastructure.sources.nyt.most_popular import (
    NytMostPopularSource,
)
from wordwinnow.infrastructure.sources.nyt.rss import (
    NytRssSource,
)
from wordwinnow.infrastructure.sources.nyt.top_stories import (
    NytTopStoriesSource,
)

_logger: Final = getLogger(
    name="wordwinnow.composition",
)


def configure_process(
    *,
    settings: Settings,
    service_name: str,
    tracing: bool = True,
    default_log_level: str = "INFO",
) -> Tracing | None:
    """
    The things every process does first: logging, the NLTK data path, and, for
    a service, tracing.

    `default_log_level` applies when the settings name no level, an empty
    value included.
    """

    configure_logging(
        log_format=settings.log_format,
        level=settings.log_level or default_log_level,
    )

    if settings.nltk_data is not None:
        add_data_path(
            path=settings.nltk_data,
        )

    # NOTE:
    # A tracer provider can be installed once per process, so the CLI, which starts services from within a command,
    # leaves tracing to the service.
    if not tracing or not settings.tracing_enabled:
        return None

    return configure_tracing(
        service_name=service_name,
        endpoint=settings.otel_exporter_otlp_endpoint,
    )


async def prepare_local_store(
    *,
    settings: Settings,
) -> None:
    """
    Bring a SQLite analysis store up to date before a local command uses it,
    so the local mode needs no separate migration step.

    A store on any other engine is left to the migration job.
    """

    if not is_sqlite(
        database_url=settings.database_url,
    ):
        return

    # NOTE:
    # Alembic runs its own event loop, so it has to run outside this one.
    await to_thread(
        upgrade_to_head,
        database_url=settings.database_url,
    )


async def purge_local_store(
    *,
    new_unit_of_work: UnitOfWorkFactory,
    clock: Clock,
) -> None:
    """
    Delete the analyses whose text may no longer be kept, each time a local
    command opens the store.
    """

    deleted = await purge_expired_analyses(
        new_unit_of_work=new_unit_of_work,
        clock=clock,
    )

    if deleted:
        _logger.info(
            msg="analyses.purged",
            extra={
                "count": deleted,
            },
        )


def build_level_estimator(
    *,
    settings: Settings,
) -> LevelEstimator:
    """
    The trained level model when its file exists, otherwise a
    `NoLevelEstimator` that leaves every word to the frequency heuristic.
    """

    estimator = load(
        path=settings.level_model_path,
    )

    if estimator is None:
        _logger.info(
            msg="level_model.absent",
            extra={
                "path": str(
                    object=settings.level_model_path,
                ),
            },
        )

        return NoLevelEstimator()

    return estimator


@dataclass(
    frozen=True,
    kw_only=True,
    slots=True,
)
@final
class LinguisticAdapters:
    """
    The local linguistic stack every pipeline needs.
    """

    linguistics: NltkLinguisticAnalyzer

    frequency: WordfreqFrequency

    lexical_semantics: WordNetLexicalSemantics

    reference_lexicon: CsvReferenceLexicon

    level_estimator: LevelEstimator


def build_linguistic_adapters(
    *,
    settings: Settings,
) -> LinguisticAdapters:
    """
    Load NLTK, wordfreq, WordNet, the reference lists, and the model, failing
    before any request is accepted when a corpus is missing.
    """

    return LinguisticAdapters(
        linguistics=NltkLinguisticAnalyzer(),
        frequency=WordfreqFrequency(),
        lexical_semantics=WordNetLexicalSemantics(),
        reference_lexicon=CsvReferenceLexicon.from_directory(
            directory=settings.reference_lists_dir,
        ),
        level_estimator=build_level_estimator(
            settings=settings,
        ),
    )


def build_pipeline(
    *,
    adapters: LinguisticAdapters,
    dictionary: Dictionary,
    dictionary_concurrency: int,
) -> Pipeline:
    """
    The pipeline over the local stack and whichever dictionary is given.
    """

    return Pipeline(
        linguistics=adapters.linguistics,
        frequency=adapters.frequency,
        reference_lexicon=adapters.reference_lexicon,
        level_estimator=adapters.level_estimator,
        dictionary=dictionary,
        lexical_semantics=adapters.lexical_semantics,
        dictionary_concurrency=dictionary_concurrency,
    )


@dataclass(
    frozen=True,
    kw_only=True,
    slots=True,
)
@final
class ProviderDictionary:
    """
    A dictionary, and how to read what it is doing when it asks a provider.

    `activity` is `None` for a dictionary that answers locally and keeps
    nothing worth watching.
    """

    dictionary: Dictionary

    activity: Callable[[], DictionaryActivity] | None


def build_provider_dictionary(
    *,
    settings: Settings,
    client: AsyncClient,
    cache: DictionaryCache,
    on_lookup: LookupObserver | None,
    observer: ProviderObserver | None,
) -> ProviderDictionary:
    """
    The dictionary the settings name: the Free Dictionary API behind a cache,
    or WordNet, which answers locally and needs neither the cache nor the
    budget.

    This is the one place that chooses a `Dictionary`; another dictionary is
    an adapter for that port and one branch here.
    """

    if settings.dictionary is DictionaryName.WORDNET:
        return ProviderDictionary(
            dictionary=WordNetDictionary(),
            activity=None,
        )

    provider = FreeDictionaryClient(
        base_url=settings.dictionary_base_url,
        timeout_seconds=settings.dictionary_timeout_seconds,
        requests_per_second=settings.dictionary_requests_per_second,
        burst=settings.dictionary_burst,
        client=client,
        observer=observer,
    )

    caching = CachingDictionary(
        inner=provider,
        found_ttl_seconds=settings.dictionary_found_ttl_seconds,
        not_found_ttl_seconds=settings.dictionary_not_found_ttl_seconds,
        cache=cache,
        on_lookup=on_lookup,
    )

    def activity() -> DictionaryActivity:
        return DictionaryActivity(
            counts=caching.counts(),
            provider=provider.activity(),
        )

    return ProviderDictionary(
        dictionary=caching,
        activity=activity,
    )


# NOTE:
# The names a request uses to pick a source; adding a source is one adapter and one entry here.
FEED_SOURCE: Final = "nyt-rss"

API_SOURCES: Final = (
    "nyt-top-stories",
    "nyt-most-popular",
    "nyt-article-search",
    "nyt-archive",
)


def configured_source_names(
    *,
    settings: Settings,
) -> tuple[str, ...]:
    """
    The sources these settings enable: the feed always, the APIs with a key.
    """

    if settings.nyt_api_key is None:
        return (FEED_SOURCE,)

    return (
        FEED_SOURCE,
        *API_SOURCES,
    )


def build_sources(
    *,
    settings: Settings,
    client: AsyncClient,
) -> MappingProxyType[str, DocumentSource]:
    """
    The external sources by name: the feed needs no credential, the APIs need
    the key.
    """

    sources: dict[str, DocumentSource] = {
        FEED_SOURCE: NytRssSource(
            client=client,
        ),
    }

    if settings.nyt_api_key is not None:
        api = NytApi(
            api_key=settings.nyt_api_key,
            client=client,
        )

        sources.update(
            {
                "nyt-top-stories": NytTopStoriesSource(
                    api=api,
                ),
                "nyt-most-popular": NytMostPopularSource(
                    api=api,
                ),
                "nyt-article-search": NytArticleSearchSource(
                    api=api,
                ),
                "nyt-archive": NytArchiveSource(
                    api=api,
                ),
            },
        )

    _logger.info(
        msg="sources.configured",
        extra={
            "sources": sorted(
                sources,
            ),
        },
    )

    return MappingProxyType(
        mapping=sources,
    )


def build_facts(
    *,
    settings: Settings,
) -> ClickHouseVocabularyFacts:
    """
    The fact store, connected on first use.
    """

    def connect() -> ClickHouseClient:
        return build_client(
            host=settings.clickhouse_host,
            port=settings.clickhouse_port,
            user=settings.clickhouse_user,
            password=settings.clickhouse_password,
        )

    return ClickHouseVocabularyFacts(
        database=settings.clickhouse_database,
        connect=connect,
    )


@dataclass(
    frozen=True,
    kw_only=True,
    slots=True,
)
@final
class StorageProfile:
    """
    The local store alone, for commands that only read or migrate.
    """

    new_unit_of_work: UnitOfWorkFactory

    clock: Clock


@asynccontextmanager
async def storage_profile(
    *,
    settings: Settings,
) -> AsyncIterator[StorageProfile]:
    """
    Open the local store for the duration of one command.
    """

    await prepare_local_store(
        settings=settings,
    )

    engine = build_engine(
        database_url=settings.database_url,
    )

    try:
        new_unit_of_work = new_unit_of_work_factory(
            engine=engine,
        )

        clock = SystemClock()

        await purge_local_store(
            new_unit_of_work=new_unit_of_work,
            clock=clock,
        )

        yield StorageProfile(
            new_unit_of_work=new_unit_of_work,
            clock=clock,
        )

    finally:
        await engine.dispose()


@dataclass(
    frozen=True,
    kw_only=True,
    slots=True,
)
@final
class LocalProfile:
    """
    The local mode: everything in one process, nothing on the network but the
    dictionary provider and the configured sources.

    `activity` reads what the dictionary is doing, and is `None` when no
    provider is asked.
    """

    new_unit_of_work: UnitOfWorkFactory

    publisher: AnalysisEventPublisher

    pipeline: Pipeline

    sources: Mapping[str, DocumentSource]

    clock: Clock

    activity: Callable[[], DictionaryActivity] | None


@asynccontextmanager
async def local_profile(
    *,
    settings: Settings,
    include_dictionary: bool,
) -> AsyncIterator[LocalProfile]:
    """
    Build the local mode's adapters for the duration of one command.

    Without the dictionary, the provider is never asked, and the analysis says
    so in its options.
    """

    # NOTE:
    # The adapters load before the store is prepared, so a command that cannot find its reference lists, such as one
    # run outside the repository's root, stops before it creates a store there.
    adapters = build_linguistic_adapters(
        settings=settings,
    )

    await prepare_local_store(
        settings=settings,
    )

    async with AsyncExitStack() as stack:
        engine = build_engine(
            database_url=settings.database_url,
        )

        stack.push_async_callback(
            engine.dispose,
        )

        new_unit_of_work = new_unit_of_work_factory(
            engine=engine,
        )

        clock = SystemClock()

        await purge_local_store(
            new_unit_of_work=new_unit_of_work,
            clock=clock,
        )

        client = await stack.enter_async_context(
            cm=AsyncClient(
                timeout=Timeout(
                    timeout=settings.dictionary_timeout_seconds,
                ),
            ),
        )

        provided = (
            build_provider_dictionary(
                settings=settings,
                client=client,
                cache=InMemoryDictionaryCache(),
                on_lookup=None,
                observer=None,
            )
            if include_dictionary
            else ProviderDictionary(
                dictionary=DisabledDictionary(),
                activity=None,
            )
        )

        yield LocalProfile(
            new_unit_of_work=new_unit_of_work,
            publisher=NullPublisher(),
            pipeline=build_pipeline(
                adapters=adapters,
                dictionary=provided.dictionary,
                dictionary_concurrency=settings.dictionary_concurrency,
            ),
            sources=build_sources(
                settings=settings,
                client=client,
            ),
            clock=clock,
            activity=provided.activity,
        )


@dataclass(
    frozen=True,
    kw_only=True,
    slots=True,
)
@final
class IntakeProfile:
    """
    What the intake service needs.
    """

    new_unit_of_work: UnitOfWorkFactory

    publisher: AnalysisEventPublisher

    sources: Mapping[str, DocumentSource]

    facts: ClickHouseVocabularyFacts

    clock: Clock

    metrics: Metrics


@asynccontextmanager
async def intake_profile(
    *,
    settings: Settings,
) -> AsyncIterator[IntakeProfile]:
    """
    Build the intake service's adapters for the life of the process.
    """

    async with AsyncExitStack() as stack:
        engine = build_engine(
            database_url=settings.database_url,
        )

        stack.push_async_callback(
            engine.dispose,
        )

        producer = build_producer(
            bootstrap_servers=settings.kafka_bootstrap_servers,
        )

        await producer.start()

        stack.push_async_callback(
            producer.stop,
        )

        client = await stack.enter_async_context(
            cm=AsyncClient(
                timeout=Timeout(
                    timeout=30.0,
                ),
            ),
        )

        instrument_client(
            client=client,
        )

        yield IntakeProfile(
            new_unit_of_work=new_unit_of_work_factory(
                engine=engine,
            ),
            publisher=KafkaAnalysisEventPublisher(
                producer=producer,
            ),
            sources=build_sources(
                settings=settings,
                client=client,
            ),
            facts=build_facts(
                settings=settings,
            ),
            clock=SystemClock(),
            metrics=build_metrics(),
        )


@dataclass(
    frozen=True,
    kw_only=True,
    slots=True,
)
@final
class EnrichmentProfile:
    """
    What the enrichment service needs.

    `activity` reads what the dictionary is doing, and is `None` when no
    provider is asked.
    """

    dictionary: Dictionary

    metrics: Metrics

    activity: Callable[[], DictionaryActivity] | None


@asynccontextmanager
async def enrichment_profile(
    *,
    settings: Settings,
    metrics: Metrics,
    on_lookup: LookupObserver | None = None,
    observer: ProviderObserver | None = None,
    provider_hooks: Mapping[str, list[Callable[..., Coroutine[Any, Any, None]]]] | None = None,
) -> AsyncIterator[EnrichmentProfile]:
    """
    Build the enrichment service's adapters for the life of the process.

    Redis holds the cache when a URL is configured; otherwise the cache lives
    in the process.
    """

    async with AsyncExitStack() as stack:
        client = await stack.enter_async_context(
            cm=AsyncClient(
                timeout=Timeout(
                    timeout=settings.dictionary_timeout_seconds,
                ),
                event_hooks=provider_hooks or {},
            ),
        )

        instrument_client(
            client=client,
        )

        cache: DictionaryCache

        if settings.redis_url is not None:
            redis = Redis.from_url(
                url=settings.redis_url,
            )

            stack.push_async_callback(
                redis.aclose,
            )

            cache = RedisDictionaryCache(
                client=redis,
            )

        else:
            # WARN:
            # An in-process cache is correct for one replica only; two enrichment replicas without Redis would each
            # keep their own and ask the provider twice.
            _logger.warning(
                msg="cache.in_process",
                extra={
                    "reason": "no redis url configured",
                },
            )

            cache = InMemoryDictionaryCache()

        provided = build_provider_dictionary(
            settings=settings,
            client=client,
            cache=cache,
            on_lookup=on_lookup,
            observer=observer,
        )

        yield EnrichmentProfile(
            dictionary=provided.dictionary,
            metrics=metrics,
            activity=provided.activity,
        )


@dataclass(
    frozen=True,
    kw_only=True,
    slots=True,
)
@final
class WorkerProfile:
    """
    What the analysis worker needs.
    """

    new_unit_of_work: UnitOfWorkFactory

    publisher: AnalysisEventPublisher

    pipeline: Pipeline

    clock: Clock

    metrics: Metrics


@asynccontextmanager
async def worker_profile(
    *,
    settings: Settings,
) -> AsyncIterator[WorkerProfile]:
    """
    Build the analysis worker's adapters for the life of the process.

    With an enrichment URL the worker asks the enrichment service; without one
    it calls the provider itself, which is the single-process variant of the
    distributed mode.
    """

    async with AsyncExitStack() as stack:
        engine = build_engine(
            database_url=settings.database_url,
        )

        stack.push_async_callback(
            engine.dispose,
        )

        producer = build_producer(
            bootstrap_servers=settings.kafka_bootstrap_servers,
        )

        await producer.start()

        stack.push_async_callback(
            producer.stop,
        )

        adapters = build_linguistic_adapters(
            settings=settings,
        )

        dictionary: Dictionary

        if settings.enrichment_url is not None:
            client = await stack.enter_async_context(
                cm=AsyncClient(
                    timeout=Timeout(
                        timeout=settings.enrichment_timeout_seconds,
                    ),
                ),
            )

            instrument_client(
                client=client,
            )

            dictionary = RemoteDictionary(
                enrichment_url=settings.enrichment_url,
                timeout_seconds=settings.enrichment_timeout_seconds,
                client=client,
            )

        else:
            client = await stack.enter_async_context(
                cm=AsyncClient(
                    timeout=Timeout(
                        timeout=settings.dictionary_timeout_seconds,
                    ),
                ),
            )

            instrument_client(
                client=client,
            )

            dictionary = build_provider_dictionary(
                settings=settings,
                client=client,
                cache=InMemoryDictionaryCache(),
                on_lookup=None,
                observer=None,
            ).dictionary

        yield WorkerProfile(
            new_unit_of_work=new_unit_of_work_factory(
                engine=engine,
            ),
            publisher=KafkaAnalysisEventPublisher(
                producer=producer,
            ),
            pipeline=build_pipeline(
                adapters=adapters,
                dictionary=dictionary,
                dictionary_concurrency=settings.dictionary_concurrency,
            ),
            clock=SystemClock(),
            metrics=build_metrics(),
        )
