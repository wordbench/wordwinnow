"""
What every test shares: an environment that holds only the configuration the
test sets itself.
"""

from os import (
    environ,
)
from typing import (
    Final,
)

from pytest import (
    MonkeyPatch,
    fixture,
)

# NOTE:
# A developer's shell may already carry the real settings, put there by direnv or by an exported `.env`, the New York
# Times key among them: a test that read them would call the provider with that key, and a test that expects no key
# would fail.
#
# The `WORDWINNOW_TEST_` variables only point the integration lane at its servers, and they stay.
_SETTINGS_PREFIX: Final = "WORDWINNOW_"

_TEST_PREFIX: Final = "WORDWINNOW_TEST_"

_TRACING_ENDPOINT: Final = "OTEL_EXPORTER_OTLP_ENDPOINT"


@fixture(
    autouse=True,
)
def isolated_settings(
    *,
    monkeypatch: MonkeyPatch,
) -> None:
    for name in tuple(
        environ,
    ):
        upper = name.upper()

        setting = upper.startswith(
            _SETTINGS_PREFIX,
        ) and not upper.startswith(
            _TEST_PREFIX,
        )

        if setting or upper == _TRACING_ENDPOINT:
            monkeypatch.delenv(
                name=name,
            )
