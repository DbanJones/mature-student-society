from django.urls import path

from . import views

app_name = "panel"

urlpatterns = [
    path("", views.home, name="home"),
    path("waitlist/", views.waitlist, name="waitlist"),
    path("waitlist/<int:pk>/review/", views.waitlist_review, name="waitlist_review"),
    path("members/", views.members, name="members"),
    path("members/<int:pk>/edit/", views.member_edit, name="member_edit"),
    path("members/<int:pk>/toggle-admin/", views.member_toggle_admin, name="member_toggle_admin"),
    path("members/<int:pk>/ban/", views.member_ban, name="member_ban"),
    path("members/<int:pk>/unban/", views.member_unban, name="member_unban"),
    path("members/<int:pk>/delete/", views.member_delete, name="member_delete"),
    path("members/<int:pk>/reset-whatsapp/", views.member_reset_whatsapp, name="member_reset_whatsapp"),
    path("whatsapp/", views.whatsapp_requests, name="whatsapp_requests"),
    path("whatsapp/<int:pk>/handle/", views.whatsapp_handle, name="whatsapp_handle"),
    path("stats/", views.stats, name="stats"),
    path("mailer/", views.mailer, name="mailer"),
    path("audit/", views.audit, name="audit"),
]
