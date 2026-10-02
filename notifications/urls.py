from django.urls import path

from . import views

app_name = "notifications"

urlpatterns = [
    path("", views.index, name="index"),
    path("<int:pk>/go/", views.go, name="go"),
    path("read-all/", views.mark_all_read, name="mark_all_read"),
]
