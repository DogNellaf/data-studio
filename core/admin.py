from django.contrib import admin
from core.models import BackupType, Backup, StorageType, Storage

admin.site.register(BackupType)
admin.site.register(Backup)
admin.site.register(StorageType)
admin.site.register(Storage)
