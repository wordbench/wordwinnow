"""
A cache in front of any dictionary.

A word that was found and a word the dictionary lacks are both facts worth
keeping; an unavailable dictionary is not, so an unavailable lookup is never
cached and the next lookup asks again.

A cache that cannot be reached degrades to a miss rather than failing the
lookup.
"""

from collections.abc import (
    AsyncGenerator,
    Awaitable,
    Callable,
)
from contextlib import (
    asynccontextmanager,
)
from dataclasses import (
    dataclass,
)
from logging import (
    getLogger,
)
from pathlib import (
    Path,
)
from sqlite3 import (
    Error as SqliteError,
)
from time import (
    monotonic,
    time,
)
from types import (
    MappingProxyType,
)
from typing import (
    Any,
    Final,
    Protocol,
    final,
)

from aiosqlite import (
    Connection,
    connect,
)
from redis.exceptions import (
    RedisError,
)

from wordwinnow.application.ports.dictionary import (
    Dictionary,
)
from wordwinnow.domain.dictionary import (
    DictionaryLookup,
    LookupOutcome,
)
from wordwinnow.infrastructure.dictionary.resilience import (
    MonotonicClock,
)
from wordwinnow.infrastructure.dictionary.wire import (
    LookupPayload,
    from_wire,
    to_wire,
)

type LookupObserver = Callable[[DictionaryLookup, bool], None]

type WallClock = Callable[[], float]

# WARN:
# The version in the key names the shape of the cached payload, and it changes whenever that shape does: provenance is
# optional in the payload, so an entry written in a shape without it would still validate and would show Wiktionary's
# definitions without their license.
#
# A found entry lives thirty days, so the entries of a replaced version expire on their own and need no migration.
KEY_PREFIX: Final = "wordwinnow:dictionary:v2:"

_logger: Final = getLogger(
    name="wordwinnow.dictionary",
)

# NOTE:
# The table is the cache's own and is created when it is missing; the key names the payload's shape, as in Redis, so a
# new shape needs no migration: the old entries are never read again, and the open that follows their expiry deletes
# them.
_SCHEMA: Final = (
    "CREATE TABLE IF NOT EXISTS dictionary_cache "
    "(key TEXT PRIMARY KEY, value TEXT NOT NULL, expires_at REAL NOT NULL)"
)

_PURGE: Final = "DELETE FROM dictionary_cache WHERE expires_at <= ?"

_READ: Final = "SELECT value FROM dictionary_cache WHERE key = ? AND expires_at > ?"

_WRITE: Final = (
    "INSERT INTO dictionary_cache (key, value, expires_at) VALUES (?, ?, ?) "
    "ON CONFLICT (key) DO UPDATE SET value = excluded.value, expires_at = excluded.expires_at"
)


@dataclass(
    frozen=True,
    kw_only=True,
    slots=True,
)
@final
class LookupCounts:
    """
    How the lookups a caching dictionary has served were answered: from the
    cache, found or not found by the dictionary behind it, or not at all, by
    reason.
    """

    cached: int

    found: int

    not_found: int

    unavailable: MappingProxyType[str, int]


class DictionaryCache(
    Protocol,
):
    """
    A string store with expiry, which is all the cache needs.
    """

    async def get(
        self,
        *,
        key: str,
    ) -> str | None:
        """
        The value stored under `key`, or `None` when there is none.
        """

        ...

    async def set(
        self,
        *,
        key: str,
        value: str,
        ttl_seconds: int,
    ) -> None:
        """
        Store `value` under `key` for `ttl_seconds`.
        """

        ...


class RedisCommands(
    Protocol,
):
    """
    The two commands the cache sends, as `redis.asyncio.Redis` spells them.
    """

    def get(
        self,
        *,
        name: str,
    ) -> Awaitable[Any]:
        """
        Read the string stored under `name`.
        """

        ...

    def set(
        self,
        *,
        name: str,
        value: str,
        ex: int | None,
    ) -> Awaitable[Any]:
        """
        Store `value` under `name`, expiring after `ex` seconds.
        """

        ...


def cache_key(
    *,
    lemma: str,
) -> str:
    """
    The key a lemma's lookup is stored under, the same for every spelling of
    its case.
    """

    return f"{KEY_PREFIX}{lemma.strip().lower()}"


