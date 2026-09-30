import gzip
import json
import os
import shutil
import tempfile
from datetime import timedelta
from io import StringIO
from unittest.mock import patch

import boto3
import psycopg2
from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone
from moto import mock_aws

from backuper.utils import BackupFailed
from core import crypto
from core.backends import configured_backends, get_backend
from core.models import Backup, BackupType, Storage, StorageType
from core.services import (
    BackupError,
    claim_next_job,
    enqueue_backup,
    fail_stale_jobs,
    find_base_backup,
    run_backup,
)
from core.utils import check_db_connection

S3_STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    "backups-local": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "backups-s3": {
        "BACKEND": "storages.backends.s3.S3Storage",
        "OPTIONS": {
            "bucket_name": "test-backups",
            "location": "datastudio",
            "region_name": "us-east-1",
            "access_key": "testing",
            "secret_key": "testing",
            "file_overwrite": False,
        },
    },
}


# --------------------------------------------------------------------------- #
#  Вспомогательные построители объектов
# --------------------------------------------------------------------------- #
def make_storage(location="local", backend="local"):
    storage_type, _ = StorageType.objects.get_or_create(title="Локальное")
    return Storage.objects.create(location=location, type=storage_type, backend=backend)


def make_backup(user, storage, backup_type, db="mydb", host="127.0.0.1", port=5432, **kwargs):
    """Готовая копия со снимком состояния: может служить базой для дельт."""
    kwargs.setdefault("status", Backup.SUCCEEDED)
    if kwargs["status"] == Backup.SUCCEEDED:
        kwargs.setdefault("file_name", f"{db}-{Backup.objects.count()}.sql")
        kwargs.setdefault("state_name", f"{db}-{Backup.objects.count()}.state.zip")
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


def fake_full_backup(sql="-- full dump\n", state=None):
    """Имитирует backuper.full_backup: пишет SQL и снимок состояния."""

    def run(params, sql_path, state_path):
        with open(sql_path, "w") as handle:
            handle.write(sql)
        with gzip.open(state_path, "wt") as handle:
            json.dump({"format": 1, "tables": state or {}}, handle)
        return {"tables": 0}

    return run


def fake_delta_backup(sql="-- delta\n", seen=None):
    """Имитирует backuper.delta_backup и запоминает, с каким снимком сравнивали."""

    def run(params, base_state_path, sql_path, state_path, *, label):
        with gzip.open(base_state_path, "rt") as handle:
            if seen is not None:
                seen.append(json.load(handle))
        with open(sql_path, "w") as handle:
            handle.write(sql)
        with gzip.open(state_path, "wt") as handle:
            json.dump({"format": 1, "tables": {"label": label}}, handle)
        return {"changed": 0, "deleted": 0, "tables": 0}

    return run


class MediaRootMixin:
    """Временный каталог локального хранилища на время теста."""

    def setUp(self):
        super().setUp()
        self.media_root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.media_root, ignore_errors=True)
        override = override_settings(MEDIA_ROOT=self.media_root)
        override.enable()
        self.addCleanup(override.disable)

    def put_file(self, backup, content=b"-- dump", attr="file_name"):
        backend = backup.storage.open_backend()
        name = backend.save(getattr(backup, attr) or f"backup{backup.pk}.sql", ContentFile(content))
        setattr(backup, attr, name)
        backup.save(update_fields=[attr])
        return name


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

    def test_default_storage_is_local(self):
        self.assertEqual(Storage.objects.get().backend, "local")


