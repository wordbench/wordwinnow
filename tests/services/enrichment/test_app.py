"""
The enrichment service over HTTP, against a fake dictionary behind the real
cache.
"""

from types import (
    MappingProxyType,
)
from typing import (
    Final,
    final,
)

from httpx2 import (
    ASGITransport,
    AsyncClient,
    ConnectError,
    MockTransport,
    Request,
    Response,
)
from opentelemetry.metrics import (
    get_meter_provider,
)
from opentelemetry.sdk.metrics import (
    MeterProvider,
)
from pytest import (
    MonkeyPatch,
)

from tests.fakes.ports import (
    FakeDictionary,
)
from tests.infrastructure.dictionary.doubles import (
    FakeMonotonicClock,
    RecordingSleep,
)
from tests.services.lifespan import (
    run_lifespan,
)
from wordwinnow.domain.dictionary import (
    Definition,
    DictionaryEntry,
    DictionaryLookup,
    LookupOutcome,
    Meaning,
)
from wordwinnow.domain.language import (
    PartOfSpeech,
)
from wordwinnow.infrastructure.dictionary.activity import (
    ActivityPayload,
    DictionaryActivity,
    from_payload,
)
from wordwinnow.infrastructure.dictionary.cache import (
    CachingDictionary,
    InMemoryDictionaryCache,
    LookupCounts,
)
from wordwinnow.infrastructure.dictionary.free_dictionary import (
    FreeDictionaryClient,
)
from wordwinnow.infrastructure.dictionary.wire import (
    LookupPayload,
    from_wire,
)
from wordwinnow.infrastructure.observability.metrics import (
    build_metrics,
)
from wordwinnow.services.enrichment.app import (
    EnrichmentDependencies,
    build_enrichment_app,
    lookup_observer,
    provider_hooks,
    provider_observer,
)

# NOTE:
# Six words, one more than the failures that open the circuit, so the last is refused.
_HOLMES_WORDS: Final = (
    "photograph",
    "mask",
    "groom",
    "rocket",
    "landau",
    "egria",
)

_PHOTOGRAPH: Final = DictionaryLookup(
    outcome=LookupOutcome.FOUND,
    entries=(
        DictionaryEntry(
            headword="photograph",
            phonetic="/ˈfəʊtəɡrɑːf/",
            meanings=(
                Meaning(
                    word_class="noun",
                    part_of_speech=PartOfSpeech.NOUN,
                    definitions=(
                        Definition(
                            text="A picture made with a camera.",
                            example="The King wanted the photograph back.",
                        ),
                    ),
                ),
            ),
        ),
    ),
)


def _refusing(
    request: Request,
    /,
) -> Response:
    """
    A provider whose server refuses every connection.
    """

    raise ConnectError(
        message="connection refused",
    )


@final
class TestEnrichmentApp:
    async def test_a_lookup_is_served_then_cached_and_both_are_counted(
        self,
    ) -> None:
        metrics = build_metrics()

        inner = FakeDictionary(
            lookups={
                "photograph": _PHOTOGRAPH,
                "saunter": DictionaryLookup(
                    outcome=LookupOutcome.UNAVAILABLE,
                    failure_reason="timed_out",
                ),
            },
        )

        app = build_enrichment_app(
            dependencies=EnrichmentDependencies(
                dictionary=CachingDictionary(
                    inner=inner,
                    found_ttl_seconds=60,
                    not_found_ttl_seconds=60,
                    cache=InMemoryDictionaryCache(),
                    on_lookup=lookup_observer(
                        metrics=metrics,
                    ),
                ),
                metrics=metrics,
            ),
        )

        async with AsyncClient(
            transport=ASGITransport(
                app=app,
            ),
            base_url="http://enrichment.test",
        ) as client:
            first = await client.get(
                url="/lookups/photograph",
                headers={
                    "X-Correlation-Id": "foo",
                },
            )

            second = await client.get(
                url="/lookups/photograph",
            )

            unavailable = await client.get(
                url="/lookups/saunter",
            )

            rendered = (
                await client.get(
                    url="/metrics",
                )
            ).text

        assert first.status_code == 200

        assert first.headers["X-Correlation-Id"] == "foo"

        assert (
            from_wire(
                payload=LookupPayload.model_validate(
                    obj=first.json(),
                ),
            )
            == _PHOTOGRAPH
        )

        assert second.json() == first.json()

        assert inner.looked_up == [
            "photograph",
            "saunter",
        ]

        assert unavailable.status_code == 200

        assert unavailable.json()["outcome"] == "unavailable"

        assert (
            'wordwinnow_dictionary_lookups_total{outcome="found",reason="none",served_from_cache="false"} 1.0'
            in rendered
        )

        assert (
            'wordwinnow_dictionary_lookups_total{outcome="found",reason="none",served_from_cache="true"} 1.0'
            in rendered
        )

        assert (
            'wordwinnow_dictionary_lookups_total{outcome="unavailable",reason="timed_out",served_from_cache="false"}'
            " 1.0" in rendered
        )


