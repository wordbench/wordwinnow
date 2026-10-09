"""
What a log record carries: the correlation identifier bound to the current
context, the trace identifiers of the current span, and the extras the call
site added.

The formatters render this; nothing else needs it.
"""

from contextvars import (
    ContextVar,
)
from datetime import (
    UTC,
    datetime,
)
from logging import (
    LogRecord,
)
from re import (
    compile as compile_pattern,
)
from typing import (
    Any,
    Final,
)

from opentelemetry.trace import (
    get_current_span,
)

# WARN:
# The name stays positional: CPython's `ContextVar` rejects `name=` at run time, on 3.9, 3.12, 3.13, and 3.14 alike,
# although the stub `ty` reads accepts it, and the runtime is the fact that wins.
_correlation_id: Final[ContextVar[str | None]] = ContextVar(
    "wordwinnow_correlation_id",
    default=None,
)

# NOTE:
# The attributes every `LogRecord` carries, discovered from a blank record so the set follows the interpreter.
#
# Anything else on a record is an extra the call site added.
_STANDARD_ATTRIBUTES: Final = frozenset(
    vars(
        LogRecord(
            name="",
            level=0,
            pathname="",
            lineno=0,
            msg="",
            args=(),
            exc_info=None,
        ),
    ),
) | frozenset(
    {
        "message",
        "asctime",
    },
)

# NOTE:
# Query parameters that carry a secret in a URL, such as the NYT `api-key`.
_SECRET_QUERY: Final = compile_pattern(
    pattern=r"(?i)([?&](?:api[-_]?key|token|secret|client_secret|password)=)[^&\s'\"]*",
)


def bind_correlation_id(
    *,
    correlation_id: str | None,
) -> None:
    """
    Attach a correlation identifier to every record written in this context.
    """

    _correlation_id.set(
        correlation_id,
    )


def current_correlation_id() -> str | None:
    """
    The correlation identifier bound in this context, if any.
    """

    return _correlation_id.get()


def redact(
    *,
    text: str,
) -> str:
    """
    Replace the value of any secret-bearing query parameter in `text`.
    """

    return _SECRET_QUERY.sub(
        repl=r"\1[REDACTED]",
        string=text,
    )


def payload_of(
    *,
    record: LogRecord,
) -> dict[str, Any]:
    """
    The structured fields of one record, with the correlation and trace
    identifiers bound in the current context.
    """

    payload: dict[str, Any] = {
        "timestamp": datetime.fromtimestamp(
            timestamp=record.created,
            tz=UTC,
        ).isoformat(
            timespec="milliseconds",
        ),
        "level": record.levelname,
        "logger": record.name,
        "event": redact(
            text=record.getMessage(),
        ),
    }

    correlation_id = _correlation_id.get()

    if correlation_id is not None:
        payload["correlation_id"] = correlation_id

    span_context = get_current_span().get_span_context()

    if span_context.is_valid:
        payload["trace_id"] = format(
            span_context.trace_id,
            "032x",
        )

        payload["span_id"] = format(
            span_context.span_id,
            "016x",
        )

    extras = {
        key: value
        for (
            key,
            value,
        ) in vars(
            record,
        ).items()
        if key not in _STANDARD_ATTRIBUTES
    }

    payload.update(
        extras,
    )

    return payload
