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

# Apply migrations
python manage.py migrate

# Create an admin account
python manage.py createsuperuser

# Run the development server
python manage.py runserver
```

The application will be available at `http://127.0.0.1:8000/`.

## Environment Variables

Defaults are suitable for local development; override them in production:

| Variable | Description | Default |
|---|---|---|
| `DJANGO_SECRET_KEY` | Django secret key | insecure dev key |
| `DJANGO_DEBUG` | Enable debug mode | `True` |
| `DJANGO_ALLOWED_HOSTS` | Comma-separated list of allowed hosts | _(empty)_ |
| `DB_NAME` | Application database name | `DataStudio` |
| `DB_USER` | Database user | `postgres` |
| `DB_PASSWORD` | Database password | `postgres` |
| `DB_HOST` | Database host | `localhost` |
| `DB_PORT` | Database port | `5432` |
| `PG_DUMP_PATH` | Path to the `pg_dump` executable | `C:\Program Files\PostgreSQL\17\bin\pg_dump.exe` |
| `DB_CONNECT_TIMEOUT` | Remote database connection timeout | `5` s |

## Running Tests

Tests use an in-memory SQLite database and do not require a running PostgreSQL instance:

```bash
python manage.py test --settings=datastudio.settings_test
```

Coverage report:

```bash
coverage run --source="core,custom_auth,backuper,datastudio" manage.py test --settings=datastudio.settings_test
coverage report -m
```

## Project Structure

```
datastudio/
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
└── requirements.txt
```

## License

[MIT](LICENSE)
