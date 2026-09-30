import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Max, Q, Sum
from django.http import FileResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods

from core.forms import BackupForm
from core.models import Backup, BackupType, Storage
from core.services import BackupError, delete_backup, enqueue_backup, error_message

logger = logging.getLogger(__name__)


@login_required
def index(request):
    backups = list(
        Backup.objects.filter(user=request.user).select_related("type", "storage__type", "base")
    )
    for backup in backups:
        backup.error_text = error_message(backup) if backup.status == Backup.FAILED else ""
    stats = Backup.objects.filter(user=request.user).aggregate(
        count=Count("id", filter=Q(status=Backup.SUCCEEDED)),
        total_size=Sum("size", filter=Q(status=Backup.SUCCEEDED)),
        last_created=Max("finished_at", filter=Q(status=Backup.SUCCEEDED)),
        databases=Count("db", distinct=True, filter=Q(status=Backup.SUCCEEDED)),
    )
    context = {
        "backups": backups,
        "stats": stats,
        # Пока есть незавершённые задачи, страница обновляется сама.
        "has_active": any(backup.is_active for backup in backups),
    }
    return render(request, "index.html", context)


@login_required
def download(request, id: int):
    backup = get_object_or_404(Backup, id=id, user=request.user)
    if not backup.is_ready:
        messages.error(request, _("This backup is not ready yet"))
        return redirect("core.index")

    try:
        handle = backup.storage.open_backend().open(backup.file_name, "rb")
    except Exception as exc:  # noqa: BLE001 - файла нет или хранилище недоступно
        logger.warning("Файл копии %s недоступен: %s", backup.pk, exc)
        messages.error(request, _("The backup file is missing from the storage"))
        return redirect("core.index")

    return FileResponse(
        handle,
        as_attachment=True,
        filename=backup.download_name,
        content_type="application/sql",
    )


@login_required
@require_http_methods(["GET", "POST"])
def create(request):
    form = BackupForm(request.POST or None)

    if request.method == "POST" and form.is_valid():
        try:
            backup = enqueue_backup(request.user, **form.cleaned_data)
        except BackupError as exc:
            form.add_error(None, str(exc))
        else:
            if backup.status == Backup.SUCCEEDED:
                messages.success(request, _("Backup of “%(db)s” created") % {"db": backup.db})
            elif backup.status == Backup.FAILED:
                messages.error(request, error_message(backup))
            else:
                messages.success(
                    request,
                    _("Backup of “%(db)s” queued: it will appear below when ready")
                    % {"db": backup.db},
                )
            return redirect("core.index")

    return render(request, "create.html", {"form": form})


@login_required
@require_http_methods(["GET", "POST"])
def remove(request, id: int):
    backup = get_object_or_404(
        Backup.objects.select_related("type", "storage__type"), id=id, user=request.user
    )
    if backup.status == Backup.RUNNING:
        messages.error(request, _("This backup is being taken right now; delete it when it finishes"))
        return redirect("core.index")

    if request.method != "POST":
        dependents = backup.dependents.filter(status=Backup.SUCCEEDED).count()
        return render(request, "remove.html", {"backup": backup, "dependents": dependents})

    delete_backup(backup)
    messages.success(request, _("Backup deleted"))
    return redirect("core.index")


@login_required
def backup_types(request):
    return render(request, "backup_types.html", {"types": BackupType.objects.all()})


@login_required
def storages(request):
    storages = Storage.objects.select_related("type").annotate(
        backup_count=Count("backup", filter=Q(backup__user=request.user))
    )
    return render(request, "storages.html", {"storages": storages})
