"""
Бэкенды хранения файлов копий.

Бэкенды — это записи ``STORAGES`` с именем ``backups-<имя>``; справочник
хранилищ в базе ссылается на них по ``<имени>``. Так учётные данные и адреса
остаются в окружении, а в базе — только выбор администратора.
"""
from django.conf import settings
from django.core.files.storage import storages
from django.utils.translation import gettext_lazy as _

PREFIX = "backups-"

LABELS = {
    "local": _("Local disk"),
    "s3": _("S3-compatible object storage"),
}


def configured_backends():
    """Имена бэкендов, настроенных в текущем окружении."""
    return [alias[len(PREFIX):] for alias in settings.STORAGES if alias.startswith(PREFIX)]


def backend_choices():
    """Варианты для поля модели: известные и реально настроенные бэкенды."""
    names = sorted(set(LABELS) | set(configured_backends()))
    return [(name, LABELS.get(name, name)) for name in names]


def is_configured(name):
    return name in configured_backends()


def get_backend(name):
    """Django-хранилище для бэкенда; ``KeyError``, если он не настроен."""
    if not is_configured(name):
        raise KeyError(name)
    return storages[PREFIX + name]
