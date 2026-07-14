#!/bin/bash
# Обновление бота на сервере: останавливает контейнер, подтягивает актуальную
# версию из git и поднимает заново. .env и ./data не трогает (не под git).
set -e

cd "$(dirname "$0")"

echo "-> Останавливаю контейнер..."
docker compose down

echo "-> Подтягиваю актуальную версию из git..."
git fetch origin
git reset --hard origin/main

echo "-> Пересобираю и запускаю..."
docker compose up -d --build

echo "-> Готово. Логи:"
docker compose logs -f --tail=50
