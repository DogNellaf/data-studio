#!/bin/sh
# Применяет миграции и создаёт демо-пользователя перед запуском приложения.
set -e

python manage.py migrate --noinput
python manage.py seed_demo

exec "$@"
