"""
Where the command line's log lines go, and which of them it writes.
"""

import sys
from io import (
    StringIO,
)
from logging import (
    getLogger,
)
from typing import (
    final,
)

from aiosqlite import (
    connect,
)
from httpx2 import (
    AsyncClient,
    MockTransport,
    Request,
    Response,
)
from pytest import (
    MonkeyPatch,
    fixture,
)

from wordwinnow.infrastructure.composition import (
    configure_process,
)
from wordwinnow.infrastructure.logging import (
    configure_logging,
)
from wordwinnow.infrastructure.settings import (
    LogFormat,
    Settings,
)


@fixture
def unset_logging(
    *,
    monkeypatch: MonkeyPatch,
) -> None:
    # NOTE:
    # The level and the format come from the defaults, whatever the shell running the tests exports.
    monkeypatch.delenv(
        name="WORDWINNOW_LOG_LEVEL",
        raising=False,
    )

    monkeypatch.delenv(
        name="WORDWINNOW_LOG_FORMAT",
        raising=False,
    )


def _capture_standard_error(
    monkeypatch: MonkeyPatch,
    /,
) -> StringIO:
    # TEST:
    # Standard error is replaced in the test's body, never in a fixture: pytest puts its own capture back when the
    # test's call begins, which would undo a replacement made during setup.
    stream = StringIO()

    monkeypatch.setattr(
        target=sys,
        name="stderr",
        value=stream,
    )

    return stream


@final
class TestConfigureLogging:
    def test_a_console_line_follows_standard_error_wherever_it_is_rebound(
        self,
        *,
        monkeypatch: MonkeyPatch,
    ) -> None:
        configure_logging(
            log_format=LogFormat.CONSOLE,
            level="INFO",
        )

        # NOTE:
        # A progress display rebinds `sys.stderr` after logging is configured, and a line written while it runs must
        # reach the stream it installed.
        stream = StringIO()

        monkeypatch.setattr(
            target=sys,
            name="stderr",
            value=stream,
        )

        getLogger(
            name="wordwinnow.cli",
        ).info(
            msg="analysis.requested",
        )

        assert "analysis.requested" in stream.getvalue()

    async def test_at_debug_the_local_store_does_not_write_what_it_stores(
        self,
        *,
        monkeypatch: MonkeyPatch,
    ) -> None:
        standard_error = _capture_standard_error(
            monkeypatch,
        )

        configure_logging(
            log_format=LogFormat.CONSOLE,
            level="DEBUG",
        )

        async with connect(
            database=":memory:",
        ) as database:
            await database.execute(
                sql="SELECT ?",
                parameters=("a sentence only the learner has seen",),
            )

        getLogger(
            name="wordwinnow.dictionary",
        ).debug(
            msg="dictionary.lookup",
        )

        written = standard_error.getvalue()

        assert "dictionary.lookup" in written

        assert "a sentence only the learner has seen" not in written

    async def test_at_debug_the_http_client_does_not_narrate_its_requests(
        self,
        *,
        monkeypatch: MonkeyPatch,
    ) -> None:
        standard_error = _capture_standard_error(
            monkeypatch,
        )

        configure_logging(
            log_format=LogFormat.CONSOLE,
            level="DEBUG",
        )

        def handler(
            request: Request,
            /,
        ) -> Response:
            return Response(
                status_code=200,
            )

        # NOTE:
        # The client writes a line with the URL of every request it sends at INFO, which in a service would add a line
        # for every request to a provider.
        async with AsyncClient(
            transport=MockTransport(
                handler=handler,
            ),
        ) as client:
            await client.get(
                url="https://example.com/",
            )

        getLogger(
            name="wordwinnow.dictionary",
        ).debug(
            msg="dictionary.lookup",
        )

        written = standard_error.getvalue()

        assert "dictionary.lookup" in written

        assert "example.com" not in written


@final
class TestConfigureProcess:
    def test_unset_the_command_line_writes_warnings_and_a_service_everything_from_info(
        self,
        *,
        unset_logging: None,
        monkeypatch: MonkeyPatch,
    ) -> None:
        standard_error = _capture_standard_error(
            monkeypatch,
        )

        logger = getLogger(
            name="wordwinnow.composition",
        )

        configure_process(
            settings=Settings(),
            service_name="wordwinnow-cli",
            tracing=False,
            default_log_level="WARNING",
        )

        logger.info(
            msg="sources.configured",
        )

        logger.warning(
            msg="dictionary.circuit.opened",
        )

        configure_process(
            settings=Settings(),
            service_name="wordwinnow-intake",
            tracing=False,
        )

        logger.info(
            msg="service.listening",
        )

        written = standard_error.getvalue()

        assert "sources.configured" not in written

        assert "dictionary.circuit.opened" in written

        assert "service.listening" in written

    def test_a_level_the_settings_name_holds_for_the_command_line_too(
        self,
        *,
        unset_logging: None,
        monkeypatch: MonkeyPatch,
    ) -> None:
        standard_error = _capture_standard_error(
            monkeypatch,
        )

        monkeypatch.setenv(
            name="WORDWINNOW_LOG_LEVEL",
            value="INFO",
        )

        configure_process(
            settings=Settings(),
            service_name="wordwinnow-cli",
            tracing=False,
            default_log_level="WARNING",
        )

        getLogger(
            name="wordwinnow.composition",
        ).info(
            msg="sources.configured",
        )

        assert "sources.configured" in standard_error.getvalue()
