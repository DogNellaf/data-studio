from django.contrib import admin

from core.models import Backup, BackupType, Storage, StorageType


@admin.register(BackupType)
class BackupTypeAdmin(admin.ModelAdmin):
    list_display = ("id", "code")


@admin.register(StorageType)
class StorageTypeAdmin(admin.ModelAdmin):
    list_display = ("id", "title")
    search_fields = ("title",)


@admin.register(Storage)
class StorageAdmin(admin.ModelAdmin):
    list_display = ("id", "location", "type", "backend")
    list_filter = ("type", "backend")
    search_fields = ("location",)


@admin.register(Backup)
class BackupAdmin(admin.ModelAdmin):
    list_display = ("id", "type", "status", "db", "host", "port", "size", "storage", "user", "created_at")
    list_filter = ("status", "type", "storage", "user")
    search_fields = ("db", "host", "username")
    date_hierarchy = "created_at"
    raw_id_fields = ("base",)
    readonly_fields = ("error_code", "error_detail", "started_at", "finished_at", "file_name", "state_name")