# --------------------------------------------------------------------------- #
#  Модели, шифрование, бэкенды
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

    def test_password_has_no_plain_field(self):
        field_names = {field.name for field in Backup._meta.get_fields()}
        self.assertNotIn("password", field_names)
        self.assertFalse(Backup._meta.get_field("secret").editable)

    def test_new_backup_is_queued(self):
        self.assertEqual(Backup._meta.get_field("status").default, Backup.QUEUED)

    def test_created_at_default_is_callable(self):
        """Регрессия: default должен быть вызываемым timezone.now."""
        self.assertIs(Backup._meta.get_field("created_at").default, timezone.now)

    def test_default_ordering_newest_first(self):
        self.assertEqual(Backup._meta.ordering, ["-created_at"])

    def test_download_name_and_readiness(self):
        backup = make_backup(self.user, make_storage(), self.full, db="shop")
        self.assertTrue(backup.download_name.startswith("shop_full_"))
        self.assertTrue(backup.is_ready)
        queued = make_backup(self.user, make_storage(), self.full, status=Backup.QUEUED)
        self.assertFalse(queued.is_ready)
        self.assertTrue(queued.is_active)


class CryptoTests(TestCase):
    def test_round_trip_and_no_plaintext(self):
        token = crypto.encrypt("s3cret-pass")
        self.assertNotIn("s3cret-pass", token)
        self.assertEqual(crypto.decrypt(token), "s3cret-pass")

    def test_other_key_cannot_decrypt(self):
        token = crypto.encrypt("s3cret-pass")
        with override_settings(BACKUP_CREDENTIALS_KEY="another-key"):
            with self.assertRaises(crypto.InvalidToken):
                crypto.decrypt(token)


class BackendTests(TestCase):
    def test_only_local_by_default(self):
        self.assertEqual(configured_backends(), ["local"])
        with self.assertRaises(KeyError):
            get_backend("s3")

    @override_settings(STORAGES=S3_STORAGES)
    def test_s3_when_configured(self):
        self.assertEqual(sorted(configured_backends()), ["local", "s3"])
        self.assertEqual(get_backend("s3").bucket_name, "test-backups")


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
class ServiceTestBase(MediaRootMixin, TestCase):
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

    def queued(self, backup_type=None, **kwargs):
        return make_backup(
            self.user, self.storage, backup_type or self.full, db="shop",
            status=Backup.QUEUED, secret=crypto.encrypt("dbpass"), **kwargs,
        )

    def run_job(self, backup):
        backup.status = Backup.RUNNING
        backup.save()
        run_backup(backup)
        backup.refresh_from_db()
        return backup


class FindBaseBackupTests(ServiceTestBase):
    def job(self, backup_type):
        return self.queued(backup_type)

    def test_incremental_uses_latest_of_any_type(self):
        make_backup(self.user, self.storage, self.full, db="shop")
        latest = make_backup(self.user, self.storage, self.incr, db="shop")
        self.assertEqual(find_base_backup(self.job(self.incr)), latest)

    def test_differential_uses_latest_full(self):
        full = make_backup(self.user, self.storage, self.full, db="shop")
        make_backup(self.user, self.storage, self.incr, db="shop")
        self.assertEqual(find_base_backup(self.job(self.diff)), full)

    def test_ignores_other_users_databases_and_failures(self):
        make_backup(self.other, self.storage, self.full, db="shop")
        make_backup(self.user, self.storage, self.full, db="other")
        make_backup(self.user, self.storage, self.full, db="shop", status=Backup.FAILED)
        self.assertIsNone(find_base_backup(self.job(self.incr)))

    def test_chain_stays_within_one_storage(self):
        make_backup(self.user, make_storage(location="elsewhere"), self.full, db="shop")
        self.assertIsNone(find_base_backup(self.job(self.incr)))

    def test_legacy_backup_without_state_is_still_the_base(self):
        """Такая копия остаётся базой: воркер увидит, что снимка нет, и снимет полную."""
        legacy = make_backup(self.user, self.storage, self.full, db="shop", state_name="")
        self.assertEqual(find_base_backup(self.job(self.incr)), legacy)

    def test_ignores_backups_queued_later(self):
        job = self.job(self.incr)
        make_backup(self.user, self.storage, self.full, db="shop",
                    created_at=timezone.now() + timedelta(seconds=5))
        self.assertIsNone(find_base_backup(job))


