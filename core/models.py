from django.contrib.auth.models import User
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from core.backends import backend_choices, get_backend, is_configured


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
    """Хранилище резервных копий: запись справочника, указывающая на бэкенд."""
    location = models.CharField(
        verbose_name=_("location"),
        max_length=255
    )

    type = models.ForeignKey(
        StorageType,
        verbose_name=_("storage type"),
        on_delete=models.CASCADE
    )

    backend = models.CharField(
        verbose_name=_("backend"),
        max_length=50,
        choices=backend_choices,
        default="local",
        help_text=_("Where files are written; S3 needs the BACKUP_S3_* settings"),
    )

    class Meta:
        verbose_name = _("storage")
        verbose_name_plural = _("storages")

    def __str__(self):
        return f"{self.location} - {self.type.title}"

    @property
    def is_available(self):
        return is_configured(self.backend)

    def open_backend(self):
        return get_backend(self.backend)


class Backup(models.Model):
    """
    Резервная копия базы данных и задача на её снятие.

    Копия проходит статусы «в очереди» → «выполняется» → «готова» или
    «ошибка». Пароль от исходной базы нужен только воркеру: пока задача
    ждёт, он лежит в ``secret`` зашифрованным и стирается по её завершении.
    Хранить его дольше нельзя: утечка базы приложения стала бы утечкой всех
    подключённых баз.
    """

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    STATUS_CHOICES = [
        (QUEUED, _("Queued")),
        (RUNNING, _("Running")),
        (SUCCEEDED, _("Ready")),
        (FAILED, _("Failed")),
    ]
    ACTIVE_STATUSES = (QUEUED, RUNNING)

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

    base = models.ForeignKey(
        "self",
        verbose_name=_("base backup"),
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="dependents",
        help_text=_("The backup whose state this delta was compared with"),
    )

    status = models.CharField(
        verbose_name=_("status"),
        max_length=20,
        choices=STATUS_CHOICES,
        default=QUEUED,
        db_index=True,
    )

    error_code = models.CharField(
        verbose_name=_("error"),
        max_length=50,
        blank=True
    )

    error_detail = models.TextField(
        verbose_name=_("error details"),
        blank=True,
        help_text=_("Technical details for administrators; not shown to users"),
    )

    secret = models.TextField(
        verbose_name=_("encrypted password"),
        blank=True,
        editable=False,
    )

    file_name = models.CharField(
        verbose_name=_("backup file"),
        max_length=255,
        blank=True
    )

    state_name = models.CharField(
        verbose_name=_("state file"),
        max_length=255,
        blank=True,
        help_text=_("Row hashes that later deltas are compared with"),
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

    started_at = models.DateTimeField(
        verbose_name=_("started at"),
        null=True,
        blank=True
    )

    finished_at = models.DateTimeField(
        verbose_name=_("finished at"),
        null=True,
        blank=True
    )

    class Meta:
        verbose_name = _("backup")
        verbose_name_plural = _("backups")
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.type.label} — {self.db}@{self.host} ({self.created_at:%Y-%m-%d %H:%M})"

    @property
    def is_active(self):
        return self.status in self.ACTIVE_STATUSES

    @property
    def is_ready(self):
        return self.status == self.SUCCEEDED and bool(self.file_name)

    @property
    def download_name(self):
        """Понятное имя файла при скачивании: база, тип и время снятия."""
        created_at = timezone.localtime(self.created_at)
        return f"{self.db}_{self.type.code}_{created_at:%Y%m%d_%H%M%S}.sql"
