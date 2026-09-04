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
# Pinned by digest, not by tag. A tag is a name its owner can repoint at
# different bytes; a digest is the bytes. This is the same argument the
# Python dependencies are pinned under, and it has the same condition
# attached: a digest only helps while something moves it, which is what
# the docker ecosystem entry in dependabot.yml is for. The trailing
# comment is how Dependabot knows which version it is looking at.
#
# There is no ARG for the version any more — a digest and a
# substitutable tag cannot both be the source of truth.
FROM python:3.14-slim-bookworm@sha256:416f0db2a2b561945630cef9877a7ea0581b27449eb9fd9df42f03e1b74b5b63 AS base

# Fail the build on a pipe failure rather than silently continuing.
SHELL ["/bin/bash", "-o", "pipefail", "-c"]

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# Qt links against these even though the PyQt6 wheels bundle Qt itself.
# The list is not guesswork: it is what `objdump -p` reports as NEEDED
# for libQt6Core/Gui/Widgets/DBus and the offscreen platform plugin,
# minus what the wheels bundle (ICU) and what the base image already
# has (zlib). Get it wrong and the failure is an ImportError naming a
# soname, not anything that mentions Qt.
#
# This set is what the *offscreen* plugin needs, which is all the ci
# stage ever loads. Displaying a window needs the xcb plugin and eight
# more libraries; those are installed in the runtime stage, so the ci
# image does not carry a display stack it never uses.
#
# Version-pinned installs are avoided on purpose: these are security-
# updated system packages, and pinning them freezes out those updates.
# hadolint ignore=DL3008
RUN apt-get update \
    && apt-get install --no-install-recommends -y \
        libglib2.0-0 \
        libegl1 \
        libgl1 \
        libx11-6 \
        libxkbcommon-x11-0 \
        libdbus-1-3 \
        libfontconfig1 \
        libfreetype6 \
        libzstd1 \
        tini \
    && rm -rf /var/lib/apt/lists/*


# ------------------------------------------------------------ builder
FROM base AS builder

# uv is copied from its own published image rather than curl-installed,
# so the version is explicit and the download is not a build-time
# network dependency that can change under us. Digest-pinned for the
# same reason as the base image.
COPY --from=ghcr.io/astral-sh/uv:0.12.7@sha256:95f2aa1fe59274951cfe9b0cbc7972e879ff1004bc8945d130a32eb0dbd85945 /uv /uvx /usr/local/bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv

WORKDIR /build

# Runtime dependencies only — no --extra dev. This stage is what the
# runtime image copies its virtualenv from, and a virtualenv carrying
# ruff, mypy and pytest is not a runtime environment however carefully
# the rest of the image is trimmed. The ci stage adds them back for
# itself.
#
# Dependencies first, without the source. This layer is keyed on the
# lockfile alone, so editing application code does not re-resolve or
# re-download a single package.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-install-project

# Now the source, and the project itself on top of the cached deps.
COPY netplanner/ ./netplanner/
COPY README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-editable


# ----------------------------------------------------------------- ci
# Everything needed to run the gates. Kept separate from runtime so the
# shipped image does not carry ruff, mypy, pytest and their trees.
FROM builder AS ci

COPY tests/ ./tests/
COPY .github/ ./.github/

# The toolchain, layered onto the runtime environment rather than baked
# into it. Everything below this line exists only in this stage.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-editable --extra dev

# Importing Qt is the thing the system-library list exists to make
# possible, so prove it at build time. A missing soname otherwise
# surfaces as six pytest collection errors naming a .so nobody
# recognises, several minutes into a run.
RUN /opt/venv/bin/python -c "\
import PyQt6.QtWidgets, PyQt6.QtGui, PyQt6.QtCore; \
from PyQt6.QtWidgets import QApplication; \
import sys, os; os.environ['QT_QPA_PLATFORM']='offscreen'; \
app = QApplication(sys.argv); \
print('Qt platform:', app.platformName())"

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

# The platform plugins that actually put a window on a screen: xcb for
# an X session, wayland for a Wayland one. Qt loads whichever by dlopen,
# so nothing links these at build time and no import test finds them
# missing — the failure is at startup, from Qt, saying only that it
# "could not load the Qt platform plugin". libxcb-cursor in particular
# became mandatory in Qt 6.5.
#
# Both are installed because the image does not know which session it
# will be run from, and the three wayland libraries are small.
# hadolint ignore=DL3008
RUN apt-get update \
    && apt-get install --no-install-recommends -y \
        libxcb-cursor0 \
        libxcb-icccm4 \
        libxcb-image0 \
        libxcb-keysyms1 \
        libxcb-render-util0 \
        libxcb-render0 \
        libxcb-shape0 \
        libxcb-util1 \
        libwayland-client0 \
        libwayland-cursor0 \
        libwayland-egl1 \
    && rm -rf /var/lib/apt/lists/*

# A fixed uid so a bind-mounted data directory has predictable ownership
# on the host. --system: this account exists to own a process, not to be
# logged into, so it gets no password and no shell.
RUN groupadd --system --gid 1000 netplanner \
    && useradd --system --uid 1000 --gid netplanner \
        --home-dir /home/netplanner --create-home \
        --shell /usr/sbin/nologin netplanner

# Only the virtualenv crosses over, and it comes from builder rather
# than ci: no uv, no compilers, no apt lists, no test tree, and no
# ruff/mypy/pytest. Whatever is not here cannot be exploited here.
#
# This is what the "runtime image is not the ci image" job in ci.yml
# checks, and the check earned its place: the first version of this
# Dockerfile installed --extra dev in builder, so the runtime image
# shipped the whole toolchain inside an otherwise carefully trimmed
# image.
COPY --from=builder --chown=root:root /opt/venv /opt/venv

# XDG_CACHE_HOME is set because HOME sits on the root filesystem, which
# compose mounts read-only. Without it fontconfig cannot write
# ~/.cache/fontconfig, reports "No writable cache directories" four
# times at every start, and rescans every font on disk because it has
# nowhere to remember them. Debian's fonts.conf resolves its cache
# through <cachedir prefix="xdg">, so pointing that at the one writable
# directory is enough. /tmp is a tmpfs, so the cache is rebuilt per run
# — a moment's work on a dozen fonts, and no writable home needed.
ENV PATH="/opt/venv/bin:${PATH}" \
    XDG_DATA_HOME=/data \
    NETPLANNER_LOG_DIR=/data/logs \
    XDG_CACHE_HOME=/tmp

# The plan database and logs live here. Declared so a `docker run` with
# no -v still keeps them out of the writable layer, and so the directory
# exists owned by the right user before anything tries to write to it.
RUN install -d -o netplanner -g netplanner -m 0700 /data /data/logs
VOLUME ["/data"]

# Every shared library the xcb plugin needs must resolve, checked here
# rather than discovered by a user with no window. ldd reports an
# unresolved soname as "not found", and needs no display to say so —
# which is the whole point, since the build has none.
RUN set -eu; \
    for name in libqxcb.so libqwayland.so; do \
        plugin="$(find /opt/venv -name "$name" -print -quit)"; \
        echo "checking $plugin"; \
        missing="$(ldd "$plugin" || true)"; \
        case "$missing" in \
            *"not found"*) \
                echo "$missing" >&2; \
                echo "$name has unresolved libraries" >&2; \
                exit 1 ;; \
        esac; \
    done; \
    echo "both platform plugins resolve cleanly"

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
