from django.apps import AppConfig


class BackuperConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'backuper'
    verbose_name = 'Модуль создания резервных копий'
