"""
The NLTK data packages the linguistic adapters need, and how to make sure they
are present.

A missing package fails at startup with the command that installs it, because
a pipeline that silently ran without a tagger would still produce a report,
and a wrong report is worse than none.

The packages are fetched by this module rather than by NLTK's own downloader,
which prompts on a failure and refuses hosts whose addresses it does not
trust.
"""

from collections.abc import (
    Sequence,
)
from dataclasses import (
    dataclass,
)
from pathlib import (
    Path,
)
from typing import (
    Final,
    final,
)

# NOTE:
# The module, not the name: `nltk.data.path` is NLTK's one search list for the whole process, and inserting through
# the module reaches the list NLTK reads even after something else rebinds it.
import nltk
from httpx2 import (
    Client,
    HTTPError,
)
from nltk.data import (
    find,
)

from wordwinnow.application.errors import (
    LinguisticResourcesMissingError,
)

# NOTE:
# Where the NLTK project publishes its packages, one zip file per package under its kind, read at one commit of the
# branch that publishes them.
#
# The branch moves whenever any package changes, so the commit is what makes every download the same bytes: a machine
# without the packages gets the data the documented numbers were computed from, and moving to a newer commit is a
# deliberate upgrade.
#
# No digest is recorded beside it, because the files are data that is never run, and the commit already fixes them.
NLTK_DATA_COMMIT: Final = "550b6625bcef1f2abff2ff770a5a0d272c9c6b2a"

PACKAGES_URL: Final = f"https://raw.githubusercontent.com/nltk/nltk_data/{NLTK_DATA_COMMIT}/packages"

DOWNLOAD_TIMEOUT_SECONDS: Final = 120.0


@dataclass(
    frozen=True,
    kw_only=True,
    slots=True,
)
@final
class NltkResource:
    """
    One NLTK data package, the kind of data it is, and the paths NLTK finds it
    under.
    """

    package: str

    kind: str

    paths: tuple[str, ...]


# NOTE:
# Pipeline order: sentences, tokens, tags, function words, lemmas and senses.
REQUIRED_RESOURCES: Final = (
    NltkResource(
        package="punkt_tab",
        kind="tokenizers",
        paths=(
            "tokenizers/punkt_tab/english/",
            "tokenizers/punkt_tab.zip/punkt_tab/english/",
        ),
    ),
    NltkResource(
        package="averaged_perceptron_tagger_eng",
        kind="taggers",
        paths=(
            "taggers/averaged_perceptron_tagger_eng/",
            "taggers/averaged_perceptron_tagger_eng.zip/averaged_perceptron_tagger_eng/",
        ),
    ),
    NltkResource(
        package="stopwords",
        kind="corpora",
        paths=(
            "corpora/stopwords/english",
            "corpora/stopwords.zip/stopwords/english",
        ),
    ),
    NltkResource(
        package="wordnet",
        kind="corpora",
        paths=(
            "corpora/wordnet/",
            "corpora/wordnet.zip/wordnet/",
        ),
    ),
)


def add_data_path(
    *,
    path: Path,
) -> None:
    """
    Search `path` for NLTK data before the default locations.
    """

    text = str(
        object=path,
    )

    if text not in nltk.data.path:
        nltk.data.path.insert(
            0,
            text,
        )


def missing_resources(
    *,
    resources: Sequence[NltkResource] = REQUIRED_RESOURCES,
) -> tuple[NltkResource, ...]:
    """
    The resources NLTK cannot find on its data path.
    """

    return tuple(
        resource
        for resource in resources
        if not any(
            _is_present(
                path,
            )
            for path in resource.paths
        )
    )


def _is_present(
    path: str,
    /,
) -> bool:
    try:
        find(
            resource_name=path,
        )

    except LookupError:
        return False

    return True


def ensure_resources(
    *,
    resources: Sequence[NltkResource] = REQUIRED_RESOURCES,
) -> None:
    """
    Fail clearly when a required NLTK package is not installed.
    """

    missing = missing_resources(
        resources=resources,
    )

    if not missing:
        return

    packages = " ".join(resource.package for resource in missing)

    raise LinguisticResourcesMissingError(
        f"linguistic analysis requires NLTK packages that are not installed: {packages}; run "
        "`wordwinnow doctor --install-nltk-data`",
    )


def download_resources(
    *,
    resources: Sequence[NltkResource] = REQUIRED_RESOURCES,
    target: Path | None = None,
    client: Client | None = None,
) -> tuple[NltkResource, ...]:
    """
    Download the missing packages as zip files into `target`, or into the home
    directory's NLTK data directory when none is given, and make that
    directory known to NLTK.

    Returns the packages that were downloaded.

    Raises `LinguisticResourcesMissingError` when a package could not be
    fetched.
    """

    missing = missing_resources(
        resources=resources,
    )

    if not missing:
        return ()

    directory = target if target is not None else Path.home() / "nltk_data"

    fetcher = (
        client
        if client is not None
        else Client(
            timeout=DOWNLOAD_TIMEOUT_SECONDS,
            follow_redirects=True,
        )
    )

    try:
        for resource in missing:
            _fetch(
                resource,
                directory,
                fetcher,
            )

    finally:
        if client is None:
            fetcher.close()

    add_data_path(
        path=directory,
    )

    return missing


def _fetch(
    resource: NltkResource,
    directory: Path,
    client: Client,
    /,
) -> None:
    url = f"{PACKAGES_URL}/{resource.kind}/{resource.package}.zip"

    try:
        response = client.get(
            url=url,
        )

        response.raise_for_status()

    except HTTPError as exception:
        kind = type(
            exception,
        ).__name__

        raise LinguisticResourcesMissingError(
            f"NLTK package {resource.package} could not be fetched from {url} ({kind})",
        ) from exception

    destination = directory / resource.kind / f"{resource.package}.zip"

    destination.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    destination.write_bytes(
        data=response.content,
    )
