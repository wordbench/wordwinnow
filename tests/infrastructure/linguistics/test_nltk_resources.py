"""
The NLTK data the analysis needs: what is checked, and how a missing package
is fetched.
"""

from io import (
    BytesIO,
)
from pathlib import (
    Path,
)
from typing import (
    final,
)
from zipfile import (
    ZipFile,
    is_zipfile,
)

import nltk
from httpx2 import (
    Client,
    MockTransport,
    Request,
    Response,
)
from pytest import (
    raises,
)

from wordwinnow.application.errors import (
    LinguisticResourcesMissingError,
)
from wordwinnow.infrastructure.linguistics.nltk_resources import (
    PACKAGES_URL,
    NltkResource,
    download_resources,
    missing_resources,
)


@final
class TestNltkResources:
    def test_every_required_resource_is_installed(
        self,
    ) -> None:
        assert missing_resources() == ()


# TEST:
# A real, empty archive, because NLTK opens any archive it meets on its data path and a fake one would break the other
# lookups of the same process.
def _archive() -> bytes:
    buffer = BytesIO()

    with ZipFile(
        file=buffer,
        mode="w",
    ) as archive:
        archive.writestr(
            zinfo_or_arcname="nonexistent/README",
            data="a package",
        )

    return buffer.getvalue()


@final
class TestDownloadResources:
    def test_a_missing_package_is_fetched_as_a_zip_into_the_target(
        self,
        *,
        tmp_path: Path,
    ) -> None:
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
                content=_archive(),
            )

        nonexistent = NltkResource(
            package="nonexistent",
            kind="corpora",
            paths=("corpora/nonexistent/",),
        )

        downloaded = download_resources(
            resources=(nonexistent,),
            target=tmp_path,
            client=Client(
                transport=MockTransport(
                    handler=handler,
                ),
            ),
        )

        assert downloaded == (nonexistent,)

        assert seen == [
            f"{PACKAGES_URL}/corpora/nonexistent.zip",
        ]

        assert is_zipfile(
            filename=tmp_path / "corpora" / "nonexistent.zip",
        )

        # TEST:
        # The download made the directory known to NLTK's global path; the test takes it out again so the next test
        # starts from the same path as this one did.
        assert (
            str(
                object=tmp_path,
            )
            in nltk.data.path
        )

        nltk.data.path.remove(
            str(
                object=tmp_path,
            ),
        )

    def test_a_present_package_is_not_fetched_and_a_refusal_is_the_resources_error(
        self,
        *,
        tmp_path: Path,
    ) -> None:
        def refuse(
            request: Request,
            /,
        ) -> Response:
            return Response(
                status_code=503,
            )

        assert (
            download_resources(
                target=tmp_path,
                client=Client(
                    transport=MockTransport(
                        handler=refuse,
                    ),
                ),
            )
            == ()
        )

        with raises(
            expected_exception=LinguisticResourcesMissingError,
            match="NLTK package unreachable could not be fetched",
        ):
            download_resources(
                resources=(
                    NltkResource(
                        package="unreachable",
                        kind="corpora",
                        paths=("corpora/unreachable/",),
                    ),
                ),
                target=tmp_path,
                client=Client(
                    transport=MockTransport(
                        handler=refuse,
                    ),
                ),
            )
