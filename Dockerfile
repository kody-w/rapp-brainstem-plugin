FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    COPILOT_SKIP_CLI_DOWNLOAD=0 \
    RAPP_ROOT=/app \
    RAPP_STATE_PATH=/data/copilot-home

WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir . && python -m copilot download-runtime

COPY agents ./agents
COPY soul.md ./soul.md

RUN useradd --create-home --uid 10001 brainstem \
    && mkdir -p /data/copilot-home \
    && chown -R brainstem:brainstem /app /data

USER brainstem
EXPOSE 7071

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:7071/health', timeout=3)"

CMD ["uvicorn", "rapp_brainstem_gateway.app:app", "--host", "0.0.0.0", "--port", "7071", "--proxy-headers"]
