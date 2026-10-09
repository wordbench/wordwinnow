"""
The topics the services speak over, one per message version.

A topic name carries the version because a consumer subscribes to a shape, and
a new shape is a new topic rather than a surprise on the old one.
"""

from typing import (
    Final,
)

ANALYSIS_REQUESTED_V1: Final = "wordwinnow.analysis.requested.v1"

ANALYSIS_COMPLETED_V1: Final = "wordwinnow.analysis.completed.v1"

ALL_TOPICS: Final = (
    ANALYSIS_REQUESTED_V1,
    ANALYSIS_COMPLETED_V1,
)
