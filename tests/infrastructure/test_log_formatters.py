"""
Log records: their shape, and the context they carry.
"""

from io import (
    StringIO,
)
from json import (
    loads,
)
from logging import (
    INFO,
    Logger,
    StreamHandler,
)
from typing import (
    final,
)

from pytest import (
    fixture,
)

from wordwinnow.infrastructure.log_formatters import (
    ConsoleFormatter,
    JsonFormatter,
)
from wordwinnow.infrastructure.logging import (
    bind_correlation_id,
    current_correlation_id,
)


@fixture
def unbound_correlation_id() -> None:
    bind_correlation_id(
        correlation_id=None,
    )


def _logger(
    name: str,
    formatter: JsonFormatter | ConsoleFormatter,
    /,
) -> tuple[Logger, StringIO]:
    stream = StringIO()

    handler = StreamHandler(
        stream=stream,
    )

    handler.setFormatter(
        fmt=formatter,
    )

    logger = Logger(
        name=name,
        level=INFO,
    )

    logger.addHandler(
        hdlr=handler,
    )

    return (
        logger,
        stream,
    )


@final
class TestJsonFormatter:
    def test_a_record_is_one_json_object_with_the_event_and_its_extras(
        self,
        *,
        unbound_correlation_id: None,
    ) -> None:
        (
            logger,
            stream,
        ) = _logger(
            "foo",
            JsonFormatter(),
        )

        logger.info(
            msg="analysis.completed",
            extra={
                "analysis_id": "bar",
            },
        )

        payload = loads(
            s=stream.getvalue(),
        )

        assert payload["event"] == "analysis.completed"

        assert payload["level"] == "INFO"

        assert payload["logger"] == "foo"

        assert payload["analysis_id"] == "bar"

        assert "correlation_id" not in payload

        assert payload["timestamp"].endswith(
            "+00:00",
        )

    def test_a_secret_query_parameter_in_the_event_text_is_redacted(
        self,
        *,
        unbound_correlation_id: None,
    ) -> None:
        (
            logger,
            stream,
        ) = _logger(
            "foo",
            JsonFormatter(),
        )

        logger.info(
            msg="source.fetched url=https://example.com/?api-key=hidden",
            extra={
                "count": 3,
            },
        )

        payload = loads(
            s=stream.getvalue(),
        )

        assert payload["event"] == "source.fetched url=https://example.com/?api-key=[REDACTED]"

        assert payload["count"] == 3

    def test_the_bound_correlation_id_travels_with_the_record(
        self,
        *,
        unbound_correlation_id: None,
    ) -> None:
        (
            logger,
            stream,
        ) = _logger(
            "foo",
            JsonFormatter(),
        )

        bind_correlation_id(
            correlation_id="baz",
        )

        assert current_correlation_id() == "baz"

        logger.info(
            msg="analysis.started",
        )

        assert (
            loads(
                s=stream.getvalue(),
            )["correlation_id"]
            == "baz"
        )

    def test_an_exception_is_rendered_and_redacted(
        self,
        *,
        unbound_correlation_id: None,
    ) -> None:
        (
            logger,
            stream,
        ) = _logger(
            "foo",
            JsonFormatter(),
        )

        try:
            raise RuntimeError(
                "failed for https://example.com/?api-key=hidden",
            )

        except RuntimeError:
            logger.exception(
                msg="source.failed",
            )

        payload = loads(
            s=stream.getvalue(),
        )

        assert "RuntimeError" in payload["exception"]

        assert "hidden" not in payload["exception"]


@final
class TestConsoleFormatter:
    def test_a_record_is_one_readable_line(
        self,
        *,
        unbound_correlation_id: None,
    ) -> None:
        (
            logger,
            stream,
        ) = _logger(
            "foo",
            ConsoleFormatter(),
        )

        bind_correlation_id(
            correlation_id="baz",
        )

        logger.warning(
            msg="cache.unavailable",
            extra={
                "reason": "qux",
            },
        )

        line = stream.getvalue().rstrip()

        assert line.endswith(
            "WARNING cache.unavailable correlation_id=baz reason=qux",
        )
