"""
What every HTTP service does with a request before its routes see it.

A correlation identifier arriving in `X-Correlation-Id` is bound for the
request and echoed back, so the CLI, the intake service, the enrichment
service, and their logs all name one analysis the same way.
"""

from collections.abc import (
    Awaitable,
    Callable,
)

from fastapi import (
    FastAPI,
    Request,
    Response,
)

from wordwinnow.infrastructure.logging import (
    CORRELATION_HEADER,
    bind_correlation_id,
)


def install_correlation_middleware(
    *,
    app: FastAPI,
) -> None:
    """
    Bind and echo the correlation identifier of every request.
    """

    # WARN:
    # Starlette calls an HTTP middleware as `dispatch(request, call_next)`, positionally, so the function declares the
    # positional boundary the framework imposes.
    @app.middleware(
        middleware_type="http",
    )
    async def bind_and_echo(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
        /,
    ) -> Response:
        correlation_id = request.headers.get(
            CORRELATION_HEADER,
        )

        bind_correlation_id(
            correlation_id=correlation_id,
        )

        response = await call_next(
            request,
        )

        if correlation_id is not None:
            response.headers[CORRELATION_HEADER] = correlation_id

        return response
