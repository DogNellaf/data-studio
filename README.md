# DataStudio

> 🇬🇧 English | [🇷🇺 Русский](README.ru.md)

[![CI](https://github.com/DogNellaf/data-studio/actions/workflows/ci.yml/badge.svg)](https://github.com/DogNellaf/data-studio/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.10%E2%80%933.12-3776AB)
![Django](https://img.shields.io/badge/django-5.2-092E20)
![PostgreSQL](https://img.shields.io/badge/postgresql-16%20%7C%2017-4169E1)
![License](https://img.shields.io/badge/license-MIT-green)

A web app for backing up PostgreSQL databases. It takes full backups with
`pg_dump` and incremental and differential backups on top of them. You can
download and delete backups, and each user sees only their own. The UI is in
Russian.

![Backups list](docs/screenshots/backups.png)

## Quick start

```bash
docker compose up --build
```

Open <http://localhost:8000> and sign in as **demo / demo12345**. The compose
stack includes a demo shop database. In the "new backup" form enter host
`demo-db`, port `5432`, database `shop`, user and password `shop`.

To see deltas in action, change some data, then take an incremental backup:

```bash
psql postgres://shop:shop@localhost:5433/shop \
  -c "UPDATE products SET price = price * 1.1 WHERE id <= 20"
```

## Case study

### Problem

A team needs to back up several PostgreSQL databases without SSH access to
the server and without running `pg_dump` by hand. Full dumps of a large
database are expensive, so between them they want small backups that hold
only the changes.

### Solution

| Type | Contents | Needed to restore |
|---|---|---|
| **Full** | Schema and all data (`pg_dump`) | This backup alone |
| **Incremental** | Rows changed since the last backup of any type | The full backup and every incremental since |
| **Differential** | Rows changed since the last full backup | The full backup and the latest differential |

A delta backup is a single-transaction SQL file of upserts. You apply it with
plain `psql -f` on top of a database restored from the full backup:

```sql
BEGIN;
ALTER TABLE "products" DISABLE TRIGGER USER;
INSERT INTO "products" ("id", "title", "price", "stock", "updated_at")
VALUES ('1', 'Товар №1', '8672.42', '92', '2026-09-30 11:37:21.910003+00')
ON CONFLICT ("id") DO UPDATE SET "title" = EXCLUDED."title", ...;
ALTER TABLE "products" ENABLE TRIGGER USER;
COMMIT;
```

### Engineering highlights

- **Restores are tested end to end.** Integration tests run against a real
  PostgreSQL. They take a full backup, change data, take deltas, restore
  everything into an empty database and compare it with the source.
- **Upserts by primary key.** A changed row already exists after the full
  restore, so it is updated instead of failing on a unique violation.
- **FK-aware ordering.** Tables are sorted topologically from
  `pg_constraint`, so a new order is inserted after its new customer.
- **PostgreSQL does the escaping.** `quote_nullable()` runs server-side, so
  JSON, arrays, `bytea`, quotes and NULLs round-trip without formatting values
  in Python.
- **One consistent snapshot.** Deltas are read in a `REPEATABLE READ READ ONLY`
  transaction.
- **Values are restored verbatim.** User triggers, such as "touch
  `updated_at`", are disabled while rows are inserted, so they cannot
  overwrite the backed-up values.

### Security

- Source database passwords are **never stored**. They exist only for the
  duration of the backup and reach `pg_dump` through the environment, not
  the command line.
- Users can only see, download and delete their own backups; anything else is
  a 404. Base backups for deltas are also looked up only among the user's own.
- Every secret comes from the environment. The app refuses to start in
  production without `DJANGO_SECRET_KEY`, and `check --deploy` passes with no
  warnings (HSTS, HTTPS redirect, secure cookies).
- Logout is POST-only, registration validates password strength, and the
  post-login redirect is protected against open redirects.

### Architecture

```mermaid
flowchart LR
    U[Browser] -->|HTTP| V[Django views<br/>core, custom_auth]
    V --> F[Forms<br/>input validation]
    V --> S[core.services<br/>base backup lookup,<br/>transaction]
    S --> B[backuper.utils]
    B -->|pg_dump| SRC[(Source<br/>PostgreSQL)]
    B -->|psycopg2, REPEATABLE READ| SRC
    B --> FS[/BACKUP_DIR/*.sql/]
    S --> DB[(App database<br/>PostgreSQL)]
```

| Module | Responsibility |
|---|---|
| `core/views.py` | Thin views: HTTP, forms, flash messages |
| `core/services.py` | Business logic: which backup to take, which base to diff against, rollback on failure |
| `backuper/utils.py` | Full backups via `pg_dump`, delta export as upsert SQL |
| `custom_auth/` | Login, registration, profile built on Django's auth forms |
| `core/migrations/0007_*` | Data migration: stable type codes instead of display names, stored passwords dropped |

### What the overhaul changed

The project started as a coursework prototype. Getting it to a working state
involved:

- fixing bugs in the delta backups: the column lookup ignored the schema, and
  plain `INSERT` files could not be restored on top of a full backup;
- dropping plain-text storage of third-party database passwords;
- decoupling the backup algorithm from translatable type names (renaming a
  type in the admin used to break backups), with a data migration for
  existing rows;
- moving from manual `request.POST` parsing to Django forms plus a service
  layer;
- rebuilding the UI: hand-written CSS instead of a Tailwind CDN build, with
  dark mode, a responsive layout, empty states and clear errors;
- adding Docker, CI (lint, unit and integration tests, image build) and demo
  data.

## Screenshots

| New backup | Connection error |
|---|---|
| ![Create form](docs/screenshots/create.png) | ![Error](docs/screenshots/create-error.png) |

| Dark mode | Mobile |
|---|---|
| ![Dark mode](docs/screenshots/backups-dark.png) | ![Mobile](docs/screenshots/backups-mobile.png) |

| Sign in | Backup types |
|---|---|
| ![Sign in](docs/screenshots/login.png) | ![Backup types](docs/screenshots/backup-types.png) |

## Running without Docker

You need Python 3.10+, PostgreSQL, and a `pg_dump` at least as new as the
servers you back up.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # fill in DATABASE_URL
python manage.py migrate        # seeds backup types and a default storage
python manage.py createsuperuser
python manage.py runserver
```

## Configuration

Settings come from environment variables or a `.env` file. See
[`.env.example`](.env.example) for the full, commented list.

| Variable | Purpose | Default |
|---|---|---|
| `DJANGO_SECRET_KEY` | Secret key; required when `DEBUG=False` | — |
| `DJANGO_DEBUG` | Debug mode | `False` |
| `DJANGO_ALLOWED_HOSTS` | Comma-separated allowed hosts | `localhost` in debug |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | Trusted CSRF origins | — |
| `DJANGO_TIME_ZONE` | Time zone for displayed dates | `UTC` |
| `DATABASE_URL` | Application database | `postgres://…/DataStudio` |
| `BACKUP_DIR` | Where backup files are written | `./backups` |
| `PG_DUMP_PATH` | `pg_dump` executable | resolved from `PATH` |
| `PG_DUMP_TIMEOUT` | Max duration of one `pg_dump` run, s | `600` |
| `DB_CONNECT_TIMEOUT` | Source database connect timeout, s | `5` |
| `DEMO_USERNAME` / `DEMO_PASSWORD` | Demo account shown on the login page | — |
| `DJANGO_SECURE_SSL_REDIRECT`, `DJANGO_SECURE_HSTS_SECONDS`, `DJANGO_SECURE_COOKIES`, `DJANGO_USE_X_FORWARDED_PROTO` | Production HTTPS hardening | enabled |

## Tests

```bash
pip install -r requirements-dev.txt
ruff check .
python manage.py test --settings=datastudio.settings_test

# including integration tests against a real PostgreSQL:
INTEGRATION_DATABASE_URL=postgres://postgres:postgres@localhost:5432/postgres \
  coverage run manage.py test --settings=datastudio.settings_test && coverage report
```

There are 90 tests with 98% coverage. CI starts PostgreSQL 16 and runs the
real `pg_dump` and `psql`.

## Limitations

These are known limits of the current implementation:

- Deltas do not capture **deleted** rows; that needs logical replication or a
  deletion log.
- Only tables in the `public` schema that have `updated_at` or `created_at`
  are included in deltas; other tables live in full backups only.
- Backups run synchronously in the request. Databases in the tens of
  gigabytes would call for a task queue (Celery/RQ) and S3 storage.
- The only storage backend is the local `BACKUP_DIR`. The storage catalog
  model leaves room for more backends later.

## Project structure

```
├── backuper/            # Taking backups: pg_dump and delta export
├── core/                # Backups: models, forms, services, views, templates, CSS
├── custom_auth/         # Login, registration, profile
├── datastudio/          # Settings and root URLconf
├── docker/              # Entrypoint and demo database seed
├── docs/screenshots/
├── Dockerfile
├── docker-compose.yml
└── .github/workflows/ci.yml
```

## License

[MIT](LICENSE)
