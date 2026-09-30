"""
Интеграционные тесты на настоящем PostgreSQL и настоящем pg_dump.

Проверяют главное обещание приложения: полная копия плюс дельты
восстанавливают базу точно в текущее состояние — включая удалённые строки,
таблицы без колонок времени, таблицы без первичного ключа, другие схемы и
значения последовательностей. Запускаются, только если задана переменная
``INTEGRATION_DATABASE_URL`` с правами на создание баз, например:

    INTEGRATION_DATABASE_URL=postgres://postgres:postgres@localhost:5432/postgres \\
        python manage.py test backuper --settings=datastudio.settings_test
"""
import os
import shutil
import subprocess
import tempfile
import unittest
from urllib.parse import urlparse

import psycopg2
from django.test import SimpleTestCase, override_settings

from backuper.utils import BackupFailed, delta_backup, full_backup

DSN = os.environ.get("INTEGRATION_DATABASE_URL")

SCHEMA = """
CREATE TABLE customers (
    id serial PRIMARY KEY,
    name text NOT NULL,
    profile jsonb,
    tags text[],
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
-- Триггер «обнови updated_at», как в типичной прикладной базе: при
-- восстановлении он не должен перезаписывать время из копии.
CREATE FUNCTION touch() RETURNS trigger AS $$
BEGIN NEW.updated_at := clock_timestamp(); RETURN NEW; END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER customers_touch BEFORE UPDATE ON customers
    FOR EACH ROW EXECUTE FUNCTION touch();

-- Без колонок времени: раньше в дельты не попадала вовсе.
CREATE TABLE orders (
    id serial PRIMARY KEY,
    customer_id integer NOT NULL REFERENCES customers (id),
    total numeric(10, 2) NOT NULL,
    note bytea
);
-- Составной первичный ключ.
CREATE TABLE order_items (
    order_id integer NOT NULL REFERENCES orders (id),
    line smallint NOT NULL,
    sku text NOT NULL,
    PRIMARY KEY (order_id, line)
);
-- Без первичного ключа.
CREATE TABLE audit_log (message text NOT NULL, at timestamptz NOT NULL);
-- Другая схема.
CREATE SCHEMA billing;
CREATE TABLE billing.invoices (
    number text PRIMARY KEY,
    order_id integer NOT NULL REFERENCES public.orders (id),
    amount numeric(10, 2) NOT NULL
);

INSERT INTO customers (name, profile, tags) VALUES
    ('Анна', '{"vip": true}', '{a,b}'),
    ('O''Brien', NULL, NULL),
    ('Charlie', NULL, '{"x,y"}');
INSERT INTO orders (customer_id, total, note) VALUES
    (1, 100.50, '\\x00ff'), (2, 20, NULL), (3, 5, NULL);
INSERT INTO order_items VALUES (1, 1, 'A'), (1, 2, 'B'), (2, 1, 'C');
INSERT INTO audit_log VALUES ('created', '2026-01-01 00:00:00+00');
INSERT INTO billing.invoices VALUES ('INV-1', 1, 100.50), ('INV-2', 2, 20);
"""

SNAPSHOT_QUERIES = [
    "SELECT * FROM customers ORDER BY id",
    "SELECT * FROM orders ORDER BY id",
    "SELECT * FROM order_items ORDER BY order_id, line",
    "SELECT * FROM audit_log ORDER BY message, at",
    "SELECT * FROM billing.invoices ORDER BY number",
    "SELECT last_value FROM customers_id_seq",
    "SELECT last_value FROM orders_id_seq",
]


