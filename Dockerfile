# syntax=docker/dockerfile:1.7
FROM python:3.14-slim AS builder
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1
WORKDIR /build
COPY pyproject.toml README.md ./
COPY src ./src
RUN python -m pip install --upgrade pip && \
    python -m pip wheel --wheel-dir /wheels '.[postgres,api,extractors]'

FROM python:3.14-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN groupadd --system kip && useradd --system --gid kip --create-home kip
WORKDIR /app
COPY --from=builder /wheels /wheels
RUN python -m pip install --no-cache-dir /wheels/* && rm -rf /wheels
COPY config ./config
COPY contracts ./contracts
COPY ontology ./ontology
COPY migrations ./migrations
COPY scripts ./scripts
RUN mkdir -p /data/cas /app/var && chown -R kip:kip /data /app
USER kip
ENTRYPOINT ["kip"]
CMD ["capabilities"]