@patch("core.services.check_db_connection", return_value=True)
class EnqueueTests(ServiceTestBase):
    def test_queues_with_encrypted_password(self, _conn):
        backup = enqueue_backup(self.user, **self.params())
        self.assertEqual(backup.status, Backup.QUEUED)
        self.assertNotIn("dbpass", backup.secret)
        self.assertEqual(crypto.decrypt(backup.secret), "dbpass")

    def test_connection_failure(self, conn):
        conn.return_value = False
        with self.assertRaisesMessage(BackupError, "Could not connect"):
            enqueue_backup(self.user, **self.params())
        self.assertFalse(Backup.objects.exists())

    def test_delta_needs_a_possible_base(self, _conn):
        with self.assertRaisesMessage(BackupError, "An incremental backup needs a full backup"):
            enqueue_backup(self.user, **self.params(type=self.incr))
        make_backup(self.user, self.storage, self.incr, db="shop")
        with self.assertRaisesMessage(BackupError, "A differential backup needs a full backup"):
            enqueue_backup(self.user, **self.params(type=self.diff))

    def test_queued_full_backup_counts_as_base(self, _conn):
        self.queued(self.full)
        backup = enqueue_backup(self.user, **self.params(type=self.incr))
        self.assertEqual(backup.status, Backup.QUEUED)

    def test_legacy_backup_counts_as_base(self, _conn):
        make_backup(self.user, self.storage, self.full, db="shop", state_name="")
        backup = enqueue_backup(self.user, **self.params(type=self.incr))
        self.assertEqual(backup.status, Backup.QUEUED)

    def test_unconfigured_storage(self, _conn):
        s3 = make_storage(location="s3://bucket", backend="s3")
        with self.assertRaisesMessage(BackupError, "The storage is not available"):
            enqueue_backup(self.user, **self.params(storage=s3))

    @override_settings(BACKUP_RUN_INLINE=True)
    @patch("core.services.full_backup", side_effect=fake_full_backup())
    def test_inline_mode_takes_backup_immediately(self, _full, _conn):
        backup = enqueue_backup(self.user, **self.params())
        self.assertEqual(backup.status, Backup.SUCCEEDED)
        self.assertEqual(backup.secret, "")


