"""
The NLTK data the analysis needs: what is checked, and how a missing package
is fetched.
"""

from collections.abc import (
    Generator,
    Iterator,
)
from contextlib import (
    contextmanager,
)
from io import (
    BytesIO,
)
from os import (
    geteuid,
    umask,
)
from pathlib import (
    Path,
)
from stat import (
    S_IMODE,
)
from typing import (
    Final,
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
    fixture,
    mark,
    raises,
    skip,
)

from wordwinnow.application.errors import (
    LinguisticResourcesMissingError,
)
from wordwinnow.infrastructure.linguistics.nltk_resources import (
    PACKAGES_URL,
    NltkResource,
    add_data_path,
    download_resources,
    ensure_resources,
    missing_resources,
)


@final
class TestNltkResources:
    def test_every_required_resource_is_installed(
        self,
    ) -> None:
        assert missing_resources() == ()

    def test_a_missing_package_is_reported_with_the_command_that_installs_it(
        self,
    ) -> None:
        with raises(
            expected_exception=LinguisticResourcesMissingError,
            match=r"not installed: nonexistent; run `wordwinnow setup`$",
        ):
            ensure_resources(
                resources=(
                    NltkResource(
                        package="nonexistent",
                        kind="corpora",
                        paths=("corpora/nonexistent/",),
                    ),
                ),
            )


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


# TEST:
# A package that stands in for a real one, with the two paths a real package has: the folder an unpacked one would
# leave, and the entry inside its archive, which `_archive` makes, so that a whole one is found and a damaged one is
# not.
NONEXISTENT: Final = NltkResource(
    package="nonexistent",
    kind="corpora",
    paths=(
        "corpora/nonexistent/",
        "corpora/nonexistent.zip/nonexistent/",
    ),
)


