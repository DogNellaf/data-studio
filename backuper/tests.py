import gzip
import json
import os
import shutil
import tempfile
from unittest.mock import MagicMock, patch

import psycopg2
from django.test import SimpleTestCase

from backuper.utils import (
    BackupFailed,
    StateReader,
    StateWriter,
    Table,
    _key_match,
    _literal_list,
    _merge,
    _quote_identifier,
    _schema_differences,
    _topological_order,
    _upsert_suffix,
    full_backup,
)


def make_table(name="users", schema="public", key=("id",), columns=("id", "name")):
    return Table(
        oid=1, schema=schema, name=name, columns=list(columns),
        signature=[f"{c} text" for c in columns], key=list(key),
    )


class HelperTests(SimpleTestCase):
    def test_quote_identifier(self):
        self.assertEqual(_quote_identifier("users"), '"users"')

    def test_quote_identifier_escapes(self):
        self.assertEqual(_quote_identifier('we"ird'), '"we""ird"')

    def test_literal_list_uses_server_side_quoting(self):
        self.assertEqual(
            _literal_list(["id", "name"]),
            "concat_ws(', ', quote_nullable(t.\"id\"), quote_nullable(t.\"name\"))",
        )

    def test_key_match_single_and_composite(self):
        self.assertEqual(_key_match(make_table(), ["'1'", "'2'"]), "(\"id\") IN (('1'), ('2'))")
        composite = make_table(key=("order_id", "line"))
        self.assertEqual(
            _key_match(composite, ["'1', '2'"]), "(\"order_id\", \"line\") IN (('1', '2'))"
        )

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


class SchemaDifferenceTests(SimpleTestCase):
    def base(self, *tables):
        return {f"{t.schema}.{t.name}": {"columns": t.signature} for t in tables}

    def test_same_schema(self):
        table = make_table()
        self.assertEqual(_schema_differences(self.base(table), [table]), [])

    def test_new_dropped_and_changed_tables(self):
        users = make_table()
        changed_users = make_table(columns=("id", "name", "email"))
        old = make_table(name="old")
        new = make_table(name="new", schema="billing")
        differences = _schema_differences(self.base(users, old), [changed_users, new])
        self.assertEqual(
            differences,
            ["changed columns in public.users", "dropped table public.old", "new table billing.new"],
        )


class StateFileTests(SimpleTestCase):
    def setUp(self):
        self.workdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.workdir, ignore_errors=True)
        self.path = os.path.join(self.workdir, "state.zip")

    def test_round_trip_streams_each_table(self):
        users, logs = make_table(), make_table(name="logs", key=())
        writer = StateWriter(self.path)
        with writer.table(users) as record:
            record(["h1", "'1'", "row1"])
            record(["h2", "'2'", "row2"])
        with writer.table(logs) as record:
            record(["abc", 3])
        writer.close()

        reader = StateReader(self.path)
        self.addCleanup(reader.close)
        self.assertEqual(reader.tables["public.users"]["key"], ["id"])
        self.assertEqual(list(reader.records("public.users")), [("h1", "'1'", "row1"), ("h2", "'2'", "row2")])
        self.assertEqual(list(reader.records("public.logs")), [("abc", 3)])

    def test_old_gzip_format_asks_for_full_backup(self):
        with gzip.open(self.path, "wt") as handle:
            json.dump({"format": 1, "tables": {}}, handle)
        with self.assertRaises(BackupFailed) as ctx:
            StateReader(self.path)
        self.assertEqual((ctx.exception.code, ctx.exception.reason), (BackupFailed.NEEDS_FULL, "old_base"))


class MergeTests(SimpleTestCase):
    def test_pairs_matching_keys_and_reports_one_sided(self):
        base = [("a", "k1", "x"), ("c", "k3", "x"), ("d", "k4", "x")]
        current = [("a", "k1", "y"), ("b", "k2", "x"), ("d", "k4", "x")]
        self.assertEqual(
            list(_merge(base, current, width=2)),
            [
                (("a", "k1", "x"), ("a", "k1", "y")),
                (None, ("b", "k2", "x")),
                (("c", "k3", "x"), None),
                (("d", "k4", "x"), ("d", "k4", "x")),
            ],
        )

    def test_empty_sides(self):
        self.assertEqual(list(_merge([], [("a", 1)], width=1)), [(None, ("a", 1))])
        self.assertEqual(list(_merge([("a", 1)], [], width=1)), [(("a", 1), None)])
        self.assertEqual(list(_merge([], [], width=1)), [])


class FullBackupFailureTests(SimpleTestCase):
    params = {"host": "h", "port": 5432, "user": "u", "password": "p", "dbname": "db"}

    @patch("backuper.utils.psycopg2.connect", side_effect=psycopg2.OperationalError("refused"))
    def test_connection_error(self, _connect):
        with self.assertRaises(BackupFailed) as ctx:
            full_backup(self.params, "/tmp/x.sql", "/tmp/x.state")
        self.assertEqual(ctx.exception.code, BackupFailed.CONNECTION)
        self.assertIn("refused", ctx.exception.detail)

    @patch("backuper.utils.subprocess.Popen", side_effect=FileNotFoundError())
    @patch("backuper.utils.psycopg2.connect")
    def test_missing_pg_dump(self, connect, _popen):
        cursor = connect.return_value.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = ("snapshot-1",)
        with self.assertRaises(BackupFailed) as ctx:
            full_backup(self.params, "/tmp/x.sql", "/tmp/x.state")
        self.assertEqual(ctx.exception.code, BackupFailed.DUMP)
        connect.return_value.close.assert_called_once()

    @patch("backuper.utils.subprocess.Popen")
    @patch("backuper.utils.psycopg2.connect")
    def test_pg_dump_gets_snapshot_and_password_via_environment(self, connect, popen):
        cursor = connect.return_value.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = ("snapshot-1",)
        popen.return_value = MagicMock(returncode=1)
        popen.return_value.communicate.return_value = ("", "boom")
        popen.return_value.poll.return_value = 1
        workdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, workdir, ignore_errors=True)
        with patch("backuper.utils._discover_tables", return_value=[]):
            with self.assertRaises(BackupFailed) as ctx:
                full_backup(self.params, os.path.join(workdir, "x.sql"), os.path.join(workdir, "x.state"))
        self.assertEqual(ctx.exception.detail, "boom")
        command = popen.call_args.args[0]
        self.assertIn("--snapshot=snapshot-1", command)
        self.assertIn("--no-password", command)
        self.assertNotIn("p", " ".join(command).split())
        self.assertEqual(popen.call_args.kwargs["env"]["PGPASSWORD"], "p")
