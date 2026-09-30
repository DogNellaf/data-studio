from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Create or update the demo user from DEMO_USERNAME / DEMO_PASSWORD."

    def handle(self, *args, **options):
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
