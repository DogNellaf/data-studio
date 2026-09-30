from django import forms
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.utils.translation import gettext_lazy as _


class LoginForm(AuthenticationForm):
    error_messages = {
        "invalid_login": _("Incorrect username or password"),
        "inactive": _("This account is disabled"),
    }


class RegisterForm(UserCreationForm):
    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("username",)


class ProfileForm(forms.ModelForm):
    """Смена логина и, по желанию, пароля. Пустой пароль — оставить прежний."""

    new_password1 = forms.CharField(
        label=_("New password"),
        required=False,
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
        help_text=_("Leave empty to keep the current password."),
    )
    new_password2 = forms.CharField(
        label=_("Repeat password"),
        required=False,
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )

    class Meta:
        model = User
        fields = ("username",)

    def clean(self):
        cleaned_data = super().clean()
        password1 = cleaned_data.get("new_password1")
        password2 = cleaned_data.get("new_password2")
        if password1 or password2:
            if password1 != password2:
                self.add_error("new_password2", _("The passwords do not match"))
            else:
                try:
                    validate_password(password1, self.instance)
                except forms.ValidationError as exc:
                    self.add_error("new_password1", exc)
        return cleaned_data

    def save(self, commit=True):
        user = super().save(commit=False)
        if self.cleaned_data.get("new_password1"):
            user.set_password(self.cleaned_data["new_password1"])
        if commit:
            user.save()
        return user
