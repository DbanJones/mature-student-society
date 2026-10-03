from django.urls import path

from . import views

app_name = "posters"

urlpatterns = [
    path("<slug:slug>/", views.studio, name="studio"),
    path("<slug:slug>/print/", views.print_page, name="print"),
    path("<slug:slug>/poster.svg", views.svg, name="svg"),
    path("<slug:slug>/map.png", views.map_image, name="map"),
]
