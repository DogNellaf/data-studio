from django.contrib import messages
from django.contrib.auth import get_user_model, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView, LogoutView
from django.shortcuts import redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods

from custom_auth.forms import LoginForm, ProfileForm, RegisterForm

login = LoginView.as_view(
    template_name="login.html",
    authentication_form=LoginForm,
    redirect_authenticated_user=True,
)

# Выход только POST-запросом: GET-ссылку можно подсунуть в <img> на чужой
# странице и разлогинить пользователя (Django 5 требует того же).
logout = LogoutView.as_view()


@require_http_methods(["GET", "POST"])
def register(request):
    if request.user.is_authenticated:
        return redirect("core.index")

    form = RegisterForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, _("Your account is ready, you can sign in now"))
        return redirect("custom_auth.login")

    return render(request, "register.html", {"form": form})


@login_required
@require_http_methods(["GET", "POST"])
def profile(request):
    # Форма правит отдельную копию пользователя: ModelForm записывает введённые
    # значения в instance ещё при валидации, и при ошибке в шапке страницы
    # оказался бы неподтверждённый логин.
    user = get_user_model().objects.get(pk=request.user.pk)
    form = ProfileForm(request.POST or None, instance=user)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        # Смена пароля меняет хэш сессии — без обновления пользователя разлогинит.
        update_session_auth_hash(request, user)
        messages.success(request, _("Profile saved"))
        return redirect("custom_auth.profile")

    return render(request, "profile.html", {"form": form})
