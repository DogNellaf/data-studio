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

# Применить миграции
python manage.py migrate

# Создать учётную запись администратора
python manage.py createsuperuser

# Запустить сервер разработки
python manage.py runserver
```

Приложение будет доступно по адресу `http://127.0.0.1:8000/`.

## Переменные окружения

Значения по умолчанию подходят для локальной разработки; в продакшене их следует переопределить:

| Переменная | Назначение | По умолчанию |
|---|---|---|
| `DJANGO_SECRET_KEY` | Секретный ключ Django | dev-ключ |
| `DJANGO_DEBUG` | Режим отладки | `True` |
| `DJANGO_ALLOWED_HOSTS` | Разрешённые хосты (через запятую) | _(пусто)_ |
| `DB_NAME` | Имя базы данных приложения | `DataStudio` |
| `DB_USER` | Пользователь базы данных | `postgres` |
| `DB_PASSWORD` | Пароль базы данных | `postgres` |
| `DB_HOST` | Хост базы данных | `localhost` |
| `DB_PORT` | Порт базы данных | `5432` |
| `PG_DUMP_PATH` | Путь к исполняемому файлу `pg_dump` | `C:\Program Files\PostgreSQL\17\bin\pg_dump.exe` |
| `DB_CONNECT_TIMEOUT` | Таймаут подключения к удалённой БД | `5` сек |

## Тесты

Тесты используют SQLite в памяти и не требуют работающего PostgreSQL:

```bash
python manage.py test --settings=datastudio.settings_test
```

Отчёт о покрытии:

```bash
coverage run --source="core,custom_auth,backuper,datastudio" manage.py test --settings=datastudio.settings_test
coverage report -m
```

## Структура проекта

```
datastudio/
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
└── requirements.txt
```

## Лицензия

[MIT](LICENSE)
