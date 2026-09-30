from django import forms

from core.models import BackupType, Storage


class BackupForm(forms.Form):
    """Параметры подключения к исходной базе и способ копирования."""

    type = forms.ModelChoiceField(
        label="Тип резервной копии",
        queryset=BackupType.objects.all(),
        empty_label=None,
        widget=forms.RadioSelect,
        error_messages={"invalid_choice": "Указанный тип копирования не существует"},
    )
    host = forms.CharField(
        label="Хост",
        max_length=255,
        widget=forms.TextInput(attrs={"placeholder": "db.example.com или 10.0.0.5"}),
    )
    port = forms.IntegerField(
        label="Порт",
        min_value=1,
        max_value=65535,
        initial=5432,
    )
    db = forms.CharField(
        label="База данных",
        max_length=255,
        widget=forms.TextInput(attrs={"placeholder": "shop"}),
    )
    username = forms.CharField(
        label="Пользователь БД",
        max_length=255,
        widget=forms.TextInput(attrs={"placeholder": "postgres", "autocomplete": "off"}),
    )
    password = forms.CharField(
        label="Пароль БД",
        max_length=255,
        strip=False,
        help_text="Используется только для снятия копии и нигде не сохраняется.",
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )
    storage = forms.ModelChoiceField(
        label="Хранилище",
        queryset=Storage.objects.select_related("type"),
        empty_label=None,
        error_messages={"invalid_choice": "Указанное хранилище не существует"},
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # По умолчанию предлагаем полную копию: остальные без неё невозможны.
        self.fields["type"].initial = (
            BackupType.objects.filter(code=BackupType.FULL).values_list("pk", flat=True).first()
        )
