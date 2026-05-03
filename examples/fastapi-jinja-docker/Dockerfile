# syntax=docker/dockerfile:1.7

# ---- builder stage -----------------------------------------------------
# Resolve dependencies in a build stage so the final image carries no
# pip/build-essentials bloat. Pinned to slim Python 3.12; downgrade if
# your base host needs an older interpreter.
FROM python:3.12-slim AS builder

WORKDIR /build

# git is required because requirements.txt installs Bug-Fab from a
# pinned GitHub commit (until the package is on PyPI).
RUN apt-get update \
 && apt-get install -y --no-install-recommends git \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ---- runtime stage -----------------------------------------------------
FROM python:3.12-slim

WORKDIR /app

# Pull resolved deps from the builder so the final image stays slim.
COPY --from=builder /install /usr/local

COPY main.py ./
COPY templates/ ./templates/

# Persistent state lives in /data; mount this as a volume in
# docker-compose.yml so reports survive container rebuilds.
ENV BUG_FAB_DATA_DIR=/data
RUN mkdir -p /data
VOLUME ["/data"]

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
