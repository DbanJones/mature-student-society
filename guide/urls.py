from django.urls import path

from . import views

app_name = "guide"

urlpatterns = [
    path("", views.index, name="index"),
    # "new/" must precede the slug route; "new" is also a reserved slug
    # (see views._unique_slug) so a page can never shadow this URL.
    path("new/", views.new, name="new"),
    path("<slug:slug>/", views.page, name="page"),
    path("<slug:slug>/edit/", views.edit, name="edit"),
    path("<slug:slug>/history/", views.history, name="history"),
    path("<slug:slug>/history/<int:revision_id>/", views.revision, name="revision"),
    path("<slug:slug>/history/<int:revision_id>/restore/", views.restore, name="restore"),
]