@final
class InMemoryDictionaryCache:
    """
    A cache in process memory, for the local mode and for tests.
    """

    def __init__(
        self,
        *,
        clock: MonotonicClock = monotonic,
    ) -> None:
        self._clock: Final = clock

        self._entries: Final[dict[str, tuple[str, float]]] = {}

    async def get(
        self,
        *,
        key: str,
    ) -> str | None:
        entry = self._entries.get(
            key,
        )

        if entry is None:
            return None

        (
            value,
            expires_at,
        ) = entry

        if self._clock() >= expires_at:
            del self._entries[key]

            return None

        return value

    async def set(
        self,
        *,
        key: str,
        value: str,
        ttl_seconds: int,
    ) -> None:
        self._entries[key] = (
            value,
            self._clock() + ttl_seconds,
        )


@final
class RedisDictionaryCache:
    """
    A cache in Redis, shared by every enrichment process.
    """

    def __init__(
        self,
        *,
        client: RedisCommands,
    ) -> None:
        self._client: Final = client

    async def get(
        self,
        *,
        key: str,
    ) -> str | None:
        try:
            raw = await self._client.get(
                name=key,
            )

        except RedisError as exception:
            _warn(
                "get",
                exception,
            )

            return None

        if raw is None:
            return None

        return (
            raw.decode()
            if isinstance(
                raw,
                bytes,
            )
            else str(
                object=raw,
            )
        )

    async def set(
        self,
        *,
        key: str,
        value: str,
        ttl_seconds: int,
    ) -> None:
        try:
            await self._client.set(
                name=key,
                value=value,
                ex=ttl_seconds,
            )

        except RedisError as exception:
            _warn(
                "set",
                exception,
            )


@final
class SqliteDictionaryCache:
    """
    A cache in a SQLite file, for the local mode: what one command learns from
    the provider, the next command finds, until the entry expires.

    Expiry is by the wall clock, because a monotonic clock starts again with
    every process, and an entry has to outlive the process that wrote it.
    """

    def __init__(
        self,
        *,
        connection: Connection,
        clock: WallClock = time,
    ) -> None:
        self._connection: Final = connection

        self._clock: Final = clock

    async def get(
        self,
        *,
        key: str,
    ) -> str | None:
        try:
            async with self._connection.execute(
                sql=_READ,
                parameters=(
                    key,
                    self._clock(),
                ),
            ) as cursor:
                row = await cursor.fetchone()

        except SqliteError as exception:
            _warn(
                "get",
                exception,
            )

            return None

        if row is None:
            return None

        return str(
            object=row[0],
        )

    async def set(
        self,
        *,
        key: str,
        value: str,
        ttl_seconds: int,
    ) -> None:
        try:
            await self._connection.execute(
                sql=_WRITE,
                parameters=(
                    key,
                    value,
                    self._clock() + ttl_seconds,
                ),
            )

        except SqliteError as exception:
            _warn(
                "set",
                exception,
            )


@asynccontextmanager
async def open_sqlite_dictionary_cache(
    *,
    path: Path,
    clock: WallClock = time,
) -> AsyncGenerator[DictionaryCache]:
    """
    Open the cache file at `path`, creating it and its folder when missing,
    and delete the entries that have expired, for the duration of one command.

    A file that cannot be opened, such as one another program has damaged,
    leaves the command with a cache in process memory, as the local mode had
    before, rather than failing it; the file is left as it was, for whoever
    wants to look at it.
    """

    connection = await _open(
        path,
        clock(),
    )

    if connection is None:
        yield InMemoryDictionaryCache()

        return

    try:
        yield SqliteDictionaryCache(
            connection=connection,
            clock=clock,
        )

    finally:
        await connection.close()


async def _open(
    path: Path,
    now: float,
    /,
) -> Connection | None:
    try:
        path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        # NOTE:
        # Every statement commits on its own, so a command stopped part way keeps every answer it has written.
        connection = await connect(
            database=path,
            isolation_level=None,
        )

    except (
        OSError,
        SqliteError,
    ) as exception:
        _warn(
            "open",
            exception,
        )

        return None

    try:
        await connection.execute(
            sql=_SCHEMA,
        )

        await connection.execute(
            sql=_PURGE,
            parameters=(now,),
        )

    except SqliteError as exception:
        await connection.close()

        _warn(
            "open",
            exception,
        )

        return None

    return connection


