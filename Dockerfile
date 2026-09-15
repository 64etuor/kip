# syntax=docker/dockerfile:1.27@sha256:bde3983e9c939224420ddaf6b784cc30e09b035a4dea01f581230c50809f372e
ARG BUILDKIT_SBOM_SCAN_STAGE=true
ARG PYTHON_IMAGE=python:3.12-slim@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea
ARG NODE_IMAGE=node:22-trixie-slim@sha256:7b8a0c89c54499bee567618f96578e1a12a800f062fbdbfd1fb6a443fa6f6284

FROM ${NODE_IMAGE} AS kordoc
ENV KORDOC_MODEL_CACHE=/opt/kordoc-models
WORKDIR /opt/kordoc-runtime
COPY requirements/kordoc/package.json requirements/kordoc/package-lock.json /opt/requirements/kordoc/
COPY scripts/common.sh scripts/audit-kordoc.sh /opt/scripts/
RUN /opt/scripts/audit-kordoc.sh && \
    cp /opt/requirements/kordoc/package.json /opt/requirements/kordoc/package-lock.json ./ && \
    npm ci --omit=dev --ignore-scripts --no-audit && \
    test "$(node node_modules/kordoc/dist/cli.js --version)" = "$(node -p 'require("./package.json").dependencies.kordoc')" && \
    node node_modules/kordoc/dist/cli.js check-ocr-models

FROM ${PYTHON_IMAGE} AS builder
ARG BUILDKIT_SBOM_SCAN_STAGE=true
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1
WORKDIR /build
COPY requirements/build.txt requirements/runtime.txt ./requirements/
RUN --mount=type=cache,target=/root/.cache/pip \
    python -m pip install --require-hashes -r requirements/build.txt && \
    python -m pip wheel --require-hashes --wheel-dir /wheels -r requirements/runtime.txt
COPY pyproject.toml uv.lock README.md VERSION ./
COPY src ./src
RUN python -m pip wheel --no-build-isolation --no-deps --wheel-dir /wheels .

FROM ${PYTHON_IMAGE} AS runtime
ARG VERSION=dev
ARG VCS_REF=unknown
ARG SOURCE_URL=unknown
LABEL org.opencontainers.image.title="KIP Knowledge Fabric" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.source="${SOURCE_URL}" \
      org.opencontainers.image.licenses="MIT"
ENV PATH=/app/scripts:/opt/venv/bin:$PATH \
    KIP_USE_MANAGED_RUNTIMES=0 \
    KIP_KORDOC_PACKAGE_DIR=/opt/kordoc-runtime/node_modules/kordoc \
    KORDOC_MODEL_CACHE=/opt/kordoc-models \
    KORDOC_OFFLINE=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
RUN groupadd --system --gid 10001 kip && \
    useradd --system --uid 10001 --gid 10001 --no-create-home --home-dir /nonexistent kip && \
    python -m venv /opt/venv
WORKDIR /app
COPY --from=builder /wheels /wheels
COPY --from=kordoc /usr/local/bin/node /usr/local/bin/node
COPY --from=kordoc /opt/kordoc-runtime/node_modules /opt/kordoc-runtime/node_modules
COPY --from=kordoc /opt/kordoc-models /opt/kordoc-models
RUN /opt/venv/bin/pip install --no-cache-dir --no-index /wheels/* && \
    rm -rf /wheels
COPY config ./config
COPY contracts ./contracts
COPY ontology ./ontology
COPY migrations ./migrations
COPY scripts ./scripts
COPY VERSION LICENSE ./
RUN mkdir -p /data/cas /app/var && \
    chown -R 10001:10001 /data /app/var /app/ontology && \
    chmod -R a-w /app/config /app/contracts /app/migrations /app/scripts
USER 10001:10001
STOPSIGNAL SIGTERM
ENTRYPOINT ["kip"]
CMD ["capabilities"]
