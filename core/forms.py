from django import forms
from django.utils.translation import gettext_lazy as _

from core.models import BackupType, Storage


class BackupForm(forms.Form):
    """Параметры подключения к исходной базе и способ копирования."""

    type = forms.ModelChoiceField(
        label=_("Backup type"),
        queryset=BackupType.objects.all(),
        empty_label=None,
        widget=forms.RadioSelect,
        error_messages={"invalid_choice": _("This backup type does not exist")},
    )
    host = forms.CharField(
        label=_("Host"),
        max_length=255,
        widget=forms.TextInput(attrs={"placeholder": _("db.example.com or 10.0.0.5")}),
    )
    port = forms.IntegerField(
        label=_("Port"),
        min_value=1,
        max_value=65535,
        initial=5432,
    )
    db = forms.CharField(
        label=_("Database"),
        max_length=255,
        widget=forms.TextInput(attrs={"placeholder": "shop"}),
    )
    username = forms.CharField(
        label=_("Database user"),
        max_length=255,
        widget=forms.TextInput(attrs={"placeholder": "postgres", "autocomplete": "off"}),
    )
    password = forms.CharField(
        label=_("Database password"),
        max_length=255,
        strip=False,
        help_text=_("Used only to take this backup and never stored."),
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )
    storage = forms.ModelChoiceField(
        label=_("Storage"),
        queryset=Storage.objects.select_related("type"),
        empty_label=None,
        error_messages={"invalid_choice": _("This storage does not exist")},
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # По умолчанию предлагаем полную копию: остальные без неё невозможны.
        self.fields["type"].initial = (
            BackupType.objects.filter(code=BackupType.FULL).values_list("pk", flat=True).first()
        )
