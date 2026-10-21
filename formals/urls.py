from django.urls import path

from . import alert_views, report_views, views

app_name = "formals"

urlpatterns = [
    path("", views.index, name="index"),
    path("new/", views.create, name="create"),
    path("mine/", views.mine, name="mine"),
    path("alerts/", alert_views.alerts, name="alerts"),
    path("alerts/save/", alert_views.save_alert, name="save_alert"),
    path("alerts/<int:pk>/delete/", alert_views.delete_alert, name="delete_alert"),
    path("committee/", views.committee, name="committee"),
    path("committee/statistics/", report_views.statistics, name="statistics"),
    path("committee/statistics/export/", report_views.statistics_export, name="statistics_export"),
    path("<int:pk>/", views.detail, name="detail"),
    path("<int:pk>/edit/", views.edit, name="edit"),
    path("<int:pk>/apply/", views.apply, name="apply"),
    path("<int:pk>/cancel/", views.cancel, name="cancel"),
    path("requests/<int:pk>/<str:action>/", views.request_action, name="request_action"),
    path("bookings/<int:pk>/<str:action>/", views.booking_action, name="booking_action"),
    path("swaps/<int:pk>/", views.exchange, name="exchange"),
    path("swaps/<int:pk>/<str:action>/", views.exchange_action, name="exchange_action"),
    path("returns/<int:pk>/<str:action>/", views.proposal_action, name="proposal_action"),
]
