FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN useradd --create-home --shell /bin/bash appuser

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY bot/ bot/
COPY data/media/.gitkeep data/media/.gitkeep
COPY data/db_media/.gitkeep data/db_media/.gitkeep
COPY data/tmp/.gitkeep data/tmp/.gitkeep

RUN mkdir -p data/media data/db_media data/tmp && chown -R appuser:appuser /app

USER appuser

CMD ["python", "-m", "bot.main"]
