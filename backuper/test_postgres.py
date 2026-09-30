"""
Интеграционные тесты на настоящем PostgreSQL и настоящем pg_dump.

Проверяют главное обещание приложения: полная копия плюс дельта
восстанавливают базу в актуальное состояние. Запускаются, только если задана
переменная ``INTEGRATION_DATABASE_URL`` с правами на создание баз, например:

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

from backuper.utils import differential_db_backup, full_db_backup, incremental_db_backup

DSN = os.environ.get("INTEGRATION_DATABASE_URL")

SCHEMA = """
CREATE TABLE customers (
    id serial PRIMARY KEY,
    name text NOT NULL,
    profile jsonb,
    tags text[],
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE orders (
    id serial PRIMARY KEY,
    customer_id integer NOT NULL REFERENCES customers (id),
    total numeric(10, 2) NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
-- Триггер «обнови updated_at», как в типичной прикладной базе: при
-- восстановлении он не должен перезаписывать время из копии.
CREATE FUNCTION touch() RETURNS trigger AS $$
BEGIN NEW.updated_at := clock_timestamp(); RETURN NEW; END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER customers_touch BEFORE UPDATE ON customers
    FOR EACH ROW EXECUTE FUNCTION touch();
INSERT INTO customers (name, profile, tags) VALUES
    ('Анна', '{"vip": true}', '{a,b}'),
    ('O''Brien', NULL, NULL);
INSERT INTO orders (customer_id, total) VALUES (1, 100.50), (2, 20);
"""


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
        for name in (cls.SOURCE, cls.TARGET):
            cls._admin(f'DROP DATABASE IF EXISTS "{name}"')
            cls._admin(f'CREATE DATABASE "{name}"')
        cls._execute(cls.SOURCE, SCHEMA)

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
    def _execute(cls, database, sql, fetch=False):
        connection = psycopg2.connect(dbname=database, **cls.conn_args)
        try:
            with connection, connection.cursor() as cursor:
                cursor.execute(sql)
                return cursor.fetchall() if fetch else None
        finally:
            connection.close()

    def setUp(self):
        # каждый тест восстанавливает копии в пустую базу
        self._admin(f'DROP DATABASE IF EXISTS "{self.TARGET}"')
        self._admin(f'CREATE DATABASE "{self.TARGET}"')
        self.media_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.media_dir, ignore_errors=True)
        override = override_settings(MEDIA_DIR=self.media_dir, PG_DUMP_PATH="pg_dump")
        override.enable()
        self.addCleanup(override.disable)

    def _args(self):
        a = self.conn_args
        return a["host"], a["port"], a["user"], a["password"], self.SOURCE

    def _restore(self, name):
        env = dict(os.environ, PGPASSWORD=self.conn_args["password"])
        subprocess.run(
            [
                "psql", "--quiet", "--set=ON_ERROR_STOP=1",
                f"--host={self.conn_args['host']}", f"--port={self.conn_args['port']}",
                f"--username={self.conn_args['user']}", f"--dbname={self.TARGET}",
                f"--file={os.path.join(self.media_dir, name + '.sql')}",
            ],
            check=True, env=env, capture_output=True,
        )

    def _snapshot(self, database):
        return self._execute(
            database,
            "SELECT c.id, c.name, c.profile::text, c.tags, c.updated_at, o.id, o.total "
            "FROM customers c LEFT JOIN orders o ON o.customer_id = c.id "
            "ORDER BY c.id, o.id",
            fetch=True,
        )

    def test_full_plus_deltas_restore_current_state(self):
        self.assertTrue(full_db_backup(*self._args(), "full"))
        (full_at,) = self._execute(self.SOURCE, "SELECT clock_timestamp()", fetch=True)[0]

        # изменения после полной копии: правка, новая строка с внешним ключом
        self._execute(self.SOURCE, """
            UPDATE customers SET name = 'Анна К.', profile = '{"vip": false}' WHERE id = 1;
            INSERT INTO customers (name, tags) VALUES ('Новый', '{"x,y"}');
            INSERT INTO orders (customer_id, total) VALUES (3, 7.25);
        """)
        self.assertTrue(incremental_db_backup(*self._args(), full_at, "incr"))
        (incr_at,) = self._execute(self.SOURCE, "SELECT clock_timestamp()", fetch=True)[0]

        self._execute(self.SOURCE, """
            UPDATE customers SET name = 'O''Brien Jr.' WHERE id = 2;
        """)
        self.assertTrue(incremental_db_backup(*self._args(), incr_at, "incr2"))
        self.assertTrue(differential_db_backup(*self._args(), full_at, "diff"))

        expected = self._snapshot(self.SOURCE)

        # цепочка: полная + все инкрементальные
        self._restore("full")
        self._restore("incr")
        self._restore("incr2")
        self.assertEqual(self._snapshot(self.TARGET), expected)

        # дифференциальная поверх уже восстановленной базы идемпотентна
        self._restore("diff")
        self.assertEqual(self._snapshot(self.TARGET), expected)

    def test_differential_restores_on_top_of_full(self):
        self.assertTrue(full_db_backup(*self._args(), "full2"))
        (full_at,) = self._execute(self.SOURCE, "SELECT clock_timestamp()", fetch=True)[0]
        self._execute(self.SOURCE, """
            INSERT INTO customers (name) VALUES ('Diff');
            INSERT INTO orders (customer_id, total)
            SELECT id, 1 FROM customers WHERE name = 'Diff';
        """)
        self.assertTrue(differential_db_backup(*self._args(), full_at, "diff2"))

        self._restore("full2")
        self._restore("diff2")
        self.assertEqual(self._snapshot(self.TARGET), self._snapshot(self.SOURCE))

    def test_wrong_password_fails_fast(self):
        host, port, user, _password, db = self._args()
        self.assertFalse(full_db_backup(host, port, user, "definitely-wrong", db, "bad"))
