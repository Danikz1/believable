FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml /app/
COPY alembic.ini /app/
COPY src /app/src
COPY alembic /app/alembic
COPY data /app/data
COPY tools /app/tools
COPY believable_minds_final_spec.md /app/
COPY believable_minds_addendum_official_transcripts.md /app/

RUN pip install --upgrade pip \
    && pip install .

CMD ["sh", "-c", "alembic upgrade head && uvicorn src.api.app:app --host 0.0.0.0 --port ${PORT:-8000}"]
