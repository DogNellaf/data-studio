# DataStudio

Веб-приложение на Django для управления резервными копиями баз данных
PostgreSQL: полное (`pg_dump`), инкрементальное и дифференциальное копирование
с разграничением доступа по пользователям.

## Возможности

- Регистрация, вход, редактирование профиля.
- Создание резервных копий трёх типов (полная / инкрементальная /
  дифференциальная) с проверкой соединения с исходной БД.
- Просмотр, скачивание и удаление собственных копий.
- Справочники типов копий и хранилищ.

## Структура проекта

| Приложение    | Назначение                                              |
|---------------|---------------------------------------------------------|
| `core`        | Модели, представления и страницы управления копиями.    |
| `custom_auth` | Регистрация, вход, профиль, выход.                      |
| `backuper`    | Утилиты создания резервных копий (`backuper/utils.py`). |

## Установка

```bash
pip install -r requirements.txt
```

## Настройка через переменные окружения

Значения по умолчанию подходят для локальной разработки; в продакшене их
следует переопределить:

| Переменная            | Назначение                          | По умолчанию          |
|-----------------------|-------------------------------------|-----------------------|
| `DJANGO_SECRET_KEY`   | Секретный ключ Django               | dev-ключ              |
| `DJANGO_DEBUG`        | Режим отладки                       | `True`                |
| `DJANGO_ALLOWED_HOSTS`| Разрешённые хосты (через запятую)   | пусто                 |
| `DB_NAME` / `DB_USER` / `DB_PASSWORD` / `DB_HOST` / `DB_PORT` | Подключение к БД приложения | `DataStudio` / `postgres` / `postgres` / `localhost` / `5432` |
| `PG_DUMP_PATH`        | Путь к `pg_dump`                    | `C:\Program Files\PostgreSQL\17\bin\pg_dump.exe` |
| `DB_CONNECT_TIMEOUT`  | Таймаут подключения к удалённой БД  | `5` сек               |

## Запуск

```bash
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

## Тесты

Тесты используют SQLite в памяти и не требуют работающего PostgreSQL:

```bash
python manage.py test --settings=datastudio.settings_test
```

Покрытие:

```bash
coverage run --source="core,custom_auth,backuper,datastudio" manage.py test --settings=datastudio.settings_test
coverage report -m
```
