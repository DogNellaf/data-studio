import os
import shutil
import subprocess
import tempfile
from unittest.mock import MagicMock, patch

import psycopg2
from django.test import SimpleTestCase, override_settings

from backuper.utils import (
    _quote_identifier,
    _topological_order,
    _upsert_suffix,
    differential_db_backup,
    full_db_backup,
    incremental_db_backup,
)


class HelperTests(SimpleTestCase):
    def test_quote_identifier(self):
        self.assertEqual(_quote_identifier("users"), '"users"')

    def test_quote_identifier_escapes(self):
        self.assertEqual(_quote_identifier('we"ird'), '"we""ird"')

    def test_upsert_suffix_updates_non_key_columns(self):
        self.assertEqual(
            _upsert_suffix(["id", "name"], ["id"]),
            ' ON CONFLICT ("id") DO UPDATE SET "name" = EXCLUDED."name"',
        )

    def test_upsert_suffix_key_only_table(self):
        self.assertEqual(_upsert_suffix(["a", "b"], ["a", "b"]), ' ON CONFLICT ("a", "b") DO NOTHING')

    def test_upsert_suffix_without_primary_key(self):
        self.assertEqual(_upsert_suffix(["a"], []), "")

    def test_topological_order_parents_first(self):
        parents = {"order_items": {"orders", "products"}, "orders": {"customers"},
                   "customers": set(), "products": set()}
        order = _topological_order(sorted(parents), parents)
        self.assertLess(order.index("customers"), order.index("orders"))
        self.assertLess(order.index("orders"), order.index("order_items"))
        self.assertLess(order.index("products"), order.index("order_items"))

    def test_topological_order_survives_cycles(self):
        parents = {"a": {"b"}, "b": {"a"}, "c": set()}
        self.assertEqual(_topological_order(["a", "b", "c"], parents), ["c", "a", "b"])


class MediaDirTestCase(SimpleTestCase):
    """Базовый класс: временный каталог под резервные копии."""

    def setUp(self):
        self.media_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.media_dir, ignore_errors=True)
        override = override_settings(MEDIA_DIR=self.media_dir, PG_DUMP_PATH="pg_dump")
        override.enable()
        self.addCleanup(override.disable)


class FullDbBackupTests(MediaDirTestCase):
    @patch("backuper.utils.subprocess.run")
    def test_success(self, mock_run):
        result = full_db_backup("127.0.0.1", "5432", "u", "p", "shop", "backup1")
        self.assertTrue(result)
        mock_run.assert_called_once()
        command = mock_run.call_args.args[0]
        self.assertEqual(command[0], "pg_dump")
        self.assertIn("--dbname=shop", command)
        self.assertIn("--no-password", command)
        self.assertIn("timeout", mock_run.call_args.kwargs)
        self.assertTrue(command[-1].endswith(os.path.join("backup1.sql")))
        # пароль передаётся через переменную окружения, а не в командной строке
        self.assertEqual(mock_run.call_args.kwargs["env"]["PGPASSWORD"], "p")

    @patch("backuper.utils.subprocess.run",
           side_effect=subprocess.CalledProcessError(1, "pg_dump", stderr="boom"))
    def test_called_process_error(self, _mock_run):
        self.assertFalse(full_db_backup("h", "5432", "u", "p", "db", "backup2"))

    @patch("backuper.utils.subprocess.run",
           side_effect=subprocess.TimeoutExpired("pg_dump", 1))
    def test_timeout(self, _mock_run):
        self.assertFalse(full_db_backup("h", "5432", "u", "p", "db", "backup5"))

    @patch("backuper.utils.subprocess.run", side_effect=FileNotFoundError())
    def test_pg_dump_not_found(self, _mock_run):
        self.assertFalse(full_db_backup("h", "5432", "u", "p", "db", "backup3"))

    @patch("backuper.utils.subprocess.run", side_effect=ValueError("unexpected"))
    def test_unexpected_error(self, _mock_run):
        self.assertFalse(full_db_backup("h", "5432", "u", "p", "db", "backup4"))


