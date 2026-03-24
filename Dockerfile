# =============================================================================
# Talaria Dockerfile — multi-stage build (development + production)
# =============================================================================

# ---------------------------------------------------------------------------
# Stage 1: builder — install all deps (cached unless requirements change)
# ---------------------------------------------------------------------------
FROM python:3.11-slim AS builder

WORKDIR /app

# Install system deps required by pip/gunicorn and git (needed at runtime too)
RUN apt-get update && apt-get install -y --no-install-recommends \
        git \
        curl \
    && rm -rf /var/lib/apt/lists/*

# Copy only dependency manifests first (best caching practice)
COPY requirements.txt pyproject.toml ./
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -e .

# ---------------------------------------------------------------------------
# Stage 2: production image
# ---------------------------------------------------------------------------
FROM python:3.11-slim AS production

WORKDIR /app

# Runtime system deps (git, curl for healthchecks)
RUN apt-get update && apt-get install -y --no-install-recommends \
        git \
        curl \
    && rm -rf /var/lib/apt/lists/*

# Copy installed packages from builder
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin           /usr/local/bin

# Copy application source
COPY . .

EXPOSE 8400

# Run BOTH the API server and the agent watcher as background processes.
# 'wait' blocks until all background children exit, so Docker won't exit
# prematurely if one process dies.
#
# agent_watcher.py reads these env vars:
#   TALARIA_PORT      — board API port  (default: 8400)
#   TALARIA_HOME      — Talaria data dir (default: ~/.talaria/talaria)
#   TALARIA_WORK_DIR  — repo working directory (default: .)
#   MAX_CONCURRENT    — max simultaneous agents (default: 2)
#   POLL_INTERVAL     — seconds between board polls (default: 15)
#
# server.py reads these env vars:
#   TALARIA_PORT      — must match the EXPOSE value
#   TALARIA_WORK_DIR  — same value as agent_watcher uses
#
CMD python server.py & \
    python agent_watcher.py & \
    wait
