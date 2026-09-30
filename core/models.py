import os

from django.conf import settings
from django.contrib.auth.models import User
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class BackupType(models.Model):
    """
    Тип резервных копий.

    Хранится только код: он выбирает алгоритм копирования, а название и
    описания берутся из переводимых строк, поэтому интерфейс показывает их
    на языке пользователя.
    """

    FULL = "full"
    INCREMENTAL = "incremental"
    DIFFERENTIAL = "differential"
    CODE_CHOICES = [
        (FULL, _("Full")),
        (INCREMENTAL, _("Incremental")),
        (DIFFERENTIAL, _("Differential")),
    ]

    HINTS = {
        FULL: _("The whole database, the base for the others"),
        INCREMENTAL: _("Changes since the last backup of any type"),
        DIFFERENTIAL: _("Changes since the last full backup"),
    }

    SUMMARIES = {
        FULL: _(
            "A complete pg_dump snapshot: schema and all data. Self-contained "
            "and the base for the other backup types."
        ),
        INCREMENTAL: _(
            "Rows changed since the last backup of any type. The smallest "
            "backup, but a restore needs the whole chain."
        ),
        DIFFERENTIAL: _(
            "Rows changed since the last full backup. A restore needs only the "
            "full backup and the latest differential one."
        ),
    }

    code = models.CharField(
        verbose_name=_("code"),
        max_length=20,
        choices=CODE_CHOICES,
        unique=True,
        help_text=_("Selects the backup algorithm"),
    )

    class Meta:
        verbose_name = _("backup type")
        verbose_name_plural = _("backup types")
        ordering = ["id"]

    def __str__(self):
        return str(self.label)

    @property
    def label(self):
        return self.get_code_display()

    @property
    def hint(self):
        """Короткое пояснение для формы создания копии."""
        return self.HINTS.get(self.code, "")

    @property
    def summary(self):
        """Подробное описание для справочника типов."""
        return self.SUMMARIES.get(self.code, "")


class StorageType(models.Model):
    """Тип хранилища."""
    title = models.CharField(
        verbose_name=_("title"),
        max_length=100
    )

    class Meta:
        verbose_name = _("storage type")
        verbose_name_plural = _("storage types")

    def __str__(self):
        return self.title


class Storage(models.Model):
    """Хранилище резервных копий."""
    location = models.CharField(
        verbose_name=_("location"),
        max_length=255
    )

    type = models.ForeignKey(
        StorageType,
        verbose_name=_("storage type"),
        on_delete=models.CASCADE
    )

    class Meta:
        verbose_name = _("storage")
        verbose_name_plural = _("storages")

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
        verbose_name=_("database user"),
        max_length=255
    )

    db = models.CharField(
        verbose_name=_("database"),
        max_length=255
    )

    host = models.CharField(
        verbose_name=_("host"),
        max_length=255
    )

    port = models.PositiveIntegerField(
        verbose_name=_("port"),
    )

    storage = models.ForeignKey(
        Storage,
        verbose_name=_("storage"),
        on_delete=models.CASCADE
    )

    type = models.ForeignKey(
        BackupType,
        verbose_name=_("backup type"),
        on_delete=models.CASCADE
    )

    user = models.ForeignKey(
        User,
        verbose_name=_("owner"),
        on_delete=models.CASCADE
    )

    size = models.PositiveBigIntegerField(
        verbose_name=_("file size, bytes"),
        null=True,
        blank=True
    )

    created_at = models.DateTimeField(
        verbose_name=_("created at"),
        default=timezone.now
    )

    class Meta:
        verbose_name = _("backup")
        verbose_name_plural = _("backups")
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.type.label} — {self.db}@{self.host} ({self.created_at:%Y-%m-%d %H:%M})"

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
