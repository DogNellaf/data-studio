import logging
import os
import subprocess

import psycopg2

from django.conf import settings

logger = logging.getLogger(__name__)

# Колонки-маркеры времени изменения строки. Используются инкрементальным и
# дифференциальным копированием для отбора изменившихся данных.
TIMESTAMP_COLUMNS = ("updated_at", "created_at")


def _ensure_media_dir():
    """Создаёт каталог для хранения резервных копий, если он отсутствует."""
    os.makedirs(settings.MEDIA_DIR, exist_ok=True)


def _backup_path(backup_name):
    """Возвращает полный путь к SQL-файлу резервной копии."""
    return os.path.join(settings.MEDIA_DIR, backup_name + ".sql")


def _format_value(value):
    """Возвращает безопасное SQL-представление значения для INSERT."""
    if value is None:
        return "NULL"
    return "'%s'" % str(value).replace("'", "''")


def _quote_identifier(name):
    """
    Безопасно экранирует идентификатор (имя таблицы/колонки) для SQL.

    Имена берутся из системного каталога PostgreSQL, поэтому достаточно
    стандартного экранирования двойными кавычками.
    """
    return '"%s"' % str(name).replace('"', '""')


def full_db_backup(host, port, user, password, database, backup_name) -> bool:
    """
    Выполняет полное копирование базы данных PostgreSQL в файл.

    :param host: IP-адрес или хост базы данных
    :param port: Порт базы данных
    :param user: Имя пользователя для подключения
    :param password: Пароль для подключения
    :param database: Имя базы данных
    :param backup_name: Название файла бэкапа
    :return: Boolean результат формирования бэкапа
    """
    _ensure_media_dir()
    backup_path = _backup_path(backup_name)

    dump_command = [
        settings.PG_DUMP_PATH,
        f"--host={host}",
        f"--port={port}",
        f"--username={user}",
        f"--dbname={database}",
        f"--file={backup_path}",
    ]

    env = os.environ.copy()
    env["PGPASSWORD"] = password

    try:
        subprocess.run(dump_command, check=True, env=env, capture_output=True, text=True)
        logger.info("Бэкап успешно сохранён в: %s", backup_path)
        return True
    except FileNotFoundError:
        logger.error("Не найдена утилита pg_dump по пути: %s", settings.PG_DUMP_PATH)
    except subprocess.CalledProcessError as exc:
        logger.error("Ошибка при выполнении pg_dump: %s", exc.stderr or exc)
    except Exception as exc:  # noqa: BLE001 - финальная защита от непредвиденных ошибок
        logger.error("Общая ошибка при создании бэкапа: %s", exc)
    return False


def _export_changes_since(host, port, user, password, database, since, backup_name) -> bool:
    """
    Экспортирует строки, изменённые после ``since``, в SQL-файл.

    Общая реализация для инкрементального и дифференциального копирования:
    для каждой таблицы схемы ``public`` ищется колонка времени изменения
    (``updated_at`` / ``created_at``) и выгружаются строки новее ``since``.

    :param since: Момент времени, изменения после которого попадут в копию
    :return: Boolean результат формирования бэкапа
    """
    _ensure_media_dir()
    backup_path = _backup_path(backup_name)

    connection = None
    try:
        connection = psycopg2.connect(
            host=host,
            port=port,
            user=user,
            password=password,
            dbname=database,
            connect_timeout=getattr(settings, "DB_CONNECT_TIMEOUT", 5),
        )
        with connection.cursor() as cursor, open(backup_path, "w", encoding="utf-8") as backup_file:
            cursor.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public';"
            )
            tables = [row[0] for row in cursor.fetchall()]

            for table_name in tables:
                cursor.execute(
                    """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_name = %s AND column_name IN %s;
                    """,
                    (table_name, TIMESTAMP_COLUMNS),
                )
                timestamp_columns = cursor.fetchall()
                if not timestamp_columns:
                    continue

                timestamp_column = timestamp_columns[0][0]
                quoted_table = _quote_identifier(table_name)
                cursor.execute(
                    f"SELECT * FROM {quoted_table} "
                    f"WHERE {_quote_identifier(timestamp_column)} > %s;",
                    (since,),
                )

                for row in cursor.fetchall():
                    values = ", ".join(_format_value(value) for value in row)
                    backup_file.write(f"INSERT INTO {quoted_table} VALUES ({values});\n")

        logger.info("Резервная копия успешно создана: %s", backup_path)
        return True
    except (psycopg2.Error, OSError) as exc:
        logger.error("Ошибка при создании резервной копии: %s", exc)
        return False
    finally:
        if connection is not None:
            connection.close()


def incremental_db_backup(host, port, user, password, database, last_backup_time, backup_name) -> bool:
    """
    Выполняет инкрементальное резервное копирование PostgreSQL: выгружает
    строки, изменившиеся с момента последнего бэкапа любого типа.

    :param last_backup_time: Дата и время последнего бэкапа
    :param backup_name: Название файла резервной копии
    :return: Boolean результат формирования бэкапа
    """
    return _export_changes_since(
        host, port, user, password, database, last_backup_time, backup_name
    )


def differential_db_backup(host, port, user, password, database, last_full_backup_time, backup_name) -> bool:
    """
    Выполняет дифференциальное резервное копирование PostgreSQL: выгружает
    строки, изменившиеся с момента последнего полного бэкапа.

    :param last_full_backup_time: Дата и время последнего полного бэкапа
    :param backup_name: Название файла резервной копии
    :return: Boolean результат формирования бэкапа
    """
    return _export_changes_since(
        host, port, user, password, database, last_full_backup_time, backup_name
    )
