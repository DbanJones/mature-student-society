from django.urls import path

from . import views

app_name = "testimonials"

urlpatterns = [
    path("", views.index, name="index"),
    path("submit/", views.submit, name="submit"),
    path("<int:pk>/withdraw/", views.withdraw, name="withdraw"),
]