@final
class TestProviderHooks:
    async def test_every_round_trip_is_timed_by_status_class(
        self,
    ) -> None:
        metrics = build_metrics()

        def handler(
            request: Request,
            /,
        ) -> Response:
            return Response(
                status_code=503,
            )

        async with AsyncClient(
            transport=MockTransport(
                handler=handler,
            ),
            event_hooks=provider_hooks(
                metrics=metrics,
            ),
        ) as client:
            await client.get(
                url="https://provider.test/api/v2/entries/en/photograph",
            )

        rendered = metrics.render().decode()

        assert 'wordwinnow_dictionary_provider_request_seconds_count{status_class="5xx"} 1.0' in rendered


@final
class TestProviderObserver:
    async def test_waits_and_retries_reach_the_metrics(
        self,
    ) -> None:
        metrics = build_metrics()

        clock = FakeMonotonicClock()

        answers: list[Response | Exception] = [
            Response(
                status_code=503,
                headers={
                    "Retry-After": "2",
                },
            ),
            Response(
                status_code=404,
            ),
        ]

        def handler(
            request: Request,
            /,
        ) -> Response:
            clock.advance(
                seconds=20.0,
            )

            answer = (
                answers.pop(
                    0,
                )
                if len(
                    answers,
                )
                > 1
                else answers[0]
            )

            if isinstance(
                answer,
                Exception,
            ):
                raise answer

            return answer

        client = FreeDictionaryClient(
            base_url="https://provider.test/api/v2/entries/en",
            timeout_seconds=5.0,
            requests_per_second=1_000.0,
            burst=100,
            client=AsyncClient(
                transport=MockTransport(
                    handler=handler,
                ),
            ),
            sleep=RecordingSleep(
                clock=clock,
            ),
            clock=clock,
            observer=provider_observer(
                metrics=metrics,
            ),
        )

        built = metrics.render().decode()

        await client.look_up(
            lemma="photograph",
        )

        answered = metrics.render().decode()

        answers[:] = [
            ConnectError(
                message="connection refused",
            ),
        ]

        for _ in range(
            5,
        ):
            await client.look_up(
                lemma="photograph",
            )

        failing = metrics.render().decode()

        assert 'wordwinnow_dictionary_wait_seconds_total{cause="trial"} 0.0' in built

        assert 'wordwinnow_dictionary_wait_seconds_total{cause="provider"} 40.0' in answered

        assert 'wordwinnow_dictionary_wait_seconds_total{cause="retry"} 2.0' in answered

        assert 'wordwinnow_dictionary_wait_seconds_total{cause="budget"} 0.0' in answered

        assert 'wordwinnow_dictionary_retries_total{reason="provider_error"} 1.0' in answered

        assert 'wordwinnow_dictionary_retries_total{reason="transport_error"} 10.0' in failing


