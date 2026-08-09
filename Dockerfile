# syntax=docker/dockerfile:1.18@sha256:dabfc0969b935b2080555ace70ee69a5261af8a8f1b4df97b9e7fbcf6722eddf
ARG BUILDKIT_SBOM_SCAN_STAGE=true
ARG PYTHON_IMAGE=python:3.12-slim@sha256:229a2c5bfa27522db7815ea81f9bed70af17ccb9de9fc7ad142b1877b5830d36

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
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
RUN groupadd --system --gid 10001 kip && \
    useradd --system --uid 10001 --gid 10001 --no-create-home --home-dir /nonexistent kip && \
    python -m venv /opt/venv
WORKDIR /app
COPY --from=builder /wheels /wheels
RUN /opt/venv/bin/pip install --no-cache-dir --no-index /wheels/* && \
    rm -rf /wheels
COPY config ./config
COPY contracts ./contracts
COPY ontology ./ontology
COPY migrations ./migrations
COPY scripts ./scripts
COPY VERSION LICENSE ./
RUN mkdir -p /data/cas /app/var && \
    chown -R 10001:10001 /data /app/var && \
    chmod -R a-w /app/config /app/contracts /app/ontology /app/migrations /app/scripts
USER 10001:10001
STOPSIGNAL SIGTERM
ENTRYPOINT ["kip"]
CMD ["capabilities"]
