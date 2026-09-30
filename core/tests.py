import os
import shutil
import tempfile
from io import StringIO
from unittest.mock import patch

import psycopg2
from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from core.models import Backup, BackupType, Storage, StorageType
from core.services import BackupError, create_backup, find_base_backup
from core.utils import check_db_connection


# --------------------------------------------------------------------------- #
#  Вспомогательные построители объектов
# --------------------------------------------------------------------------- #
def make_storage(location="local"):
    storage_type = StorageType.objects.create(title="Локальное")
    return Storage.objects.create(location=location, type=storage_type)


def make_backup(user, storage, backup_type, db="mydb", host="127.0.0.1", port=5432, **kwargs):
    return Backup.objects.create(
        user=user,
        storage=storage,
        type=backup_type,
        username="dbuser",
        db=db,
        host=host,
        port=port,
        **kwargs,
    )


class MediaDirMixin:
    """Временный каталог под файлы копий на время теста."""

    def setUp(self):
        super().setUp()
        self.media_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.media_dir, ignore_errors=True)
        override = override_settings(MEDIA_DIR=self.media_dir)
        override.enable()
        self.addCleanup(override.disable)

    def write_backup_file(self, backup, content="-- dump"):
        with open(backup.file_path, "w", encoding="utf-8") as handle:
            handle.write(content)
        return backup.file_path


# --------------------------------------------------------------------------- #
#  Справочники, заполненные миграцией
# --------------------------------------------------------------------------- #
class ReferenceDataTests(TestCase):
    def test_backup_types_seeded_with_codes(self):
        codes = set(BackupType.objects.values_list("code", flat=True))
        self.assertEqual(codes, {"full", "incremental", "differential"})

    def test_backup_types_have_labels_hints_and_summaries(self):
        for backup_type in BackupType.objects.all():
            with self.subTest(code=backup_type.code):
                self.assertTrue(backup_type.label)
                self.assertTrue(backup_type.hint)
                self.assertTrue(backup_type.summary)

    def test_default_storage_seeded(self):
        self.assertTrue(Storage.objects.exists())


# --------------------------------------------------------------------------- #
#  Модели
# --------------------------------------------------------------------------- #
class ModelTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("u", password="p")
        self.full = BackupType.objects.get(code=BackupType.FULL)

    def test_storage_str(self):
        self.assertEqual(str(make_storage(location="/data")), "/data - Локальное")

    def test_backup_str(self):
        backup = make_backup(self.user, make_storage(), self.full, db="shop")
        self.assertIn("Full", str(backup))
        self.assertIn("shop", str(backup))

    def test_password_is_not_stored(self):
        field_names = {field.name for field in Backup._meta.get_fields()}
        self.assertNotIn("password", field_names)

    def test_created_at_default_is_callable(self):
        """Регрессия: default должен быть вызываемым timezone.now."""
        self.assertIs(Backup._meta.get_field("created_at").default, timezone.now)

    def test_default_ordering_newest_first(self):
        self.assertEqual(Backup._meta.ordering, ["-created_at"])

    @override_settings(MEDIA_DIR="/srv/backups")
    def test_file_path_and_download_name(self):
        backup = make_backup(self.user, make_storage(), self.full, db="shop")
        self.assertEqual(backup.file_path, os.path.join("/srv/backups", f"backup{backup.id}.sql"))
        self.assertTrue(backup.download_name.startswith("shop_full_"))
        self.assertTrue(backup.download_name.endswith(".sql"))


# --------------------------------------------------------------------------- #
#  core.utils.check_db_connection
# --------------------------------------------------------------------------- #
class CheckDbConnectionTests(TestCase):
    @patch("core.utils.psycopg2.connect")
    def test_returns_true_and_closes_on_success(self, mock_connect):
        connection = mock_connect.return_value
        self.assertTrue(check_db_connection("h", 5432, "u", "p", "db"))
        connection.close.assert_called_once()
        self.assertIn("connect_timeout", mock_connect.call_args.kwargs)

    @patch("core.utils.psycopg2.connect", side_effect=psycopg2.OperationalError("boom"))
    def test_returns_false_on_error(self, _mock_connect):
        self.assertFalse(check_db_connection("h", 5432, "u", "p", "db"))


