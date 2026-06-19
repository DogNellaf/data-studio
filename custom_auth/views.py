from django.shortcuts import render, redirect
from django.contrib.auth.models import User
from django.contrib.auth import authenticate, login as auth_login, logout as auth_logout
from django.contrib import messages

def login(request):
    if request.method == "POST":
        username = request.POST.get("login")
        password = request.POST.get("password")

        user = authenticate(request, username=username, password=password)
        if user is not None:
            auth_login(request, user)
            return redirect("/auth/profile")
        else:
            messages.error(request, "Неверный логин или пароль")
            return render(request, "login.html")
    else:
        return render(request=request, template_name="login.html")

def register(request):
    if request.method == "POST":
        username = request.POST.get("username")
        password = request.POST.get("password")
        confirm_password = request.POST.get("confirm_password")

        if User.objects.filter(username=username).exists():
            messages.error(request, "Пользователь с таким логином уже существует")
            return render(request, "register.html")

        if username == "":
            messages.error(request, "Не указано имя пользователя")
            return render(request, "register.html")

        if password == "":
            messages.error(request, "Не указан пароль")
            return render(request, "register.html")

        if password != confirm_password:
            messages.error(request, "Пароли не совпадают")
            return render(request, "register.html")
        
        User.objects.create_user(username=username, password=password)
        messages.success(request, "Регистрация прошла успешно! Вы можете войти.")
        return redirect("/auth/login")
    else:
        return render(request=request, template_name="register.html")

def profile(request):
    user = request.user

    if not user.is_authenticated:
        return redirect("/auth/login")

    if request.method == "POST":
        username = request.POST.get("username")
        password = request.POST.get("password")
        confirm_password = request.POST.get("confirm_password")

        if User.objects.filter(username=username).exclude(id=user.id).exists():
            messages.error(request, "Пользователь с таким логином уже существует")
            return render(request=request, template_name="profile.html")
        
        if username == "":
            messages.error(request, "Не указано имя пользователя")
            return render(request, "profile.html")

        if password == "":
            messages.error(request, "Не указан пароль")
            return render(request, "profile.html")

        if password != confirm_password:
            messages.error(request, "Пароли не совпадают")
            return render(request=request, template_name="profile.html")

        user.set_password(password)
        user.username = username
        user.save()
        messages.info(request, "Пароль и логин успешно изменены")
    
    return render(request=request, template_name="profile.html")

def logout(request):
    auth_logout(request)
    return redirect(login)