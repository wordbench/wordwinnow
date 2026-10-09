"""
What every New York Times adapter shares: the credentialed request, the
payload shapes the APIs document, and how stories become one document that
carries its attribution, its links, and how long it may be kept.
"""

from collections.abc import (
    Mapping,
    Sequence,
)
from datetime import (
    timedelta,
)
from logging import (
    getLogger,
)
from typing import (
    Final,
    final,
)

from httpx2 import (
    AsyncClient,
    HTTPError,
    TimeoutException,
)
from pydantic import (
    BaseModel,
    ConfigDict,
    SecretStr,
)

from wordwinnow.application.errors import (
    SourceNotConfiguredError,
    SourceRejectedError,
    SourceUnavailableError,
)
from wordwinnow.domain.document import (
    Document,
    DocumentOrigin,
    InvalidDocumentError,
    Reference,
)
from wordwinnow.infrastructure.user_agent import (
    IDENTIFYING_HEADERS,
)

DEFAULT_BASE_URL: Final = "https://api.nytimes.com"

# NOTE:
# The written attribution the API branding guide allows where a logo cannot be shown, and the longest the API terms
# allow content to be kept for a user's access: twenty-four hours.
ATTRIBUTION: Final = "Data provided by The New York Times"

RETENTION: Final = timedelta(
    hours=24,
)

_logger: Final = getLogger(
    name="wordwinnow.sources.nyt",
)

# NOTE:
# A section's results can include page embeds rather than stories: the science section carries a newsletter sign-up
# whose URL is the string "null", and an empty embed beside it.
_EMBEDDED_PAGE: Final = "EmbeddedInteractive"

_WEB_ADDRESS_PREFIXES: Final = (
    "https://",
    "http://",
)


class Story(
    BaseModel,
):
    """
    One story as the Top Stories and Most Popular APIs describe it, reduced to
    what a document needs.
    """

    model_config = ConfigDict(
        extra="ignore",
    )

    title: str = ""

    abstract: str = ""

    url: str = ""

    item_type: str = ""


class StoriesPayload(
    BaseModel,
):
    """
    The envelope those two APIs share.
    """

    model_config = ConfigDict(
        extra="ignore",
    )

    results: list[Story] = []


class Headline(
    BaseModel,
):
    """
    A headline as the Article Search and Archive APIs describe it.
    """

    model_config = ConfigDict(
        extra="ignore",
    )

    main: str = ""


class SearchDocument(
    BaseModel,
):
    """
    One article as the Article Search and Archive APIs describe it.
    """

    model_config = ConfigDict(
        extra="ignore",
    )

    headline: Headline = Headline()

    abstract: str = ""

    lead_paragraph: str = ""

    web_url: str = ""


class SearchResponse(
    BaseModel,
):
    """
    The documents of one search page or one archive month.
    """

    model_config = ConfigDict(
        extra="ignore",
    )

    docs: list[SearchDocument] = []


class SearchPayload(
    BaseModel,
):
    """
    The envelope the Article Search and Archive APIs share.
    """

    model_config = ConfigDict(
        extra="ignore",
    )

    response: SearchResponse = SearchResponse()


def raise_for_status(
    *,
    status: int,
    what: str,
    credentialed: bool,
) -> None:
    """
    Turn an HTTP error status into the application error it means.
    """

    if credentialed and status in {
        401,
        403,
    }:
        raise SourceNotConfiguredError(
            f"{what} refused the key in WORDWINNOW_NYT_API_KEY (HTTP {status}): check the key, and that the New York "
            "Times app it belongs to has this API enabled",
        )

    if status == 404:
        raise SourceRejectedError(
            f"{what} is not something the New York Times has (HTTP 404)",
        )

    if status == 429:
        raise SourceUnavailableError(
            f"{what} was rate limited (HTTP 429)",
        )

    if status >= 400:
        raise SourceUnavailableError(
            f"{what} answered HTTP {status}",
        )


