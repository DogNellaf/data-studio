"""
Создание резервных копий.

Представления отвечают только за HTTP, а решение о том, какую копию снимать
и от какой базовой копии отсчитывать изменения, принимается здесь.
"""
import logging
import os

from django.db import transaction
from django.utils.translation import gettext as _

from backuper.utils import (
    differential_db_backup,
    full_db_backup,
    incremental_db_backup,
)
from core.models import Backup, BackupType
from core.utils import check_db_connection

logger = logging.getLogger(__name__)


# Отдельные фразы, а не одна с подстановкой типа: в русском переводе
# название типа пришлось бы склонять.
MISSING_BASE_MESSAGES = {
    BackupType.INCREMENTAL: lambda: _(
        "An incremental backup needs a full backup of the same database first"
    ),
    BackupType.DIFFERENTIAL: lambda: _(
        "A differential backup needs a full backup of the same database first"
    ),
}


class BackupError(Exception):
    """Копию снять нельзя; сообщение показывается пользователю."""


def find_base_backup(user, backup_type, db, host, port):
    """
    Возвращает копию, изменения после которой попадут в новую копию.

    Инкрементальная отсчитывается от последней копии любого типа,
    дифференциальная — от последней полной. Учитываются только копии этого
    же пользователя: чужие копии той же базы ему недоступны для скачивания,
    и цепочка восстановления из них была бы неполной.
    """
    backups = Backup.objects.filter(user=user, db=db, host=host, port=port)
    if backup_type.code == BackupType.DIFFERENTIAL:
        backups = backups.filter(type__code=BackupType.FULL)
    return backups.order_by("created_at").last()


def create_backup(user, *, type, host, port, db, username, password, storage):
    """
    Снимает копию базы и регистрирует её.

    :raises BackupError: база недоступна, нет базовой копии или дамп не удался
    :return: созданная запись ``Backup``
    """
    if backup_type_is_delta(type):
        base = find_base_backup(user, type, db, host, port)
        if base is None:
            raise BackupError(MISSING_BASE_MESSAGES[type.code]())
    else:
        base = None

    if not check_db_connection(host, port, username, password, db):
        raise BackupError(_("Could not connect to the database: check the address and credentials"))

    # Запись создаётся до дампа, чтобы получить id для имени файла; если дамп
    # не удался, транзакция откатывается и в списке не остаётся «пустышек».
    with transaction.atomic():
        backup = Backup.objects.create(
            type=type,
            host=host,
            port=port,
            db=db,
            username=username,
            storage=storage,
            user=user,
        )
        args = (host, port, username, password, db)
        if type.code == BackupType.FULL:
            succeeded = full_db_backup(*args, backup.file_name)
        elif type.code == BackupType.INCREMENTAL:
            succeeded = incremental_db_backup(*args, base.created_at, backup.file_name)
        else:
            succeeded = differential_db_backup(*args, base.created_at, backup.file_name)

        if not succeeded:
            transaction.set_rollback(True)
            _remove_file(backup.file_path)
            raise BackupError(_("The backup failed, see the server log for details"))

        backup.size = _file_size(backup.file_path)
        backup.save(update_fields=["size"])

    logger.info("Пользователь %s создал копию %s", user, backup)
    return backup


def delete_backup(backup):
    """Удаляет запись о копии и её файл; отсутствие файла не ошибка."""
    path = backup.file_path
    backup.delete()
    _remove_file(path)


def backup_type_is_delta(backup_type):
    return backup_type.code in (BackupType.INCREMENTAL, BackupType.DIFFERENTIAL)


def _file_size(path):
    try:
        return os.path.getsize(path)
    except OSError:
        return None


def _remove_file(path):
    if not os.path.exists(path):
        return
    try:
        os.remove(path)
    except OSError as exc:
        logger.warning("Не удалось удалить файл копии %s: %s", path, exc)