class ExportChangesTests(MediaDirTestCase):
    def _make_connection(self, fetch_results):
        connection = MagicMock()
        cursor = MagicMock()
        cursor.fetchall.side_effect = fetch_results
        connection.cursor.return_value.__enter__.return_value = cursor
        return connection, cursor

    @patch("backuper.utils.psycopg2.connect")
    def test_writes_upsert_statements(self, mock_connect):
        connection, cursor = self._make_connection([
            [("logs",), ("users",)],                    # список таблиц
            [],                                         # внешних ключей нет
            [("id",), ("note",)],                       # колонки logs — без времени
            [("id",), ("name",), ("updated_at",)],      # колонки users
            [("id",)],                                  # первичный ключ users
            [("'1', 'O''Brien', NULL",)],               # изменённые строки users
        ])
        mock_connect.return_value = connection

        result = incremental_db_backup(
            "h", "5432", "u", "p", "db", "2024-01-01 00:00:00", "backup10"
        )
        self.assertTrue(result)
        connection.close.assert_called_once()
        # все выборки делаются в одном согласованном снимке
        connection.set_session.assert_called_once_with(
            isolation_level="REPEATABLE READ", readonly=True
        )

        with open(os.path.join(self.media_dir, "backup10.sql"), encoding="utf-8") as handle:
            lines = handle.read().splitlines()
        self.assertEqual(lines[1], "BEGIN;")
        self.assertEqual(lines[-1], "COMMIT;")
        self.assertEqual(lines[2], 'ALTER TABLE "users" DISABLE TRIGGER USER;')
        self.assertEqual(lines[4], 'ALTER TABLE "users" ENABLE TRIGGER USER;')
        self.assertEqual(
            lines[3],
            'INSERT INTO "users" ("id", "name", "updated_at") '
            "VALUES ('1', 'O''Brien', NULL) "
            'ON CONFLICT ("id") DO UPDATE SET "name" = EXCLUDED."name", '
            '"updated_at" = EXCLUDED."updated_at";',
        )
        self.assertNotIn("logs", "\n".join(lines))
        # фильтр по времени передаётся параметром, а не подставляется в SQL
        self.assertEqual(cursor.execute.call_args_list[-1].args[1], ("2024-01-01 00:00:00",))

    @patch("backuper.utils.psycopg2.connect")
    def test_no_timestamp_columns_writes_empty_transaction(self, mock_connect):
        connection, _cursor = self._make_connection([
            [("settings",)],   # одна таблица
            [],                # внешних ключей нет
            [("key",)],        # без колонок времени -> пропускается
        ])
        mock_connect.return_value = connection
        result = differential_db_backup(
            "h", "5432", "u", "p", "db", "2024-01-01 00:00:00", "backup11"
        )
        self.assertTrue(result)
        with open(os.path.join(self.media_dir, "backup11.sql"), encoding="utf-8") as handle:
            lines = handle.read().splitlines()
        self.assertTrue(lines[0].startswith("-- DataStudio"))
        self.assertEqual(lines[1:], ["BEGIN;", "COMMIT;"])

    @patch("backuper.utils.psycopg2.connect",
           side_effect=psycopg2.OperationalError("cannot connect"))
    def test_connection_failure_returns_false(self, _mock_connect):
        """Регрессия: при сбое connect() блок finally не должен падать с NameError."""
        self.assertFalse(
            incremental_db_backup("h", "5432", "u", "p", "db", "t", "backup12")
        )


class DelegationTests(SimpleTestCase):
    @patch("backuper.utils._export_changes_since", return_value=True)
    def test_incremental_delegates(self, mock_export):
        self.assertTrue(
            incremental_db_backup("h", "5432", "u", "p", "db", "ts", "name")
        )
        mock_export.assert_called_once_with("h", "5432", "u", "p", "db", "ts", "name")

    @patch("backuper.utils._export_changes_since", return_value=True)
    def test_differential_delegates(self, mock_export):
        self.assertTrue(
            differential_db_backup("h", "5432", "u", "p", "db", "ts", "name")
        )
        mock_export.assert_called_once_with("h", "5432", "u", "p", "db", "ts", "name")
