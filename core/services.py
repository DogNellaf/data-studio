"""
Резервные копии как задачи.

Представление ставит копию в очередь (``enqueue_backup``) и сразу отвечает.
Отдельный процесс (``python manage.py backup_worker``) забирает задачи по
одной (``claim_next_job``) и выполняет их (``run_backup``): снимает копию во
временный каталог, выгружает файлы в хранилище и записывает результат.
"""
import logging
import os
import tempfile
from datetime import timedelta

from django.conf import settings
from django.core.files import File
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from backuper.utils import BackupFailed, delta_backup, full_backup
from core import crypto
from core.models import Backup, BackupType
from core.utils import check_db_connection

logger = logging.getLogger(__name__)

MISSING_BASE = "missing_base"
STORAGE = "storage"
INTERRUPTED = "interrupted"
INTERNAL = "internal"

# Текст ошибок хранится кодом и переводится при показе: задачу выполняет
# воркер, который не знает, на каком языке смотрит пользователь.
ERROR_MESSAGES = {
    BackupFailed.CONNECTION: gettext_lazy(
        "Could not connect to the database: check the address and credentials"
    ),
    BackupFailed.DUMP: gettext_lazy("The backup failed, see the server log for details"),
    BackupFailed.SCHEMA_CHANGED: gettext_lazy(
        "The database schema changed since the base backup: take a full backup first"
    ),
    MISSING_BASE: gettext_lazy("There is no base backup to compare with: take a full backup first"),
    STORAGE: gettext_lazy("The storage is not available"),
    INTERRUPTED: gettext_lazy("The backup was interrupted, please try again"),
    INTERNAL: gettext_lazy("The backup failed, see the server log for details"),
}

# Отдельные фразы, а не одна с подстановкой типа: в переводах название
# типа пришлось бы склонять.
MISSING_BASE_MESSAGES = {
    BackupType.INCREMENTAL: lambda: _(
        "An incremental backup needs a full backup of the same database first"
    ),
    BackupType.DIFFERENTIAL: lambda: _(
        "A differential backup needs a full backup of the same database first"
    ),
}


class BackupError(Exception):
    """Копию нельзя поставить в очередь; сообщение показывается пользователю."""


def error_message(backup):
    return ERROR_MESSAGES.get(backup.error_code, ERROR_MESSAGES[INTERNAL])


def is_delta(backup_type):
    return backup_type.code in (BackupType.INCREMENTAL, BackupType.DIFFERENTIAL)


def _same_chain(user, db, host, port, storage):
    """Копии, которые могут стоять в одной цепочке восстановления.

    Цепочка не выходит за пределы хранилища: чтобы восстановить копию из S3,
    не должен понадобиться файл с локального диска сервера.
    """
    return Backup.objects.filter(user=user, db=db, host=host, port=port, storage=storage)


def _base_candidates(backups, backup_type):
    if backup_type.code == BackupType.DIFFERENTIAL:
        backups = backups.filter(type__code=BackupType.FULL)
    return backups


def find_base_backup(backup):
    """
    Копия, с состоянием которой сравнивается дельта ``backup``.

    Инкрементальная сравнивается с последней готовой копией любого типа,
    дифференциальная — с последней готовой полной. Учитываются только копии
    того же пользователя в том же хранилище, снятые раньше этой задачи:
    чужие копии той же базы ему недоступны, а цепочка из разных хранилищ не
    восстанавливалась бы из одного места. Копии, снятые до появления снимков
    состояния, базой служить не могут.
    """
    backups = _same_chain(
        backup.user, backup.db, backup.host, backup.port, backup.storage
    ).filter(
        status=Backup.SUCCEEDED, created_at__lt=backup.created_at
    ).exclude(state_name="")
    return _base_candidates(backups, backup.type).order_by("created_at", "id").last()


# --------------------------------------------------------------------------- #
#  Постановка в очередь
# --------------------------------------------------------------------------- #
def enqueue_backup(user, *, type, host, port, db, username, password, storage):
    """
    Проверяет параметры и ставит копию в очередь.

    Подключение проверяется сразу, чтобы опечатка в пароле давала ошибку в
    форме, а не упавшую задачу через минуту.

    :raises BackupError: хранилище недоступно, нет базовой копии, нет соединения
    :return: созданная запись ``Backup``
    """
    if not storage.is_available:
        raise BackupError(ERROR_MESSAGES[STORAGE])

    if is_delta(type):
        possible_bases = _base_candidates(
            _same_chain(user, db, host, port, storage).filter(
                status__in=(Backup.SUCCEEDED, *Backup.ACTIVE_STATUSES)
            ).exclude(status=Backup.SUCCEEDED, state_name=""),
            type,
        )
        if not possible_bases.exists():
            raise BackupError(MISSING_BASE_MESSAGES[type.code]())

    if not check_db_connection(host, port, username, password, db):
        raise BackupError(ERROR_MESSAGES[BackupFailed.CONNECTION])

    backup = Backup.objects.create(
        type=type,
        host=host,
        port=port,
        db=db,
        username=username,
        storage=storage,
        user=user,
        status=Backup.QUEUED,
        secret=crypto.encrypt(password),
    )
    logger.info("Пользователь %s поставил в очередь копию %s", user, backup.pk)

    if settings.BACKUP_RUN_INLINE:
        _mark_running(backup)
        run_backup(backup)
        backup.refresh_from_db()
    return backup


