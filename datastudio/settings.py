"""
Django settings for the datastudio project.

Every secret and environment-specific value is read from the environment (or
from a ``.env`` file in the project root). See ``.env.example`` for the full
list of supported variables.

For the full list of Django settings and their values, see
https://docs.djangoproject.com/en/5.1/ref/settings/
"""

from pathlib import Path

import environ
from django.core.exceptions import ImproperlyConfigured
from django.utils.crypto import get_random_string

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env()

# Load .env when present. In production the variables usually come from the
# process environment instead, and a missing file is not an error.
env.read_env(BASE_DIR / '.env')


# Security
# https://docs.djangoproject.com/en/5.1/howto/deployment/checklist/

# Defaults to False so that a forgotten environment variable can never expose
# tracebacks and settings in production.
DEBUG = env.bool('DJANGO_DEBUG', default=False)

SECRET_KEY = env.str('DJANGO_SECRET_KEY', default='')
if not SECRET_KEY:
    if not DEBUG:
        raise ImproperlyConfigured(
            'DJANGO_SECRET_KEY is not set. Copy .env.example to .env and fill '
            'it in, or export the variable in the environment.'
        )
    # Ephemeral key so a fresh clone runs straight away in development.
    # It changes on every restart, which invalidates existing sessions.
    SECRET_KEY = get_random_string(
        50, 'abcdefghijklmnopqrstuvwxyz0123456789!@#$%^&*(-_=+)'
    )

ALLOWED_HOSTS = env.list('DJANGO_ALLOWED_HOSTS', default=[])
if DEBUG and not ALLOWED_HOSTS:
    ALLOWED_HOSTS = ['localhost', '127.0.0.1', '[::1]']

SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = 'DENY'
SESSION_COOKIE_HTTPONLY = True

if not DEBUG:
    SECURE_SSL_REDIRECT = env.bool('DJANGO_SECURE_SSL_REDIRECT', default=True)
    SECURE_HSTS_SECONDS = env.int('DJANGO_SECURE_HSTS_SECONDS', default=31536000)
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    # Behind a reverse proxy (nginx, or a PaaS router) TLS is terminated before
    # the request reaches Django, so the scheme has to be taken from the header
    # the proxy sets - otherwise SECURE_SSL_REDIRECT loops forever. Turn this
    # off when Django is exposed directly, as the header is client-supplied.
    if env.bool('DJANGO_USE_X_FORWARDED_PROTO', default=True):
        SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

CSRF_TRUSTED_ORIGINS = env.list('DJANGO_CSRF_TRUSTED_ORIGINS', default=[])


# Application definition

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'core',
    'custom_auth',
    'backuper'
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'datastudio.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'datastudio.wsgi.application'


# Database
# https://docs.djangoproject.com/en/5.1/ref/settings/#databases

DATABASES = {
    'default': env.db_url(
        'DATABASE_URL',
        default='postgres://postgres:postgres@localhost:5432/DataStudio',
    )
}


# Password validation
# https://docs.djangoproject.com/en/5.1/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]


# Internationalization
# https://docs.djangoproject.com/en/5.1/topics/i18n/

LANGUAGE_CODE = 'en-us'

TIME_ZONE = 'UTC'

USE_I18N = True

USE_TZ = True


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/5.1/howto/static-files/

STATIC_URL = 'static/'

STATIC_ROOT = BASE_DIR / 'staticfiles'

# Default primary key field type
# https://docs.djangoproject.com/en/5.1/ref/settings/#default-auto-field

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# Authentication
# Unauthenticated users hitting @login_required views are sent here.
LOGIN_URL = '/auth/login'

# Directory where generated backup files (*.sql) are stored. Kept as a plain
# pathlib.Path so os.path.join / os.makedirs in the backup code keep working.
MEDIA_DIR = Path(env.str('BACKUP_DIR', default=str(BASE_DIR / 'backups')))

# Path to the PostgreSQL ``pg_dump`` executable used for full backups.
# Defaults to resolving it from PATH; set PG_DUMP_PATH when it lives elsewhere
# (on Windows, typically C:\Program Files\PostgreSQL\17\bin\pg_dump.exe).
PG_DUMP_PATH = env.str('PG_DUMP_PATH', default='pg_dump')

# Network timeout (seconds) for connecting to remote PostgreSQL servers.
DB_CONNECT_TIMEOUT = env.int('DB_CONNECT_TIMEOUT', default=5)


# Logging
# Without an explicit configuration the module-level loggers used across the
# project propagate to a root logger that has no handler, so their warnings and
# errors are silently dropped.

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '{asctime} {levelname} {name}: {message}',
            'style': '{',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'verbose',
        },
    },
    'root': {
        'handlers': ['console'],
        'level': env.str('DJANGO_LOG_LEVEL', default='INFO'),
    },
}
