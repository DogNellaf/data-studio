"""
Снятие резервных копий PostgreSQL.

Каждая копия состоит из двух файлов:

* SQL-файл, который восстанавливается обычным ``psql -f``;
* снимок состояния — zip-архив, где для каждой таблицы лежит
  отсортированный поток хэшей строк: по первичному ключу, а для таблиц без
  ключа — хэш строки с числом её повторов.

Полная копия — это ``pg_dump``. Инкрементальная и дифференциальная копии
сливают отсортированный поток текущих хэшей с потоком из снимка базовой
копии (как merge join в СУБД) и выгружают разницу: новые и изменённые строки
как upsert-ы или вставки, исчезнувшие — как DELETE. Память не зависит от
размера таблиц: в ней держится только очередная пачка ключей.

Модуль не зависит от моделей Django: на вход — параметры подключения и
пути к локальным файлам, на выход — файлы или исключение ``BackupFailed``.
"""
import io
import json
import logging
import os
import subprocess
import tempfile
import zipfile
from collections import namedtuple
from contextlib import contextmanager

import psycopg2
from django.conf import settings

logger = logging.getLogger(__name__)

STATE_FORMAT = 2

# Сколько ключей подставлять в один запрос или один DELETE.
KEY_BATCH = 500

# Сколько строк за раз забирать серверным курсором.
FETCH_SIZE = 5000

