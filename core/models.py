from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone


class BackupType(models.Model):
    '''Класс реализует тип резервных копий'''
    title = models.CharField(
        verbose_name="Название",
        max_length=100
    )

    class Meta:
        verbose_name = "Тип резервной копии"
        verbose_name_plural = "Типы резервных копий"

    def __str__(self):
        return self.title


class StorageType(models.Model):
    '''Класс реализует тип хранилища'''
    title = models.CharField(
        verbose_name="Название",
        max_length=100
    )

    class Meta:
        verbose_name = "Тип хранилища"
        verbose_name_plural = "Типы хранилищ"

    def __str__(self):
        return self.title


class Storage(models.Model):
    '''Класс реализует хранилище резервных копий'''
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

    def __str__(self):
        return f"{self.location} - {self.type.title}"


class Backup(models.Model):
    '''Класс реализует резервную копию базы данных'''
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
        verbose_name="Тип резервной копии",
        on_delete=models.CASCADE
    )

    user = models.ForeignKey(
        User,
        verbose_name="Автор",
        on_delete=models.CASCADE
    )

    created_at = models.DateTimeField(
        verbose_name="Дата и время создания",
        default=timezone.now
    )

    class Meta:
        verbose_name = "Резервная копия"
        verbose_name_plural = "Резервные копии"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.type.title} — {self.db}@{self.ip} ({self.created_at:%Y-%m-%d %H:%M})"
