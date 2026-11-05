"""
What the suite gives every test: no variable that describes the shell's
terminal, and settings that no `.env` in the working directory reaches.
"""

from os import (
    environ,
)
from pathlib import (
    Path,
)
from typing import (
    final,
)

from pytest import (
    MonkeyPatch,
    mark,
)

from wordwinnow.infrastructure.settings import (
    Settings,
)


@final
class TestTheTerminal:
    @mark.parametrize(
        argnames="name",
        argvalues=(
            "COLORTERM",
            "COLUMNS",
            "FORCE_COLOR",
            "LINES",
            "NO_COLOR",
            "PY_COLORS",
            "TERMINAL_WIDTH",
            "TTY_COMPATIBLE",
            "TTY_INTERACTIVE",
            "TYPER_USE_RICH",
            "_TYPER_FORCE_DISABLE_TERMINAL",
        ),
    )
    def test_no_variable_rich_or_typer_reads_for_a_terminal_reaches_a_test(
        self,
        *,
        name: str,
    ) -> None:
        assert name not in environ


@final
class TestTheDotenv:
    def test_a_dotenv_in_the_working_directory_reaches_no_setting(
        self,
        *,
        monkeypatch: MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        (tmp_path / ".env").write_text(
            data="WORDWINNOW_CLICKHOUSE_PASSWORD=foo\n",
            encoding="utf-8",
        )

        monkeypatch.chdir(
            path=tmp_path,
        )

        assert "clickhouse_password" not in Settings().model_fields_set

    def test_a_dotenv_a_test_names_is_read(
        self,
        *,
        tmp_path: Path,
    ) -> None:
        dotenv = tmp_path / ".env"

        dotenv.write_text(
            data="WORDWINNOW_CLICKHOUSE_PASSWORD=foo\n",
            encoding="utf-8",
        )

        settings = Settings(
            _env_file=dotenv,
        )

        assert settings.clickhouse_password.get_secret_value() == "foo"
