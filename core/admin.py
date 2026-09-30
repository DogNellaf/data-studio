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
    list_display = ("id", "location", "type")
    list_filter = ("type",)
    search_fields = ("location",)


@admin.register(Backup)
class BackupAdmin(admin.ModelAdmin):
    list_display = ("id", "type", "db", "host", "port", "size", "storage", "user", "created_at")
    list_filter = ("type", "storage", "user")
    search_fields = ("db", "host", "username")
    date_hierarchy = "created_at"