@final
class CachingDictionary:
    """
    Answers from the cache when it can, and asks `inner` otherwise.

    Satisfies `Dictionary` structurally, tells `on_lookup` every lookup and
    whether the cache served it, and counts them for whoever watches.
    """

    def __init__(
        self,
        *,
        inner: Dictionary,
        found_ttl_seconds: int,
        not_found_ttl_seconds: int,
        cache: DictionaryCache,
        on_lookup: LookupObserver | None = None,
    ) -> None:
        self._inner: Final = inner

        self._found_ttl_seconds: Final = found_ttl_seconds

        self._not_found_ttl_seconds: Final = not_found_ttl_seconds

        self._cache: Final = cache

        self._on_lookup: Final = on_lookup

        self._cached = 0

        self._found = 0

        self._not_found = 0

        self._unavailable: Final[dict[str, int]] = {}

    def counts(
        self,
    ) -> LookupCounts:
        """
        How the lookups so far were answered.
        """

        return LookupCounts(
            cached=self._cached,
            found=self._found,
            not_found=self._not_found,
            unavailable=MappingProxyType(
                mapping=dict(
                    self._unavailable,
                ),
            ),
        )

    async def look_up(
        self,
        *,
        lemma: str,
    ) -> DictionaryLookup:
        key = cache_key(
            lemma=lemma,
        )

        cached = await self._cache.get(
            key=key,
        )

        if cached is not None:
            lookup = _decode(
                cached,
            )

            if lookup is not None:
                _logger.debug(
                    msg="dictionary.cache.hit",
                    extra={
                        "lemma": lemma,
                        "outcome": lookup.outcome,
                    },
                )

                self._observe(
                    lookup,
                    True,
                )

                return lookup

            _logger.warning(
                msg="dictionary.cache.invalid",
                extra={
                    "lemma": lemma,
                },
            )

        lookup = await self._inner.look_up(
            lemma=lemma,
        )

        ttl_seconds = self._ttl_for(
            lookup.outcome,
        )

        if ttl_seconds is not None:
            await self._cache.set(
                key=key,
                value=to_wire(
                    lookup=lookup,
                ).model_dump_json(),
                ttl_seconds=ttl_seconds,
            )

        self._observe(
            lookup,
            False,
        )

        return lookup

    def _ttl_for(
        self,
        outcome: LookupOutcome,
        /,
    ) -> int | None:
        if outcome is LookupOutcome.FOUND:
            return self._found_ttl_seconds

        if outcome is LookupOutcome.NOT_FOUND:
            return self._not_found_ttl_seconds

        return None

    def _observe(
        self,
        lookup: DictionaryLookup,
        served_from_cache: bool,
        /,
    ) -> None:
        if served_from_cache:
            self._cached += 1

        elif lookup.outcome is LookupOutcome.FOUND:
            self._found += 1

        elif lookup.outcome is LookupOutcome.NOT_FOUND:
            self._not_found += 1

        elif lookup.failure_reason is not None:
            self._unavailable[lookup.failure_reason] = (
                self._unavailable.get(
                    lookup.failure_reason,
                    0,
                )
                + 1
            )

        if self._on_lookup is not None:
            self._on_lookup(
                lookup,
                served_from_cache,
            )


def _decode(
    value: str,
    /,
) -> DictionaryLookup | None:
    # NOTE:
    # A validation error and an inconsistent payload are both `ValueError`, and both mean the cached value is not a
    # lookup this version wrote.
    try:
        return from_wire(
            payload=LookupPayload.model_validate_json(
                json_data=value,
            ),
        )

    except ValueError:
        return None


def _warn(
    operation: str,
    exception: RedisError | SqliteError | OSError,
    /,
) -> None:
    kind = type(
        exception,
    ).__name__

    _logger.warning(
        msg="dictionary.cache.unavailable",
        extra={
            "operation": operation,
            "kind": kind,
        },
    )
