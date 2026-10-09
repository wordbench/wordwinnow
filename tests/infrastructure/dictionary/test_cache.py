"""
The caching dictionary over an in-memory store and over a Redis double.
"""

from types import (
    MappingProxyType,
)
from typing import (
    Final,
    final,
)

from redis.exceptions import (
    RedisError,
)

from tests.fakes.ports import (
    FakeDictionary,
)
from tests.infrastructure.dictionary.doubles import (
    FakeMonotonicClock,
)
from wordwinnow.domain.dictionary import (
    Definition,
    DictionaryEntry,
    DictionaryLookup,
    License,
    LookupOutcome,
    Meaning,
    Provenance,
)
from wordwinnow.domain.language import (
    PartOfSpeech,
)
from wordwinnow.infrastructure.dictionary.cache import (
    CachingDictionary,
    InMemoryDictionaryCache,
    LookupCounts,
    RedisDictionaryCache,
    cache_key,
)
from wordwinnow.infrastructure.dictionary.wire import (
    FailureReason,
    unavailable,
)

_FOUND: Final = DictionaryLookup(
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
                Meaning(
                    word_class="proper noun",
                    part_of_speech=None,
                    definitions=(
                        Definition(
                            text="A river in Idaho that joins the Snake River.",
                        ),
                    ),
                ),
            ),
            provenance=Provenance(
                license=License(
                    name="CC BY-SA 3.0",
                    url="https://creativecommons.org/licenses/by-sa/3.0",
                ),
                source_urls=("https://example.com/photograph",),
            ),
        ),
    ),
)


_NOT_FOUND: Final = DictionaryLookup(
    outcome=LookupOutcome.NOT_FOUND,
)


_UNAVAILABLE: Final = unavailable(
    reason=FailureReason.TIMED_OUT,
)


_FOUND_TTL: Final = 100


_NOT_FOUND_TTL: Final = 10


@final
class Observer:
    """
    Records what the cache reports about every lookup.
    """

    def __init__(
        self,
    ) -> None:
        self.seen: Final[list[tuple[LookupOutcome, bool]]] = []

    def __call__(
        self,
        lookup: DictionaryLookup,
        served_from_cache: bool,
        /,
    ) -> None:
        self.seen.append(
            (
                lookup.outcome,
                served_from_cache,
            ),
        )


@final
class FakeRedis:
    """
    The two commands the cache sends, over a dictionary, or failing the way an
    unreachable server does.
    """

    def __init__(
        self,
        *,
        failing: bool = False,
    ) -> None:
        self._failing: Final = failing

        self.stored: Final[dict[str, tuple[bytes, int | None]]] = {}

    async def get(
        self,
        *,
        name: str,
    ) -> bytes | None:
        if self._failing:
            raise RedisError(
                "connection refused",
            )

        entry = self.stored.get(
            name,
        )

        return entry[0] if entry is not None else None

    async def set(
        self,
        *,
        name: str,
        value: str,
        ex: int | None,
    ) -> bool:
        if self._failing:
            raise RedisError(
                "connection refused",
            )

        self.stored[name] = (
            value.encode(),
            ex,
        )

        return True


@final
class Harness:
    """
    A caching dictionary over a recording inner dictionary and an in-memory
    store on a clock the test moves.
    """

    def __init__(
        self,
    ) -> None:
        self.clock: Final = FakeMonotonicClock()

        self.inner: Final = FakeDictionary(
            lookups={
                "photograph": _FOUND,
                "saunter": _UNAVAILABLE,
            },
        )

        self.cache: Final = InMemoryDictionaryCache(
            clock=self.clock,
        )

        self.observer: Final = Observer()

        self.dictionary: Final = CachingDictionary(
            inner=self.inner,
            found_ttl_seconds=_FOUND_TTL,
            not_found_ttl_seconds=_NOT_FOUND_TTL,
            cache=self.cache,
            on_lookup=self.observer,
        )


