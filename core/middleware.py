from django.conf import settings
from django.utils import translation
from django.utils.cache import patch_vary_headers


class LanguageCookieMiddleware:
    """
    Выбирает язык только по cookie переключателя, иначе — LANGUAGE_CODE.

    Стандартный LocaleMiddleware смотрит ещё и на Accept-Language, и
    посетитель с русским браузером видел бы русский интерфейс вместо
    английского по умолчанию.
    """

    def __init__(self, get_response):
        self.get_response = get_response
        self.supported = {code for code, _name in settings.LANGUAGES}

    def __call__(self, request):
        language = request.COOKIES.get(settings.LANGUAGE_COOKIE_NAME)
        if language not in self.supported:
            language = settings.LANGUAGE_CODE

        request.LANGUAGE_CODE = language
        # override, а не activate: язык запроса не должен остаться активным
        # в потоке и просочиться в следующий запрос или фоновый код.
        with translation.override(language):
            response = self.get_response(request)
        response.headers.setdefault("Content-Language", language)
        patch_vary_headers(response, ("Cookie",))
        return response