@final
class NytApi:
    """
    One credentialed client for every New York Times API.

    The key travels as a query parameter, which is why no URL from here
    reaches a log, a span, or an exception message.
    """

    def __init__(
        self,
        *,
        api_key: SecretStr,
        base_url: str = DEFAULT_BASE_URL,
        client: AsyncClient,
    ) -> None:
        self._api_key: Final = api_key

        self._base_url: Final = base_url.rstrip(
            "/",
        )

        self._client: Final = client

    async def get[T: BaseModel](
        self,
        *,
        path: str,
        params: Mapping[str, str],
        what: str,
        model: type[T],
    ) -> T:
        """
        Fetch one endpoint and parse its payload, naming `what` was asked for
        in every failure.
        """

        try:
            response = await self._client.get(
                url=f"{self._base_url}{path}",
                params={
                    **params,
                    "api-key": self._api_key.get_secret_value(),
                },
                headers=IDENTIFYING_HEADERS,
            )

        except TimeoutException as exception:
            raise SourceUnavailableError(
                f"{what} timed out",
            ) from exception

        except HTTPError as exception:
            kind = type(
                exception,
            ).__name__

            raise SourceUnavailableError(
                f"{what} could not be fetched ({kind})",
            ) from exception

        _logger.info(
            msg="source.fetched",
            extra={
                "origin": DocumentOrigin.NEW_YORK_TIMES,
                "what": what,
                "status": response.status_code,
            },
        )

        raise_for_status(
            status=response.status_code,
            what=what,
            credentialed=True,
        )

        try:
            return model.model_validate_json(
                json_data=response.content,
            )

        except ValueError as exception:
            raise SourceUnavailableError(
                f"{what} answered with an unexpected payload",
            ) from exception


def readable_stories(
    *,
    stories: Sequence[Story],
) -> tuple[Story, ...]:
    """
    The results that are stories a learner can read, in the order the API gave
    them.

    A page embed and a result with neither a headline nor an abstract are left
    out, so neither takes one of the places a limit allows.
    """

    return tuple(
        story
        for story in stories
        if story.item_type != _EMBEDDED_PAGE and (story.title.strip() or story.abstract.strip())
    )


def story_paragraphs(
    *,
    title: str,
    abstract: str,
) -> tuple[str, ...]:
    """
    One story as its own paragraphs: the headline, then the abstract, each
    exactly as the API gave it.
    """

    return tuple(
        part.strip()
        for part in (
            title,
            abstract,
        )
        if part.strip()
    )


def search_paragraphs(
    *,
    document: SearchDocument,
) -> tuple[str, ...]:
    """
    One article as its own paragraphs: the headline, the abstract, and the
    lead paragraph when it says more than the abstract.
    """

    lead = value if (value := document.lead_paragraph).strip() != document.abstract.strip() else ""

    return tuple(
        part.strip()
        for part in (
            document.headline.main,
            document.abstract,
            lead,
        )
        if part.strip()
    )


def reference_of(
    *,
    title: str,
    url: str,
) -> Reference | None:
    """
    The link to read a story in full, or `None` when the API gave no web
    address, such as an empty URL or the string `null`.
    """

    if not url.strip().startswith(
        _WEB_ADDRESS_PREFIXES,
    ):
        return None

    return Reference(
        title=title.strip() or url.strip(),
        url=url.strip(),
    )


def document_of(
    *,
    title: str,
    reference: str,
    paragraphs: Sequence[str],
    references: Sequence[Reference | None],
) -> Document:
    """
    Join the paragraphs into one attributed document that may be kept for a
    day, or say there is nothing to read.
    """

    try:
        return Document(
            title=title,
            text="\n\n".join(paragraph for paragraph in paragraphs if paragraph.strip()),
            origin=DocumentOrigin.NEW_YORK_TIMES,
            reference=reference,
            attribution=ATTRIBUTION,
            references=tuple(entry for entry in references if entry is not None),
            retention=RETENTION,
        )

    except InvalidDocumentError as exception:
        raise SourceUnavailableError(
            f"{title} holds no text right now",
        ) from exception
