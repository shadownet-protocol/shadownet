# shadownet-conformance — Docker image used by the GitHub Action and by
# operators who want a hermetic runner with no Python toolchain on the host.
#
# Build:   docker build -f action/Dockerfile -t shadownet-conformance:dev .
# Run:     docker run --rm --network host shadownet-conformance:dev \
#              --target sca=http://localhost:8443 --target sns=http://localhost:8444

FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_NO_CACHE=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates \
 && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv==0.5.*

WORKDIR /app

# Tests + fixtures + this package's source — uv resolves shadownet from PyPI.
COPY pyproject.toml README.md CHANGELOG.md LICENSE CLAUDE.md uv.lock ./
COPY src ./src
COPY tests ./tests
COPY fixtures ./fixtures

RUN uv sync --frozen --no-dev

ENTRYPOINT ["uv", "run", "shadownet-conformance"]
CMD ["--help"]
