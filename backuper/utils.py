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


def _quote_identifier(name):
    """
    Безопасно экранирует идентификатор (имя таблицы/колонки) для SQL.

    Имена берутся из системного каталога PostgreSQL, поэтому достаточно
    стандартного экранирования двойными кавычками.
    """
    escaped = str(name).replace('"', '""')
    return f'"{escaped}"'


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
        # Без флага pg_dump при отказе в аутентификации ждёт ввода пароля
        # с терминала, и запрос повисает до таймаута.
        "--no-password",
        f"--file={backup_path}",
    ]

    env = os.environ.copy()
    env["PGPASSWORD"] = password

    try:
        subprocess.run(
            dump_command,
            check=True,
            env=env,
            capture_output=True,
            text=True,
            timeout=settings.PG_DUMP_TIMEOUT,
        )
        logger.info("Бэкап успешно сохранён в: %s", backup_path)
        return True
    except FileNotFoundError:
        logger.error("Не найдена утилита pg_dump по пути: %s", settings.PG_DUMP_PATH)
    except subprocess.CalledProcessError as exc:
        logger.error("Ошибка при выполнении pg_dump: %s", exc.stderr or exc)
    except subprocess.TimeoutExpired:
        logger.error("pg_dump не уложился в %s с", settings.PG_DUMP_TIMEOUT)
    except Exception as exc:  # noqa: BLE001 - финальная защита от непредвиденных ошибок
        logger.error("Общая ошибка при создании бэкапа: %s", exc)
    return False


def _list_tables(cursor):
    """Таблицы схемы ``public`` в порядке, безопасном для вставки.

    Родительские таблицы идут раньше дочерних, иначе новая строка заказа
    при восстановлении сослалась бы на ещё не вставленного покупателя.
    """
    cursor.execute(
        "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename;"
    )
    tables = [row[0] for row in cursor.fetchall()]

    cursor.execute(
        """
        SELECT child.relname, parent.relname
        FROM pg_constraint c
        JOIN pg_class child ON child.oid = c.conrelid
        JOIN pg_class parent ON parent.oid = c.confrelid
        JOIN pg_namespace n ON n.oid = child.relnamespace
        WHERE c.contype = 'f' AND n.nspname = 'public';
        """
    )
    parents = {table: set() for table in tables}
    for child, parent in cursor.fetchall():
        if child in parents and parent in parents and child != parent:
            parents[child].add(parent)

    return _topological_order(tables, parents)


def _topological_order(tables, parents):
    """Сортирует таблицы так, чтобы каждая шла после своих родителей.

    Таблицы, участвующие в цикле внешних ключей, дописываются в конец в
    алфавитном порядке: для них корректного порядка не существует.
    """
    ordered, placed = [], set()
    remaining = list(tables)
    while remaining:
        ready = [t for t in remaining if parents[t] <= placed]
        if not ready:
            ready = remaining
        for table in ready:
            ordered.append(table)
            placed.add(table)
        remaining = [t for t in remaining if t not in placed]
    return ordered


def _table_columns(cursor, table_name):
    """Записываемые колонки таблицы в порядке объявления."""
    cursor.execute(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = %s AND is_generated = 'NEVER'
        ORDER BY ordinal_position;
        """,
        (table_name,),
    )
    return [row[0] for row in cursor.fetchall()]


def _primary_key(cursor, table_name):
    """Колонки первичного ключа таблицы (пустой список, если ключа нет)."""
    cursor.execute(
        """
        SELECT a.attname
        FROM pg_index i
        JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY (i.indkey)
        WHERE i.indrelid = %s::regclass AND i.indisprimary
        ORDER BY array_position(i.indkey, a.attnum);
        """,
        ("public." + _quote_identifier(table_name),),
    )
    return [row[0] for row in cursor.fetchall()]


def _upsert_suffix(columns, primary_key):
    """Хвост INSERT, превращающий его в upsert по первичному ключу.

    Изменённая строка уже есть в базе, восстановленной из предыдущих копий,
    и простой INSERT упал бы на нарушении уникальности.
    """
    if not primary_key:
        return ""
    updates = [c for c in columns if c not in primary_key]
    target = ", ".join(_quote_identifier(c) for c in primary_key)
    if not updates:
        return f" ON CONFLICT ({target}) DO NOTHING"
    assignments = ", ".join(
        f"{_quote_identifier(c)} = EXCLUDED.{_quote_identifier(c)}" for c in updates
    )
    return f" ON CONFLICT ({target}) DO UPDATE SET {assignments}"


def _export_changes_since(host, port, user, password, database, since, backup_name) -> bool:
    """
    Экспортирует строки, изменённые после ``since``, в SQL-файл.

    Общая реализация для инкрементального и дифференциального копирования:
    для каждой таблицы схемы ``public`` берётся колонка времени изменения
    (``updated_at``, а при её отсутствии ``created_at``) и выгружаются строки
    новее ``since`` в виде upsert-ов, которые накатываются поверх базы,
    восстановленной из полной копии. Значения экранирует сам PostgreSQL
    (``quote_nullable``), поэтому корректно переносятся любые типы: JSON,
    массивы, bytea, даты.

    Ограничения: удаления строк не отслеживаются, таблицы без колонок
    времени попадают только в полную копию. Для наката нужны права владельца
    таблиц (из-за отключения пользовательских триггеров на время вставки).

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
        # Все выборки видят один снимок базы, иначе строки, изменённые между
        # запросами к разным таблицам, дали бы несогласованную копию.
        connection.set_session(isolation_level="REPEATABLE READ", readonly=True)

        with connection.cursor() as cursor, open(backup_path, "w", encoding="utf-8") as backup_file:
            backup_file.write(
                f"-- DataStudio: строки {database}, изменённые после {since}\n"
                "BEGIN;\n"
            )

            for table_name in _list_tables(cursor):
                columns = _table_columns(cursor, table_name)
                timestamp_column = next((c for c in TIMESTAMP_COLUMNS if c in columns), None)
                if timestamp_column is None:
                    continue

                quoted_table = _quote_identifier(table_name)
                column_list = ", ".join(_quote_identifier(c) for c in columns)
                literals = ", ".join(
                    f"quote_nullable({_quote_identifier(c)})" for c in columns
                )
                suffix = _upsert_suffix(columns, _primary_key(cursor, table_name))

                cursor.execute(
                    f"SELECT concat_ws(', ', {literals}) FROM {quoted_table} "
                    f"WHERE {_quote_identifier(timestamp_column)} > %s "
                    f"ORDER BY {_quote_identifier(timestamp_column)};",
                    (since,),
                )
                rows = cursor.fetchall()
                if not rows:
                    continue

                # Копия воспроизводит значения как есть: пользовательские
                # триггеры (например, «обнови updated_at») при восстановлении
                # исказили бы данные. Триггеры внешних ключей не затрагиваются.
                backup_file.write(f"ALTER TABLE {quoted_table} DISABLE TRIGGER USER;\n")
                for (values,) in rows:
                    backup_file.write(
                        f"INSERT INTO {quoted_table} ({column_list}) VALUES ({values}){suffix};\n"
                    )
                backup_file.write(f"ALTER TABLE {quoted_table} ENABLE TRIGGER USER;\n")

            backup_file.write("COMMIT;\n")

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
