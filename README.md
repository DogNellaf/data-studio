# DataStudio

> 🇬🇧 English | [🇷🇺 Русский](README.ru.md) | [🇫🇷 Français](README.fr.md) | [🇩🇪 Deutsch](README.de.md)

[![CI](https://github.com/DogNellaf/data-studio/actions/workflows/ci.yml/badge.svg)](https://github.com/DogNellaf/data-studio/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.10%E2%80%933.12-3776AB)
![Django](https://img.shields.io/badge/django-5.2-092E20)
![PostgreSQL](https://img.shields.io/badge/postgresql-16%20%7C%2017-4169E1)
![License](https://img.shields.io/badge/license-MIT-green)

A web app for backing up PostgreSQL databases. It takes full backups with
`pg_dump` and incremental and differential backups on top of them, including
deleted rows. Backups run in a background worker and are stored on local disk
or in S3-compatible object storage. Each user sees only their own backups. The
UI is in English by default and also available in Russian, French and German.

![Backups list](docs/screenshots/en/backups.png)

## Quick start

```bash
docker compose up --build
```

Open <http://localhost:8000> and sign in as **demo / demo12345**. The compose
stack runs the app, the backup worker, a demo shop database and MinIO as S3
storage. In the "new backup" form enter host `demo-db`, port `5432`, database
`shop`, user and password `shop`, and pick local disk or MinIO. Files in MinIO
are visible in its console at <http://localhost:9001> (minio / minio-secret).

To see deltas in action, change some data, then take an incremental backup:

```bash
psql postgres://shop:shop@localhost:5433/shop \
  -c "UPDATE products SET price = price * 1.1 WHERE id <= 20" \
  -c "DELETE FROM order_items WHERE order_id = 1"
```

## Case study

### Problem

A team needs to back up several PostgreSQL databases without SSH access to
the server and without running `pg_dump` by hand. Full dumps of a large
database are expensive, so between them they want small backups that hold
only the changes, and a dump must never hold up the web app.

### Solution

| Type | Contents | Needed to restore |
|---|---|---|
| **Full** | Schema and all data (`pg_dump`) | This backup alone |
| **Incremental** | Changes since the last backup of any type | The full backup and every incremental since |
| **Differential** | Changes since the last full backup | The full backup and the latest differential |

Every backup also saves a **state file**: a hash of every row of every table,
keyed by primary key. A delta compares the current database with the state of
its base backup, so it needs no `updated_at` columns and sees everything: new
and changed rows become upserts, missing rows become `DELETE`s. The result is a
single-transaction SQL file applied with plain `psql -f` on top of the restored
base:

```sql
BEGIN;
ALTER TABLE "public"."products" DISABLE TRIGGER USER;
INSERT INTO "public"."products" ("id", "title", "price", "stock", "updated_at")
VALUES ('1', 'Товар №1', '5847.18', '103', '2026-09-30 20:33:18.241939+00')
ON CONFLICT ("id") DO UPDATE SET "title" = EXCLUDED."title", ...;
DELETE FROM "public"."order_items" WHERE ("order_id", "product_id") IN (('1', '2'), ('1', '15'));
ALTER TABLE "public"."products" ENABLE TRIGGER USER;
SELECT pg_catalog.setval('public.customers_id_seq', 501, true);
COMMIT;
```

### Engineering highlights

- **Restores are tested end to end.** Integration tests run against a real
  PostgreSQL: full backup, updates, inserts, cascading deletes, a table without
  a primary key, another schema, sequence changes, then a restore into an empty
  database compared with the source. Mutating the engine (no deletes, no
  sequences, `public` only) makes them fail.
- **Every change is captured.** Row hashes instead of timestamps: deleted rows,
  tables without `updated_at` and all schemas are included. Tables without a
  primary key are compared as multisets of row hashes, so a delta inserts and
  deletes exactly the rows that changed, duplicates included.
- **Constant memory.** State files are sorted streams, and a delta merges them
  with a sorted scan of the live table, like a merge join. Only one batch of
  keys is ever held in memory, whatever the table size; pending deletes wait in
  a temporary file.
- **One consistent snapshot.** The full backup exports the transaction
  snapshot to `pg_dump --snapshot`, so the dump and the state file describe
  the same moment even while the database is being written to. Deltas are read
  in a single `REPEATABLE READ READ ONLY` transaction.
- **Correct ordering.** Upserts go parents-first and deletes children-first,
  from a topological sort of `pg_constraint`. Sequences are set to their current
  values, so new rows after a restore do not collide with restored ids.
- **Values are restored verbatim.** PostgreSQL does the escaping
  (`quote_nullable()`), and user triggers such as "touch `updated_at`" are
  disabled while rows are written.
- **Schema changes never block a backup.** If a table or column appeared or
  vanished since the base backup, or the base backup is gone, the worker takes
  a full backup instead and the list says why, rather than producing a delta
  that could not be restored.
- **Background jobs without extra infrastructure.** Backups are a queue in the
  application database; `backup_worker` claims jobs with
  `SELECT … FOR UPDATE SKIP LOCKED`, so several workers can run side by side.
  Jobs of a crashed worker are marked as interrupted; a stopping worker
  finishes its current dump first.
- **Pluggable storage.** Files go through Django's storage API: local disk or
  any S3-compatible service (AWS S3, MinIO, …). A backup chain never spans two
  storages, so each one can be restored on its own.

### Security

- Source database passwords are **never stored in plain text**. While a job
  waits in the queue the password is encrypted (Fernet, with a key derived from
  `BACKUP_CREDENTIALS_KEY`), and it is erased as soon as the job finishes. It
  reaches `pg_dump` through the environment, not the command line.
- The connection is checked before a job is queued, so a wrong password is
  reported in the form rather than as a failed job later.
- Users can only see, download and delete their own backups; anything else is
  a 404. Base backups for deltas are also looked up only among the user's own.
- Every secret comes from the environment. The app refuses to start in
  production without `DJANGO_SECRET_KEY`, and `check --deploy` passes with no
  warnings (HSTS, HTTPS redirect, secure cookies).
- Logout is POST-only, registration validates password strength, and the
  post-login redirect is protected against open redirects.

### Localization

- The whole UI is translated with Django's gettext: English source strings,
  with Russian, French and German catalogs in `locale/`, including plural
  forms ("2 databases", "5 баз данных", "2 bases de données").
- English is the default for everyone. The browser's `Accept-Language` is
  deliberately ignored; the EN / RU / FR / DE switcher stores the choice in a
  cookie, and the language is scoped to the request so it cannot leak into the
  next one.
- Job errors are stored as codes and translated when shown, since the worker
  does not know which language the user reads.
- CI checks that the compiled `.mo` catalogs match the `.po` sources.

### Architecture

```mermaid
flowchart LR
    U[Browser] -->|HTTP| V[Django views]
    V -->|check connection,<br/>enqueue| Q[(App database<br/>job queue)]
    W[backup_worker] -->|SKIP LOCKED| Q
    W --> B[backuper<br/>pg_dump + row hashes]
    B -->|REPEATABLE READ| SRC[(Source<br/>PostgreSQL)]
    W -->|Django storage API| ST[/Local disk or S3/]
    V -->|download| ST
```

| Module | Responsibility |
|---|---|
| `core/views.py` | Thin views: HTTP, forms, flash messages |
| `core/services.py` | Queue and jobs: enqueue, claim, run, pick the base backup, erase the password |
| `core/management/commands/backup_worker.py` | The worker process |
| `backuper/utils.py` | Full backups via `pg_dump --snapshot`, state files, delta export |
| `core/backends.py`, `core/crypto.py` | Storage backends and encryption of queued passwords |
| `custom_auth/` | Login, registration, profile built on Django's auth forms |

### What the overhaul changed

The project started as a coursework prototype. Getting it to a working state
involved:

- rebuilding delta backups: they used to miss deleted rows, tables without
  `updated_at` and non-`public` schemas, and their files could not be restored
  on top of a full backup;
- moving backups out of the web request into a worker, and files from one
  directory into pluggable storage with S3 support;
- dropping plain-text storage of third-party database passwords;
- decoupling the backup algorithm from translatable type names, with data
  migrations for existing rows;
- moving from manual `request.POST` parsing to Django forms plus a service
  layer;
- rebuilding the UI: hand-written CSS instead of a Tailwind CDN build, with
  dark mode, a responsive layout, job statuses, empty states and clear errors;
- translating the interface into Russian, French and German;
- adding Docker, CI (lint, unit and integration tests, image build, an
  end-to-end run of the whole stack) and demo data.

## Screenshots

| New backup | Connection error |
|---|---|
| ![Create form](docs/screenshots/en/create.png) | ![Error](docs/screenshots/en/create-error.png) |

| Storages | Deleting a base backup |
|---|---|
| ![Storages](docs/screenshots/en/storages.png) | ![Delete](docs/screenshots/en/remove.png) |

| Dark mode | Mobile |
|---|---|
| ![Dark mode](docs/screenshots/en/backups-dark.png) | ![Mobile](docs/screenshots/en/backups-mobile.png) |

| Sign in | Backup types |
|---|---|
| ![Sign in](docs/screenshots/en/login.png) | ![Backup types](docs/screenshots/en/backup-types.png) |

## Running without Docker

You need Python 3.10+, PostgreSQL, and a `pg_dump` at least as new as the
servers you back up.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # fill in DATABASE_URL
python manage.py migrate        # seeds backup types and a default storage
python manage.py createsuperuser
python manage.py runserver      # the web app
python manage.py backup_worker  # in a second terminal: takes queued backups
```

For a quick try without a worker, set `BACKUP_RUN_INLINE=True` and backups
are taken inside the request.

## Configuration

Settings come from environment variables or a `.env` file. See
[`.env.example`](.env.example) for the full, commented list.

| Variable | Purpose | Default |
|---|---|---|
| `DJANGO_SECRET_KEY` | Secret key; required when `DEBUG=False` | a local key in `.dev-secret-key` in debug |
| `DJANGO_DEBUG` | Debug mode | `False` |
| `DJANGO_ALLOWED_HOSTS` | Comma-separated allowed hosts | `localhost` in debug |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | Trusted CSRF origins | — |
| `DJANGO_TIME_ZONE` | Time zone for displayed dates | `UTC` |
| `DATABASE_URL` | Application database (also holds the job queue) | `postgres://…/DataStudio` |
| `BACKUP_DIR` | Directory of the local storage | `./backups` |
| `BACKUP_S3_BUCKET` | Enables the S3 storage | — |
| `BACKUP_S3_PREFIX`, `BACKUP_S3_ENDPOINT_URL`, `BACKUP_S3_REGION`, `BACKUP_S3_ACCESS_KEY`, `BACKUP_S3_SECRET_KEY`, `BACKUP_S3_ADDRESSING_STYLE` | S3 location and credentials; the endpoint and `path` style are for MinIO and other S3-compatible services | `datastudio` prefix, AWS defaults |
| `BACKUP_CREDENTIALS_KEY` | Key for queued passwords; must be the same for the app and the workers | derived from `DJANGO_SECRET_KEY` |
| `BACKUP_WORKER_POLL_INTERVAL` | How often an idle worker checks the queue, s | `2` |
| `BACKUP_RUN_INLINE` | Take backups in the request instead of queueing | `False` |
| `PG_DUMP_PATH` | `pg_dump` executable | resolved from `PATH` |
| `PG_DUMP_TIMEOUT` | Max duration of one `pg_dump` run, s | `600` |
| `DB_CONNECT_TIMEOUT` | Source database connect timeout, s | `5` |
| `DEMO_USERNAME` / `DEMO_PASSWORD` | Demo account shown on the login page | — |
| `DJANGO_SECURE_SSL_REDIRECT`, `DJANGO_SECURE_HSTS_SECONDS`, `DJANGO_SECURE_COOKIES`, `DJANGO_USE_X_FORWARDED_PROTO` | Production HTTPS hardening | enabled |

`python manage.py seed_demo` creates the demo user and, when S3 is configured,
registers the S3 storage and creates the bucket if it does not exist.

## Tests

```bash
pip install -r requirements-dev.txt
ruff check .
python manage.py test --settings=datastudio.settings_test

# including integration tests against a real PostgreSQL:
INTEGRATION_DATABASE_URL=postgres://postgres:postgres@localhost:5432/postgres \
  coverage run manage.py test --settings=datastudio.settings_test && coverage report
```

There are 131 tests with 97% coverage; S3 is tested against moto. CI also
starts PostgreSQL 16 for the integration tests and, after building the image,
brings up the whole compose stack and takes full and incremental backups
through the worker into both local disk and MinIO, including a schema change
that turns a delta into a full backup
([`docker/smoke_test.py`](docker/smoke_test.py)).

## Project structure

```
├── backuper/            # Taking backups: pg_dump, state files, deltas
├── core/                # Backups: models, jobs, storage, views, templates, CSS
├── custom_auth/         # Login, registration, profile
├── datastudio/          # Settings and root URLconf
├── locale/              # Russian, French and German translations (gettext)
├── docker/              # Entrypoint, demo database seed, stack smoke test
├── docs/screenshots/
├── Dockerfile
├── docker-compose.yml
└── .github/workflows/ci.yml
```

## License

[MIT](LICENSE)
