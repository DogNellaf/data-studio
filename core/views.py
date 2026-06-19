import os

from django.shortcuts import render, redirect, HttpResponse
from django.contrib import messages
from django.core.files.base import ContentFile
from datastudio.settings import MEDIA_DIR
from core.models import Backup, BackupType, Storage
from core.utils import check_db_connection
from backuper.utils import full_db_backup, incremental_db_backup, differential_db_backup

def index(request):
    if not request.user.is_authenticated:
        return redirect("/auth/login")

    backups = Backup.objects.filter(user=request.user)

    return render(request, "index.html", {"backups": backups})

def download(request, id: int):
    if not request.user.is_authenticated:
        return redirect("/auth/login")

    if Backup.objects.filter(id=id).exists():
        backup_path = os.path.join(MEDIA_DIR, f"backup{id}" + '.sql')
        sql = open(backup_path, "r", encoding="UTF8").read()
        file_to_send = ContentFile(sql)

        data = open(backup_path, "br").read()
        response = HttpResponse(data,'application/sql')
        response['Content-Length']      = file_to_send.size    
        response['Content-Disposition'] = 'attachment; filename="backup.sql"'
        return response


    return redirect(index)

def create(request):
    if not request.user.is_authenticated:
        return redirect("/auth/login")

    backup_types = BackupType.objects.all()
    storages = Storage.objects.all()

    if request.method == "POST":
        type_id = request.POST.get("type")

        if not BackupType.objects.filter(id=type_id).exists():
            messages.error(request, "Указанный тип копирования не существует")
            return render(request, "create.html", {
                "backup_types": backup_types,
                "storages": storages
            })
        
        backup_type = BackupType.objects.filter(id=type_id).first()

        username = request.POST.get("username")
        if username is None or username == "":
            messages.error(request, "Не указано имя пользователя")
            return render(request, "create.html", {
                "backup_types": backup_types,
                "storages": storages
            })

        password = request.POST.get("password")
        if password is None or password == "":
            messages.error(request, "Не указан пароль")
            return render(request, "create.html", {
                "backup_types": backup_types,
                "storages": storages
            })

        db = request.POST.get("db")
        if db is None or db == "":
            messages.error(request, "Не указана база данных")
            return render(request, "create.html", {
                "backup_types": backup_types,
                "storages": storages
            })

        ip = request.POST.get("ip")
        if ip is None or ip == "":
            messages.error(request, "Не указан адрес сервера")
            return render(request, "create.html", {
                "backup_types": backup_types,
                "storages": storages
            })

        port = request.POST.get("port")
        if port is None or port == "":
            messages.error(request, "Не указан порт")
            return render(request, "create.html", {
                "backup_types": backup_types,
                "storages": storages
            })

        storage_id = request.POST.get("storage")

        if not Storage.objects.filter(id=storage_id).exists():
            messages.error(request, "Указанное хранилище не существует")
            return render(request, "create.html", {
                "backup_types": backup_types,
                "storages": storages
            })
        
        storage = Storage.objects.filter(id=storage_id).first()
        
        if not check_db_connection(ip, port, username, password, db):
            messages.error(request, "Соединение с указанной базой данных отсутствует")
            return render(request, "create.html", {
                "backup_types": backup_types,
                "storages": storages
            })
        
        backup = Backup(
            type=backup_type,
            username=username,
            password=password,
            db=db,
            ip=ip,
            port=port,
            storage=storage,
            user=request.user
        )

        backup.save()

        if backup_type.title == "Полная":
            if not full_db_backup(ip, port, username, password, db, f"backup{backup.id}"):
                backup.delete()
                messages.error(request, "Бэкап создать не удалось")
                return render(request, "create.html", {
                    "backup_types": backup_types,
                    "storages": storages
                })

        elif backup_type.title == "Инкрементальная":
            last_backup = Backup.objects.filter(db=db, ip=ip, port=port).order_by('created_at')
            if not last_backup.exists():
                messages.error(request, "Для инкреметальной копии нужна полная копия этой же базы")
                return render(request, "create.html", {
                    "backup_types": backup_types,
                    "storages": storages
                })
            
            last_backup = Backup.objects.filter(db=db, ip=ip, port=port).order_by('created_at').last()

            if not incremental_db_backup(ip, port, username, password, db, last_backup.created_at, f"backup{backup.id}"):
                backup.delete()
                messages.error(request, "Бэкап создать не удалось")
                return render(request, "create.html", {
                    "backup_types": backup_types,
                    "storages": storages
                })

        elif backup_type.title == "Дифференциальная":

            last_backup = Backup.objects.filter(db=db, ip=ip, port=port, type__title="Полная").order_by('created_at')
            if not last_backup.exists():
                messages.error(request, "Для дифференциальной копии нужна полная копия этой же базы")
                return render(request, "create.html", {
                    "backup_types": backup_types,
                    "storages": storages
                })
            
            last_backup = Backup.objects.filter(db=db, ip=ip, port=port, type__title="Полная").order_by('created_at').last()

            if not differential_db_backup(ip, port, username, password, db, last_backup.created_at, f"backup{backup.id}"):
                backup.delete()
                messages.error(request, "Бэкап создать не удалось")
                return render(request, "create.html", {
                    "backup_types": backup_types,
                    "storages": storages
                })
        else:
            messages.error(request, "Указан неизвестный тип резервного копирования")
            return render(request, "create.html", {
                "backup_types": backup_types,
                "storages": storages
            })        

        return redirect(index)

    return render(request, "create.html", {
        "backup_types": backup_types,
        "storages": storages
    })

def remove(request, id: int):
    if not request.user.is_authenticated:
        return redirect("/auth/login")

    if not Backup.objects.filter(id=id).exists():
        return redirect(index)

    backup = Backup.objects.filter(id=id).first()

    if request.method != "POST":
        return render(request, "remove.html", {"backup": backup})
    else:
        backup.delete()
        return redirect(index)

def backup_types(request):
    if not request.user.is_authenticated:
        return redirect("/auth/login")

    types = BackupType.objects.all()

    return render(request, "backup_types.html", {"types": types})

def storages(request):
    if not request.user.is_authenticated:
        return redirect("/auth/login")

    storages = Storage.objects.all()

    return render(request, "storages.html", {"storages": storages})
