FROM python:3.12-slim-trixie AS build
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends g++ && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.11.33 /uv /usr/local/bin/uv
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --locked --no-dev --no-install-project
ARG NORTHSTAR_GIT_REVISION
ARG NORTHSTAR_DEVELOPMENT_BUILD=0
COPY backend/scripts/build_identity.py ./scripts/build_identity.py
COPY backend/README.md ./
COPY backend/src ./src
RUN NORTHSTAR_GIT_REVISION="$NORTHSTAR_GIT_REVISION" NORTHSTAR_DEVELOPMENT_BUILD="$NORTHSTAR_DEVELOPMENT_BUILD" uv sync --locked --no-dev --no-editable

FROM python:3.12-slim-trixie AS runtime
RUN apt-get update && apt-get install -y --no-install-recommends postgresql-client-17 libstdc++6 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 18081 19080 19082 19084
CMD ["uvicorn", "northstar_quant.apps.live:application", "--factory", "--host", "0.0.0.0", "--port", "19080"]
