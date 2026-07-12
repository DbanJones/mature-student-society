"""Members-only pages at /members/: the directory and member profiles."""

from django.urls import path

from . import views

app_name = "members"

urlpatterns = [
    path("", views.member_directory, name="directory"),
    path("<str:username>/", views.member_profile, name="profile"),
]
