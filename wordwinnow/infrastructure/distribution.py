"""
The installed distribution Wordwinnow runs from: the version it was built
with, read once from the distribution's own metadata.
"""

from importlib.metadata import (
    version,
)
from typing import (
    Final,
)

# NOTE:
# `pyproject.toml` is the one place the version is written, and the build copies it into the distribution's metadata,
# so this read is the one place the package learns it: the command line, the doctor, and the providers' User-Agent
# take it from here and state it nowhere.
VERSION: Final = version(
    distribution_name="wordwinnow",
)
