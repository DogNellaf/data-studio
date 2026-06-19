import os
import shutil
import subprocess
import tempfile
from unittest.mock import MagicMock, patch

import psycopg2
from django.test import SimpleTestCase, override_settings

from backuper import utils
from backuper.utils import (
    _format_value,
    _quote_identifier,
    differential_db_backup,
    full_db_backup,
    incremental_db_backup,
)


class HelperTests(SimpleTestCase):
    def test_format_value_none(self):
        self.assertEqual(_format_value(None), "NULL")

    def test_format_value_escapes_quotes(self):
        self.assertEqual(_format_value("O'Brien"), "'O''Brien'")

    def test_format_value_number(self):
        self.assertEqual(_format_value(42), "'42'")

    def test_quote_identifier(self):
        self.assertEqual(_quote_identifier("users"), '"users"')

    def test_quote_identifier_escapes(self):
        self.assertEqual(_quote_identifier('we"ird'), '"we""ird"')


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
        self.assertTrue(command[-1].endswith(os.path.join("backup1.sql")))
        # пароль передаётся через переменную окружения, а не в командной строке
        self.assertEqual(mock_run.call_args.kwargs["env"]["PGPASSWORD"], "p")

    @patch("backuper.utils.subprocess.run",
           side_effect=subprocess.CalledProcessError(1, "pg_dump", stderr="boom"))
    def test_called_process_error(self, _mock_run):
        self.assertFalse(full_db_backup("h", "5432", "u", "p", "db", "backup2"))

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
    def test_writes_insert_statements(self, mock_connect):
        connection, _cursor = self._make_connection([
            [("users",), ("logs",)],          # список таблиц
            [("created_at",)],                # колонки времени для users
            [(1, "O'Brien", None)],           # изменённые строки users
            [],                               # у logs нет колонок времени
        ])
        mock_connect.return_value = connection

        result = incremental_db_backup(
            "h", "5432", "u", "p", "db", "2024-01-01 00:00:00", "backup10"
        )
        self.assertTrue(result)
        connection.close.assert_called_once()

        with open(os.path.join(self.media_dir, "backup10.sql"), encoding="utf-8") as handle:
            content = handle.read()
        self.assertIn('INSERT INTO "users" VALUES (\'1\', \'O\'\'Brien\', NULL);', content)
        self.assertNotIn("logs", content)

    @patch("backuper.utils.psycopg2.connect")
    def test_no_timestamp_columns_writes_empty_file(self, mock_connect):
        connection, _cursor = self._make_connection([
            [("settings",)],   # одна таблица
            [],                # без колонок времени -> пропускается
        ])
        mock_connect.return_value = connection
        result = differential_db_backup(
            "h", "5432", "u", "p", "db", "2024-01-01 00:00:00", "backup11"
        )
        self.assertTrue(result)
        with open(os.path.join(self.media_dir, "backup11.sql"), encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "")

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
