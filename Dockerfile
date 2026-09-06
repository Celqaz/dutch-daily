FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Europe/Amsterdam \
    DATA_DIR=/app/data \
    OUTPUT_DIR=/app/output

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

RUN mkdir -p /app/data /app/output \
    && useradd --create-home --uid 1000 appuser \
    && chown -R appuser:appuser /app

USER appuser

VOLUME ["/app/data", "/app/output"]

# Default: run as a daily scheduler. Use `--once` (docker compose run) for a test.
CMD ["python", "-m", "app.main"]
