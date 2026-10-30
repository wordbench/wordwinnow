"""
Where the reference lists are downloaded from, and how a download is checked
and put in place.

No test here reaches the network: the distributor is a transport of the test's
own, serving the repository's copies.
"""

from hashlib import (
    sha256,
)
from pathlib import (
    Path,
    PurePosixPath,
)
from typing import (
    Final,
    final,
)

from httpx2 import (
    Client,
    ConnectError,
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
from wordwinnow.infrastructure.lexicon.reference_lists import (
    REFERENCE_FILES,
)
from wordwinnow.infrastructure.lexicon.reference_sources import (
    REFERENCE_CITATIONS,
    REFERENCE_DIGESTS,
    REFERENCE_LISTS_COMMIT,
    download_reference_lists,
    reference_url,
)

_REFERENCE_DIR: Final = (
    Path(
        __file__,
    ).parents[3]
    / "data/reference"
)

_FIRST: Final = REFERENCE_FILES[0]

_SECOND: Final = REFERENCE_FILES[1]


def _client_of(
    seen: list[str],
    /,
) -> Client:
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

    return Client(
        transport=MockTransport(
            handler=handler,
        ),
    )


@final
class TestTheSources:
    def test_each_digest_is_the_sha256_of_the_repositorys_own_copy(
        self,
    ) -> None:
        assert {
            name: sha256(
                data=(_REFERENCE_DIR / name).read_bytes(),
            ).hexdigest()
            for name in REFERENCE_FILES
        } == dict(
            REFERENCE_DIGESTS,
        )

    def test_the_digests_name_exactly_the_reference_files(
        self,
    ) -> None:
        assert tuple(
            REFERENCE_DIGESTS,
        ) == tuple(
            REFERENCE_FILES,
        )

    def test_each_list_is_read_from_the_distributors_repository_at_the_fixed_commit(
        self,
    ) -> None:
        assert REFERENCE_LISTS_COMMIT == "d4e45b75b38f27b30dfc5c44d8c571aec7e7092f"

        assert tuple(
            reference_url(
                name=name,
            )
            for name in REFERENCE_FILES
        ) == (
            "https://raw.githubusercontent.com/openlanguageprofiles/olp-en-cefrj/"
            "d4e45b75b38f27b30dfc5c44d8c571aec7e7092f/cefrj-vocabulary-profile-1.5.csv",
            "https://raw.githubusercontent.com/openlanguageprofiles/olp-en-cefrj/"
            "d4e45b75b38f27b30dfc5c44d8c571aec7e7092f/octanove-vocabulary-profile-c1c2-1.0.csv",
        )

    def test_the_citations_say_who_made_each_list_and_under_what_terms(
        self,
    ) -> None:
        assert REFERENCE_CITATIONS == (
            "        CEFR-J Vocabulary Profile 1.5, by Tono Laboratory, Tokyo University of Foreign Studies, "
            'cited as "The CEFR-J Wordlist Version 1.5. Compiled by Yukio Tono, Tokyo University of Foreign '
            'Studies." under its terms: "CEFR-J vocabulary and grammar profile datasets can be used for research '
            'and commercial purposes with no charge, provided that you cite the dataset properly."',
            "        Octanove Vocabulary Profile C1/C2 1.0, by Octanove Labs, under the Creative Commons "
            "Attribution-ShareAlike 4.0 International license: https://creativecommons.org/licenses/by-sa/4.0/",
        )


@final
class TestDownloadReferenceLists:
    def test_absent_lists_are_fetched_checked_and_written(
        self,
        *,
        tmp_path: Path,
    ) -> None:
        seen: list[str] = []

        directory = tmp_path / "reference"

        downloaded = download_reference_lists(
            directory=directory,
            client=_client_of(
                seen,
            ),
        )

        assert downloaded == REFERENCE_FILES

        assert seen == [
            reference_url(
                name=name,
            )
            for name in REFERENCE_FILES
        ]

        assert sorted(path.name for path in directory.iterdir()) == sorted(
            REFERENCE_FILES,
        )

        for name in REFERENCE_FILES:
            assert (directory / name).read_bytes() == (_REFERENCE_DIR / name).read_bytes()

    def test_a_download_with_another_digest_raises_with_its_url_and_leaves_no_file(
        self,
        *,
        tmp_path: Path,
    ) -> None:
        def altered(
            request: Request,
            /,
        ) -> Response:
            return Response(
                status_code=200,
                content=b"foo",
            )

        with raises(
            expected_exception=LinguisticResourcesMissingError,
        ) as caught:
            download_reference_lists(
                directory=tmp_path / "reference",
                client=Client(
                    transport=MockTransport(
                        handler=altered,
                    ),
                ),
            )

        url = reference_url(
            name=_FIRST,
        )

        actual = sha256(
            data=b"foo",
        ).hexdigest()

        assert (
            str(
                object=caught.value,
            )
            == f"the reference list downloaded from {url} has SHA-256 {actual}, not {REFERENCE_DIGESTS[_FIRST]}; "
            "nothing was kept"
        )

        assert (
            tuple(
                tmp_path.rglob(
                    pattern="*",
                ),
            )
            == ()
        )

    def test_a_refused_request_raises_naming_the_list_and_its_url(
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

        with raises(
            expected_exception=LinguisticResourcesMissingError,
        ) as caught:
            download_reference_lists(
                directory=tmp_path / "reference",
                client=Client(
                    transport=MockTransport(
                        handler=refuse,
                    ),
                ),
            )

        assert (
            str(
                object=caught.value,
            )
            == f"the reference list {_FIRST} could not be fetched from "
            f"{
                reference_url(
                    name=_FIRST,
                )
            } (HTTPStatusError)"
        )

        assert (
            tuple(
                tmp_path.rglob(
                    pattern="*",
                ),
            )
            == ()
        )

    def test_a_lost_connection_raises_the_same_error(
        self,
        *,
        tmp_path: Path,
    ) -> None:
        def lose(
            request: Request,
            /,
        ) -> Response:
            raise ConnectError(
                message="no connection",
            )

        with raises(
            expected_exception=LinguisticResourcesMissingError,
            match=r"\(ConnectError\)$",
        ):
            download_reference_lists(
                directory=tmp_path / "reference",
                client=Client(
                    transport=MockTransport(
                        handler=lose,
                    ),
                ),
            )

    def test_a_present_list_with_the_right_digest_is_not_requested(
        self,
        *,
        tmp_path: Path,
    ) -> None:
        seen: list[str] = []

        (tmp_path / _FIRST).write_bytes(
            data=(_REFERENCE_DIR / _FIRST).read_bytes(),
        )

        downloaded = download_reference_lists(
            directory=tmp_path,
            client=_client_of(
                seen,
            ),
        )

        assert downloaded == (_SECOND,)

        assert seen == [
            reference_url(
                name=_SECOND,
            ),
        ]

    def test_lists_that_are_all_present_and_right_need_no_request(
        self,
        *,
        tmp_path: Path,
    ) -> None:
        seen: list[str] = []

        for name in REFERENCE_FILES:
            (tmp_path / name).write_bytes(
                data=(_REFERENCE_DIR / name).read_bytes(),
            )

        assert (
            download_reference_lists(
                directory=tmp_path,
                client=_client_of(
                    seen,
                ),
            )
            == ()
        )

        assert seen == []

    def test_a_present_list_with_another_digest_raises_and_is_left_as_it_is(
        self,
        *,
        tmp_path: Path,
    ) -> None:
        seen: list[str] = []

        (tmp_path / _FIRST).write_bytes(
            data=b"foo",
        )

        with raises(
            expected_exception=LinguisticResourcesMissingError,
        ) as caught:
            download_reference_lists(
                directory=tmp_path,
                client=_client_of(
                    seen,
                ),
            )

        actual = sha256(
            data=b"foo",
        ).hexdigest()

        assert (
            str(
                object=caught.value,
            )
            == f"the reference list {tmp_path / _FIRST} differs from the published one (SHA-256 {actual}, not "
            f"{REFERENCE_DIGESTS[_FIRST]}); delete it, and `wordwinnow setup` downloads it again"
        )

        assert (tmp_path / _FIRST).read_bytes() == b"foo"

        assert sorted(path.name for path in tmp_path.iterdir()) == [
            _FIRST,
        ]

        assert seen == []
