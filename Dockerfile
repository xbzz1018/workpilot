FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    WORKPILOT_WORKSPACE=/data/workspace \
    WORKPILOT_TASK_DB=/data/workspace/workpilot.sqlite

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN python -m pip install .

RUN useradd --create-home --uid 10001 workpilot && mkdir -p /data/workspace && chown -R workpilot:workpilot /data /app
USER workpilot

EXPOSE 8000
CMD ["uvicorn", "workpilot.api:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
