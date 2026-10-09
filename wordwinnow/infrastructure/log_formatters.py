"""
The two log formatters: JSON lines for services, readable lines for the CLI.
"""

# WARN:
# Both classes override `logging.Formatter.format`, whose parameter the standard library declares
# positional-or-keyword and calls positionally.
#
# The override keeps that exact shape because the type checker proves any narrower one breaks substitution, and the
# conventions checker cannot tell a framework-imposed signature from a project one, so `pyproject.toml` excludes this
# module from its scan and says why.

from json import (
    dumps,
)
from logging import (
    Formatter,
    LogRecord,
)
from typing import (
    final,
    override,
)

from wordwinnow.infrastructure.log_payload import (
    payload_of,
    redact,
)


@final
class JsonFormatter(
    Formatter,
):
    """
    One JSON object per line, for the services.
    """

    @override
    def format(
        self,
        record: LogRecord,
    ) -> str:
        payload = payload_of(
            record=record,
        )

        if record.exc_info:
            payload["exception"] = redact(
                text=self.formatException(
                    ei=record.exc_info,
                ),
            )

        return dumps(
            obj=payload,
            ensure_ascii=False,
            default=str,
        )


@final
class ConsoleFormatter(
    Formatter,
):
    """
    A readable line for the CLI: time, level, event, then `key=value` extras.
    """

    @override
    def format(
        self,
        record: LogRecord,
    ) -> str:
        payload = payload_of(
            record=record,
        )

        clock = payload.pop(
            "timestamp",
        )[11:23]

        level = payload.pop(
            "level",
        )

        event = payload.pop(
            "event",
        )

        payload.pop(
            "logger",
        )

        fields = " ".join(
            f"{key}={value}"
            for (
                key,
                value,
            ) in payload.items()
        )

        line = f"{clock} {level:<7} {event} {fields}".rstrip()

        if record.exc_info:
            exception = redact(
                text=self.formatException(
                    ei=record.exc_info,
                ),
            )

            return f"{line}\n{exception}"

        return line