class RunBackupTests(ServiceTestBase):
    @patch("core.services.full_backup", side_effect=fake_full_backup("x" * 42))
    def test_full_backup_is_stored_and_password_erased(self, full):
        backup = self.run_job(self.queued())
        self.assertEqual(backup.status, Backup.SUCCEEDED)
        self.assertEqual(backup.size, 42)
        self.assertEqual(backup.secret, "")
        self.assertIsNotNone(backup.finished_at)
        backend = self.storage.open_backend()
        self.assertTrue(backend.exists(backup.file_name))
        self.assertTrue(backend.exists(backup.state_name))
        params = full.call_args.args[0]
        self.assertEqual(params["password"], "dbpass")
        self.assertEqual(params["dbname"], "shop")

    def test_delta_compares_with_base_state_and_records_base(self):
        with patch("core.services.full_backup", side_effect=fake_full_backup(state={"t": 1})):
            base = self.run_job(self.queued())
        seen = []
        with patch("core.services.delta_backup", side_effect=fake_delta_backup(seen=seen)):
            delta = self.run_job(self.queued(self.incr))
        self.assertEqual(delta.status, Backup.SUCCEEDED)
        self.assertEqual(delta.base, base)
        self.assertEqual(seen, [{"format": 1, "tables": {"t": 1}}])

    def assert_promoted(self, backup, reason):
        self.assertEqual(backup.status, Backup.SUCCEEDED)
        self.assertEqual(backup.type, self.full)
        self.assertEqual(backup.promoted_reason, reason)
        self.assertIsNone(backup.base)
        self.assertEqual(backup.secret, "")

    @patch("core.services.full_backup", side_effect=fake_full_backup())
    def test_no_base_at_run_time_takes_full_backup(self, full):
        backup = self.run_job(self.queued(self.incr))
        self.assert_promoted(backup, "no_base")
        full.assert_called_once()

    @patch("core.services.full_backup", side_effect=fake_full_backup())
    def test_legacy_base_takes_full_backup(self, _full):
        make_backup(self.user, self.storage, self.full, db="shop", state_name="")
        backup = self.run_job(self.queued(self.diff))
        self.assert_promoted(backup, "old_base")

    def test_schema_change_takes_full_backup(self):
        with patch("core.services.full_backup", side_effect=fake_full_backup()):
            self.run_job(self.queued())
        needs_full = BackupFailed(BackupFailed.NEEDS_FULL, "new table public.x", reason="schema_changed")
        with patch("core.services.delta_backup", side_effect=needs_full), \
                patch("core.services.full_backup", side_effect=fake_full_backup("-- full again")) as full:
            backup = self.run_job(self.queued(self.incr))
        self.assert_promoted(backup, "schema_changed")
        self.assertEqual(backup.error_detail, "new table public.x")
        full.assert_called_once()
        with self.storage.open_backend().open(backup.file_name) as handle:
            self.assertEqual(handle.read(), b"-- full again")

    @patch("core.services.delta_backup",
           side_effect=BackupFailed(BackupFailed.CONNECTION, "gone"))
    def test_other_delta_errors_still_fail(self, _delta):
        with patch("core.services.full_backup", side_effect=fake_full_backup()):
            self.run_job(self.queued())
        backup = self.run_job(self.queued(self.incr))
        self.assertEqual((backup.status, backup.error_code), (Backup.FAILED, BackupFailed.CONNECTION))

    @patch("core.services.full_backup",
           side_effect=BackupFailed(BackupFailed.CONNECTION, "password authentication failed"))
    def test_backup_failure_keeps_code_and_detail(self, _full):
        backup = self.run_job(self.queued())
        self.assertEqual(backup.status, Backup.FAILED)
        self.assertEqual(backup.error_code, BackupFailed.CONNECTION)
        self.assertIn("password authentication", backup.error_detail)
        self.assertEqual(backup.file_name, "")

    @patch("core.services.full_backup", side_effect=RuntimeError("unexpected"))
    def test_unexpected_error_does_not_escape(self, _full):
        backup = self.run_job(self.queued())
        self.assertEqual((backup.status, backup.error_code), (Backup.FAILED, "internal"))

    def test_unconfigured_storage_fails_the_job(self):
        s3 = make_storage(location="s3://bucket", backend="s3")
        backup = make_backup(self.user, s3, self.full, db="shop", status=Backup.QUEUED,
                             secret=crypto.encrypt("dbpass"))
        backup = self.run_job(backup)
        self.assertEqual((backup.status, backup.error_code), (Backup.FAILED, "storage"))

    @patch("core.services.full_backup", side_effect=fake_full_backup())
    def test_job_deleted_while_running_leaves_no_files(self, _full):
        backup = self.queued()
        backup.status = Backup.RUNNING
        backup.save()
        Backup.objects.filter(pk=backup.pk).delete()
        run_backup(backup)
        self.assertEqual(os.listdir(self.media_root), [])


