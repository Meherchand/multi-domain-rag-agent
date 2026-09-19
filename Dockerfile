# Single image serving both the API and the UI; the compose file runs one
# container per role by overriding the command.
FROM python:3.12-slim

WORKDIR /app

# curl is needed by the container healthcheck; the compilers are needed to
# build wheels that have no prebuilt slim-image variant.
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc g++ curl \
    && rm -rf /var/lib/apt/lists/*

# Dependencies first, so a source change does not invalidate the layer.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/
COPY data/ ./data/
COPY .chainlit/ ./.chainlit/
COPY api_app.py chainlit_app.py streamlit_app.py mcp_app.py cli.py init.sql chainlit.md ./
COPY examples/ ./examples/

# Run as a non-root user. The app writes nothing outside /tmp, so the
# filesystem can also be mounted read-only in a deployment.
RUN useradd --create-home --uid 10001 appuser && chown -R appuser:appuser /app
USER appuser

ENV PYTHONPATH=/app \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

EXPOSE 8000 8502

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -fsS http://localhost:8000/health || exit 1

CMD ["uvicorn", "api_app:app", "--host", "0.0.0.0", "--port", "8000"]
