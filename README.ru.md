# DataStudio

> [🇬🇧 English](README.md) | 🇷🇺 Русский

Веб-приложение на Django для управления резервными копиями баз данных PostgreSQL: полное (`pg_dump`), инкрементальное и дифференциальное копирование с разграничением доступа по пользователям.

## Возможности

- Регистрация, вход и редактирование профиля
- Создание резервных копий трёх типов (полная / инкрементальная / дифференциальная) с проверкой соединения с исходной БД
- Просмотр, скачивание и удаление собственных копий
- Справочники типов копий и хранилищ

## Стек технологий

| Слой | Технология |
|---|---|
| Бэкенд | Python 3, Django |
| База данных приложения | PostgreSQL |
| Утилита резервного копирования | `pg_dump` (PostgreSQL 17) |

## Требования

- Python 3.10+
- pip
- PostgreSQL (для базы данных приложения и резервных копий)

## Установка

```bash
# Клонировать репозиторий
git clone <repository-url>
cd datastudio

# Создать и активировать виртуальное окружение
python -m venv .venv
source .venv/bin/activate      # Linux / macOS
.venv\Scripts\activate         # Windows

# Установить зависимости
pip install -r requirements.txt

# Создать локальную конфигурацию и заполнить DATABASE_URL
cp .env.example .env

# Применить миграции
python manage.py migrate

# Создать учётную запись администратора
python manage.py createsuperuser

# Запустить сервер разработки
python manage.py runserver
```

Приложение будет доступно по адресу `http://127.0.0.1:8000/`.

## Конфигурация

Настройки читаются из переменных окружения или из файла `.env` в корне проекта.
Скопируйте [`.env.example`](.env.example) в `.env` — файл `.env` добавлен в
`.gitignore` и не должен попадать в репозиторий.

| Переменная | Назначение | По умолчанию |
|---|---|---|
| `DJANGO_SECRET_KEY` | Секретный ключ Django. Обязателен в продакшене; в режиме отладки генерируется одноразовый ключ при запуске | _(нет)_ |
| `DJANGO_DEBUG` | Режим отладки. Никогда не включайте в продакшене | `False` |
| `DJANGO_ALLOWED_HOSTS` | Разрешённые хосты (через запятую). Обязателен, если отладка выключена | `localhost,127.0.0.1` в режиме отладки |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | Доверенные источники для CSRF (через запятую) | _(пусто)_ |
| `DJANGO_LOG_LEVEL` | Уровень корневого логгера | `INFO` |
| `DATABASE_URL` | DSN базы данных приложения | `postgres://postgres:postgres@localhost:5432/DataStudio` |
| `BACKUP_DIR` | Каталог для сгенерированных копий `*.sql` | `./backups` |
| `PG_DUMP_PATH` | Путь к исполняемому файлу `pg_dump` | `pg_dump`, ищется в `PATH` |
| `DB_CONNECT_TIMEOUT` | Таймаут подключения к удалённой БД | `5` сек |

Без `DJANGO_SECRET_KEY` приложение в продакшене не стартует — ошибка конфигурации
проявляется сразу, а не оборачивается работой на общеизвестном ключе.

### Настройки безопасности для продакшена

Применяются только при `DJANGO_DEBUG=False`; HSTS, редирект на HTTPS и
secure-cookies включены по умолчанию.

| Переменная | Назначение | По умолчанию |
|---|---|---|
| `DJANGO_SECURE_SSL_REDIRECT` | Перенаправлять HTTP на HTTPS | `True` |
| `DJANGO_SECURE_HSTS_SECONDS` | Значение max-age для `Strict-Transport-Security` | `31536000` |
| `DJANGO_USE_X_FORWARDED_PROTO` | Доверять заголовку `X-Forwarded-Proto` от обратного прокси. Выключите, если Django открыт напрямую | `True` |

Проверить конфигурацию продакшена:

```bash
python manage.py check --deploy
```

## Тесты

Тесты используют SQLite в памяти и не требуют ни работающего PostgreSQL, ни файла `.env`:

```bash
python manage.py test --settings=datastudio.settings_test
```

Отчёт о покрытии (нужны зависимости для разработки):

```bash
pip install -r requirements-dev.txt
coverage run --source="core,custom_auth,backuper,datastudio" manage.py test --settings=datastudio.settings_test
coverage report -m
```

Проверка зависимостей на известные уязвимости:

```bash
pip-audit -r requirements.txt
```

## Структура проекта

```
datastudio/
├── .env.example         # Шаблон локального .env с описанием переменных
├── datastudio/          # Настройки проекта Django и корневая конфигурация URL
├── core/                # Управление резервными копиями: модели, представления, страницы
│   ├── migrations/
│   ├── templates/
│   ├── models.py
│   ├── views.py
│   └── admin.py
├── custom_auth/         # Регистрация, вход, профиль, выход
│   ├── templates/
│   ├── views.py
│   └── forms.py
├── backuper/            # Утилиты создания резервных копий
│   └── utils.py
├── manage.py
├── requirements.txt     # Зависимости приложения
└── requirements-dev.txt # Инструменты тестирования и аудита
```

## Лицензия

[MIT](LICENSE)