class WorkerTests(ServiceTestBase):
    def test_claims_oldest_job_and_marks_it_running(self):
        first = self.queued()
        self.queued()
        job = claim_next_job()
        self.assertEqual(job, first)
        self.assertEqual(job.status, Backup.RUNNING)
        self.assertIsNotNone(job.started_at)

    def test_empty_queue(self):
        self.assertIsNone(claim_next_job())

    def test_stale_running_jobs_fail(self):
        stale = self.queued()
        Backup.objects.filter(pk=stale.pk).update(
            status=Backup.RUNNING, started_at=timezone.now() - timedelta(days=1)
        )
        fresh = self.queued()
        Backup.objects.filter(pk=fresh.pk).update(status=Backup.RUNNING, started_at=timezone.now())
        self.assertEqual(fail_stale_jobs(), 1)
        stale.refresh_from_db()
        self.assertEqual((stale.status, stale.error_code, stale.secret), (Backup.FAILED, "interrupted", ""))

    @patch("core.services.delta_backup", side_effect=fake_delta_backup())
    @patch("core.services.full_backup", side_effect=fake_full_backup())
    def test_command_processes_queue_in_order(self, _full, _delta):
        full = self.queued()
        incr = self.queued(self.incr)
        out = StringIO()
        call_command("backup_worker", "--once", stdout=out)
        self.assertIn("Processed 2 job(s)", out.getvalue())
        full.refresh_from_db()
        incr.refresh_from_db()
        self.assertEqual((full.status, incr.status), (Backup.SUCCEEDED, Backup.SUCCEEDED))
        self.assertEqual(incr.base, full)


@mock_aws
@override_settings(STORAGES=S3_STORAGES)
class S3StorageTests(ServiceTestBase):
    def setUp(self):
        super().setUp()
        boto3.client("s3", region_name="us-east-1").create_bucket(Bucket="test-backups")
        self.s3 = make_storage(location="s3://test-backups/datastudio", backend="s3")

    def objects(self):
        client = boto3.client("s3", region_name="us-east-1")
        return sorted(o["Key"] for o in client.list_objects_v2(Bucket="test-backups").get("Contents", []))

    @patch("core.services.full_backup", side_effect=fake_full_backup("SELECT 1;"))
    def test_backup_download_and_delete_through_s3(self, _full):
        backup = make_backup(self.user, self.s3, self.full, db="shop", status=Backup.QUEUED,
                             secret=crypto.encrypt("dbpass"))
        backup = self.run_job(backup)
        self.assertEqual(backup.status, Backup.SUCCEEDED)
        self.assertEqual(
            self.objects(),
            [f"datastudio/backup{backup.pk}.sql", f"datastudio/backup{backup.pk}.state.zip"],
        )
        self.assertEqual(os.listdir(self.media_root), [])

        self.client.force_login(self.user)
        response = self.client.get(f"/download/{backup.id}")
        self.assertEqual(b"".join(response.streaming_content), b"SELECT 1;")

        self.client.post(f"/remove/{backup.id}")
        self.assertEqual(self.objects(), [])

    def test_seed_demo_registers_s3_storage(self):
        Storage.objects.filter(backend="s3").delete()
        boto3.client("s3", region_name="us-east-1").delete_bucket(Bucket="test-backups")
        with override_settings(DEMO_USERNAME=""):
            call_command("seed_demo", stdout=StringIO())
            call_command("seed_demo", stdout=StringIO())
        self.assertEqual(Storage.objects.filter(backend="s3").count(), 1)
        self.assertEqual(self.objects(), [])  # бакет создан


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
        make_backup(self.user, self.storage, self.full, db="mine", status=Backup.FAILED)
        make_backup(self.other, self.storage, self.full, db="theirs", size=999)
        self.login()
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(mine, response.context["backups"])
        self.assertEqual(len(response.context["backups"]), 2)
        # в сводку входят только готовые копии
        self.assertEqual(response.context["stats"]["count"], 1)
        self.assertEqual(response.context["stats"]["total_size"], 100)
        self.assertNotContains(response, "theirs")

    def test_job_statuses_and_auto_refresh(self):
        self.queued()
        make_backup(self.user, self.storage, self.full, db="shop",
                    status=Backup.FAILED, error_code=BackupFailed.CONNECTION)
        make_backup(self.user, self.storage, self.full, db="shop", promoted_reason="schema_changed")
        self.login()
        response = self.client.get("/")
        self.assertContains(response, "Queued")
        self.assertContains(response, "Could not connect to the database")
        self.assertContains(response, "Taken as a full backup: the database schema changed")
        self.assertContains(response, 'http-equiv="refresh"')

    def test_no_refresh_when_idle(self):
        make_backup(self.user, self.storage, self.full)
        self.login()
        self.assertNotContains(self.client.get("/"), 'http-equiv="refresh"')

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

    def test_storages_counts_only_own_backups_and_shows_backend(self):
        make_backup(self.user, self.storage, self.full)
        make_backup(self.other, self.storage, self.full)
        make_storage(location="s3://bucket", backend="s3")
        self.login()
        response = self.client.get("/storages")
        storage = next(s for s in response.context["storages"] if s.pk == self.storage.pk)
        self.assertEqual(storage.backup_count, 1)
        self.assertContains(response, "Local disk")
        self.assertContains(response, "not configured")

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

    def test_unconfigured_storages_are_not_offered(self):
        s3 = make_storage(location="s3://bucket", backend="s3")
        self.login()
        queryset = self.client.get("/create").context["form"].fields["storage"].queryset
        self.assertIn(self.storage, queryset)
        self.assertNotIn(s3, queryset)

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

    @patch("core.views.enqueue_backup", side_effect=BackupError("База недоступна"))
    def test_service_error_shown_on_form(self, _enqueue):
        self.login()
        response = self.client.post("/create", self.payload())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["form"].non_field_errors(), ["База недоступна"])

    @patch("core.services.check_db_connection", return_value=True)
    def test_success_queues_and_redirects(self, _conn):
        self.login()
        response = self.client.post("/create", self.payload(), follow=True)
        self.assertRedirects(response, "/")
        self.assertContains(response, "Backup of “shop” queued")
        backup = Backup.objects.get()
        self.assertEqual((backup.status, backup.host, backup.port), (Backup.QUEUED, "db.internal", 5432))


