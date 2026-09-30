import os

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Max, Q, Sum
from django.http import FileResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from core.forms import BackupForm
from core.models import Backup, BackupType, Storage
from core.services import BackupError, create_backup, delete_backup


@login_required
def index(request):
    backups = Backup.objects.filter(user=request.user).select_related("type", "storage__type")
    stats = backups.aggregate(
        count=Count("id"),
        total_size=Sum("size"),
        last_created=Max("created_at"),
        databases=Count("db", distinct=True),
    )
    return render(request, "index.html", {"backups": backups, "stats": stats})


@login_required
def download(request, id: int):
    backup = get_object_or_404(Backup, id=id, user=request.user)

    if not os.path.exists(backup.file_path):
        messages.error(request, "Файл резервной копии не найден на диске")
        return redirect("core.index")

    return FileResponse(
        open(backup.file_path, "rb"),
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
            backup = create_backup(request.user, **form.cleaned_data)
        except BackupError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, f"Копия базы «{backup.db}» создана")
            return redirect("core.index")

    return render(request, "create.html", {"form": form})


@login_required
@require_http_methods(["GET", "POST"])
def remove(request, id: int):
    backup = get_object_or_404(
        Backup.objects.select_related("type", "storage__type"), id=id, user=request.user
    )

    if request.method != "POST":
        return render(request, "remove.html", {"backup": backup})

    delete_backup(backup)
    messages.success(request, "Резервная копия удалена")
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
