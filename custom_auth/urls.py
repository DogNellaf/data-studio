from django.urls import path
from custom_auth import views

urlpatterns = [
    path('login', views.login, name="custom_auth.login"),
    path('register', views.register, name="custom_auth.register"),
    path('profile', views.profile, name="custom_auth.profile"),
    path('logout', views.logout, name="custom_auth.logout")
]