class DownloadViewTests(ViewTestBase):
    def test_requires_auth(self):
        backup = make_backup(self.user, self.storage, self.full)
        self.assertEqual(self.client.get(f"/download/{backup.id}").status_code, 302)

    def test_download_own_backup(self):
        backup = make_backup(self.user, self.storage, self.full, db="shop")
        self.put_file(backup, b"SELECT 1;")
        self.login()
        response = self.client.get(f"/download/{backup.id}")
        self.assertEqual(response.status_code, 200)
        self.assertIn('filename="shop_full_', response["Content-Disposition"])
        self.assertEqual(b"".join(response.streaming_content), b"SELECT 1;")

    def test_not_ready_backup_is_not_downloadable(self):
        backup = self.queued()
        self.login()
        self.assertRedirects(self.client.get(f"/download/{backup.id}"), "/",
                             fetch_redirect_response=False)

    def test_missing_file_redirects_with_error(self):
        backup = make_backup(self.user, self.storage, self.full)
        self.login()
        response = self.client.get(f"/download/{backup.id}", follow=True)
        self.assertContains(response, "The backup file is missing from the storage")

    def test_others_backup_is_not_found(self):
        backup = make_backup(self.other, self.storage, self.full)
        self.put_file(backup)
        self.login()
        self.assertEqual(self.client.get(f"/download/{backup.id}").status_code, 404)


