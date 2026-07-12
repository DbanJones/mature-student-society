from django.urls import path

from . import views

app_name = "faq"

urlpatterns = [
    path("", views.index, name="index"),
    path("who-to-contact/", views.contacts, name="contacts"),
    path("colleges/", views.colleges, name="colleges"),
    path("colleges/<slug:slug>/", views.college, name="college"),
    path("departments/", views.departments, name="departments"),
    path("departments/<slug:slug>/", views.department, name="department"),
]
