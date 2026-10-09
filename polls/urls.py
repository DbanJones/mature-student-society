from django.urls import path

from . import views

app_name = "polls"

urlpatterns = [
    path("", views.index, name="index"),
    path("new/", views.choose, name="choose_general"),
    path("new/<slug:kind>/", views.create, name="create_general"),
    path("event/<slug:slug>/new/", views.choose, name="choose"),
    path("event/<slug:slug>/new/<slug:kind>/", views.create, name="create"),
    path("<int:pk>/", views.detail, name="detail"),
    path("<int:pk>/vote/", views.vote, name="vote"),
    path("<int:pk>/close/", views.close, name="close"),
    path("<int:pk>/delete/", views.delete, name="delete"),
    path("<int:pk>/roster/", views.roster, name="roster"),
]
