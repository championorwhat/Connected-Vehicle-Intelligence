# syntax=docker/dockerfile:1.7
# Prognos ML (live scorer). Build context: repository root.
#   docker build -f ml/Dockerfile -t prognos-ml .
# The versioned model artefacts under ml/models are baked into the image.
FROM python:3.14-slim AS build
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /src
COPY pyproject.toml uv.lock ./
COPY packages/common packages/common
COPY apps/simulator apps/simulator
COPY apps/stream-processor apps/stream-processor
COPY ml/pyproject.toml ml/pyproject.toml
COPY ml/src ml/src
RUN --mount=type=secret,id=extra_ca,required=false \
    if [ -s /run/secrets/extra_ca ]; then \
      export PIP_CERT=/run/secrets/extra_ca SSL_CERT_FILE=/run/secrets/extra_ca; \
    fi; \
    pip install --no-cache-dir "uv>=0.8,<0.9" \
    && uv sync --frozen --no-dev --package prognos-ml --no-editable

FROM python:3.14-slim
# LightGBM needs the OpenMP runtime (libgomp). apt over HTTPS; the optional `extra_ca`
# build secret is trusted too, for builds behind a TLS-intercepting proxy.
RUN --mount=type=secret,id=extra_ca,required=false \
    sed -i 's|http://deb.debian.org|https://deb.debian.org|g' /etc/apt/sources.list.d/debian.sources \
    && CA=""; if [ -s /run/secrets/extra_ca ]; then CA="-o Acquire::https::CAInfo=/run/secrets/extra_ca"; fi \
    && apt-get $CA update && apt-get $CA install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --system --uid 10001 --no-create-home ml
COPY --from=build /opt/venv /opt/venv
COPY ml/models /models
ENV PATH=/opt/venv/bin:$PATH PYTHONUNBUFFERED=1 MODEL_DIR=/models/failure-7d-v3
USER 10001
STOPSIGNAL SIGTERM
ENTRYPOINT ["prognos-scorer"]
