from django.urls import path

from . import views

app_name = "supper"

urlpatterns = [
    path("", views.index, name="index"),
    path("restaurant/<int:pk>/", views.restaurant, name="restaurant"),
    path("rate/<int:event_pk>/", views.rate, name="rate"),
]
