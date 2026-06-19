import logging
import os

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import FileResponse
from django.shortcuts import redirect, render

from core.models import Backup, BackupType, Storage
from core.utils import check_db_connection
from backuper.utils import (
    full_db_backup,
    incremental_db_backup,
    differential_db_backup,
)

logger = logging.getLogger(__name__)

LOGIN_URL = "/auth/login"


def _backup_file_path(backup_id):
    """Возвращает путь к SQL-файлу резервной копии по её идентификатору."""
    return os.path.join(settings.MEDIA_DIR, f"backup{backup_id}.sql")


@login_required(login_url=LOGIN_URL, redirect_field_name=None)
def index(request):
    backups = Backup.objects.filter(user=request.user)
    return render(request, "index.html", {"backups": backups})


@login_required(login_url=LOGIN_URL, redirect_field_name=None)
def download(request, id: int):
    backup = Backup.objects.filter(id=id, user=request.user).first()
    if backup is None:
        return redirect("core.index")

    backup_path = _backup_file_path(backup.id)
    if not os.path.exists(backup_path):
        messages.error(request, "Файл резервной копии не найден")
        return redirect("core.index")

    return FileResponse(
        open(backup_path, "rb"),
        as_attachment=True,
        filename=f"backup{backup.id}.sql",
        content_type="application/sql",
    )


@login_required(login_url=LOGIN_URL, redirect_field_name=None)
def create(request):
    context = {
        "backup_types": BackupType.objects.all(),
        "storages": Storage.objects.all(),
    }

    if request.method != "POST":
        return render(request, "create.html", context)

    def fail(message):
        messages.error(request, message)
        return render(request, "create.html", context)

    backup_type = BackupType.objects.filter(id=request.POST.get("type")).first()
    if backup_type is None:
        return fail("Указанный тип копирования не существует")

    username = request.POST.get("username")
    if not username:
        return fail("Не указано имя пользователя")

    password = request.POST.get("password")
    if not password:
        return fail("Не указан пароль")

    db = request.POST.get("db")
    if not db:
        return fail("Не указана база данных")

    ip = request.POST.get("ip")
    if not ip:
        return fail("Не указан адрес сервера")

    port = request.POST.get("port")
    if not port:
        return fail("Не указан порт")

    storage = Storage.objects.filter(id=request.POST.get("storage")).first()
    if storage is None:
        return fail("Указанное хранилище не существует")

    if not check_db_connection(ip, port, username, password, db):
        return fail("Соединение с указанной базой данных отсутствует")

    # Базовую копию ищем ДО сохранения текущей записи, иначе новая копия
    # попадёт в выборку и сделает проверку бессмысленной.
    title = backup_type.title
    previous_backup = None

    if title == "Инкрементальная":
        previous_backup = (
            Backup.objects.filter(db=db, ip=ip, port=port).order_by("created_at").last()
        )
        if previous_backup is None:
            return fail("Для инкрементальной копии нужна полная копия этой же базы")
    elif title == "Дифференциальная":
        previous_backup = (
            Backup.objects.filter(db=db, ip=ip, port=port, type__title="Полная")
            .order_by("created_at")
            .last()
        )
        if previous_backup is None:
            return fail("Для дифференциальной копии нужна полная копия этой же базы")
    elif title != "Полная":
        return fail("Указан неизвестный тип резервного копирования")

    backup = Backup.objects.create(
        type=backup_type,
        username=username,
        password=password,
        db=db,
        ip=ip,
        port=port,
        storage=storage,
        user=request.user,
    )

    backup_name = f"backup{backup.id}"
    if title == "Полная":
        succeeded = full_db_backup(ip, port, username, password, db, backup_name)
    elif title == "Инкрементальная":
        succeeded = incremental_db_backup(
            ip, port, username, password, db, previous_backup.created_at, backup_name
        )
    else:  # Дифференциальная
        succeeded = differential_db_backup(
            ip, port, username, password, db, previous_backup.created_at, backup_name
        )

    if not succeeded:
        backup.delete()
        return fail("Бэкап создать не удалось")

    return redirect("core.index")


@login_required(login_url=LOGIN_URL, redirect_field_name=None)
def remove(request, id: int):
    backup = Backup.objects.filter(id=id, user=request.user).first()
    if backup is None:
        return redirect("core.index")

    if request.method != "POST":
        return render(request, "remove.html", {"backup": backup})

    backup_path = _backup_file_path(backup.id)
    backup.delete()

    if os.path.exists(backup_path):
        try:
            os.remove(backup_path)
        except OSError as exc:
            logger.warning("Не удалось удалить файл копии %s: %s", backup_path, exc)

    return redirect("core.index")


@login_required(login_url=LOGIN_URL, redirect_field_name=None)
def backup_types(request):
    types = BackupType.objects.all()
    return render(request, "backup_types.html", {"types": types})


@login_required(login_url=LOGIN_URL, redirect_field_name=None)
def storages(request):
    return render(request, "storages.html", {"storages": Storage.objects.all()})
