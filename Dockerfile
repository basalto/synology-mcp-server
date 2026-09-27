# Multi-stage build for Synology DSM MCP Server
# Alpine for minimal attack surface

FROM python:3.13-alpine AS builder

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apk add --no-cache --virtual .build-deps \
    gcc \
    musl-dev \
    libffi-dev \
    openssl-dev \
    cargo \
    rust

RUN pip install uv

WORKDIR /build
COPY pyproject.toml README.md ./
COPY src ./src

RUN uv venv /opt/venv && \
    . /opt/venv/bin/activate && \
    uv pip install --no-cache .

FROM python:3.13-alpine

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH"

RUN apk add --no-cache \
    libffi \
    openssl \
    ca-certificates && \
    rm -rf /var/cache/apk/*

RUN addgroup -S mcp && \
    adduser -S -G mcp -u 1000 -s /sbin/nologin -D mcp

WORKDIR /app
COPY --from=builder /opt/venv /opt/venv

USER mcp

EXPOSE 3101

ENTRYPOINT ["synology-mcp-server"]
