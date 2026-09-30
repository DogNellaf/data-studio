from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from core.backends import get_backend, is_configured
from core.models import Storage, StorageType


class Command(BaseCommand):
    help = (
        "Create or update the demo user from DEMO_USERNAME / DEMO_PASSWORD and "
        "register the S3 storage when BACKUP_S3_BUCKET is set."
    )

    def handle(self, *args, **options):
        self._seed_user()
        self._seed_s3_storage()

    def _seed_user(self):
        username = settings.DEMO_USERNAME
        password = settings.DEMO_PASSWORD
        if not username:
            self.stdout.write("DEMO_USERNAME is not set, skipping the demo user.")
            return
        if not password:
            raise CommandError("DEMO_USERNAME is set but DEMO_PASSWORD is not")

        user, created = get_user_model().objects.get_or_create(username=username)
        user.set_password(password)
        user.save()
        action = "created" if created else "updated"
        self.stdout.write(self.style.SUCCESS(f"Demo user '{username}' {action}"))

    def _seed_s3_storage(self):
        if not is_configured("s3"):
            return
        backend = get_backend("s3")
        # Для демо с MinIO бакета ещё нет; у настоящего S3 он обычно создан заранее.
        client = backend.connection.meta.client
        existing = {bucket["Name"] for bucket in client.list_buckets().get("Buckets", [])}
        if backend.bucket_name not in existing:
            client.create_bucket(Bucket=backend.bucket_name)
            self.stdout.write(self.style.SUCCESS(f"Bucket '{backend.bucket_name}' created"))

        if Storage.objects.filter(backend="s3").exists():
            return
        storage_type, _created = StorageType.objects.get_or_create(title="S3")
        location = f"s3://{backend.bucket_name}/{backend.location}".rstrip("/")
        Storage.objects.create(location=location, type=storage_type, backend="s3")
        self.stdout.write(self.style.SUCCESS(f"S3 storage '{location}' registered"))
