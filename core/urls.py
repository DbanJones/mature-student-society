from django.urls import path

from . import views

app_name = "core"

urlpatterns = [
    path("", views.home, name="home"),
    path("about/", views.about, name="about"),
    path("winter-ball/", views.winter_ball, name="winter_ball"),
    path("wellbeing/", views.wellbeing, name="wellbeing"),
    path("community-policies/", views.policies, name="policies"),
    path("terms/", views.terms, name="terms"),
    path("pages/<slug:slug>/", views.site_page, name="site_page"),
]
