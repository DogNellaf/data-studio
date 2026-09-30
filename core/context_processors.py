from django.conf import settings


def app_info(request):
    """Данные, общие для всех шаблонов: демо-учётка для страницы входа."""
    return {
        "demo_username": settings.DEMO_USERNAME,
        "demo_password": settings.DEMO_PASSWORD,
    }
