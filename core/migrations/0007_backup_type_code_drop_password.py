from django.db import migrations, models

# Коды типов и соответствующие им названия, по которым раньше выбирался
# алгоритм копирования. Используются для переноса существующих записей.
BACKUP_TYPES = [
    (
        "full",
        "Полная",
        "Полный снимок базы через pg_dump: схема и все данные. "
        "Самодостаточна и служит основой для остальных типов.",
    ),
    (
        "incremental",
        "Инкрементальная",
        "Строки, изменившиеся с момента последней копии любого типа. "
        "Самая компактная, но для восстановления нужна вся цепочка.",
    ),
    (
        "differential",
        "Дифференциальная",
        "Строки, изменившиеся с момента последней полной копии. "
        "Для восстановления достаточно полной копии и последней дифференциальной.",
    ),
]


def seed_reference_data(apps, schema_editor):
    """Проставляет коды существующим типам и создаёт недостающие справочники."""
    BackupType = apps.get_model("core", "BackupType")
    StorageType = apps.get_model("core", "StorageType")
    Storage = apps.get_model("core", "Storage")

    for code, title, description in BACKUP_TYPES:
        backup_type = (
            BackupType.objects.filter(code=code).first()
            or BackupType.objects.filter(title=title, code__isnull=True).first()
            or BackupType(title=title)
        )
        backup_type.code = code
        if not backup_type.description:
            backup_type.description = description
        backup_type.save()

    # Типы с неизвестными названиями не соответствуют ни одному алгоритму,
    # поэтому копий по ним быть не может; удаляем, чтобы code стал обязательным.
    BackupType.objects.filter(code__isnull=True).delete()

    if not Storage.objects.exists():
        storage_type, _ = StorageType.objects.get_or_create(title="Локальный диск")
        Storage.objects.create(location="Каталог BACKUP_DIR на сервере", type=storage_type)


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0006_alter_backup_options_alter_backup_created_at_and_more"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="backuptype",
            options={
                "ordering": ["id"],
                "verbose_name": "Тип резервной копии",
                "verbose_name_plural": "Типы резервных копий",
            },
        ),
        migrations.AddField(
            model_name="backuptype",
            name="code",
            field=models.CharField(max_length=20, null=True, verbose_name="Код"),
        ),
        migrations.AddField(
            model_name="backuptype",
            name="description",
            field=models.TextField(blank=True, verbose_name="Описание"),
        ),
        migrations.RunPython(seed_reference_data, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="backuptype",
            name="code",
            field=models.CharField(
                choices=[
                    ("full", "Полная"),
                    ("incremental", "Инкрементальная"),
                    ("differential", "Дифференциальная"),
                ],
                help_text="Определяет алгоритм копирования, от названия не зависит",
                max_length=20,
                unique=True,
                verbose_name="Код",
            ),
        ),
        migrations.RemoveField(
            model_name="backup",
            name="password",
        ),
        migrations.RenameField(
            model_name="backup",
            old_name="ip",
            new_name="host",
        ),
        migrations.AlterField(
            model_name="backup",
            name="host",
            field=models.CharField(max_length=255, verbose_name="Хост для подключения"),
        ),
        migrations.AlterField(
            model_name="backup",
            name="port",
            field=models.PositiveIntegerField(verbose_name="Порт для подключения"),
        ),
        migrations.AddField(
            model_name="backup",
            name="size",
            field=models.PositiveBigIntegerField(
                blank=True, null=True, verbose_name="Размер файла, байт"
            ),
        ),
    ]