class RemoveViewTests(ViewTestBase):
    def test_get_renders_confirmation_with_dependents(self):
        backup = make_backup(self.user, self.storage, self.full)
        make_backup(self.user, self.storage, self.incr, base=backup)
        self.login()
        response = self.client.get(f"/remove/{backup.id}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["dependents"], 1)
        self.assertContains(response, "1 later backup was taken on top of this one")

    def test_post_deletes_backup_and_files(self):
        backup = make_backup(self.user, self.storage, self.full)
        self.put_file(backup)
        self.put_file(backup, b"{}", attr="state_name")
        self.login()
        response = self.client.post(f"/remove/{backup.id}")
        self.assertRedirects(response, "/", fetch_redirect_response=False)
        self.assertFalse(Backup.objects.filter(id=backup.id).exists())
        self.assertEqual(os.listdir(self.media_root), [])

    def test_post_missing_file_still_deletes_record(self):
        backup = make_backup(self.user, self.storage, self.full)
        self.login()
        self.client.post(f"/remove/{backup.id}")
        self.assertFalse(Backup.objects.filter(id=backup.id).exists())

    def test_running_backup_cannot_be_deleted(self):
        backup = self.queued()
        Backup.objects.filter(pk=backup.pk).update(status=Backup.RUNNING)
        self.login()
        response = self.client.post(f"/remove/{backup.id}", follow=True)
        self.assertContains(response, "being taken right now")
        self.assertTrue(Backup.objects.filter(id=backup.id).exists())

    def test_queued_backup_can_be_cancelled(self):
        backup = self.queued()
        self.login()
        self.client.post(f"/remove/{backup.id}")
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

    def test_every_language_is_translated(self):
        expected = {
            "ru": ("Типы копий", "Дифференциальная"),
            "fr": ("Types de sauvegarde", "Différentielle"),
            "de": ("Sicherungstypen", "Differenziell"),
        }
        for language, phrases in expected.items():
            with self.subTest(language=language):
                self.client.cookies["django_language"] = language
                page = self.client.get("/backup_types")
                self.assertContains(page, f'<html lang="{language}">')
                for phrase in phrases:
                    self.assertContains(page, phrase)

    def test_switcher_lists_all_languages(self):
        page = self.client.get("/")
        for code in ("en", "ru", "fr", "de"):
            self.assertContains(page, f'name="language" value="{code}"')

    def test_language_does_not_leak_after_request(self):
        from django.utils import translation

        self.client.cookies["django_language"] = "de"
        self.client.get("/")
        self.assertEqual(translation.get_language(), "en")

    def test_unknown_language_cookie_falls_back_to_english(self):
        self.client.cookies["django_language"] = "it"
        self.assertContains(self.client.get("/"), '<html lang="en">')

    def test_russian_plural_forms(self):
        self.client.cookies["django_language"] = "ru"
        storage = make_storage()
        full = BackupType.objects.get(code=BackupType.FULL)
        for db in ("a", "b", "c", "d", "e"):
            make_backup(self.user, storage, full, db=db)
        self.assertContains(self.client.get("/"), "5 баз данных")

    def test_german_relative_time_is_grammatical(self):
        """Регрессия: встроенный перевод Django давал «1 Tage, 1 Stunde her»."""
        full = BackupType.objects.get(code=BackupType.FULL)
        ago = timezone.now() - timedelta(days=1, hours=1, minutes=5)
        make_backup(self.user, make_storage(), full, created_at=ago)
        make_backup(self.user, make_storage(), full, created_at=ago - timedelta(days=1))
        self.client.cookies["django_language"] = "de"
        page = self.client.get("/").content.decode().replace("\xa0", " ")
        self.assertIn("vor 1 Tag, 1 Stunde", page)
        self.assertIn("vor 2 Tagen, 1 Stunde", page)
        self.assertNotIn(" her<", page)

    def test_builtin_storage_names_are_translated(self):
        """Хранилище из миграции переводится, заданное администратором — нет."""
        full = BackupType.objects.get(code=BackupType.FULL)
        make_backup(self.user, make_storage("BACKUP_DIR on the server"), full)
        make_backup(self.user, make_storage("s3://bucket/backups"), full)
        self.client.cookies["django_language"] = "fr"
        page = self.client.get("/").content.decode()
        self.assertIn("Répertoire BACKUP_DIR du serveur", page)
        self.assertNotIn("on the server", page)
        self.assertIn("s3://bucket/backups", page)

    def test_service_errors_are_translated(self):
        from django.utils import translation

        storage = make_storage()
        incr = BackupType.objects.get(code=BackupType.INCREMENTAL)
        with translation.override("ru"):
            with self.assertRaisesMessage(BackupError, "Для инкрементальной копии"):
                enqueue_backup(
                    self.user, type=incr, host="h", port=5432, db="shop",
                    username="u", password="p", storage=storage,
                )
