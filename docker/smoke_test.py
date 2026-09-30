"""
Сквозная проверка запущенного docker compose: копии через очередь и воркер.

    docker compose exec -T app python manage.py shell < docker/smoke_test.py

Ставит полную копию демо-базы в каждое хранилище (локальный диск и MinIO),
меняет данные, ставит инкрементальную с удалением строки и ждёт, пока воркер
всё снимет. Любой сбой — ненулевой код выхода.
"""
import sys
import time

import psycopg2
from django.contrib.auth.models import User

from core.models import Backup, BackupType, Storage
from core.services import enqueue_backup

SOURCE = {"host": "demo-db", "port": 5432, "db": "shop", "username": "shop", "password": "shop"}


def wait(backups, timeout=120):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        states = {b.pk: Backup.objects.get(pk=b.pk) for b in backups}
        if all(not b.is_active for b in states.values()):
            return list(states.values())
        time.sleep(1)
    sys.exit(f"Timed out waiting for backups {[b.pk for b in backups]}")


user = User.objects.get(username="demo")
full, incremental = (BackupType.objects.get(code=c) for c in ("full", "incremental"))
storages = list(Storage.objects.all())
print("Storages:", [(s.pk, s.backend) for s in storages])
if {s.backend for s in storages} != {"local", "s3"}:
    sys.exit("Expected both a local and an S3 storage")

fulls = wait([enqueue_backup(user, type=full, storage=s, **SOURCE) for s in storages])

connection = psycopg2.connect(host="demo-db", dbname="shop", user="shop", password="shop")
with connection, connection.cursor() as cursor:
    cursor.execute("UPDATE products SET price = price + 1 WHERE id = 1")
    cursor.execute("DELETE FROM order_items WHERE order_id = 1")
connection.close()

deltas = wait([enqueue_backup(user, type=incremental, storage=s, **SOURCE) for s in storages])

failed = False
for backup in fulls + deltas:
    print(f"#{backup.pk} {backup.type.code:<12} {backup.storage.backend:<5} "
          f"{backup.status:<9} {backup.size or 0:>8} B {backup.error_code}")
    failed |= backup.status != Backup.SUCCEEDED

for delta in deltas:
    with delta.storage.open_backend().open(delta.file_name, "rb") as handle:
        sql = handle.read().decode()
    if "DELETE FROM" not in sql or 'INSERT INTO "public"."products"' not in sql:
        print(f"#{delta.pk}: the delta is missing the expected changes")
        failed = True

sys.exit(1 if failed else 0)
