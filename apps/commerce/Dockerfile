FROM python:3.13-slim-bookworm@sha256:a5a7017efc5969e00ae2b91c8eb0631049a569652aa4ffc4501769049ae45c72 AS dependencies
WORKDIR /app
RUN pip install --no-cache-dir uv==0.13.7
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project --no-editable

FROM python:3.13-slim-bookworm@sha256:a5a7017efc5969e00ae2b91c8eb0631049a569652aa4ffc4501769049ae45c72 AS runtime
ARG VCS_REF=unknown
LABEL org.opencontainers.image.title="Cross Studio API/Worker" org.opencontainers.image.revision=$VCS_REF
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/app/src PATH=/app/.venv/bin:$PATH
WORKDIR /app
RUN useradd --uid 10001 --create-home --home-dir /home/visual visual && mkdir -p /app/work && chown visual:visual /app/work
COPY --from=dependencies /app/.venv /app/.venv
COPY src ./src
COPY migrations ./migrations
COPY alembic.ini ./
COPY rules ./rules
COPY contracts ./contracts
COPY scripts ./scripts
COPY ops/recovery-objectives.json ./ops/recovery-objectives.json
COPY third_party ./third_party
USER 10001:10001
EXPOSE 8000
CMD ["uvicorn","visual_platform.api.main:app","--host","0.0.0.0","--port","8000"]
