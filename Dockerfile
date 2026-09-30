# syntax=docker/dockerfile:1
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# pg_dump должен быть не старше сервера, с которого снимается копия, поэтому
# ставим клиент 17 из официального репозитория PostgreSQL, а не 15 из Debian.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl gnupg \
    && install -d /usr/share/postgresql-common/pgdg \
    && curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc \
        -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc \
    && echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt bookworm-pgdg main" \
        > /etc/apt/sources.list.d/pgdg.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client-17 \
    && apt-get purge -y curl gnupg && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

RUN useradd --create-home --uid 1000 app \
    && mkdir -p /app/backups /app/staticfiles \
    && chown -R app:app /app/backups /app/staticfiles
USER app

# collectstatic не обращается к базе, но settings требуют ключ вне DEBUG.
RUN DJANGO_SECRET_KEY=build-only python manage.py collectstatic --noinput

ENV BACKUP_DIR=/app/backups
VOLUME ["/app/backups"]
EXPOSE 8000

ENTRYPOINT ["./docker/entrypoint.sh"]
CMD ["gunicorn", "datastudio.wsgi", "--bind", "0.0.0.0:8000", "--workers", "3", "--timeout", "660", "--access-logfile", "-"]
