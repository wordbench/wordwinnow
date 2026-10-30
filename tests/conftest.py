"""
What every test shares: an environment that holds only the configuration the
test sets itself.
"""

from collections.abc import (
    Callable,
)
from os import (
    environ,
)
from pathlib import (
    Path,
    PurePosixPath,
)
from typing import (
    Final,
)

from httpx2 import (
    Client,
    MockTransport,
    Request,
    Response,
)
from pytest import (
    MonkeyPatch,
    fixture,
)

from tests.services.ml.synthetic import (
    synthetic_examples,
)
from wordwinnow.domain.difficulty import (
    ALL_FAMILIES,
)
from wordwinnow.infrastructure.lexicon.reference_sources import (
    download_reference_lists as download_from_the_distributor,
)
from wordwinnow.infrastructure.ml.model import (
    ModelKind,
    SklearnLevelEstimator,
    metadata_path,
    save,
    train,
)
from wordwinnow.services.cli import (
    commands,
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

# NOTE:
# The settings put the store, the model, the lists, and the dictionary cache in the user's own directories, which a
# test must neither read nor write: platformdirs 4.12.2 takes both directories from these two variables on macOS and
# on Linux, so pointing them into the test's own folder moves the defaults there.
_DATA_HOME: Final = "XDG_DATA_HOME"

_CACHE_HOME: Final = "XDG_CACHE_HOME"

_REFERENCE_DIR: Final = (
    Path(
        __file__,
    ).parents[1]
    / "data/reference"
)


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


@fixture(
    autouse=True,
)
def isolated_user_directories(
    *,
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(
        name=_DATA_HOME,
        value=str(
            object=tmp_path / "xdg" / "data",
        ),
    )

    monkeypatch.setenv(
        name=_CACHE_HOME,
        value=str(
            object=tmp_path / "xdg" / "cache",
        ),
    )


# TEST:
# The reference lists' distributor, answering from the repository's own copies, which the published digests describe,
# so that no test reaches the network.
#
# It takes the place of the download `setup` makes, and returns the URLs asked for, in order.
@fixture
def requested_lists(
    *,
    monkeypatch: MonkeyPatch,
) -> list[str]:
    seen: list[str] = []

    def handler(
        request: Request,
        /,
    ) -> Response:
        seen.append(
            str(
                object=request.url,
            ),
        )

        return Response(
            status_code=200,
            content=(
                _REFERENCE_DIR
                / PurePosixPath(
                    request.url.path,
                ).name
            ).read_bytes(),
        )

    def download_reference_lists(
        *,
        directory: Path,
        client: Client | None = None,
    ) -> tuple[str, ...]:
        return download_from_the_distributor(
            directory=directory,
            client=Client(
                transport=MockTransport(
                    handler=handler,
                ),
            ),
        )

    monkeypatch.setattr(
        target=commands,
        name="download_reference_lists",
        value=download_reference_lists,
    )

    return seen


# TEST:
# A model small enough to train in a moment, and the same model recorded as trained with a version of scikit-learn
# that no release has.
@fixture(
    scope="module",
)
def fitted_model() -> SklearnLevelEstimator:
    examples = synthetic_examples(
        count=120,
        seed=0,
    )

    return train(
        features=[example.features for example in examples],
        levels=[example.level for example in examples],
        families=ALL_FAMILIES,
        model_kind=ModelKind.GRADIENT_BOOSTING,
        seed=0,
    )


@fixture(
    scope="module",
)
def stale_model(
    *,
    fitted_model: SklearnLevelEstimator,
) -> SklearnLevelEstimator:
    return SklearnLevelEstimator(
        pipeline=fitted_model.pipeline,
        metadata=fitted_model.metadata.model_copy(
            update={
                "sklearn_version": "0.0.0",
            },
        ),
    )


# TEST:
# A whole pickle that names a module no interpreter has, which is how a model from another scikit-learn can look to
# this one: it is not cut short, and it cannot be read.
@fixture
def unreadable_pickle() -> bytes:
    return b"cwordwinnow_no_such_module\nThing\n."


# TEST:
# A level model file put into the state a test names, in the same words for the tests that ask what `doctor` says of
# it and the ones that ask what `setup` does about it: `absent`, `current`, `without a sidecar`, `a sidecar that is
# not JSON`, `cut short`, `another scikit-learn`, or `another scikit-learn and unreadable`.
@fixture
def put_model(
    *,
    fitted_model: SklearnLevelEstimator,
    stale_model: SklearnLevelEstimator,
    unreadable_pickle: bytes,
) -> Callable[..., None]:
    def put(
        *,
        path: Path,
        state: str,
    ) -> None:
        if state == "absent":
            return

        save(
            estimator=stale_model
            if state.startswith(
                "another scikit-learn",
            )
            else fitted_model,
            path=path,
        )

        match state:
            case "without a sidecar":
                metadata_path(
                    path=path,
                ).unlink()

            case "a sidecar that is not JSON":
                metadata_path(
                    path=path,
                ).write_text(
                    data="{not json",
                    encoding="utf-8",
                )

            case "cut short":
                path.write_bytes(
                    data=path.read_bytes()[:100],
                )

            case "another scikit-learn and unreadable":
                path.write_bytes(
                    data=unreadable_pickle,
                )

    return put
