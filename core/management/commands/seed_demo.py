from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Создаёт (или обновляет) демо-пользователя из DEMO_USERNAME / DEMO_PASSWORD."

    def handle(self, *args, **options):
        username = settings.DEMO_USERNAME
        password = settings.DEMO_PASSWORD
        if not username:
            self.stdout.write("DEMO_USERNAME не задан — демо-пользователь не нужен.")
            return
        if not password:
            raise CommandError("Задан DEMO_USERNAME, но не задан DEMO_PASSWORD")

        user, created = get_user_model().objects.get_or_create(username=username)
        user.set_password(password)
        user.save()
        action = "создан" if created else "обновлён"
        self.stdout.write(self.style.SUCCESS(f"Демо-пользователь «{username}» {action}"))
