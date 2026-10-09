"""
The errors the application raises across its boundary, and the one its
publisher port raises into it.

Each class says in its docstring whether the condition is retryable, because
that is what the delivery layer needs to decide what to answer.
"""

from typing import (
    final,
)


@final
class AnalysisNotFoundError(
    LookupError,
):
    """
    Raised when no analysis has the requested identifier.

    Not retryable.
    """


@final
class MessagingUnavailableError(
    RuntimeError,
):
    """
    Raised by the publisher port when the message broker could not be reached
    or refused a message.

    Retryable.
    """


@final
class FactStoreUnavailableError(
    RuntimeError,
):
    """
    Raised by the fact query port when the fact store could not be reached.

    Retryable.
    """


@final
class AnalysisNotPublishedError(
    RuntimeError,
):
    """
    Raised when an analysis was stored but its request could not be published
    to the workers.

    Retryable: the analysis stays requested, and the requeue command publishes
    it once it has waited long enough.
    """


@final
class SourceNotConfiguredError(
    RuntimeError,
):
    """
    Raised when a document origin has no configured source in this process.

    Not retryable without configuration, such as a missing API key.
    """


@final
class SourceRejectedError(
    ValueError,
):
    """
    Raised when a source refused the request, such as an unknown section or a
    file that holds no text.

    Not retryable with the same request.
    """


@final
class SourceUnavailableError(
    RuntimeError,
):
    """
    Raised when a source could not be reached or answered with a failure.

    Retryable.
    """


@final
class LinguisticResourcesMissingError(
    RuntimeError,
):
    """
    Raised when the corpora or the reference lists the linguistic adapters
    need are not installed where the settings say.

    Not retryable until the resources are installed.
    """
