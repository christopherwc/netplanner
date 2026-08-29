# syntax=docker/dockerfile:1.7

# NetPlanner in a container.
#
# Two targets, for two different jobs:
#
#   --target ci       the full toolchain, for running the gates in a
#                     reproducible environment. No display needed; the
#                     GUI tests run under the offscreen Qt platform.
#
#   --target runtime  the application, with only the libraries it needs
#                     to run. Needs a display forwarded in — see
#                     compose.yaml and the README.
#
# The runtime image is not a substitute for packaging a desktop
# application. It exists so the app can be run against a known-good
# dependency set without touching the host's Python, which is useful for
# reproducing a bug report. A user who just wants NetPlanner is better
# served by a distribution package.

# --------------------------------------------------------------- base
# Digest pinning is the best practice here and is deliberately not done:
# see "Known gaps" in README.md. Resolving a real digest needs registry
# access, and a digest nobody verified is worse than an honest tag.
ARG PYTHON_VERSION=3.12
FROM python:${PYTHON_VERSION}-slim-bookworm AS base

# Fail the build on a pipe failure rather than silently continuing.
SHELL ["/bin/bash", "-o", "pipefail", "-c"]

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# Qt links against these even though the PyQt6 wheels bundle Qt itself.
# Without them the offscreen platform plugin refuses to load and every
# GUI test fails with a bare "could not load the Qt platform plugin".
# Version-pinned installs are avoided on purpose: these are security-
# updated system packages, and pinning them freezes out those updates.
# hadolint ignore=DL3008
RUN apt-get update \
    && apt-get install --no-install-recommends -y \
        libegl1 \
        libgl1 \
        libxkbcommon-x11-0 \
        libdbus-1-3 \
        libfontconfig1 \
        tini \
    && rm -rf /var/lib/apt/lists/*


# ------------------------------------------------------------ builder
FROM base AS builder

# uv is copied from its own published image rather than curl-installed,
# so the version is explicit and the download is not a build-time
# network dependency that can change under us.
COPY --from=ghcr.io/astral-sh/uv:0.12.7 /uv /uvx /usr/local/bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv

WORKDIR /build

# Dependencies first, without the source. This layer is keyed on the
# lockfile alone, so editing application code does not re-resolve or
# re-download a single package.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-install-project --extra dev

# Now the source, and the project itself on top of the cached deps.
COPY netplanner/ ./netplanner/
COPY README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-editable --extra dev


# ----------------------------------------------------------------- ci
# Everything needed to run the gates. Kept separate from runtime so the
# shipped image does not carry ruff, mypy, pytest and their trees.
FROM builder AS ci

COPY tests/ ./tests/
COPY .github/ ./.github/

ENV PATH="/opt/venv/bin:${PATH}" \
    QT_QPA_PLATFORM=offscreen \
    XDG_DATA_HOME=/tmp/netplanner-data \
    NETPLANNER_LOG_DIR=/tmp/netplanner-logs

# Runs as root because a CI container is torn down after one job and
# needs to write wherever the runner mounts things. The runtime image,
# which is the one that outlives a job, does not.
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["sh", "-c", "ruff check . && mypy netplanner && pytest --cov=netplanner --cov-fail-under=100 -q"]


# ------------------------------------------------------------ runtime
FROM base AS runtime

LABEL org.opencontainers.image.title="NetPlanner" \
      org.opencontainers.image.description="Network planning and diagramming tool" \
      org.opencontainers.image.source="https://github.com/christopherwc/netplanner" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.documentation="https://github.com/christopherwc/netplanner#readme"

# A fixed uid so a bind-mounted data directory has predictable ownership
# on the host. --system: this account exists to own a process, not to be
# logged into, so it gets no password and no shell.
RUN groupadd --system --gid 1000 netplanner \
    && useradd --system --uid 1000 --gid netplanner \
        --home-dir /home/netplanner --create-home \
        --shell /usr/sbin/nologin netplanner

# Only the virtualenv crosses over: no uv, no compilers, no apt lists,
# and no test tree. Whatever is not here cannot be exploited here.
COPY --from=builder --chown=root:root /opt/venv /opt/venv

ENV PATH="/opt/venv/bin:${PATH}" \
    XDG_DATA_HOME=/data \
    NETPLANNER_LOG_DIR=/data/logs

# The plan database and logs live here. Declared so a `docker run` with
# no -v still keeps them out of the writable layer, and so the directory
# exists owned by the right user before anything tries to write to it.
RUN install -d -o netplanner -g netplanner -m 0700 /data /data/logs
VOLUME ["/data"]

USER netplanner
WORKDIR /home/netplanner

# tini as pid 1: Qt does not reap children or handle SIGTERM as an init
# process is expected to, so without it a stopped container waits out
# the full timeout and leaves zombies behind.
ENTRYPOINT ["/usr/bin/tini", "--", "netplanner"]

# No HEALTHCHECK. A healthcheck should answer "is this serving
# correctly", and a desktop application with no listener and no endpoint
# has no honest answer to give. One that only proved the process had not
# exited would be decoration.
