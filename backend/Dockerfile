FROM node:22-alpine AS frontend

WORKDIR /build/dashboard-frontend/frontend
COPY dashboard-frontend/frontend/package.json dashboard-frontend/frontend/package-lock.json ./
RUN npm ci
COPY dashboard-frontend/frontend ./
RUN npm run build

FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_NO_CACHE=1 \
    PATH="/opt/ai4sbench/.venv/bin:$PATH"

RUN groupadd --system ai4sbench && useradd --system --gid ai4sbench --home /opt/ai4sbench ai4sbench
RUN apt-get update && apt-get install -y --no-install-recommends git openssh-client \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /opt/ai4sbench

COPY --from=ghcr.io/astral-sh/uv:0.11.9 /uv /usr/local/bin/uv
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --extra aws --extra harbor-ec2 --no-install-project

COPY backend/src ./src
COPY --from=frontend /build/backend/src/static ./src/static
COPY backend/migrations ./migrations
COPY backend/alembic.ini ./
RUN uv sync --frozen --no-dev --extra aws --extra harbor-ec2 \
    && mkdir -p /var/lib/ai4sbench \
    && chown -R ai4sbench:ai4sbench /var/lib/ai4sbench

USER ai4sbench
EXPOSE 8080
CMD ["uvicorn", "src.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers", "--forwarded-allow-ips", "127.0.0.1"]
