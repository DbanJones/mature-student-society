from django.urls import path

from . import views

app_name = "dashboard"

urlpatterns = [
    path("", views.home, name="home"),
    path("game/scores/", views.game_scores, name="game_scores"),
    path("calendar.ics", views.calendar_feed, name="calendar_feed"),
    path("calendar/reset/", views.calendar_token_reset, name="calendar_token_reset"),
]
