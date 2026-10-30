"""
Where the reference lists come from, and how setup puts them in place.

The lists are downloaded from their distributor at one fixed commit, and each
is checked against the SHA-256 of its published bytes before it is kept.
"""

from hashlib import (
    sha256,
)
from pathlib import (
    Path,
)
from types import (
    MappingProxyType,
)
from typing import (
    Final,
)

from httpx2 import (
    Client,
    HTTPError,
)

from wordwinnow.application.errors import (
    LinguisticResourcesMissingError,
)
from wordwinnow.infrastructure.lexicon.reference_lists import (
    REFERENCE_FILES,
)

# NOTE:
# The distributor's repository at one commit of 2020, which every download names: a commit cannot move, so a learner
# gets the bytes the digests below describe and the documented numbers were computed from.
REFERENCE_LISTS_COMMIT: Final = "d4e45b75b38f27b30dfc5c44d8c571aec7e7092f"

_BASE_URL: Final = f"https://raw.githubusercontent.com/openlanguageprofiles/olp-en-cefrj/{REFERENCE_LISTS_COMMIT}"

# NOTE:
# The SHA-256 of each list as published at that commit, byte for byte the file of the same name under
# `data/reference/`.
REFERENCE_DIGESTS: Final = MappingProxyType(
    mapping={
        "cefrj-vocabulary-profile-1.5.csv": "b0dd3c635f1c9a4fdf1490c7e5b7c48e8bbe55b652ad0c9860a95f98e10ae498",
        "octanove-vocabulary-profile-c1c2-1.0.csv": (
            "18c33a407f2f89f7b8de9671c6d45fe3ea0bce45e7d2d7dcaab48d73e0f7b380"
        ),
    },
)

# NOTE:
# What setup prints about each list's origin and terms, indented to line up under the lines that report a status.
REFERENCE_CITATIONS: Final = (
    (
        "        CEFR-J Vocabulary Profile 1.5, by Tono Laboratory, Tokyo University of Foreign Studies, cited as "
        '"The CEFR-J Wordlist Version 1.5. Compiled by Yukio Tono, Tokyo University of Foreign Studies." under its '
        'terms: "CEFR-J vocabulary and grammar profile datasets can be used for research and commercial purposes '
        'with no charge, provided that you cite the dataset properly."'
    ),
    (
        "        Octanove Vocabulary Profile C1/C2 1.0, by Octanove Labs, under the Creative Commons "
        "Attribution-ShareAlike 4.0 International license: https://creativecommons.org/licenses/by-sa/4.0/"
    ),
)

DOWNLOAD_TIMEOUT_SECONDS: Final = 120.0


def reference_url(
    *,
    name: str,
) -> str:
    """
    Where the distributor serves the list called `name`.
    """

    return f"{_BASE_URL}/{name}"


def download_reference_lists(
    *,
    directory: Path,
    client: Client | None = None,
) -> tuple[str, ...]:
    """
    Put every reference list that `directory` lacks in place, and return the
    names of the lists downloaded.

    A list is kept only when its SHA-256 is the published one, and it is
    written beside its final name and renamed, so a failed or wrong download
    leaves no file.

    A list already present is checked and left as it is, whatever the result.

    Raises `LinguisticResourcesMissingError` when a list could not be fetched,
    when a list has another digest than the published one, present or
    downloaded, and then nothing is overwritten.
    """

    absent: list[str] = []

    for name in REFERENCE_FILES:
        path = directory / name

        if not path.exists():
            absent.append(
                name,
            )

            continue

        actual = _digest(
            path.read_bytes(),
        )

        expected = REFERENCE_DIGESTS[name]

        if actual != expected:
            raise LinguisticResourcesMissingError(
                f"the reference list {path} differs from the published one (SHA-256 {actual}, not {expected}); "
                "delete it, and `wordwinnow setup` downloads it again",
            )

    if not absent:
        return ()

    fetcher = (
        client
        if client is not None
        else Client(
            timeout=DOWNLOAD_TIMEOUT_SECONDS,
            follow_redirects=True,
        )
    )

    try:
        for name in absent:
            _download(
                name,
                directory,
                fetcher,
            )

    finally:
        if client is None:
            fetcher.close()

    return tuple(
        absent,
    )


def _download(
    name: str,
    directory: Path,
    client: Client,
    /,
) -> None:
    url = reference_url(
        name=name,
    )

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
            f"the reference list {name} could not be fetched from {url} ({kind})",
        ) from exception

    actual = _digest(
        response.content,
    )

    expected = REFERENCE_DIGESTS[name]

    if actual != expected:
        raise LinguisticResourcesMissingError(
            f"the reference list downloaded from {url} has SHA-256 {actual}, not {expected}; nothing was kept",
        )

    directory.mkdir(
        parents=True,
        exist_ok=True,
    )

    destination = directory / name

    partial = destination.with_name(
        name=f"{name}.part",
    )

    try:
        partial.write_bytes(
            data=response.content,
        )

        partial.replace(
            target=destination,
        )

    finally:
        partial.unlink(
            missing_ok=True,
        )


def _digest(
    data: bytes,
    /,
) -> str:
    return sha256(
        data=data,
    ).hexdigest()
