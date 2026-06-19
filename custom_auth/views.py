from django.contrib import messages
from django.contrib.auth import (
    authenticate,
    login as auth_login,
    logout as auth_logout,
    update_session_auth_hash,
)
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.shortcuts import redirect, render

LOGIN_URL = "/auth/login"


def login(request):
    if request.method != "POST":
        return render(request, "login.html")

    username = request.POST.get("login")
    password = request.POST.get("password")

    user = authenticate(request, username=username, password=password)
    if user is None:
        messages.error(request, "Неверный логин или пароль")
        return render(request, "login.html")

    auth_login(request, user)
    return redirect("/auth/profile")


def register(request):
    if request.method != "POST":
        return render(request, "register.html")

    username = request.POST.get("username")
    password = request.POST.get("password")
    confirm_password = request.POST.get("confirm_password")

    if not username:
        messages.error(request, "Не указано имя пользователя")
        return render(request, "register.html")

    if not password:
        messages.error(request, "Не указан пароль")
        return render(request, "register.html")

    if password != confirm_password:
        messages.error(request, "Пароли не совпадают")
        return render(request, "register.html")

    if User.objects.filter(username=username).exists():
        messages.error(request, "Пользователь с таким логином уже существует")
        return render(request, "register.html")

    User.objects.create_user(username=username, password=password)
    messages.success(request, "Регистрация прошла успешно! Вы можете войти.")
    return redirect("/auth/login")


@login_required(login_url=LOGIN_URL, redirect_field_name=None)
def profile(request):
    if request.method != "POST":
        return render(request, "profile.html")

    user = request.user
    username = request.POST.get("username")
    password = request.POST.get("password")
    confirm_password = request.POST.get("confirm_password")

    if not username:
        messages.error(request, "Не указано имя пользователя")
        return render(request, "profile.html")

    if User.objects.filter(username=username).exclude(id=user.id).exists():
        messages.error(request, "Пользователь с таким логином уже существует")
        return render(request, "profile.html")

    # Пароль необязателен: пустое поле означает «оставить прежний».
    if password:
        if password != confirm_password:
            messages.error(request, "Пароли не совпадают")
            return render(request, "profile.html")
        user.set_password(password)

    user.username = username
    user.save()

    # set_password меняет хэш сессии — без обновления пользователя разлогинит.
    update_session_auth_hash(request, user)

    messages.info(request, "Данные профиля успешно изменены")
    return render(request, "profile.html")


def logout(request):
    auth_logout(request)
    return redirect("/auth/login")
