"""
Снятие резервных копий PostgreSQL.

Каждая копия состоит из двух файлов:

* SQL-файл, который восстанавливается обычным ``psql -f``;
* снимок состояния — сжатый JSON с хэшем каждой строки каждой таблицы,
  сгруппированным по первичному ключу.

Полная копия — это ``pg_dump``. Инкрементальная и дифференциальная копии
сравнивают текущее состояние базы со снимком базовой копии и выгружают
разницу: новые и изменённые строки как upsert-ы, исчезнувшие — как DELETE.
Колонки вида ``updated_at`` для этого не нужны, поэтому в дельты попадают
таблицы любых схем, и удаления тоже.

Модуль не зависит от моделей Django: на вход — параметры подключения и
пути к локальным файлам, на выход — файлы или исключение ``BackupFailed``.
"""
import gzip
import json
import logging
import os
import subprocess
from collections import namedtuple
from contextlib import contextmanager

import psycopg2
from django.conf import settings

logger = logging.getLogger(__name__)

STATE_FORMAT = 1

# Сколько ключей подставлять в один запрос или один DELETE.
KEY_BATCH = 500

# Сколько строк за раз забирать серверным курсором.
FETCH_SIZE = 5000

# От этих настроек зависит текстовое представление значений: и хэши строк,
# и литералы в SQL-файле должны получаться одинаковыми при любых настройках
# сервера, иначе неизменённая строка выглядела бы изменённой.
SESSION_SETUP = (
    "SET TIME ZONE 'UTC';"
    "SET datestyle = 'ISO, YMD';"
    "SET intervalstyle = 'postgres';"
    "SET extra_float_digits = 1;"
    "SET bytea_output = 'hex';"
)

# Схемы, которые принадлежат самому PostgreSQL.
SYSTEM_SCHEMAS_SQL = (
    "n.nspname NOT IN ('pg_catalog', 'information_schema') "
    "AND n.nspname NOT LIKE 'pg\\_%'"
)

Table = namedtuple("Table", "oid schema name columns signature key")


