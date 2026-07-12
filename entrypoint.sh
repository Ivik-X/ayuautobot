#!/bin/sh
set -e

# ./data монтируется с хоста поверх /app/data, поэтому права, выставленные
# в Dockerfile на этапе сборки, при старте контейнера перетираются правами
# хостовой директории. Чиним владельца здесь (ещё под root), затем передаём
# управление appuser — так БД и медиа всегда доступны на запись независимо
# от того, кем и как была создана папка ./data на хосте.
mkdir -p /app/data/media /app/data/db_media /app/data/tmp /app/data/stt_models
chown -R appuser:appuser /app/data

exec gosu appuser "$@"
