"""
Настройки для запуска тестов.

Используют быструю SQLite-базу в памяти, чтобы тесты не зависели от
работающего сервера PostgreSQL.

Запуск:
    python manage.py test --settings=datastudio.settings_test
"""

import os

# Значения задаются до импорта основных настроек: те требуют DJANGO_SECRET_KEY
# и падают без него, а тесты не должны зависеть от окружения разработчика
# или наличия файла .env. setdefault не затирает уже заданные переменные.
os.environ.setdefault("DJANGO_SECRET_KEY", "test-secret-key-not-for-production")
os.environ.setdefault("DJANGO_DEBUG", "True")

from .settings import *  # noqa: E402,F401,F403

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

# Быстрый и предсказуемый хэшер паролей для тестов.
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.MD5PasswordHasher",
]

# Не обращаемся к настоящему pg_dump из тестов (он всегда мокается),
# но фиксируем безопасное значение по умолчанию.
PG_DUMP_PATH = "pg_dump"

# Тесты намеренно проверяют ветки с ошибками, и логгеры печатали бы их
# трассировки в вывод прогона. Оставляем только критические сообщения.
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"null": {"class": "logging.NullHandler"}},
    "root": {"handlers": ["null"], "level": "CRITICAL"},
}
