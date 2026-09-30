# syntax=docker/dockerfile:1.7
# Prognos simulator image. Build context: repository root.
#   docker build -f apps/simulator/Dockerfile -t prognos-simulator .

# Optional build secret `extra_ca`: a CA bundle for builds behind a TLS-intercepting
# proxy (corporate networks). Empty by default, in which case nothing changes.
FROM python:3.14-slim AS build
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /src
COPY pyproject.toml uv.lock ./
COPY packages/common packages/common
COPY apps/simulator apps/simulator
RUN --mount=type=secret,id=extra_ca,required=false \
    if [ -s /run/secrets/extra_ca ]; then \
      export PIP_CERT=/run/secrets/extra_ca SSL_CERT_FILE=/run/secrets/extra_ca; \
    fi; \
    pip install --no-cache-dir "uv>=0.8,<0.9" \
    && uv sync --frozen --no-dev --package prognos-simulator --no-editable

FROM python:3.14-slim
RUN useradd --system --uid 10001 --no-create-home sim
COPY --from=build /opt/venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH PYTHONUNBUFFERED=1
USER 10001
EXPOSE 9101
ENTRYPOINT ["prognos-sim"]