# От этих настроек зависит текстовое представление значений: и хэши строк,
# и литералы в SQL-файле должны получаться одинаковыми при любых настройках
# сервера, иначе неизменённая строка выглядела бы изменённой. Те же настройки
# ставятся в начале дельты: удаление строк таблицы без ключа ищет их по хэшу
# уже в восстанавливаемой базе.
SESSION_SETTINGS = (
    ("TIME ZONE", "'UTC'"),
    ("datestyle", "'ISO, YMD'"),
    ("intervalstyle", "'postgres'"),
    ("extra_float_digits", "1"),
    ("bytea_output", "'hex'"),
)
SESSION_SETUP = "".join(
    f"SET {name} {'' if name == 'TIME ZONE' else '= '}{value};" for name, value in SESSION_SETTINGS
)
SESSION_SETUP_LOCAL = "".join(
    f"SET LOCAL {name} {'' if name == 'TIME ZONE' else '= '}{value};\n"
    for name, value in SESSION_SETTINGS
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
    # Дельту снять нельзя, но полная копия всё исправит: изменилась схема
    # или снимок базовой копии в старом формате.
    NEEDS_FULL = "needs_full"

    def __init__(self, code, detail="", reason=""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail
        # Для NEEDS_FULL: почему дельта невозможна (schema_changed, old_base).
        self.reason = reason


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


# --------------------------------------------------------------------------- #
#  Снимок состояния
# --------------------------------------------------------------------------- #
class StateWriter:
    """Пишет снимок состояния потоково, по таблице за раз."""

    def __init__(self, path):
        self._zip = zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED)
        self._tables = {}

    @contextmanager
    def table(self, table):
        entry = f"t{len(self._tables):05d}.jsonl"
        self._tables[_state_key(table)] = {
            "columns": table.signature, "key": table.key, "entry": entry,
        }
        with self._zip.open(entry, "w") as raw, io.TextIOWrapper(raw, encoding="utf-8") as text:
            yield lambda record: text.write(json.dumps(record, ensure_ascii=False) + "\n")

    def close(self):
        meta = {"format": STATE_FORMAT, "tables": self._tables}
        self._zip.writestr("meta.json", json.dumps(meta))
        self._zip.close()


class StateReader:
    """Читает снимок состояния; таблицы — отсортированными потоками."""

    def __init__(self, path):
        try:
            self._zip = zipfile.ZipFile(path)
            meta = json.loads(self._zip.read("meta.json"))
        except (zipfile.BadZipFile, KeyError, ValueError) as exc:
            # Снимки первого формата (gzip JSON целиком) дельтами не читаются.
            raise BackupFailed(
                BackupFailed.NEEDS_FULL, "unsupported state file", reason="old_base"
            ) from exc
        if meta.get("format") != STATE_FORMAT:
            raise BackupFailed(
                BackupFailed.NEEDS_FULL, "unsupported state format", reason="old_base"
            )
        self.tables = meta["tables"]

    def records(self, name):
        with self._zip.open(self.tables[name]["entry"]) as raw:
            for line in io.TextIOWrapper(raw, encoding="utf-8"):
                yield tuple(json.loads(line))

    def close(self):
        self._zip.close()


def _scan(connection, table):
    """Отсортированный поток текущего состояния таблицы.

    Для таблицы с ключом: (хэш ключа, ключ литералом, хэш строки). Сортировка
    по md5 ключа не зависит от кодировки и правил сортировки сервера и
    совпадает с порядком сравнения строк в Python.
    Для таблицы без ключа: (хэш строки, число таких строк).
    """
    relation = _qualified(table)
    if table.key:
        key = _literal_list(table.key)
        query = (
            f"SELECT md5({key}), {key}, md5(t::text) FROM {relation} t "
            f'ORDER BY md5({key}) COLLATE "C", ({key}) COLLATE "C";'
        )
    else:
        query = (
            f"SELECT h, count(*) FROM (SELECT md5(t::text) AS h FROM {relation} t) AS s "
            'GROUP BY h ORDER BY h COLLATE "C";'
        )
    with connection.cursor(name=f"scan_{table.oid}") as cursor:
        cursor.itersize = FETCH_SIZE
        cursor.execute(query)
        for row in cursor:
            yield tuple(row)


def _merge(base, current, width):
    """Слияние двух отсортированных потоков по первым ``width`` полям.

    Выдаёт (запись базы или None, текущая запись или None) для каждого ключа.
    """
    base, current = iter(base), iter(current)
    b, c = next(base, None), next(current, None)
    while b is not None or c is not None:
        if c is None or (b is not None and b[:width] < c[:width]):
            yield b, None
            b = next(base, None)
        elif b is None or c[:width] < b[:width]:
            yield None, c
            c = next(current, None)
        else:
            yield b, c
            b, c = next(base, None), next(current, None)


def _record_state(connection, table, state):
    with state.table(table) as record:
        for row in _scan(connection, table):
            record(row)


# --------------------------------------------------------------------------- #
#  Полная копия
# --------------------------------------------------------------------------- #
def full_backup(params, sql_path, state_path):
    """
    Полная копия: ``pg_dump`` и снимок состояния из одного снимка транзакции.

    ``pg_dump`` получает экспортированный снимок нашей транзакции
    (``--snapshot``), поэтому дамп и хэши строк описывают одно и то же
    состояние базы, даже если в неё пишут во время копирования. Хэши
    считаются параллельно с работой ``pg_dump``.
    """
    with _snapshot_connection(params) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_export_snapshot();")
            snapshot = cursor.fetchone()[0]

        process = _start_pg_dump(params, sql_path, snapshot)
        try:
            state = StateWriter(state_path)
            tables = _discover_tables(connection)
            for table in tables:
                _record_state(connection, table, state)
            state.close()
            _wait_pg_dump(process)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()

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


class _DeltaWriter:
    """SQL-файл дельты: upsert-ы по ходу сканирования, удаления — в конце."""

    def __init__(self, connection, out, workdir):
        self.connection = connection
        self.out = out
        self.workdir = workdir
        self.touched = []
        self.deletions = {}
        self.stats = {"changed": 0, "deleted": 0, "tables": 0}

    def touch(self, table):
        # Копия воспроизводит значения как есть: пользовательские триггеры
        # (например, «обнови updated_at») при восстановлении исказили бы
        # данные. Триггеры внешних ключей не затрагиваются.
        if table.oid not in {t.oid for t in self.touched}:
            self.out.write(f"ALTER TABLE {_qualified(table)} DISABLE TRIGGER USER;\n")
            self.touched.append(table)

    # --- таблицы с первичным ключом -------------------------------------- #
    def keyed(self, table, base_records, record):
        pending = []
        for base, current in _merge(base_records, _scan(self.connection, table), width=2):
            if current is not None:
                record(current)
                if base is None or base[2] != current[2]:
                    pending.append(current[1])
                    if len(pending) >= KEY_BATCH:
                        self._write_upserts(table, pending)
                        pending = []
            else:
                self._defer_delete(table, base[1])
        if pending:
            self._write_upserts(table, pending)

    def _write_upserts(self, table, keys):
        self.touch(table)
        prefix = _insert_prefix(table)
        suffix = _upsert_suffix(table.columns, table.key)
        with self.connection.cursor() as cursor:
            cursor.execute(
                f"SELECT {_literal_list(table.columns)} FROM {_qualified(table)} t "
                f"WHERE {_key_match(table, keys)};"
            )
            for (values,) in cursor.fetchall():
                self.out.write(f"{prefix}({values}){suffix};\n")
                self.stats["changed"] += 1

    # --- таблицы без первичного ключа ------------------------------------ #
    def unkeyed(self, table, base_records, record):
        """Строки без ключа сравниваются как мультимножество хэшей."""
        pending = {}
        for base, current in _merge(base_records, _scan(self.connection, table), width=1):
            if current is not None:
                record(current)
            have = base[1] if base else 0
            want = current[1] if current else 0
            digest = (current or base)[0]
            if want > have:
                pending[digest] = want - have
                if len(pending) >= KEY_BATCH:
                    self._write_inserts(table, pending)
                    pending = {}
            elif have > want:
                self._defer_delete(table, [digest, have - want])
        if pending:
            self._write_inserts(table, pending)

    def _write_inserts(self, table, counts):
        self.touch(table)
        prefix = _insert_prefix(table)
        digests = ", ".join(f"'{digest}'" for digest in counts)
        with self.connection.cursor() as cursor:
            cursor.execute(
                f"SELECT md5(t::text), {_literal_list(table.columns)} FROM {_qualified(table)} t "
                f"WHERE md5(t::text) IN ({digests});"
            )
            for digest, values in cursor.fetchall():
                if counts.get(digest, 0) > 0:
                    counts[digest] -= 1
                    self.out.write(f"{prefix}({values});\n")
                    self.stats["changed"] += 1

    # --- удаления --------------------------------------------------------- #
    def _defer_delete(self, table, item):
        """Удаления пишутся в конце, в обратном порядке таблиц; до тех пор
        они ждут во временном файле, а не в памяти."""
        if table.oid not in self.deletions:
            path = os.path.join(self.workdir, f"delete_{table.oid}.jsonl")
            self.deletions[table.oid] = (open(path, "w+", encoding="utf-8"), 0)
        handle, count = self.deletions[table.oid]
        handle.write(json.dumps(item, ensure_ascii=False) + "\n")
        self.deletions[table.oid] = (handle, count + 1)

    def write_deletions(self, tables):
        for table in reversed(tables):
            if table.oid not in self.deletions:
                continue
            handle, _count = self.deletions.pop(table.oid)
            handle.seek(0)
            self.touch(table)
            items = (json.loads(line) for line in handle)
            if table.key:
                batch = []
                for key in items:
                    batch.append(key)
                    if len(batch) >= KEY_BATCH:
                        self._delete_keys(table, batch)
                        batch = []
                if batch:
                    self._delete_keys(table, batch)
            else:
                relation = _qualified(table)
                for digest, count in items:
                    self.out.write(
                        f"DELETE FROM {relation} WHERE ctid IN (SELECT ctid FROM {relation} t "
                        f"WHERE md5(t::text) = '{digest}' LIMIT {int(count)});\n"
                    )
                    self.stats["deleted"] += int(count)
            handle.close()

    def _delete_keys(self, table, keys):
        self.out.write(f"DELETE FROM {_qualified(table)} WHERE {_key_match(table, keys)};\n")
        self.stats["deleted"] += len(keys)

    def finish(self):
        for table in self.touched:
            self.out.write(f"ALTER TABLE {_qualified(table)} ENABLE TRIGGER USER;\n")
        _write_sequences(self.connection, self.out)
        self.stats["tables"] = len(self.touched)


def delta_backup(params, base_state_path, sql_path, state_path, *, label):
    """
    Дельта относительно снимка состояния базовой копии.

    Для таблиц с первичным ключом строки сравниваются по хэшу: новые и
    изменённые выгружаются upsert-ами, исчезнувшие — DELETE по ключу. Для
    таблиц без ключа сравнивается мультимножество хэшей строк: недостающие
    строки вставляются, лишние удаляются по хэшу. В конце выставляются
    значения последовательностей, чтобы новые строки после восстановления не
    получали уже занятые идентификаторы.

    Схема должна совпадать со схемой базовой копии; если нет — бросается
    ``BackupFailed(NEEDS_FULL)``, и вызывающий код снимает полную копию.

    :param label: вид копии для заголовка файла (``incremental`` и т. п.)
    :raises BackupFailed: нет соединения, нужна полная копия, ошибка SQL
    :return: статистика: сколько строк изменено и удалено
    """
    base = StateReader(base_state_path)
    try:
        with _snapshot_connection(params) as connection:
            tables = _discover_tables(connection)
            differences = _schema_differences(base.tables, tables)
            if differences:
                raise BackupFailed(
                    BackupFailed.NEEDS_FULL, "; ".join(differences), reason="schema_changed"
                )

            state = StateWriter(state_path)
            with open(sql_path, "w", encoding="utf-8") as out, \
                    tempfile.TemporaryDirectory(prefix="delta-") as workdir:
                out.write(
                    f"-- DataStudio {label} backup of {params['dbname']}\n"
                    "-- Apply with psql on top of the restored base backup.\n"
                    "BEGIN;\n" + SESSION_SETUP_LOCAL
                )
                delta = _DeltaWriter(connection, out, workdir)
                # Вставки — в порядке «родители раньше детей».
                for table in tables:
                    records = base.records(_state_key(table))
                    with state.table(table) as record:
                        if table.key:
                            delta.keyed(table, records, record)
                        else:
                            delta.unkeyed(table, records, record)
                # Удаления — в обратном порядке: сначала дети, потом родители.
                delta.write_deletions(tables)
                delta.finish()
                out.write(
                    f"-- rows changed: {delta.stats['changed']}, "
                    f"rows deleted: {delta.stats['deleted']}\n"
                    "COMMIT;\n"
                )
            state.close()
    finally:
        base.close()

    logger.info("Копия %s сохранена: %s (%s)", label, sql_path, delta.stats)
    return delta.stats


def _insert_prefix(table):
    column_list = ", ".join(_quote_identifier(c) for c in table.columns)
    return f"INSERT INTO {_qualified(table)} ({column_list}) VALUES "


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
