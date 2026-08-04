# DataStudio

> 🇬🇧 English | [🇷🇺 Русский](README.ru.md)

A Django web application for managing PostgreSQL database backups: full (`pg_dump`), incremental, and differential backups with per-user access control.

## Features

- User registration, login, and profile editing
- Three backup types (full / incremental / differential) with source database connection validation
- View, download, and delete your own backups
- Reference directories for backup types and storage locations

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python 3, Django |
| Application database | PostgreSQL |
| Backup tool | `pg_dump` (PostgreSQL 17) |

## Requirements

- Python 3.10+
- pip
- PostgreSQL (for the application database and backups)

## Installation

```bash
# Clone the repository
git clone <repository-url>
cd datastudio

# Create and activate a virtual environment
python -m venv .venv
source .venv/bin/activate      # Linux / macOS
.venv\Scripts\activate         # Windows

# Install dependencies
pip install -r requirements.txt

# Create the local configuration and fill in DATABASE_URL
cp .env.example .env

# Apply migrations
python manage.py migrate

# Create an admin account
python manage.py createsuperuser

# Run the development server
python manage.py runserver
```

The application will be available at `http://127.0.0.1:8000/`.

## Configuration

Settings are read from the environment, or from a `.env` file in the project root.
Copy [`.env.example`](.env.example) to `.env` to get started — `.env` is git-ignored and
must never be committed.

| Variable | Description | Default |
|---|---|---|
| `DJANGO_SECRET_KEY` | Django secret key. Required in production; in debug mode an ephemeral key is generated at startup | _(none)_ |
| `DJANGO_DEBUG` | Enable debug mode. Never enable in production | `False` |
| `DJANGO_ALLOWED_HOSTS` | Comma-separated list of allowed hosts. Required when debug is off | `localhost,127.0.0.1` in debug mode |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | Comma-separated origins trusted for CSRF | _(empty)_ |
| `DJANGO_LOG_LEVEL` | Root logger level | `INFO` |
| `DATABASE_URL` | Application database DSN | `postgres://postgres:postgres@localhost:5432/DataStudio` |
| `BACKUP_DIR` | Directory where generated `*.sql` backups are written | `./backups` |
| `PG_DUMP_PATH` | Path to the `pg_dump` executable | `pg_dump`, resolved from `PATH` |
| `DB_CONNECT_TIMEOUT` | Remote database connection timeout | `5` s |

The application refuses to start in production without `DJANGO_SECRET_KEY`, so a
misconfigured deployment fails loudly instead of running on a known key.

### Production hardening

These apply only when `DJANGO_DEBUG=False`; HSTS, the HTTPS redirect and secure
cookies are enabled by default.

| Variable | Description | Default |
|---|---|---|
| `DJANGO_SECURE_SSL_REDIRECT` | Redirect HTTP requests to HTTPS | `True` |
| `DJANGO_SECURE_HSTS_SECONDS` | `Strict-Transport-Security` max-age | `31536000` |
| `DJANGO_USE_X_FORWARDED_PROTO` | Trust `X-Forwarded-Proto` from the reverse proxy. Set to `False` when Django is exposed directly | `True` |

Verify a production configuration with:

```bash
python manage.py check --deploy
```

## Running Tests

Tests use an in-memory SQLite database and do not require a running PostgreSQL
instance or a `.env` file:

```bash
python manage.py test --settings=datastudio.settings_test
```

Coverage report (needs the development dependencies):

```bash
pip install -r requirements-dev.txt
coverage run --source="core,custom_auth,backuper,datastudio" manage.py test --settings=datastudio.settings_test
coverage report -m
```

Audit dependencies for known vulnerabilities:

```bash
pip-audit -r requirements.txt
```

## Project Structure

```
datastudio/
├── .env.example         # Documented template for the local .env
├── datastudio/          # Django project settings and root URL conf
├── core/                # Backup management: models, views, and pages
│   ├── migrations/
│   ├── templates/
│   ├── models.py
│   ├── views.py
│   └── admin.py
├── custom_auth/         # Registration, login, profile, logout
│   ├── templates/
│   ├── views.py
│   └── forms.py
├── backuper/            # Backup creation utilities
│   └── utils.py
├── manage.py
├── requirements.txt     # Runtime dependencies
└── requirements-dev.txt # Testing and auditing tools
```

## License

[MIT](LICENSE)
