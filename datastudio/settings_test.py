"""
Настройки для запуска тестов.

Используют быструю SQLite-базу в памяти, чтобы тесты не зависели от
работающего сервера PostgreSQL.

Запуск:
    python manage.py test --settings=datastudio.settings_test
"""

from .settings import *  # noqa: F401,F403

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
