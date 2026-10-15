from django.urls import path

from . import views

app_name = "surveys"

urlpatterns = [
    path("", views.index, name="index"),
    path("new/", views.create, name="create"),
    path("<slug:slug>/", views.detail, name="detail"),
    path("<slug:slug>/results/", views.results, name="results"),
    path("<slug:slug>/edit/", views.edit, name="edit"),
    path("<slug:slug>/open/", views.open_survey, name="open"),
    path("<slug:slug>/close/", views.close_survey, name="close"),
    path("<slug:slug>/delete/", views.delete_survey, name="delete"),
    path("<slug:slug>/remind/", views.remind, name="remind"),
    path("<slug:slug>/questions/new/", views.question_add, name="question_add"),
    path("<slug:slug>/questions/<int:pk>/edit/", views.question_edit, name="question_edit"),
    path("<slug:slug>/questions/<int:pk>/delete/", views.question_delete, name="question_delete"),
    path("<slug:slug>/questions/<int:pk>/move/", views.question_move, name="question_move"),
]
