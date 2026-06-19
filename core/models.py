from django.db import models
from django.contrib.auth.models import User
from datetime import datetime as dt

class BackupType(models.Model):
    '''Класс реализует тип резервных копий'''
    title = models.CharField(
        verbose_name="Название",
        max_length=100
    )

    class Meta:
        verbose_name = "Тип резервной копии"
        verbose_name_plural = "Типы резервных копий"

class StorageType(models.Model):
    title = models.CharField(
        verbose_name="Название",
        max_length=100
    )

    class Meta:
        verbose_name = "Тип хранилища"
        verbose_name_plural = "Типы хранилищ"

class Storage(models.Model):
    location = models.CharField(
        verbose_name="Местоположение",
        max_length=255
    )

    type = models.ForeignKey(
        StorageType,
        verbose_name="Тип хранилища",
        on_delete=models.CASCADE
    )

    class Meta:
        verbose_name = "Хранилище"
        verbose_name_plural = "Хранилища"

class Backup(models.Model):
    username = models.CharField(
        verbose_name="Имя пользователя для подключения",
        max_length=255
    )

    password = models.CharField(
        verbose_name="Пароль для подключения",
        max_length=255
    )

    db = models.CharField(
        verbose_name="База данных",
        max_length=255
    )

    ip = models.CharField(
        verbose_name="IP-адрес для подключения",
        max_length=255
    )

    port = models.CharField(
        verbose_name="Порт для подключения",
        max_length=255
    )

    storage = models.ForeignKey(
        Storage,
        verbose_name="Хранилище",
        on_delete=models.CASCADE
    )

    type = models.ForeignKey(
        BackupType,
        verbose_name="Тип хранилища",
        on_delete=models.CASCADE
    )

    user = models.ForeignKey(
        User,
        verbose_name="Автор",
        on_delete=models.CASCADE
    )

    created_at = models.DateTimeField(
        verbose_name="Дата и время создания",
        default=dt.now()
    )

    class Meta:
        verbose_name = "Резервная копия"
        verbose_name_plural = "Резервные копии"
