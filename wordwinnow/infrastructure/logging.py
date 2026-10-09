"""
Logging for the CLI and the long-running services.

One record is one event: the message is a dotted event name, the fields are
structured extras, and a correlation identifier bound for the current unit of
work travels with every record written inside it.

Call sites log identifiers and facts, never credentials, and the one secret
that travels in a URL, the New York Times key, is redacted from event text and
from spans.
"""

import sys
from logging import (
    StreamHandler,
    getLogger,
)
from sys import (
    stdout,
)
from typing import (
    Final,
    final,
)

from wordwinnow.infrastructure.log_formatters import (
    ConsoleFormatter,
    JsonFormatter,
)
from wordwinnow.infrastructure.log_payload import (
    bind_correlation_id,
    current_correlation_id,
    redact,
)
from wordwinnow.infrastructure.settings import (
    LogFormat,
)

__all__ = (
    "CORRELATION_HEADER",
    "bind_correlation_id",
    "configure_logging",
    "current_correlation_id",
    "redact",
)

# NOTE:
# The HTTP header a correlation identifier travels in between the CLI, intake, and enrichment.
CORRELATION_HEADER: Final = "X-Correlation-Id"

# NOTE:
# Third-party loggers that narrate at INFO and would drown the project's own events.
#
# WARN:
# `aiosqlite` writes every statement it runs at DEBUG with its parameters, the text of an analysis among them, so
# without this entry the local mode's DEBUG output would carry the learner's text.
_QUIET: Final = (
    "aiokafka",
    "aiosqlite",
    "alembic",
    "asyncio",
    "httpcore2",
    "httpx2",
    "uvicorn",
)


@final
class _CurrentStandardError:
    """
    Standard error as it is when a record is written, not as it was when
    logging was configured.
    """

    # NOTE:
    # `sys.stderr` is read through the module on every write, because a progress display rebinds it while it runs and
    # prints what arrives there above itself; a handler holding the original stream would write into the display.
    #
    # `write` keeps the name and the positional parameter of a text stream, because `StreamHandler` calls it that way;
    # the checker's naming candidate here is that protocol's signature, not a choice this module made, and §13 of the
    # standard leaves such a signature to the protocol.
    def write(
        self,
        text: str,
        /,
    ) -> int:
        return sys.stderr.write(
            text,
        )

    def flush(
        self,
    ) -> None:
        sys.stderr.flush()


def configure_logging(
    *,
    log_format: LogFormat,
    level: str,
) -> None:
    """
    Install one handler on the root logger, replacing any earlier one.

    The CLI writes readable lines to stderr so stdout stays a report; a
    service writes JSON lines to stdout for whatever collects them.
    """

    if log_format is LogFormat.JSON:
        handler = StreamHandler(
            stream=stdout,
        )

        handler.setFormatter(
            fmt=JsonFormatter(),
        )

    else:
        handler = StreamHandler(
            stream=_CurrentStandardError(),
        )

        handler.setFormatter(
            fmt=ConsoleFormatter(),
        )

    root = getLogger()

    root.handlers = [
        handler,
    ]

    root.setLevel(
        level=level.upper(),
    )

    for name in _QUIET:
        getLogger(
            name=name,
        ).setLevel(
            level="WARNING",
        )
