import os
import shutil
import tempfile
from unittest.mock import patch

import psycopg2
from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.utils import timezone

from core.models import Backup, BackupType, Storage, StorageType
from core.utils import check_db_connection


# --------------------------------------------------------------------------- #
#  Вспомогательные построители объектов
# --------------------------------------------------------------------------- #
def make_storage(location="local"):
    storage_type = StorageType.objects.create(title="Локальное")
    return Storage.objects.create(location=location, type=storage_type)


def make_backup(user, storage, backup_type, db="mydb", ip="127.0.0.1", port="5432", **kwargs):
    return Backup.objects.create(
        user=user,
        storage=storage,
        type=backup_type,
        username="dbuser",
        password="dbpass",
        db=db,
        ip=ip,
        port=port,
        **kwargs,
    )


# --------------------------------------------------------------------------- #
#  Модели
# --------------------------------------------------------------------------- #
class ModelStrTests(TestCase):
    def test_backup_type_str(self):
        self.assertEqual(str(BackupType.objects.create(title="Полная")), "Полная")

    def test_storage_type_str(self):
        self.assertEqual(str(StorageType.objects.create(title="Облако")), "Облако")

    def test_storage_str(self):
        storage = make_storage(location="/data")
        self.assertEqual(str(storage), "/data - Локальное")

    def test_backup_str(self):
        user = User.objects.create_user("u", password="p")
        storage = make_storage()
        backup_type = BackupType.objects.create(title="Полная")
        backup = make_backup(user, storage, backup_type, db="shop")
        self.assertIn("Полная", str(backup))
        self.assertIn("shop", str(backup))


class BackupFieldTests(TestCase):
    def test_created_at_default_is_callable(self):
        """Регрессия: default должен быть вызываемым timezone.now, а не
        зафиксированным во время импорта значением dt.now()."""
        self.assertIs(Backup._meta.get_field("created_at").default, timezone.now)

    def test_created_at_is_set_to_now(self):
        user = User.objects.create_user("u", password="p")
        backup = make_backup(user, make_storage(), BackupType.objects.create(title="Полная"))
        self.assertAlmostEqual(
            backup.created_at.timestamp(),
            timezone.now().timestamp(),
            delta=10,
        )

    def test_type_field_verbose_name(self):
        """Регрессия: verbose_name поля type был ошибочно «Тип хранилища»."""
        self.assertEqual(
            Backup._meta.get_field("type").verbose_name,
            "Тип резервной копии",
        )

    def test_default_ordering_newest_first(self):
        self.assertEqual(Backup._meta.ordering, ["-created_at"])


# --------------------------------------------------------------------------- #
#  core.utils.check_db_connection
# --------------------------------------------------------------------------- #
class CheckDbConnectionTests(TestCase):
    @patch("core.utils.psycopg2.connect")
    def test_returns_true_and_closes_on_success(self, mock_connect):
        connection = mock_connect.return_value
        self.assertTrue(check_db_connection("h", "5432", "u", "p", "db"))
        connection.close.assert_called_once()
        # таймаут соединения должен передаваться
        self.assertIn("connect_timeout", mock_connect.call_args.kwargs)

    @patch("core.utils.psycopg2.connect", side_effect=psycopg2.OperationalError("boom"))
    def test_returns_false_on_error(self, _mock_connect):
        self.assertFalse(check_db_connection("h", "5432", "u", "p", "db"))


# --------------------------------------------------------------------------- #
#  Базовый класс для тестов представлений
# --------------------------------------------------------------------------- #
class ViewTestBase(TestCase):
    def setUp(self):
        self.password = "secret123"
        self.user = User.objects.create_user("owner", password=self.password)
        self.other = User.objects.create_user("intruder", password=self.password)
        self.storage = make_storage()
        self.full_type = BackupType.objects.create(title="Полная")
        self.incr_type = BackupType.objects.create(title="Инкрементальная")
        self.diff_type = BackupType.objects.create(title="Дифференциальная")

    def login(self, user=None):
        self.client.force_login(user or self.user)


# --------------------------------------------------------------------------- #
#  index / storages / backup_types
# --------------------------------------------------------------------------- #
class IndexViewTests(ViewTestBase):
    def test_redirects_when_anonymous(self):
        response = self.client.get("/")
        self.assertRedirects(response, "/auth/login", fetch_redirect_response=False)

    def test_shows_only_own_backups(self):
        mine = make_backup(self.user, self.storage, self.full_type, db="mine")
        make_backup(self.other, self.storage, self.full_type, db="theirs")
        self.login()
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        backups = list(response.context["backups"])
        self.assertEqual(backups, [mine])


