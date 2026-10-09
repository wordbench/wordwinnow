"""
An application's lifespan, run the way a server runs it, without a server.
"""

from fastapi import (
    FastAPI,
)
from starlette.types import (
    Message,
)


async def run_lifespan(
    *,
    app: FastAPI,
) -> tuple[str, ...]:
    """
    Start `app` and stop it again, and return the types of the messages it
    sent back, which say whether each step completed.
    """

    events = iter(
        (
            {
                "type": "lifespan.startup",
            },
            {
                "type": "lifespan.shutdown",
            },
        ),
    )

    sent: list[str] = []

    async def receive() -> Message:
        return next(
            events,
        )

    # WARN:
    # The application calls `send(message)` positionally, as the ASGI protocol does, so `send` declares the positional
    # boundary the protocol imposes.
    async def send(
        message: Message,
        /,
    ) -> None:
        sent.append(
            message["type"],
        )

    await app(
        scope={
            "type": "lifespan",
            "asgi": {
                "version": "3.0",
            },
        },
        receive=receive,
        send=send,
    )

    return tuple(
        sent,
    )
