# syntax=docker/dockerfile:1.7
# Prognos stream processor (normalizer). Build context: repository root.
#   docker build -f apps/stream-processor/Dockerfile -t prognos-stream .
# Optional build secret `extra_ca`: CA bundle for builds behind a TLS-intercepting proxy.
FROM python:3.12-slim AS build
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /src
COPY pyproject.toml uv.lock ./
COPY packages/common packages/common
COPY apps/stream-processor apps/stream-processor
RUN --mount=type=secret,id=extra_ca,required=false \
    if [ -s /run/secrets/extra_ca ]; then \
      export PIP_CERT=/run/secrets/extra_ca SSL_CERT_FILE=/run/secrets/extra_ca; \
    fi; \
    pip install --no-cache-dir "uv>=0.8,<0.9" \
    && uv sync --frozen --no-dev --package prognos-stream --no-editable

FROM python:3.12-slim
RUN useradd --system --uid 10001 --no-create-home stream
COPY --from=build /opt/venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH PYTHONUNBUFFERED=1
USER 10001
EXPOSE 9102
STOPSIGNAL SIGTERM
ENTRYPOINT ["prognos-normalizer"]