class BackupFailed(Exception):
    """Копию снять не удалось; ``code`` объясняет почему."""

    CONNECTION = "connection"
    DUMP = "dump"
    SCHEMA_CHANGED = "schema_changed"

    def __init__(self, code, detail=""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


# --------------------------------------------------------------------------- #
#  Вспомогательные функции
# --------------------------------------------------------------------------- #
def _quote_identifier(name):
    """
    Безопасно экранирует идентификатор (имя таблицы/колонки) для SQL.

    Имена берутся из системного каталога PostgreSQL, поэтому достаточно
    стандартного экранирования двойными кавычками.
    """
    escaped = str(name).replace('"', '""')
    return f'"{escaped}"'


def _qualified(table):
    return f"{_quote_identifier(table.schema)}.{_quote_identifier(table.name)}"


def _state_key(table):
    return f"{table.schema}.{table.name}"


def _literal_list(columns, alias="t"):
    """SQL-выражение: значения колонок как готовые SQL-литералы через запятую.

    Экранирует сам PostgreSQL (``quote_nullable``), поэтому JSON, массивы,
    bytea, кавычки и NULL переносятся без форматирования в Python.
    """
    parts = ", ".join(f"quote_nullable({alias}.{_quote_identifier(c)})" for c in columns)
    return f"concat_ws(', ', {parts})"


def _batches(items, size=KEY_BATCH):
    items = list(items)
    for start in range(0, len(items), size):
        yield items[start:start + size]


def _topological_order(tables, parents):
    """Сортирует таблицы так, чтобы каждая шла после своих родителей.

    Таблицы, участвующие в цикле внешних ключей, дописываются в конец в
    исходном порядке: для них корректного порядка не существует.
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


def _key_match(table, key_literals):
    """Условие WHERE для набора ключей, уже записанных SQL-литералами."""
    columns = ", ".join(_quote_identifier(c) for c in table.key)
    values = ", ".join(f"({literal})" for literal in key_literals)
    return f"({columns}) IN ({values})"


# --------------------------------------------------------------------------- #
#  Подключение и чтение каталога
# --------------------------------------------------------------------------- #
@contextmanager
def _snapshot_connection(params):
    """Соединение в одной транзакции REPEATABLE READ только для чтения.

    Все выборки видят один снимок базы, иначе строки, изменённые между
    запросами к разным таблицам, дали бы несогласованную копию.
    """
    try:
        connection = psycopg2.connect(
            **params, connect_timeout=getattr(settings, "DB_CONNECT_TIMEOUT", 5)
        )
    except psycopg2.Error as exc:
        raise BackupFailed(BackupFailed.CONNECTION, str(exc).strip()) from exc
    try:
        connection.set_session(isolation_level="REPEATABLE READ", readonly=True)
        with connection.cursor() as cursor:
            cursor.execute(SESSION_SETUP)
        yield connection
    except psycopg2.Error as exc:
        raise BackupFailed(BackupFailed.DUMP, str(exc).strip()) from exc
    finally:
        connection.close()


def _discover_tables(connection):
    """Пользовательские таблицы всех схем в порядке, безопасном для вставки.

    Родительские таблицы идут раньше дочерних, иначе новая строка заказа
    при восстановлении сослалась бы на ещё не вставленного покупателя.
    Таблицы расширений пропускаются: их данные восстанавливает само расширение.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            SELECT c.oid, n.nspname, c.relname
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE c.relkind = 'r' AND {SYSTEM_SCHEMAS_SQL}
              AND NOT EXISTS (
                  SELECT 1 FROM pg_depend d
                  WHERE d.classid = 'pg_class'::regclass AND d.objid = c.oid
                    AND d.deptype = 'e'
              )
            ORDER BY n.nspname, c.relname;
            """
        )
        relations = cursor.fetchall()

        tables = {}
        for oid, schema, name in relations:
            cursor.execute(
                """
                SELECT a.attname, format_type(a.atttypid, a.atttypmod), a.attgenerated <> ''
                FROM pg_attribute a
                WHERE a.attrelid = %s AND a.attnum > 0 AND NOT a.attisdropped
                ORDER BY a.attnum;
                """,
                (oid,),
            )
            attributes = cursor.fetchall()
            cursor.execute(
                """
                SELECT a.attname
                FROM pg_index i
                JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY (i.indkey)
                WHERE i.indrelid = %s AND i.indisprimary
                ORDER BY array_position(i.indkey, a.attnum);
                """,
                (oid,),
            )
            key = [row[0] for row in cursor.fetchall()]
            tables[oid] = Table(
                oid=oid,
                schema=schema,
                name=name,
                columns=[name_ for name_, _type, generated in attributes if not generated],
                signature=[f"{name_} {type_}" for name_, type_, _generated in attributes],
                key=key,
            )

        cursor.execute("SELECT conrelid, confrelid FROM pg_constraint WHERE contype = 'f';")
        parents = {oid: set() for oid in tables}
        for child, parent in cursor.fetchall():
            if child in parents and parent in parents and child != parent:
                parents[child].add(parent)

    return [tables[oid] for oid in _topological_order(list(tables), parents)]


def _row_hashes(connection, table):
    """Хэш каждой строки таблицы по её первичному ключу (литералом)."""
    hashes = {}
    with connection.cursor(name=f"hashes_{table.oid}") as cursor:
        cursor.itersize = FETCH_SIZE
        cursor.execute(
            f"SELECT {_literal_list(table.key)}, md5(t::text) FROM {_qualified(table)} t;"
        )
        for key, digest in cursor:
            hashes[key] = digest
    return hashes


def _table_hash(connection, table):
    """Один хэш на всё содержимое таблицы без первичного ключа."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT md5(coalesce(string_agg(h, ',' ORDER BY h), '')) "
            f"FROM (SELECT md5(t::text) AS h FROM {_qualified(table)} t) AS s;"
        )
        return cursor.fetchone()[0]


def _table_state(connection, table):
    state = {"columns": table.signature, "key": table.key}
    if table.key:
        state["rows"] = _row_hashes(connection, table)
    else:
        state["hash"] = _table_hash(connection, table)
    return state


def _write_state(path, tables):
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        json.dump({"format": STATE_FORMAT, "tables": tables}, handle, separators=(",", ":"))