@final
class TestActivity:
    async def test_the_circuit_and_the_activity_are_read_as_they_are_now(
        self,
    ) -> None:
        metrics = build_metrics()

        clock = FakeMonotonicClock()

        client = FreeDictionaryClient(
            base_url="https://provider.test/api/v2/entries/en",
            timeout_seconds=5.0,
            requests_per_second=1_000.0,
            burst=100,
            client=AsyncClient(
                transport=MockTransport(
                    handler=_refusing,
                ),
            ),
            sleep=RecordingSleep(
                clock=clock,
            ),
            clock=clock,
        )

        dictionary = CachingDictionary(
            inner=client,
            found_ttl_seconds=60,
            not_found_ttl_seconds=60,
            cache=InMemoryDictionaryCache(),
        )

        def activity() -> DictionaryActivity:
            return DictionaryActivity(
                counts=dictionary.counts(),
                provider=client.activity(),
            )

        app = build_enrichment_app(
            dependencies=EnrichmentDependencies(
                dictionary=dictionary,
                metrics=metrics,
                activity=activity,
            ),
        )

        async with AsyncClient(
            transport=ASGITransport(
                app=app,
            ),
            base_url="http://enrichment.test",
        ) as service:
            closed = (
                await service.get(
                    url="/metrics",
                )
            ).text

            for lemma in _HOLMES_WORDS:
                await service.get(
                    url=f"/lookups/{lemma}",
                )

            opened = (
                await service.get(
                    url="/metrics",
                )
            ).text

            read = await service.get(
                url="/activity",
            )

            clock.advance(
                seconds=31.0,
            )

            # NOTE:
            # Nothing has been looked up since the circuit opened, and the gauge says half-open all the same: the next
            # lookup is the trial.
            recovered = (
                await service.get(
                    url="/metrics",
                )
            ).text

        assert 'wordwinnow_dictionary_circuit_state{state="closed"} 1.0' in closed

        assert 'wordwinnow_dictionary_circuit_state{state="open"} 1.0' in opened

        assert 'wordwinnow_dictionary_circuit_state{state="half_open"} 1.0' in recovered

        assert 'wordwinnow_dictionary_circuit_state{state="open"} 0.0' in recovered

        assert read.status_code == 200

        assert from_payload(
            payload=ActivityPayload.model_validate(
                obj=read.json(),
            ),
        ).counts == LookupCounts(
            cached=0,
            found=0,
            not_found=0,
            unavailable=MappingProxyType(
                mapping={
                    "transport_error": 5,
                    "circuit_open": 1,
                },
            ),
        )

    async def test_a_dictionary_that_asks_no_provider_has_no_activity(
        self,
    ) -> None:
        app = build_enrichment_app(
            dependencies=EnrichmentDependencies(
                dictionary=FakeDictionary(),
                metrics=build_metrics(),
            ),
        )

        async with AsyncClient(
            transport=ASGITransport(
                app=app,
            ),
            base_url="http://enrichment.test",
        ) as service:
            read = await service.get(
                url="/activity",
            )

        assert read.status_code == 404


@final
class TestTelemetry:
    async def test_starting_up_leaves_the_exporters_to_the_process(
        self,
        *,
        monkeypatch: MonkeyPatch,
    ) -> None:
        # NOTE:
        # With an endpoint in the environment, FastAPI's own setup would install a meter provider at startup and add a
        # second span exporter beside the process's own.
        monkeypatch.setenv(
            name="OTEL_EXPORTER_OTLP_ENDPOINT",
            value="http://collector.invalid:4318",
        )

        app = build_enrichment_app(
            dependencies=EnrichmentDependencies(
                dictionary=FakeDictionary(),
                metrics=build_metrics(),
            ),
        )

        sent = await run_lifespan(
            app=app,
        )

        assert "lifespan.startup.complete" in sent

        assert not isinstance(
            get_meter_provider(),
            MeterProvider,
        )