class SimpleListViewTests(ViewTestBase):
    def test_storages_requires_auth(self):
        self.assertRedirects(
            self.client.get("/storages"), "/auth/login", fetch_redirect_response=False
        )

    def test_storages_ok(self):
        self.login()
        response = self.client.get("/storages")
        self.assertEqual(response.status_code, 200)
        self.assertIn(self.storage, list(response.context["storages"]))

    def test_backup_types_requires_auth(self):
        self.assertRedirects(
            self.client.get("/backup_types"), "/auth/login", fetch_redirect_response=False
        )

    def test_backup_types_ok(self):
        self.login()
        response = self.client.get("/backup_types")
        self.assertEqual(response.status_code, 200)
        self.assertIn(self.full_type, list(response.context["types"]))


# --------------------------------------------------------------------------- #
#  create
# --------------------------------------------------------------------------- #
class CreateViewTests(ViewTestBase):
    def valid_payload(self, **overrides):
        payload = {
            "type": self.full_type.id,
            "username": "dbuser",
            "password": "dbpass",
            "db": "shop",
            "ip": "127.0.0.1",
            "port": "5432",
            "storage": self.storage.id,
        }
        payload.update(overrides)
        return payload

    def assert_error(self, response, text):
        self.assertEqual(response.status_code, 200)
        msgs = [str(m) for m in response.context["messages"]]
        self.assertIn(text, msgs)

    def test_get_requires_auth(self):
        self.assertRedirects(
            self.client.get("/create"), "/auth/login", fetch_redirect_response=False
        )

    def test_get_renders_form(self):
        self.login()
        response = self.client.get("/create")
        self.assertEqual(response.status_code, 200)
        self.assertIn(self.full_type, list(response.context["backup_types"]))
        self.assertIn(self.storage, list(response.context["storages"]))

    def test_invalid_type(self):
        self.login()
        response = self.client.post("/create", self.valid_payload(type=99999))
        self.assert_error(response, "Указанный тип копирования не существует")
        self.assertEqual(Backup.objects.count(), 0)

    def test_missing_required_fields(self):
        self.login()
        cases = {
            "username": "Не указано имя пользователя",
            "password": "Не указан пароль",
            "db": "Не указана база данных",
            "ip": "Не указан адрес сервера",
            "port": "Не указан порт",
        }
        for field, message in cases.items():
            with self.subTest(field=field):
                response = self.client.post("/create", self.valid_payload(**{field: ""}))
                self.assert_error(response, message)
        self.assertEqual(Backup.objects.count(), 0)

    def test_invalid_storage(self):
        self.login()
        response = self.client.post("/create", self.valid_payload(storage=99999))
        self.assert_error(response, "Указанное хранилище не существует")
        self.assertEqual(Backup.objects.count(), 0)

    @patch("core.views.check_db_connection", return_value=False)
    def test_connection_failure(self, _mock):
        self.login()
        response = self.client.post("/create", self.valid_payload())
        self.assert_error(response, "Соединение с указанной базой данных отсутствует")
        self.assertEqual(Backup.objects.count(), 0)

    @patch("core.views.full_db_backup", return_value=True)
    @patch("core.views.check_db_connection", return_value=True)
    def test_full_backup_success(self, _conn, mock_full):
        self.login()
        response = self.client.post("/create", self.valid_payload())
        self.assertRedirects(response, "/", fetch_redirect_response=False)
        self.assertEqual(Backup.objects.count(), 1)
        backup = Backup.objects.get()
        self.assertEqual(backup.user, self.user)
        mock_full.assert_called_once()
        self.assertEqual(mock_full.call_args.args[-1], f"backup{backup.id}")

    @patch("core.views.full_db_backup", return_value=False)
    @patch("core.views.check_db_connection", return_value=True)
    def test_full_backup_failure_removes_record(self, _conn, _mock_full):
        self.login()
        response = self.client.post("/create", self.valid_payload())
        self.assert_error(response, "Бэкап создать не удалось")
        self.assertEqual(Backup.objects.count(), 0)

    @patch("core.views.check_db_connection", return_value=True)
    def test_incremental_without_previous(self, _conn):
        self.login()
        response = self.client.post("/create", self.valid_payload(type=self.incr_type.id))
        self.assert_error(response, "Для инкрементальной копии нужна полная копия этой же базы")
        self.assertEqual(Backup.objects.count(), 0)

    @patch("core.views.incremental_db_backup", return_value=True)
    @patch("core.views.check_db_connection", return_value=True)
    def test_incremental_with_previous(self, _conn, mock_incr):
        previous = make_backup(self.user, self.storage, self.full_type, db="shop")
        self.login()
        response = self.client.post("/create", self.valid_payload(type=self.incr_type.id))
        self.assertRedirects(response, "/", fetch_redirect_response=False)
        self.assertEqual(Backup.objects.count(), 2)
        # время предыдущей копии (а не самой создаваемой) передано в бэкап
        self.assertEqual(mock_incr.call_args.args[-2], previous.created_at)

    @patch("core.views.check_db_connection", return_value=True)
    def test_differential_without_full(self, _conn):
        # есть только инкрементальная копия — полной нет
        make_backup(self.user, self.storage, self.incr_type, db="shop")
        self.login()
        response = self.client.post("/create", self.valid_payload(type=self.diff_type.id))
        self.assert_error(response, "Для дифференциальной копии нужна полная копия этой же базы")

    @patch("core.views.check_db_connection", return_value=True)
    def test_unknown_backup_type(self, _conn):
        weird = BackupType.objects.create(title="Странная")
        self.login()
        response = self.client.post("/create", self.valid_payload(type=weird.id))
        self.assert_error(response, "Указан неизвестный тип резервного копирования")
        self.assertEqual(Backup.objects.count(), 0)

    @patch("core.views.differential_db_backup", return_value=True)
    @patch("core.views.check_db_connection", return_value=True)
    def test_differential_with_full(self, _conn, mock_diff):
        full = make_backup(self.user, self.storage, self.full_type, db="shop")
        self.login()
        response = self.client.post("/create", self.valid_payload(type=self.diff_type.id))
        self.assertRedirects(response, "/", fetch_redirect_response=False)
        self.assertEqual(mock_diff.call_args.args[-2], full.created_at)