def read_state(path):
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        state = json.load(handle)
    if state.get("format") != STATE_FORMAT:
        raise BackupFailed(BackupFailed.SCHEMA_CHANGED, "unsupported state format")
    return state["tables"]


# --------------------------------------------------------------------------- #
#  Полная копия
# --------------------------------------------------------------------------- #
def full_backup(params, sql_path, state_path):
    """
    Полная копия: ``pg_dump`` и снимок состояния из одного снимка транзакции.

    ``pg_dump`` получает экспортированный снимок нашей транзакции
    (``--snapshot``), поэтому дамп и хэши строк описывают одно и то же
    состояние базы, даже если в неё пишут во время копирования.
    """
    with _snapshot_connection(params) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_export_snapshot();")
            snapshot = cursor.fetchone()[0]

        process = _start_pg_dump(params, sql_path, snapshot)
        try:
            tables = {
                _state_key(table): _table_state(connection, table)
                for table in _discover_tables(connection)
            }
            _wait_pg_dump(process)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()

    _write_state(state_path, tables)
    logger.info("Полная копия сохранена: %s", sql_path)
    return {"tables": len(tables)}


def _start_pg_dump(params, sql_path, snapshot):
    command = [
        settings.PG_DUMP_PATH,
        f"--host={params['host']}",
        f"--port={params['port']}",
        f"--username={params['user']}",
        f"--dbname={params['dbname']}",
        f"--snapshot={snapshot}",
        # Без флага pg_dump при отказе в аутентификации ждёт ввода пароля
        # с терминала, и задача повисает до таймаута.
        "--no-password",
        f"--file={sql_path}",
    ]
    env = os.environ.copy()
    env["PGPASSWORD"] = params["password"]
    try:
        return subprocess.Popen(
            command, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
    except FileNotFoundError as exc:
        raise BackupFailed(BackupFailed.DUMP, f"pg_dump not found: {settings.PG_DUMP_PATH}") from exc


def _wait_pg_dump(process):
    try:
        _stdout, stderr = process.communicate(timeout=settings.PG_DUMP_TIMEOUT)
    except subprocess.TimeoutExpired as exc:
        raise BackupFailed(
            BackupFailed.DUMP, f"pg_dump exceeded {settings.PG_DUMP_TIMEOUT} s"
        ) from exc
    if process.returncode != 0:
        raise BackupFailed(BackupFailed.DUMP, (stderr or "").strip())


# --------------------------------------------------------------------------- #
#  Инкрементальная и дифференциальная копии
# --------------------------------------------------------------------------- #
def _schema_differences(base_tables, tables):
    """Чем схема базы отличается от схемы на момент базовой копии."""
    current = {_state_key(t): t.signature for t in tables}
    differences = sorted(
        [f"new table {name}" for name in current.keys() - base_tables.keys()]
        + [f"dropped table {name}" for name in base_tables.keys() - current.keys()]
        + [
            f"changed columns in {name}"
            for name in current.keys() & base_tables.keys()
            if current[name] != base_tables[name]["columns"]
        ]
    )
    return differences


def delta_backup(params, base_state_path, sql_path, state_path, *, label):
    """
    Дельта относительно снимка состояния базовой копии.

    Для таблиц с первичным ключом строки сравниваются по хэшу: новые и
    изменённые выгружаются upsert-ами, исчезнувшие — DELETE. Таблица без
    первичного ключа при любом изменении переписывается целиком. В конце
    выставляются значения последовательностей, чтобы новые строки после
    восстановления не получали уже занятые идентификаторы.

    Схема должна совпадать со схемой базовой копии: новые таблицы и колонки
    переносит только полная копия.

    :param label: вид копии для заголовка файла (``incremental`` и т. п.)
    :raises BackupFailed: нет соединения, схема изменилась, ошибка SQL
    :return: статистика: сколько строк изменено и удалено
    """
    base_tables = read_state(base_state_path)
    stats = {"changed": 0, "deleted": 0, "tables": 0}

    with _snapshot_connection(params) as connection:
        tables = _discover_tables(connection)
        differences = _schema_differences(base_tables, tables)
        if differences:
            raise BackupFailed(BackupFailed.SCHEMA_CHANGED, "; ".join(differences))

        new_state, deletions, touched = {}, {}, []
        with open(sql_path, "w", encoding="utf-8") as out:
            out.write(
                f"-- DataStudio {label} backup of {params['dbname']}\n"
                "-- Apply with psql on top of the restored base backup.\n"
                "BEGIN;\n"
            )

            # Upsert-ы — в порядке «родители раньше детей».
            for table in tables:
                base = base_tables[_state_key(table)]
                state = _table_state(connection, table)
                new_state[_state_key(table)] = state

                if table.key:
                    changed = [k for k, h in state["rows"].items() if base["rows"].get(k) != h]
                    removed = base["rows"].keys() - state["rows"].keys()
                    if removed:
                        deletions[table.oid] = (table, sorted(removed))
                    if changed:
                        touched.append(_disable_triggers(out, table))
                        stats["changed"] += _write_upserts(connection, out, table, changed)
                elif state["hash"] != base["hash"]:
                    touched.append(_disable_triggers(out, table))
                    out.write(f"DELETE FROM {_qualified(table)};\n")
                    stats["changed"] += _write_all_rows(connection, out, table)

            # Удаления — в обратном порядке: сначала дети, потом родители.
            for table in reversed(tables):
                if table.oid not in deletions:
                    continue
                _table, keys = deletions[table.oid]
                if table not in touched:
                    touched.append(_disable_triggers(out, table))
                for batch in _batches(keys):
                    out.write(f"DELETE FROM {_qualified(table)} WHERE {_key_match(table, batch)};\n")
                stats["deleted"] += len(keys)

            for table in touched:
                out.write(f"ALTER TABLE {_qualified(table)} ENABLE TRIGGER USER;\n")
            _write_sequences(connection, out)
            out.write(
                f"-- rows changed: {stats['changed']}, rows deleted: {stats['deleted']}\n"
                "COMMIT;\n"
            )
        stats["tables"] = len(touched)

    _write_state(state_path, new_state)
    logger.info("Копия %s сохранена: %s (%s)", label, sql_path, stats)
    return stats


def _disable_triggers(out, table):
    # Копия воспроизводит значения как есть: пользовательские триггеры
    # (например, «обнови updated_at») при восстановлении исказили бы данные.
    # Триггеры внешних ключей не затрагиваются.
    out.write(f"ALTER TABLE {_qualified(table)} DISABLE TRIGGER USER;\n")
    return table


def _insert_prefix(table):
    column_list = ", ".join(_quote_identifier(c) for c in table.columns)
    return f"INSERT INTO {_qualified(table)} ({column_list}) VALUES "


def _write_upserts(connection, out, table, keys):
    prefix = _insert_prefix(table)
    suffix = _upsert_suffix(table.columns, table.key)
    written = 0
    with connection.cursor() as cursor:
        for batch in _batches(keys):
            cursor.execute(
                f"SELECT {_literal_list(table.columns)} FROM {_qualified(table)} t "
                f"WHERE {_key_match(table, batch)};"
            )
            for (values,) in cursor.fetchall():
                out.write(f"{prefix}({values}){suffix};\n")
                written += 1
    return written


def _write_all_rows(connection, out, table):
    prefix = _insert_prefix(table)
    written = 0
    with connection.cursor(name=f"rows_{table.oid}") as cursor:
        cursor.itersize = FETCH_SIZE
        cursor.execute(f"SELECT {_literal_list(table.columns)} FROM {_qualified(table)} t;")
        for (values,) in cursor:
            out.write(f"{prefix}({values});\n")
            written += 1
    return written


def _write_sequences(connection, out):
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT format('SELECT pg_catalog.setval(%L, %s, true);',
                          quote_ident(schemaname) || '.' || quote_ident(sequencename),
                          last_value)
            FROM pg_sequences
            WHERE last_value IS NOT NULL
              AND schemaname NOT IN ('pg_catalog', 'information_schema')
            ORDER BY schemaname, sequencename;
            """
        )
        for (statement,) in cursor.fetchall():
            out.write(statement + "\n")