# --------------------------------------------------------------------------- #
#  Сервисный слой
# --------------------------------------------------------------------------- #
class ServiceTestBase(MediaDirMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user("owner", password="secret123")
        self.other = User.objects.create_user("intruder", password="secret123")
        self.storage = make_storage()
        self.full = BackupType.objects.get(code=BackupType.FULL)
        self.incr = BackupType.objects.get(code=BackupType.INCREMENTAL)
        self.diff = BackupType.objects.get(code=BackupType.DIFFERENTIAL)

    def params(self, **overrides):
        params = {
            "type": self.full,
            "host": "127.0.0.1",
            "port": 5432,
            "db": "shop",
            "username": "dbuser",
            "password": "dbpass",
            "storage": self.storage,
        }
        params.update(overrides)
        return params


class FindBaseBackupTests(ServiceTestBase):
    def test_incremental_uses_latest_of_any_type(self):
        make_backup(self.user, self.storage, self.full, db="shop")
        latest = make_backup(self.user, self.storage, self.incr, db="shop")
        self.assertEqual(find_base_backup(self.user, self.incr, "shop", "127.0.0.1", 5432), latest)

    def test_differential_uses_latest_full(self):
        full = make_backup(self.user, self.storage, self.full, db="shop")
        make_backup(self.user, self.storage, self.incr, db="shop")
        self.assertEqual(find_base_backup(self.user, self.diff, "shop", "127.0.0.1", 5432), full)

    def test_ignores_other_users_backups(self):
        make_backup(self.other, self.storage, self.full, db="shop")
        self.assertIsNone(find_base_backup(self.user, self.incr, "shop", "127.0.0.1", 5432))

    def test_ignores_other_databases(self):
        make_backup(self.user, self.storage, self.full, db="other")
        self.assertIsNone(find_base_backup(self.user, self.incr, "shop", "127.0.0.1", 5432))


@patch("core.services.check_db_connection", return_value=True)
class CreateBackupTests(ServiceTestBase):
    def fake_dump(self, content="-- dump\n"):
        """Имитирует успешный дамп: пишет файл, как это сделал бы pg_dump."""

        def dump(*args):
            with open(os.path.join(self.media_dir, args[-1] + ".sql"), "w") as handle:
                handle.write(content)
            return True

        return dump

    def test_full_backup_records_size(self, _conn):
        with patch("core.services.full_db_backup", side_effect=self.fake_dump("x" * 42)) as dump:
            backup = create_backup(self.user, **self.params())
        dump.assert_called_once_with("127.0.0.1", 5432, "dbuser", "dbpass", "shop", backup.file_name)
        self.assertEqual(backup.size, 42)
        self.assertEqual(backup.user, self.user)

    def test_failed_dump_leaves_no_record_or_file(self, _conn):
        with patch("core.services.full_db_backup", return_value=False):
            with self.assertRaisesMessage(BackupError, "The backup failed"):
                create_backup(self.user, **self.params())
        self.assertFalse(Backup.objects.exists())
        self.assertEqual(os.listdir(self.media_dir), [])

    def test_connection_failure(self, conn):
        conn.return_value = False
        with self.assertRaisesMessage(BackupError, "Could not connect"):
            create_backup(self.user, **self.params())
        self.assertFalse(Backup.objects.exists())

    def test_incremental_requires_base(self, _conn):
        with self.assertRaisesMessage(BackupError, "An incremental backup needs a full backup"):
            create_backup(self.user, **self.params(type=self.incr))

    def test_incremental_passes_base_timestamp(self, _conn):
        previous = make_backup(self.user, self.storage, self.full, db="shop")
        with patch("core.services.incremental_db_backup", side_effect=self.fake_dump()) as dump:
            create_backup(self.user, **self.params(type=self.incr))
        # время базовой копии, а не самой создаваемой
        self.assertEqual(dump.call_args.args[-2], previous.created_at)

    def test_differential_requires_full(self, _conn):
        make_backup(self.user, self.storage, self.incr, db="shop")
        with self.assertRaisesMessage(BackupError, "A differential backup needs a full backup"):
            create_backup(self.user, **self.params(type=self.diff))

    def test_differential_passes_full_timestamp(self, _conn):
        full = make_backup(self.user, self.storage, self.full, db="shop")
        make_backup(self.user, self.storage, self.incr, db="shop")
        with patch("core.services.differential_db_backup", side_effect=self.fake_dump()) as dump:
            create_backup(self.user, **self.params(type=self.diff))
        self.assertEqual(dump.call_args.args[-2], full.created_at)


# --------------------------------------------------------------------------- #
#  Представления
# --------------------------------------------------------------------------- #
class ViewTestBase(ServiceTestBase):
    def login(self, user=None):
        self.client.force_login(user or self.user)


class IndexViewTests(ViewTestBase):
    def test_redirects_when_anonymous(self):
        response = self.client.get("/")
        self.assertRedirects(response, "/auth/login?next=/", fetch_redirect_response=False)

    def test_shows_only_own_backups_with_stats(self):
        mine = make_backup(self.user, self.storage, self.full, db="mine", size=100)
        make_backup(self.other, self.storage, self.full, db="theirs", size=999)
        self.login()
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context["backups"]), [mine])
        self.assertEqual(response.context["stats"]["count"], 1)
        self.assertEqual(response.context["stats"]["total_size"], 100)
        self.assertNotContains(response, "theirs")

    def test_empty_state(self):
        self.login()
        self.assertContains(self.client.get("/"), "No backups yet")