# --------------------------------------------------------------------------- #
#  download / remove (работают с файлами на диске)
# --------------------------------------------------------------------------- #
class FileBackedViewTests(ViewTestBase):
    def setUp(self):
        super().setUp()
        self.media_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.media_dir, ignore_errors=True)
        self.override = override_settings(MEDIA_DIR=self.media_dir)
        self.override.enable()
        self.addCleanup(self.override.disable)

    def write_backup_file(self, backup, content="-- dump"):
        path = os.path.join(self.media_dir, f"backup{backup.id}.sql")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(content)
        return path


class DownloadViewTests(FileBackedViewTests):
    def test_requires_auth(self):
        backup = make_backup(self.user, self.storage, self.full_type)
        self.assertRedirects(
            self.client.get(f"/download/{backup.id}"),
            "/auth/login",
            fetch_redirect_response=False,
        )

    def test_download_own_backup(self):
        backup = make_backup(self.user, self.storage, self.full_type)
        self.write_backup_file(backup, content="SELECT 1;")
        self.login()
        response = self.client.get(f"/download/{backup.id}")
        self.assertEqual(response.status_code, 200)
        self.assertIn("attachment", response["Content-Disposition"])
        self.assertEqual(b"".join(response.streaming_content), b"SELECT 1;")

    def test_missing_file_redirects(self):
        backup = make_backup(self.user, self.storage, self.full_type)
        self.login()
        response = self.client.get(f"/download/{backup.id}")
        self.assertRedirects(response, "/", fetch_redirect_response=False)

    def test_cannot_download_others_backup(self):
        backup = make_backup(self.other, self.storage, self.full_type)
        self.write_backup_file(backup)
        self.login()
        response = self.client.get(f"/download/{backup.id}")
        self.assertRedirects(response, "/", fetch_redirect_response=False)

    def test_nonexistent_backup_redirects(self):
        self.login()
        response = self.client.get("/download/99999")
        self.assertRedirects(response, "/", fetch_redirect_response=False)


class RemoveViewTests(FileBackedViewTests):
    def test_requires_auth(self):
        backup = make_backup(self.user, self.storage, self.full_type)
        self.assertRedirects(
            self.client.get(f"/remove/{backup.id}"),
            "/auth/login",
            fetch_redirect_response=False,
        )

    def test_get_renders_confirmation(self):
        backup = make_backup(self.user, self.storage, self.full_type)
        self.login()
        response = self.client.get(f"/remove/{backup.id}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["backup"], backup)

    def test_post_deletes_backup_and_file(self):
        backup = make_backup(self.user, self.storage, self.full_type)
        path = self.write_backup_file(backup)
        self.login()
        response = self.client.post(f"/remove/{backup.id}")
        self.assertRedirects(response, "/", fetch_redirect_response=False)
        self.assertFalse(Backup.objects.filter(id=backup.id).exists())
        self.assertFalse(os.path.exists(path))

    def test_post_missing_file_still_deletes_record(self):
        backup = make_backup(self.user, self.storage, self.full_type)
        self.login()
        response = self.client.post(f"/remove/{backup.id}")
        self.assertRedirects(response, "/", fetch_redirect_response=False)
        self.assertFalse(Backup.objects.filter(id=backup.id).exists())

    def test_post_file_deletion_error_is_handled(self):
        backup = make_backup(self.user, self.storage, self.full_type)
        self.write_backup_file(backup)
        self.login()
        with patch("core.views.os.remove", side_effect=OSError("locked")):
            response = self.client.post(f"/remove/{backup.id}")
        self.assertRedirects(response, "/", fetch_redirect_response=False)
        self.assertFalse(Backup.objects.filter(id=backup.id).exists())

    def test_cannot_remove_others_backup(self):
        backup = make_backup(self.other, self.storage, self.full_type)
        self.login()
        response = self.client.post(f"/remove/{backup.id}")
        self.assertRedirects(response, "/", fetch_redirect_response=False)
        self.assertTrue(Backup.objects.filter(id=backup.id).exists())
