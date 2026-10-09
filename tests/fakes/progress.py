"""
Recording doubles for the progress a long command reports, and for what a
watch of the dictionary hears.
"""

from typing import (
    Final,
    final,
)

from wordwinnow.application.dto import (
    ProcessingProgress,
)
from wordwinnow.infrastructure.dictionary.activity import (
    DictionaryActivity,
)


@final
class RecordingWorkProgress:
    """
    Records each phase begun, followed by its steps when it named them, each
    later description of a phase as it was given, each finished step as
    `step`, and each report of a worker as its stage and count.
    """

    def __init__(
        self,
    ) -> None:
        self.events: Final[list[str]] = []

    def begin(
        self,
        *,
        description: str,
        steps: int | None,
    ) -> None:
        self.events.append(
            description if steps is None else f"{description} of {steps}",
        )

    def describe(
        self,
        *,
        description: str,
    ) -> None:
        self.events.append(
            description,
        )

    def advance(
        self,
    ) -> None:
        self.events.append(
            "step",
        )

    def processing(
        self,
        *,
        progress: ProcessingProgress,
    ) -> None:
        paused = ", paused" if progress.dictionary_paused else ""

        self.events.append(
            f"{progress.stage}: {progress.done} of {progress.steps}, {progress.unavailable} unavailable{paused}",
        )


@final
class RecordingWatch:
    """
    Records what a watch of the dictionary hears: each activity as the state
    of its circuit, and each reason the activity could not be read.
    """

    def __init__(
        self,
    ) -> None:
        self.heard: Final[list[str]] = []

    def watched(
        self,
        *,
        activity: DictionaryActivity,
    ) -> None:
        self.heard.append(
            f"circuit {activity.provider.circuit.name.lower()}",
        )

    def unwatched(
        self,
        *,
        reason: str,
    ) -> None:
        self.heard.append(
            reason,
        )