class ReferenceViewTests(ViewTestBase):
    def test_require_auth(self):
        for url in ("/storages", "/backup_types"):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 302)
                self.assertTrue(response["Location"].startswith("/auth/login"))

    def test_storages_counts_only_own_backups(self):
        make_backup(self.user, self.storage, self.full)
        make_backup(self.other, self.storage, self.full)
        self.login()
        response = self.client.get("/storages")
        storage = next(s for s in response.context["storages"] if s.pk == self.storage.pk)
        self.assertEqual(storage.backup_count, 1)

    def test_backup_types(self):
        self.login()
        response = self.client.get("/backup_types")
        self.assertEqual(response.status_code, 200)
        self.assertIn(self.full, list(response.context["types"]))


class CreateViewTests(ViewTestBase):
    def payload(self, **overrides):
        payload = {
            "type": self.full.id,
            "username": "dbuser",
            "password": "dbpass",
            "db": "shop",
            "host": "db.internal",
            "port": "5432",
            "storage": self.storage.id,
        }
        payload.update(overrides)
        return payload

    def test_get_renders_form_with_full_preselected(self):
        self.login()
        response = self.client.get("/create")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["form"]["type"].initial, self.full.pk)

    def test_required_fields(self):
        self.login()
        for field in ("username", "password", "db", "host", "port"):
            with self.subTest(field=field):
                response = self.client.post("/create", self.payload(**{field: ""}))
                self.assertEqual(response.status_code, 200)
                self.assertIn(field, response.context["form"].errors)
        self.assertFalse(Backup.objects.exists())

    def test_invalid_choices(self):
        self.login()
        response = self.client.post("/create", self.payload(type=99999, storage=99999))
        errors = response.context["form"].errors
        self.assertEqual(errors["type"], ["This backup type does not exist"])
        self.assertEqual(errors["storage"], ["This storage does not exist"])

    def test_port_range(self):
        self.login()
        response = self.client.post("/create", self.payload(port="70000"))
        self.assertIn("port", response.context["form"].errors)

    def test_password_is_not_echoed_back(self):
        self.login()
        response = self.client.post("/create", self.payload(db=""))
        self.assertNotContains(response, "dbpass")

    @patch("core.views.create_backup", side_effect=BackupError("База недоступна"))
    def test_service_error_shown_on_form(self, _create):
        self.login()
        response = self.client.post("/create", self.payload())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["form"].non_field_errors(), ["База недоступна"])
        self.assertContains(response, "База недоступна")

    @patch("core.views.create_backup")
    def test_success_redirects_with_message(self, create):
        create.return_value = make_backup(self.user, self.storage, self.full, db="shop")
        self.login()
        response = self.client.post("/create", self.payload(), follow=True)
        self.assertRedirects(response, "/")
        self.assertContains(response, "Backup of “shop” created")
        kwargs = create.call_args.kwargs
        self.assertEqual(kwargs["host"], "db.internal")
        self.assertEqual(kwargs["port"], 5432)


class DownloadViewTests(ViewTestBase):
    def test_requires_auth(self):
        backup = make_backup(self.user, self.storage, self.full)
        self.assertEqual(self.client.get(f"/download/{backup.id}").status_code, 302)

    def test_download_own_backup(self):
        backup = make_backup(self.user, self.storage, self.full, db="shop")
        self.write_backup_file(backup, content="SELECT 1;")
        self.login()
        response = self.client.get(f"/download/{backup.id}")
        self.assertEqual(response.status_code, 200)
        self.assertIn('filename="shop_full_', response["Content-Disposition"])
        self.assertEqual(b"".join(response.streaming_content), b"SELECT 1;")

    def test_missing_file_redirects_with_error(self):
        backup = make_backup(self.user, self.storage, self.full)
        self.login()
        response = self.client.get(f"/download/{backup.id}")
        self.assertRedirects(response, "/", fetch_redirect_response=False)

    def test_others_backup_is_not_found(self):
        backup = make_backup(self.other, self.storage, self.full)
        self.write_backup_file(backup)
        self.login()
        self.assertEqual(self.client.get(f"/download/{backup.id}").status_code, 404)


