from django.urls import path

from . import views

app_name = "events"

urlpatterns = [
    path("", views.calendar_view, name="calendar"),
    path("new/", views.create, name="create"),
    path("<int:pk>/", views.detail, name="detail"),
    path("<int:pk>/edit/", views.edit, name="edit"),
    path("<int:pk>/cancel/", views.cancel, name="cancel"),
    path("<int:pk>/rsvp/", views.rsvp, name="rsvp"),
    path("<int:pk>/export/", views.export, name="export"),
]
