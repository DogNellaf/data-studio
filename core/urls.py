from django.urls import path
from core import views

urlpatterns = [
    path('', views.index, name="core.index"),
    path('create', views.create, name="core.create"),
    path('remove/<int:id>', views.remove, name="core.remove"),
    path('storages', views.storages, name="core.storages"),
    path('backup_types', views.backup_types, name="core.backup_types"),
    path('download/<int:id>', views.download, name="core.download")
]