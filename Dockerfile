# NOTE:
# One image for every application service; the command selects the process.
#
# The build stage installs the locked environment, the NLTK data the linguistic adapters need, and the trained level
# model, so a container never downloads or trains anything at run time.
#
# Every image here is pinned by digest as well as by tag: the tag says which release it is, and the digest makes a
# rebuild start from the same bytes even if the tag is later moved.
#
# The application verifies every HTTPS request against the operating system's trust store, which is where `httpx2`
# looks, so both stages rely on the base image's `ca-certificates` package: the build stage to download the NLTK data,
# the final stage to reach the providers.

# NOTE:
# The uv binary is pinned to the version the lock file was produced with, copied from its own image.
FROM ghcr.io/astral-sh/uv:0.13.6@sha256:af22a52f5eafef3d78e551599d405c02df61919d18028f0cea8eda93bc9986d0 AS uv

FROM python:3.14.10-slim-trixie@sha256:3cabfd7d7aaf394e6040e79fa1eec6ca1c52b74ed9188650472695d9a9326999 AS build

COPY --from=uv /uv /uvx /bin/

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

COPY pyproject.toml uv.lock README.md LICENSE NOTICE ./

RUN uv sync --frozen --no-dev --no-install-project

COPY wordwinnow ./wordwinnow

COPY data/reference ./data/reference

RUN uv sync --frozen --no-dev

# NOTE:
# The image keeps explicit paths rather than the user's directories: it trains from the lists it copies above, and the
# final stage names the same two paths.
ENV WORDWINNOW_NLTK_DATA=/opt/nltk_data \
    WORDWINNOW_REFERENCE_LISTS_DIR=/app/data/reference \
    WORDWINNOW_LEVEL_MODEL_PATH=/app/models/cefr_level_model.joblib

RUN /app/.venv/bin/wordwinnow doctor --install-nltk-data

# NOTE:
# The level model is a derived artifact: trained here from the reference lists in under a minute, so the image assigns
# levels the way the experiment recommends without a model file being committed.
RUN /app/.venv/bin/wordwinnow ml train --model-path /app/models/cefr_level_model.joblib

FROM python:3.14.10-slim-trixie@sha256:3cabfd7d7aaf394e6040e79fa1eec6ca1c52b74ed9188650472695d9a9326999

# NOTE:
# `wget` is what the Compose health checks use; nothing else in the image needs a shell tool.
RUN apt-get update \
    && apt-get install --no-install-recommends -y wget \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 1000 wordwinnow

WORKDIR /app

COPY --from=build --chown=wordwinnow:wordwinnow /app /app

COPY --from=build --chown=wordwinnow:wordwinnow /opt/nltk_data /opt/nltk_data

ENV PATH=/app/.venv/bin:$PATH \
    WORDWINNOW_NLTK_DATA=/opt/nltk_data \
    WORDWINNOW_REFERENCE_LISTS_DIR=/app/data/reference \
    WORDWINNOW_LEVEL_MODEL_PATH=/app/models/cefr_level_model.joblib \
    PYTHONUNBUFFERED=1

USER wordwinnow

EXPOSE 8010 8100 9464

CMD ["wordwinnow", "--help"]