# --------------------------------------------------------------------------- #
#  Выполнение задач
# --------------------------------------------------------------------------- #
def claim_next_job():
    """Забирает самую старую задачу из очереди; ``None``, если очередь пуста.

    ``SKIP LOCKED`` позволяет запустить несколько воркеров: каждый берёт
    свою задачу и не ждёт, пока другой отпустит блокировку.
    """
    with transaction.atomic():
        backup = (
            Backup.objects.select_for_update(skip_locked=True)
            .filter(status=Backup.QUEUED)
            .order_by("created_at", "id")
            .first()
        )
        if backup is None:
            return None
        _mark_running(backup)
    return backup


def _mark_running(backup):
    backup.status = Backup.RUNNING
    backup.started_at = timezone.now()
    backup.save(update_fields=["status", "started_at"])


def fail_stale_jobs():
    """Помечает ошибкой задачи, воркер которых погиб, не закончив работу."""
    deadline = timezone.now() - timedelta(seconds=settings.PG_DUMP_TIMEOUT * 2 + 60)
    return Backup.objects.filter(status=Backup.RUNNING, started_at__lt=deadline).update(
        status=Backup.FAILED,
        error_code=INTERRUPTED,
        secret="",
        finished_at=timezone.now(),
    )


def run_backup(backup):
    """
    Снимает копию для задачи, уже переведённой в статус «выполняется».

    Никогда не бросает исключений: любой исход записывается в задачу, а
    пароль стирается.
    """
    try:
        result = _take_backup(backup)
    except BackupFailed as exc:
        logger.warning("Копия %s не снята: %s", backup.pk, exc)
        _finish(backup, status=Backup.FAILED, error_code=exc.code, error_detail=exc.detail)
    except _StorageUnavailable as exc:
        logger.error("Хранилище копии %s недоступно: %s", backup.pk, exc)
        _finish(backup, status=Backup.FAILED, error_code=STORAGE, error_detail=str(exc))
    except Exception as exc:  # noqa: BLE001 - задача не должна уронить воркер
        logger.exception("Непредвиденная ошибка при снятии копии %s", backup.pk)
        _finish(backup, status=Backup.FAILED, error_code=INTERNAL, error_detail=repr(exc))
    else:
        _finish(backup, status=Backup.SUCCEEDED, **result)


class _StorageUnavailable(Exception):
    pass


def _open_backend(storage):
    try:
        return storage.open_backend()
    except Exception as exc:  # noqa: BLE001 - неверная настройка любого рода
        raise _StorageUnavailable(f"{storage.backend}: {exc}") from exc


def _take_backup(backup):
    backend = _open_backend(backup.storage)
    params = {
        "host": backup.host,
        "port": backup.port,
        "user": backup.username,
        "password": crypto.decrypt(backup.secret),
        "dbname": backup.db,
    }

    with tempfile.TemporaryDirectory(prefix="datastudio-") as workdir:
        sql_path = os.path.join(workdir, "backup.sql")
        state_path = os.path.join(workdir, "backup.state.json.gz")

        base = None
        if backup.type.code == BackupType.FULL:
            full_backup(params, sql_path, state_path)
        else:
            base = find_base_backup(backup)
            if base is None:
                raise BackupFailed(MISSING_BASE)
            base_state_path = os.path.join(workdir, "base.state.json.gz")
            try:
                with base.storage.open_backend().open(base.state_name, "rb") as src, \
                        open(base_state_path, "wb") as dst:
                    for chunk in iter(lambda: src.read(1024 * 1024), b""):
                        dst.write(chunk)
            except Exception as exc:  # noqa: BLE001 - файл состояния недоступен
                raise _StorageUnavailable(f"base state of #{base.pk}: {exc}") from exc
            delta_backup(params, base_state_path, sql_path, state_path, label=backup.type.code)

        stem = f"backup{backup.pk}"
        try:
            with open(sql_path, "rb") as handle:
                file_name = backend.save(f"{stem}.sql", File(handle))
            with open(state_path, "rb") as handle:
                state_name = backend.save(f"{stem}.state.json.gz", File(handle))
        except Exception as exc:  # noqa: BLE001 - сеть, права, квоты
            raise _StorageUnavailable(str(exc)) from exc

        return {
            "file_name": file_name,
            "state_name": state_name,
            "size": os.path.getsize(sql_path),
            "base": base,
        }


def _finish(backup, **fields):
    fields.setdefault("error_code", "")
    fields.setdefault("error_detail", "")
    fields.update(secret="", finished_at=timezone.now())
    updated = Backup.objects.filter(pk=backup.pk).update(**fields)
    if not updated:
        # Задачу удалили, пока она выполнялась: файлы больше никому не нужны.
        _delete_files(backup.storage, fields.get("file_name"), fields.get("state_name"))
        return
    for name, value in fields.items():
        setattr(backup, name, value)
    if fields["status"] == Backup.SUCCEEDED:
        logger.info("Копия %s готова: %s", backup.pk, fields.get("file_name"))


# --------------------------------------------------------------------------- #
#  Удаление
# --------------------------------------------------------------------------- #
def delete_backup(backup):
    """Удаляет запись о копии и её файлы; отсутствие файлов не ошибка."""
    backup.delete()
    _delete_files(backup.storage, backup.file_name, backup.state_name)


def _delete_files(storage, *names):
    names = [name for name in names if name]
    if not names:
        return
    try:
        backend = storage.open_backend()
    except KeyError:
        logger.warning("Хранилище %s не настроено, файлы %s не удалены", storage.backend, names)
        return
    for name in names:
        try:
            backend.delete(name)
        except Exception as exc:  # noqa: BLE001 - удаление файла не критично
            logger.warning("Не удалось удалить файл копии %s: %s", name, exc)