@final
class TestCachingDictionary:
    async def test_a_found_lookup_is_served_from_the_cache_until_its_ttl_passes(
        self,
    ) -> None:
        harness = Harness()

        first = await harness.dictionary.look_up(
            lemma="photograph",
        )

        second = await harness.dictionary.look_up(
            lemma="photograph",
        )

        assert first == _FOUND

        assert second == _FOUND

        assert harness.inner.looked_up == [
            "photograph",
        ]

        assert harness.observer.seen == [
            (
                LookupOutcome.FOUND,
                False,
            ),
            (
                LookupOutcome.FOUND,
                True,
            ),
        ]

        harness.clock.advance(
            seconds=_FOUND_TTL,
        )

        await harness.dictionary.look_up(
            lemma="photograph",
        )

        assert harness.inner.looked_up == [
            "photograph",
            "photograph",
        ]

    async def test_a_not_found_lookup_is_cached_for_its_own_shorter_ttl(
        self,
    ) -> None:
        harness = Harness()

        await harness.dictionary.look_up(
            lemma="photograph",
        )

        await harness.dictionary.look_up(
            lemma="egria",
        )

        await harness.dictionary.look_up(
            lemma="egria",
        )

        assert harness.inner.looked_up == [
            "photograph",
            "egria",
        ]

        harness.clock.advance(
            seconds=_NOT_FOUND_TTL,
        )

        assert (
            await harness.dictionary.look_up(
                lemma="egria",
            )
        ) == _NOT_FOUND

        assert (
            await harness.dictionary.look_up(
                lemma="photograph",
            )
        ) == _FOUND

        assert harness.inner.looked_up == [
            "photograph",
            "egria",
            "egria",
        ]

    async def test_an_unavailable_lookup_is_never_cached(
        self,
    ) -> None:
        harness = Harness()

        first = await harness.dictionary.look_up(
            lemma="saunter",
        )

        second = await harness.dictionary.look_up(
            lemma="saunter",
        )

        assert first == _UNAVAILABLE

        assert second == _UNAVAILABLE

        assert harness.inner.looked_up == [
            "saunter",
            "saunter",
        ]

        assert harness.observer.seen == [
            (
                LookupOutcome.UNAVAILABLE,
                False,
            ),
            (
                LookupOutcome.UNAVAILABLE,
                False,
            ),
        ]

        assert (
            await harness.cache.get(
                key=cache_key(
                    lemma="saunter",
                ),
            )
        ) is None

    async def test_the_counts_say_where_each_answer_came_from(
        self,
    ) -> None:
        harness = Harness()

        for lemma in (
            "photograph",
            "photograph",
            "egria",
            "egria",
            "saunter",
            "saunter",
        ):
            await harness.dictionary.look_up(
                lemma=lemma,
            )

        assert harness.dictionary.counts() == LookupCounts(
            cached=2,
            found=1,
            not_found=1,
            unavailable=MappingProxyType(
                mapping={
                    "timed_out": 2,
                },
            ),
        )

    async def test_the_key_ignores_case_and_surrounding_space(
        self,
    ) -> None:
        harness = Harness()

        await harness.dictionary.look_up(
            lemma="Photograph",
        )

        await harness.dictionary.look_up(
            lemma=" photograph ",
        )

        assert harness.inner.looked_up == [
            "Photograph",
        ]

        assert (
            cache_key(
                lemma=" Photograph ",
            )
            == "wordwinnow:dictionary:v2:photograph"
        )

    async def test_an_unreadable_cached_value_is_a_miss_and_is_replaced(
        self,
    ) -> None:
        harness = Harness()

        key = cache_key(
            lemma="photograph",
        )

        await harness.cache.set(
            key=key,
            value="not json",
            ttl_seconds=_FOUND_TTL,
        )

        await harness.dictionary.look_up(
            lemma="photograph",
        )

        await harness.dictionary.look_up(
            lemma="photograph",
        )

        assert harness.inner.looked_up == [
            "photograph",
        ]

    async def test_a_failing_redis_degrades_to_a_miss(
        self,
    ) -> None:
        inner = FakeDictionary(
            lookups={
                "photograph": _FOUND,
            },
        )

        dictionary = CachingDictionary(
            inner=inner,
            found_ttl_seconds=_FOUND_TTL,
            not_found_ttl_seconds=_NOT_FOUND_TTL,
            cache=RedisDictionaryCache(
                client=FakeRedis(
                    failing=True,
                ),
            ),
        )

        first = await dictionary.look_up(
            lemma="photograph",
        )

        second = await dictionary.look_up(
            lemma="photograph",
        )

        assert first == _FOUND

        assert second == _FOUND

        assert inner.looked_up == [
            "photograph",
            "photograph",
        ]

    async def test_a_working_redis_stores_the_lookup_with_its_ttl(
        self,
    ) -> None:
        redis = FakeRedis()

        inner = FakeDictionary(
            lookups={
                "photograph": _FOUND,
            },
        )

        dictionary = CachingDictionary(
            inner=inner,
            found_ttl_seconds=_FOUND_TTL,
            not_found_ttl_seconds=_NOT_FOUND_TTL,
            cache=RedisDictionaryCache(
                client=redis,
            ),
        )

        await dictionary.look_up(
            lemma="photograph",
        )

        await dictionary.look_up(
            lemma="egria",
        )

        assert (
            redis.stored[
                cache_key(
                    lemma="photograph",
                )
            ][1]
            == _FOUND_TTL
        )

        assert (
            redis.stored[
                cache_key(
                    lemma="egria",
                )
            ][1]
            == _NOT_FOUND_TTL
        )

        assert (
            await dictionary.look_up(
                lemma="photograph",
            )
        ) == _FOUND

        assert inner.looked_up == [
            "photograph",
            "egria",
        ]


@final
class TestInMemoryDictionaryCache:
    async def test_a_value_expires_when_its_ttl_has_passed(
        self,
    ) -> None:
        clock = FakeMonotonicClock()

        cache = InMemoryDictionaryCache(
            clock=clock,
        )

        await cache.set(
            key="foo",
            value="bar",
            ttl_seconds=10,
        )

        clock.advance(
            seconds=9.5,
        )

        assert (
            await cache.get(
                key="foo",
            )
        ) == "bar"

        clock.advance(
            seconds=0.5,
        )

        assert (
            await cache.get(
                key="foo",
            )
        ) is None

        assert (
            await cache.get(
                key="nonexistent",
            )
        ) is None
