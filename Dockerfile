FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_NO_CACHE=1 \
    PATH="/opt/ai4sbench/.venv/bin:$PATH"

RUN groupadd --system ai4sbench && useradd --system --gid ai4sbench --home /opt/ai4sbench ai4sbench
WORKDIR /opt/ai4sbench

COPY --from=ghcr.io/astral-sh/uv:0.11.9 /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --extra aws --no-install-project

COPY control_panel ./control_panel
COPY migrations ./migrations
COPY alembic.ini README.md ./
RUN uv sync --frozen --no-dev --extra aws && mkdir -p /var/lib/ai4sbench && chown -R ai4sbench:ai4sbench /var/lib/ai4sbench

USER ai4sbench
EXPOSE 8080
CMD ["uvicorn", "control_panel.main:app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers", "--forwarded-allow-ips", "127.0.0.1"]

