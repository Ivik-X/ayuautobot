FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN useradd --create-home --shell /bin/bash appuser \
    && apt-get update \
    && apt-get install -y --no-install-recommends gosu \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY bot/ bot/
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

COPY data/media/.gitkeep data/media/.gitkeep
COPY data/db_media/.gitkeep data/db_media/.gitkeep
COPY data/tmp/.gitkeep data/tmp/.gitkeep
COPY data/stt_models/.gitkeep data/stt_models/.gitkeep

RUN mkdir -p data/media data/db_media data/tmp data/stt_models && chown -R appuser:appuser /app

# entrypoint выполняется как root: чинит права на смонтированный volume
# ./data, затем сам понижает привилегии до appuser перед запуском бота.
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["python", "-m", "bot.main"]
