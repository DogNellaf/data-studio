"""
Строки Django, перевод которых проект переопределяет в ``locale/``.

Модуль нигде не импортируется: он нужен, чтобы ``makemessages`` находил эти
строки и не выбрасывал их из каталогов проекта. Каталоги из ``LOCALE_PATHS``
важнее каталогов Django, поэтому исправленный перевод побеждает встроенный.

Зачем: во встроенном немецком переводе ``naturaltime`` выдаёт «1 Tage,
1 Stunde her». Правильно — «vor 1 Tag, 1 Stunde» и «vor 2 Tagen»: после «vor»
нужен дательный падеж.
"""
from django.utils.translation import gettext_noop, npgettext_lazy

NATURALTIME_PAST = (
    gettext_noop("%(delta)s ago"),
    npgettext_lazy("naturaltime-past", "%(num)d year", "%(num)d years", "num"),
    npgettext_lazy("naturaltime-past", "%(num)d month", "%(num)d months", "num"),
    npgettext_lazy("naturaltime-past", "%(num)d week", "%(num)d weeks", "num"),
    npgettext_lazy("naturaltime-past", "%(num)d day", "%(num)d days", "num"),
    npgettext_lazy("naturaltime-past", "%(num)d hour", "%(num)d hours", "num"),
    npgettext_lazy("naturaltime-past", "%(num)d minute", "%(num)d minutes", "num"),
)