def _cut(
    archive: bytes,
    /,
) -> bytes:
    size = len(
        archive,
    )

    return archive[: size // 2]


def _unsigned(
    archive: bytes,
    /,
) -> bytes:
    return archive.replace(
        b"PK\x01\x02",
        b"PK\x00\x00",
    )


# TEST:
# The two ways an archive can be left that NLTK finds and cannot open: its end cut off, which takes the table of
# contents with it, as a write cut short leaves it, and the table's first signature overwritten.
DAMAGED: Final = (
    _cut(
        _archive(),
    ),
    _unsigned(
        _archive(),
    ),
)


# TEST:
# NLTK refuses a folder or a file that another account can write to, so a developer's own umask could make it refuse a
# package for a reason the test is not about; every test here fixes the umask for its duration, and those about a
# umask say so.
@contextmanager
def _umask(
    mask: int,
    /,
) -> Generator[None]:
    # WARN:
    # `os.umask` takes its mask by position alone, `(mask, /)` by `inspect.signature`, so the call stays positional.
    previous = umask(
        mask,
    )

    try:
        yield

    finally:
        umask(
            previous,
        )


@fixture
def umask_022() -> Iterator[None]:
    with _umask(
        0o022,
    ):
        yield


# TEST:
# Downloading a package puts the target on NLTK's search list for the whole process; the list is put back as it was,
# so that the next test starts from the path this one did.
@fixture
def nltk_path() -> Iterator[None]:
    before = list(
        nltk.data.path,
    )

    try:
        yield

    finally:
        nltk.data.path[:] = before


def _client(
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
            content=_archive(),
        )

    return Client(
        transport=MockTransport(
            handler=handler,
        ),
    )


def _put(
    root: Path,
    content: bytes,
    /,
) -> Path:
    folder = root / "corpora"

    folder.mkdir()

    package = folder / "nonexistent.zip"

    package.write_bytes(
        data=content,
    )

    return package


def _names(
    folder: Path,
    /,
) -> tuple[str, ...]:
    return tuple(
        sorted(path.name for path in folder.iterdir()),
    )


def _mode(
    path: Path,
    /,
) -> int:
    # WARN:
    # `S_IMODE` takes its argument by position alone, `(object, /)` by `inspect.signature`, and a keyword raises
    # `TypeError`.
    return S_IMODE(
        path.stat().st_mode,
    )


def _modes(
    target: Path,
    /,
) -> tuple[int, int, int]:
    return (
        _mode(
            target,
        ),
        _mode(
            target / "corpora",
        ),
        _mode(
            target / "corpora" / "nonexistent.zip",
        ),
    )


@mark.usefixtures(
    "umask_022",
    "nltk_path",
)
@final
class TestAPackageIsWrittenWhole:
    def test_a_package_fetched_into_a_new_target_leaves_one_file_and_no_partial_file(
        self,
        *,
        tmp_path: Path,
    ) -> None:
        download_resources(
            resources=(NONEXISTENT,),
            target=tmp_path,
            client=_client(
                [],
            ),
        )

        assert _names(
            tmp_path / "corpora",
        ) == ("nonexistent.zip",)

    def test_a_target_under_a_file_ends_in_one_line_that_names_the_package_and_the_reason(
        self,
        *,
        tmp_path: Path,
    ) -> None:
        (tmp_path / "foo").write_bytes(
            data=b"foo",
        )

        target = tmp_path / "foo" / "bar"

        with raises(
            expected_exception=LinguisticResourcesMissingError,
        ) as refusal:
            download_resources(
                resources=(NONEXISTENT,),
                target=target,
                client=_client(
                    [],
                ),
            )

        destination = target / "corpora" / "nonexistent.zip"

        assert (
            str(
                object=refusal.value,
            )
            == f"NLTK package nonexistent could not be written to {destination} (Not a directory)"
        )

        assert isinstance(
            refusal.value.__cause__,
            OSError,
        )

    def test_a_folder_that_cannot_be_written_keeps_what_it_held(
        self,
        *,
        tmp_path: Path,
    ) -> None:
        if geteuid() == 0:
            skip(
                reason="the root user can write to any folder, so there is none to refuse it",
            )

        package = _put(
            tmp_path,
            b"foo",
        )

        package.parent.chmod(
            mode=0o500,
        )

        try:
            with raises(
                expected_exception=LinguisticResourcesMissingError,
            ) as refusal:
                download_resources(
                    resources=(NONEXISTENT,),
                    target=tmp_path,
                    client=_client(
                        [],
                    ),
                )

        finally:
            package.parent.chmod(
                mode=0o700,
            )

        assert (
            str(
                object=refusal.value,
            )
            == f"NLTK package nonexistent could not be written to {package} (Permission denied)"
        )

        assert package.read_bytes() == b"foo"

        assert _names(
            package.parent,
        ) == ("nonexistent.zip",)

    def test_a_folder_at_the_final_name_of_the_package_stays_and_no_partial_file_is_left(
        self,
        *,
        tmp_path: Path,
    ) -> None:
        destination = tmp_path / "corpora" / "nonexistent.zip"

        destination.mkdir(
            parents=True,
        )

        with raises(
            expected_exception=LinguisticResourcesMissingError,
        ) as refusal:
            download_resources(
                resources=(NONEXISTENT,),
                target=tmp_path,
                client=_client(
                    [],
                ),
            )

        assert (
            str(
                object=refusal.value,
            )
            == f"NLTK package nonexistent could not be written to {destination} (Is a directory)"
        )

        assert destination.is_dir()

        assert _names(
            destination.parent,
        ) == ("nonexistent.zip",)

    def test_a_partial_file_from_an_interrupted_run_is_replaced_and_its_mode_does_not_reach_the_package(
        self,
        *,
        tmp_path: Path,
    ) -> None:
        folder = tmp_path / "corpora"

        folder.mkdir()

        partial = folder / "nonexistent.zip.part"

        partial.write_bytes(
            data=b"foo",
        )

        partial.chmod(
            mode=0o666,
        )

        download_resources(
            resources=(NONEXISTENT,),
            target=tmp_path,
            client=_client(
                [],
            ),
        )

        package = folder / "nonexistent.zip"

        assert is_zipfile(
            filename=package,
        )

        assert (
            _mode(
                package,
            )
            == 0o644
        )

        assert _names(
            folder,
        ) == ("nonexistent.zip",)


@mark.usefixtures(
    "umask_022",
    "nltk_path",
)
@final
class TestADamagedPackageCountsAsMissing:
    @mark.parametrize(
        argnames="content",
        argvalues=DAMAGED,
        ids=(
            "cut to half its length",
            "central directory signature overwritten",
        ),
    )
    def test_a_package_that_is_not_a_whole_archive_is_missing(
        self,
        *,
        tmp_path: Path,
        content: bytes,
    ) -> None:
        _put(
            tmp_path,
            content,
        )

        add_data_path(
            path=tmp_path,
        )

        assert missing_resources(
            resources=(NONEXISTENT,),
        ) == (NONEXISTENT,)

    def test_a_damaged_package_is_reported_with_the_command_that_installs_it(
        self,
        *,
        tmp_path: Path,
    ) -> None:
        _put(
            tmp_path,
            DAMAGED[0],
        )

        add_data_path(
            path=tmp_path,
        )

        with raises(
            expected_exception=LinguisticResourcesMissingError,
            match=r"not installed: nonexistent; run `wordwinnow setup`$",
        ):
            ensure_resources(
                resources=(NONEXISTENT,),
            )

    def test_a_damaged_package_is_fetched_again_and_found_in_the_same_process(
        self,
        *,
        tmp_path: Path,
    ) -> None:
        package = _put(
            tmp_path,
            DAMAGED[0],
        )

        # TEST:
        # The target is on NLTK's path before the download, as every command puts it there before it runs: off the
        # path the damaged file goes unseen and is overwritten in place, so the test would pass without the repair.
        add_data_path(
            path=tmp_path,
        )

        assert download_resources(
            resources=(NONEXISTENT,),
            target=tmp_path,
            client=_client(
                [],
            ),
        ) == (NONEXISTENT,)

        assert (
            missing_resources(
                resources=(NONEXISTENT,),
            )
            == ()
        )

        assert is_zipfile(
            filename=package,
        )

        assert _names(
            package.parent,
        ) == ("nonexistent.zip",)

    def test_a_whole_package_is_found_and_nothing_is_fetched(
        self,
        *,
        tmp_path: Path,
    ) -> None:
        _put(
            tmp_path,
            _archive(),
        )

        add_data_path(
            path=tmp_path,
        )

        assert (
            missing_resources(
                resources=(NONEXISTENT,),
            )
            == ()
        )

        seen: list[str] = []

        assert (
            download_resources(
                resources=(NONEXISTENT,),
                target=tmp_path,
                client=_client(
                    seen,
                ),
            )
            == ()
        )

        assert seen == []


@mark.usefixtures(
    "nltk_path",
)
@final
class TestWhatIsWrittenIsNotWritableByOthers:
    def test_a_group_umask_still_gives_folders_and_a_file_that_nltk_reads(
        self,
        *,
        tmp_path: Path,
    ) -> None:
        target = tmp_path / "nltk_data"

        with _umask(
            0o002,
        ):
            download_resources(
                resources=(NONEXISTENT,),
                target=target,
                client=_client(
                    [],
                ),
            )

        assert _modes(
            target,
        ) == (
            0o755,
            0o755,
            0o644,
        )

        assert (
            missing_resources(
                resources=(NONEXISTENT,),
            )
            == ()
        )

    def test_a_stricter_umask_keeps_what_it_takes_away(
        self,
        *,
        tmp_path: Path,
    ) -> None:
        target = tmp_path / "nltk_data"

        with _umask(
            0o077,
        ):
            download_resources(
                resources=(NONEXISTENT,),
                target=target,
                client=_client(
                    [],
                ),
            )

        assert _modes(
            target,
        ) == (
            0o700,
            0o700,
            0o600,
        )

        assert (
            missing_resources(
                resources=(NONEXISTENT,),
            )
            == ()
        )
