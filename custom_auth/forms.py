from django import forms
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password


class LoginForm(AuthenticationForm):
    error_messages = {
        "invalid_login": "Неверный логин или пароль",
        "inactive": "Учётная запись отключена",
    }


class RegisterForm(UserCreationForm):
    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("username",)


class ProfileForm(forms.ModelForm):
    """Смена логина и, по желанию, пароля. Пустой пароль — оставить прежний."""

    new_password1 = forms.CharField(
        label="Новый пароль",
        required=False,
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
        help_text="Оставьте пустым, чтобы не менять.",
    )
    new_password2 = forms.CharField(
        label="Повторите пароль",
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
                self.add_error("new_password2", "Пароли не совпадают")
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
