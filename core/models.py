import os

from django.conf import settings
from django.contrib.auth.models import User
from django.db import models
from django.utils import timezone


class BackupType(models.Model):
    '''Класс реализует тип резервных копий'''

    FULL = "full"
    INCREMENTAL = "incremental"
    DIFFERENTIAL = "differential"
    CODE_CHOICES = [
        (FULL, "Полная"),
        (INCREMENTAL, "Инкрементальная"),
        (DIFFERENTIAL, "Дифференциальная"),
    ]

    code = models.CharField(
        verbose_name="Код",
        max_length=20,
        choices=CODE_CHOICES,
        unique=True,
        help_text="Определяет алгоритм копирования, от названия не зависит",
    )

    title = models.CharField(
        verbose_name="Название",
        max_length=100
    )

    description = models.TextField(
        verbose_name="Описание",
        blank=True
    )

    HINTS = {
        FULL: "Вся база целиком, основа для остальных",
        INCREMENTAL: "Изменения с последней копии любого типа",
        DIFFERENTIAL: "Изменения с последней полной копии",
    }

    class Meta:
        verbose_name = "Тип резервной копии"
        verbose_name_plural = "Типы резервных копий"
        ordering = ["id"]

    def __str__(self):
        return self.title

    @property
    def hint(self):
        """Короткое пояснение для формы создания копии."""
        return self.HINTS.get(self.code, "")


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
    '''
    Класс реализует резервную копию базы данных.

    Пароль от исходной базы намеренно не хранится: он нужен только на время
    снятия копии, а хранение чужих учётных данных в открытом виде превращает
    утечку базы приложения в утечку всех подключённых баз.
    '''
    username = models.CharField(
        verbose_name="Имя пользователя для подключения",
        max_length=255
    )

    db = models.CharField(
        verbose_name="База данных",
        max_length=255
    )

    host = models.CharField(
        verbose_name="Хост для подключения",
        max_length=255
    )

    port = models.PositiveIntegerField(
        verbose_name="Порт для подключения",
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

    size = models.PositiveBigIntegerField(
        verbose_name="Размер файла, байт",
        null=True,
        blank=True
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
        return f"{self.type.title} — {self.db}@{self.host} ({self.created_at:%Y-%m-%d %H:%M})"

    @property
    def file_name(self):
        """Имя SQL-файла копии в каталоге ``MEDIA_DIR``."""
        return f"backup{self.id}"

    @property
    def file_path(self):
        """Полный путь к SQL-файлу копии."""
        return os.path.join(settings.MEDIA_DIR, f"{self.file_name}.sql")

    @property
    def download_name(self):
        """Понятное имя файла при скачивании: база, тип и время снятия."""
        created_at = timezone.localtime(self.created_at)
        return f"{self.db}_{self.type.code}_{created_at:%Y%m%d_%H%M%S}.sql"