class RemoveViewTests(ViewTestBase):
    def test_get_renders_confirmation(self):
        backup = make_backup(self.user, self.storage, self.full)
        self.login()
        response = self.client.get(f"/remove/{backup.id}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["backup"], backup)

    def test_post_deletes_backup_and_file(self):
        backup = make_backup(self.user, self.storage, self.full)
        path = self.write_backup_file(backup)
        self.login()
        response = self.client.post(f"/remove/{backup.id}")
        self.assertRedirects(response, "/", fetch_redirect_response=False)
        self.assertFalse(Backup.objects.filter(id=backup.id).exists())
        self.assertFalse(os.path.exists(path))

    def test_post_missing_file_still_deletes_record(self):
        backup = make_backup(self.user, self.storage, self.full)
        self.login()
        self.client.post(f"/remove/{backup.id}")
        self.assertFalse(Backup.objects.filter(id=backup.id).exists())

    def test_file_deletion_error_is_handled(self):
        backup = make_backup(self.user, self.storage, self.full)
        self.write_backup_file(backup)
        self.login()
        with patch("core.services.os.remove", side_effect=OSError("locked")):
            response = self.client.post(f"/remove/{backup.id}")
        self.assertRedirects(response, "/", fetch_redirect_response=False)
        self.assertFalse(Backup.objects.filter(id=backup.id).exists())

    def test_cannot_remove_others_backup(self):
        backup = make_backup(self.other, self.storage, self.full)
        self.login()
        self.assertEqual(self.client.post(f"/remove/{backup.id}").status_code, 404)
        self.assertTrue(Backup.objects.filter(id=backup.id).exists())


# --------------------------------------------------------------------------- #
#  Команда seed_demo
# --------------------------------------------------------------------------- #
class SeedDemoCommandTests(TestCase):
    @override_settings(DEMO_USERNAME="demo", DEMO_PASSWORD="demo-pass-123")
    def test_creates_and_updates_user(self):
        call_command("seed_demo", stdout=StringIO())
        self.assertTrue(User.objects.get(username="demo").check_password("demo-pass-123"))
        with override_settings(DEMO_PASSWORD="changed-pass-456"):
            call_command("seed_demo", stdout=StringIO())
        self.assertTrue(User.objects.get(username="demo").check_password("changed-pass-456"))

    @override_settings(DEMO_USERNAME="")
    def test_noop_without_username(self):
        call_command("seed_demo", stdout=StringIO())
        self.assertFalse(User.objects.exists())


# --------------------------------------------------------------------------- #
#  Язык интерфейса
# --------------------------------------------------------------------------- #
class LanguageTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("owner", password="secret123")
        self.client.force_login(self.user)

    def test_english_by_default_even_for_russian_browser(self):
        response = self.client.get("/", HTTP_ACCEPT_LANGUAGE="ru-RU,ru;q=0.9")
        self.assertContains(response, "No backups yet")
        self.assertContains(response, '<html lang="en">')
        self.assertEqual(response["Content-Language"], "en")

    def test_switch_to_russian_and_back(self):
        response = self.client.post("/i18n/setlang/", {"language": "ru", "next": "/backup_types"})
        self.assertRedirects(response, "/backup_types", fetch_redirect_response=False)

        page = self.client.get("/backup_types")
        self.assertContains(page, '<html lang="ru">')
        self.assertContains(page, "Дифференциальная")
        self.assertContains(page, "Для восстановления достаточно полной копии")

        self.client.post("/i18n/setlang/", {"language": "en", "next": "/"})
        self.assertContains(self.client.get("/"), "No backups yet")

    def test_unknown_language_cookie_falls_back_to_english(self):
        self.client.cookies["django_language"] = "de"
        self.assertContains(self.client.get("/"), '<html lang="en">')

    def test_russian_plural_forms(self):
        self.client.cookies["django_language"] = "ru"
        storage = make_storage()
        full = BackupType.objects.get(code=BackupType.FULL)
        for db in ("a", "b", "c", "d", "e"):
            make_backup(self.user, storage, full, db=db)
        self.assertContains(self.client.get("/"), "5 баз данных")

    def test_service_errors_are_translated(self):
        from django.utils import translation

        storage = make_storage()
        incr = BackupType.objects.get(code=BackupType.INCREMENTAL)
        with translation.override("ru"):
            with self.assertRaisesMessage(BackupError, "Для инкрементальной копии"):
                create_backup(
                    self.user, type=incr, host="h", port=5432, db="shop",
                    username="u", password="p", storage=storage,
                )
