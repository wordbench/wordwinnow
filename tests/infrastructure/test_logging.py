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
from re import (
    fullmatch,
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
    mark,
)

from wordwinnow.infrastructure import (
    logging as project_logging,
)
from wordwinnow.infrastructure.composition import (
    configure_process,
)
from wordwinnow.infrastructure.dictionary.free_dictionary import (
    CIRCUIT_OPENED,
)
from wordwinnow.infrastructure.logging import (
    bind_correlation_id,
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


def _as_the_command_line_does(
    *,
    service_name: str = "wordwinnow-cli",
) -> None:
    configure_process(
        settings=Settings(),
        service_name=service_name,
        tracing=False,
        default_log_level="WARNING",
        default_events_left_out=frozenset(
            (CIRCUIT_OPENED,),
        ),
    )


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

    @mark.parametrize(
        argnames="logger_name",
        argvalues=(
            "wordwinnow.dictionary",
            "wordwinnow.composition",
        ),
    )
    def test_the_handler_leaves_out_the_events_it_is_given_at_any_level_on_any_logger(
        self,
        *,
        monkeypatch: MonkeyPatch,
        logger_name: str,
    ) -> None:
        standard_error = _capture_standard_error(
            monkeypatch,
        )

        configure_logging(
            log_format=LogFormat.CONSOLE,
            level="WARNING",
            events_left_out=frozenset(
                (CIRCUIT_OPENED,),
            ),
        )

        logger = getLogger(
            name=logger_name,
        )

        logger.warning(
            msg=CIRCUIT_OPENED,
        )

        logger.error(
            msg=CIRCUIT_OPENED,
        )

        assert standard_error.getvalue() == ""

        logger.warning(
            msg="dictionary.lookup.failed",
        )

        logger.error(
            msg="dictionary.cache.invalid",
        )

        written = standard_error.getvalue()

        assert "dictionary.lookup.failed" in written

        assert "dictionary.cache.invalid" in written

        assert CIRCUIT_OPENED not in written

    def test_a_malformed_record_is_reported_by_the_handler_and_not_raised_into_the_caller(
        self,
        *,
        monkeypatch: MonkeyPatch,
    ) -> None:
        standard_error = _capture_standard_error(
            monkeypatch,
        )

        configure_logging(
            log_format=LogFormat.CONSOLE,
            level="WARNING",
            events_left_out=frozenset(
                (CIRCUIT_OPENED,),
            ),
        )

        # WARN:
        # `Logger.warning` takes the message's arguments positionally, so this call cannot go by keyword: `%d` given
        # `"x"` makes the record unformattable, which the handler reports on standard error and never raises.
        getLogger(
            name="wordwinnow.dictionary",
        ).warning(
            "%d",
            "x",
        )

        assert "--- Logging error ---" in standard_error.getvalue()

    def test_a_record_whose_message_is_not_a_string_is_written_and_not_raised_into_the_caller(
        self,
        *,
        monkeypatch: MonkeyPatch,
    ) -> None:
        standard_error = _capture_standard_error(
            monkeypatch,
        )

        configure_logging(
            log_format=LogFormat.CONSOLE,
            level="WARNING",
            events_left_out=frozenset(
                (CIRCUIT_OPENED,),
            ),
        )

        # NOTE:
        # Other tests bind a correlation identifier and never unbind it, and a record carries the one bound in its
        # context, so the line this test reads is written with none.
        bind_correlation_id(
            correlation_id=None,
        )

        # NOTE:
        # `in` on a `frozenset` hashes its operand, and a `dict` is not hashable, so a filter that did not check the
        # type of `msg` would raise into the code that logged it.
        getLogger(
            name="wordwinnow.dictionary",
        ).warning(
            msg={
                "key": "value",
            },
        )

        assert (
            fullmatch(
                pattern=r"\d\d:\d\d:\d\d\.\d{3} WARNING \{'key': 'value'\}\n",
                string=standard_error.getvalue(),
            )
            is not None
        )

    def test_with_nothing_left_out_the_event_is_written(
        self,
        *,
        monkeypatch: MonkeyPatch,
    ) -> None:
        standard_error = _capture_standard_error(
            monkeypatch,
        )

        configure_logging(
            log_format=LogFormat.CONSOLE,
            level="WARNING",
        )

        getLogger(
            name="wordwinnow.dictionary",
        ).warning(
            msg=CIRCUIT_OPENED,
        )

        assert CIRCUIT_OPENED in standard_error.getvalue()

    def test_the_json_handler_leaves_the_event_out_of_standard_output_the_same_way(
        self,
        *,
        monkeypatch: MonkeyPatch,
    ) -> None:
        standard_output = StringIO()

        monkeypatch.setattr(
            target=project_logging,
            name="stdout",
            value=standard_output,
        )

        configure_logging(
            log_format=LogFormat.JSON,
            level="WARNING",
            events_left_out=frozenset(
                (CIRCUIT_OPENED,),
            ),
        )

        logger = getLogger(
            name="wordwinnow.dictionary",
        )

        logger.warning(
            msg=CIRCUIT_OPENED,
        )

        assert standard_output.getvalue() == ""

        logger.warning(
            msg="dictionary.lookup.failed",
        )

        written = standard_output.getvalue()

        assert "dictionary.lookup.failed" in written

        assert CIRCUIT_OPENED not in written


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

    @mark.parametrize(
        argnames=(
            "named",
            "written",
        ),
        argvalues=(
            (
                None,
                False,
            ),
            (
                "",
                False,
            ),
            (
                "WARNING",
                True,
            ),
            (
                "warning",
                True,
            ),
        ),
    )
    def test_the_command_line_leaves_the_circuit_warning_out_only_at_its_default_level(
        self,
        *,
        unset_logging: None,
        monkeypatch: MonkeyPatch,
        named: str | None,
        written: bool,
    ) -> None:
        standard_error = _capture_standard_error(
            monkeypatch,
        )

        if named is not None:
            monkeypatch.setenv(
                name="WORDWINNOW_LOG_LEVEL",
                value=named,
            )

        _as_the_command_line_does()

        logger = getLogger(
            name="wordwinnow.dictionary",
        )

        logger.warning(
            msg=CIRCUIT_OPENED,
        )

        logger.warning(
            msg="dictionary.lookup.failed",
        )

        output = standard_error.getvalue()

        assert "dictionary.lookup.failed" in output

        assert output.count(
            CIRCUIT_OPENED,
        ) == int(
            written,
        )

    def test_a_service_started_after_the_command_line_logs_the_circuit_warning(
        self,
        *,
        unset_logging: None,
        monkeypatch: MonkeyPatch,
    ) -> None:
        standard_error = _capture_standard_error(
            monkeypatch,
        )

        _as_the_command_line_does()

        # NOTE:
        # `serve_enrichment` calls it with the service's name and nothing left out, and the handler it installs
        # replaces the command line's; tracing is off here because a tracer provider installs once per process.
        configure_process(
            settings=Settings(),
            service_name="wordwinnow-enrichment",
            tracing=False,
        )

        getLogger(
            name="wordwinnow.dictionary",
        ).warning(
            msg=CIRCUIT_OPENED,
        )

        assert CIRCUIT_OPENED in standard_error.getvalue()
