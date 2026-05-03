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

# Copy only what we need to build & run. Tests + fixtures + shadownet-py
# editable path-dep are all required because the runner imports them.
COPY pyproject.toml README.md CHANGELOG.md LICENSE CLAUDE.md uv.lock* ./
COPY src ./src
COPY tests ./tests
COPY fixtures ./fixtures

# shadownet-py is a path-dep; copy it next to ourselves so uv can resolve.
# The ../shadownet-py checkout must be present in the build context.
COPY ./.docker-shadownet-py /shadownet-py

# Rewrite the path-dep to point at /shadownet-py (the in-image location)
# instead of ../shadownet-py (host-relative).
RUN sed -i 's|path = "../shadownet-py"|path = "/shadownet-py"|' pyproject.toml

RUN uv sync --frozen --no-dev || uv sync --no-dev

ENTRYPOINT ["uv", "run", "shadownet-conformance"]
CMD ["--help"]
