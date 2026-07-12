from django.urls import path

from . import views

app_name = "core"

urlpatterns = [
    path("", views.home, name="home"),
    path("about/", views.about, name="about"),
    path("wellbeing/", views.wellbeing, name="wellbeing"),
    path("community-policies/", views.policies, name="policies"),
]
