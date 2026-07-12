from django.urls import path

from . import views

app_name = "inbox"

urlpatterns = [
    path("", views.inbox, name="inbox"),
    path("<str:username>/", views.thread, name="thread"),
    path("<str:username>/block/", views.block_toggle, name="block_toggle"),
]