@unittest.skipUnless(DSN, "INTEGRATION_DATABASE_URL не задан")
class PostgresRoundTripTests(SimpleTestCase):
    SOURCE = "ds_it_source"
    TARGET = "ds_it_restore"

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        url = urlparse(DSN)
        cls.conn_args = {
            "host": url.hostname or "localhost",
            "port": url.port or 5432,
            "user": url.username,
            "password": url.password or "",
        }
        cls.admin_db = url.path.lstrip("/") or "postgres"

    def setUp(self):
        # каждый тест начинает с чистой исходной базы и пустой целевой
        for name in (self.SOURCE, self.TARGET):
            self._admin(f'DROP DATABASE IF EXISTS "{name}"')
            self._admin(f'CREATE DATABASE "{name}"')
        self._execute(self.SOURCE, SCHEMA)
        self.workdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.workdir, ignore_errors=True)
        override = override_settings(PG_DUMP_PATH="pg_dump")
        override.enable()
        self.addCleanup(override.disable)

    @classmethod
    def tearDownClass(cls):
        for name in (cls.SOURCE, cls.TARGET):
            cls._admin(f'DROP DATABASE IF EXISTS "{name}"')
        super().tearDownClass()

    @classmethod
    def _admin(cls, sql):
        connection = psycopg2.connect(dbname=cls.admin_db, **cls.conn_args)
        connection.autocommit = True
        try:
            with connection.cursor() as cursor:
                cursor.execute(sql)
        finally:
            connection.close()

    @classmethod
    def _execute(cls, database, sql):
        connection = psycopg2.connect(dbname=database, **cls.conn_args)
        try:
            with connection, connection.cursor() as cursor:
                cursor.execute(sql)
        finally:
            connection.close()

    def _snapshot(self, database):
        connection = psycopg2.connect(dbname=database, **self.conn_args)
        try:
            with connection.cursor() as cursor:
                result = []
                for query in SNAPSHOT_QUERIES:
                    cursor.execute(query)
                    result.append(cursor.fetchall())
                return result
        finally:
            connection.close()

    def params(self):
        return {**self.conn_args, "dbname": self.SOURCE}

    def path(self, name):
        return os.path.join(self.workdir, name)

    def full(self, name):
        full_backup(self.params(), self.path(f"{name}.sql"), self.path(f"{name}.state"))

    def delta(self, base, name):
        return delta_backup(
            self.params(), self.path(f"{base}.state"), self.path(f"{name}.sql"),
            self.path(f"{name}.state"), label="test",
        )

    def restore(self, *names):
        env = dict(os.environ, PGPASSWORD=self.conn_args["password"])
        for name in names:
            subprocess.run(
                [
                    "psql", "--quiet", "--set=ON_ERROR_STOP=1",
                    f"--host={self.conn_args['host']}", f"--port={self.conn_args['port']}",
                    f"--username={self.conn_args['user']}", f"--dbname={self.TARGET}",
                    f"--file={self.path(name + '.sql')}",
                ],
                check=True, env=env, capture_output=True,
            )

    # ------------------------------------------------------------------ #
    def test_incremental_chain_restores_every_kind_of_change(self):
        self.full("full")

        self._execute(self.SOURCE, """
            -- обновление через триггер updated_at
            UPDATE customers SET name = 'Анна К.', profile = '{"vip": false}' WHERE id = 1;
            -- обновление в таблице без колонок времени
            UPDATE orders SET total = 99.99, note = '\\xdeadbeef' WHERE id = 1;
            -- новые строки с внешними ключами
            INSERT INTO customers (name) VALUES ('Новый');
            INSERT INTO orders (customer_id, total) VALUES (4, 7.25);
            INSERT INTO order_items VALUES (4, 1, 'D');
            -- удаление с каскадом по цепочке внешних ключей
            DELETE FROM billing.invoices WHERE order_id = 3;
            DELETE FROM order_items WHERE order_id = 2;
            DELETE FROM orders WHERE id = 3;
            DELETE FROM customers WHERE id = 3;
            -- таблица без первичного ключа
            INSERT INTO audit_log VALUES ('changed', '2026-02-01 00:00:00+00');
        """)
        stats = self.delta("full", "incr1")
        self.assertGreater(stats["deleted"], 0)

        self._execute(self.SOURCE, """
            UPDATE customers SET name = 'O''Brien Jr.' WHERE id = 2;
            UPDATE billing.invoices SET amount = 21 WHERE number = 'INV-2';
            DELETE FROM audit_log WHERE message = 'created';
            -- значение последовательности уходит вперёд без новых строк
            SELECT nextval('orders_id_seq');
        """)
        self.delta("incr1", "incr2")

        self.restore("full", "incr1", "incr2")
        self.assertEqual(self._snapshot(self.TARGET), self._snapshot(self.SOURCE))

    def test_differential_restores_on_top_of_full(self):
        self.full("full")
        self._execute(self.SOURCE, """
            INSERT INTO customers (name) VALUES ('Diff');
            DELETE FROM order_items WHERE order_id = 1 AND line = 2;
            UPDATE audit_log SET message = 'edited';
        """)
        self.delta("full", "diff1")
        self._execute(self.SOURCE, "DELETE FROM billing.invoices;")
        self.delta("full", "diff2")

        # для восстановления достаточно полной и последней дифференциальной
        self.restore("full", "diff2")
        self.assertEqual(self._snapshot(self.TARGET), self._snapshot(self.SOURCE))

    def test_unchanged_database_gives_empty_delta(self):
        self.full("full")
        stats = self.delta("full", "same")
        self.assertEqual(stats, {"changed": 0, "deleted": 0, "tables": 0})
        self.restore("full", "same")
        self.assertEqual(self._snapshot(self.TARGET), self._snapshot(self.SOURCE))

    def test_schema_change_requires_new_full_backup(self):
        self.full("full")
        self._execute(self.SOURCE, "ALTER TABLE orders ADD COLUMN paid boolean;")
        with self.assertRaises(BackupFailed) as ctx:
            self.delta("full", "broken")
        self.assertEqual(ctx.exception.code, BackupFailed.SCHEMA_CHANGED)
        self.assertIn("orders", ctx.exception.detail)

    def test_wrong_password_is_a_connection_error(self):
        params = {**self.params(), "password": "definitely-wrong"}
        with self.assertRaises(BackupFailed) as ctx:
            full_backup(params, self.path("bad.sql"), self.path("bad.state"))
        self.assertEqual(ctx.exception.code, BackupFailed.CONNECTION)
