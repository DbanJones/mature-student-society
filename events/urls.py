from django.urls import path

from . import views

app_name = "events"

urlpatterns = [
    path("", views.calendar_view, name="calendar"),
    path("new/", views.create, name="create"),
    # Tag subpages (Supper Club, History Club, …).
    path("tags/<slug:slug>/", views.tag_page, name="tag_page"),
    path("tags/<slug:slug>/edit/", views.tag_edit, name="tag_edit"),
    # Old numeric URLs redirect permanently to the slug form.
    path("<int:pk>/", views.detail_by_pk, name="detail_pk"),
    path("<slug:slug>.ics", views.ics, name="ics"),
    path("<slug:slug>/duplicate/", views.duplicate, name="duplicate"),
    path("<slug:slug>/repeat/", views.repeat, name="repeat"),
    # Event pages, named after the event and its date.
    path("<slug:slug>/", views.detail, name="detail"),
    path("<slug:slug>/edit/", views.edit, name="edit"),
    path("<slug:slug>/cancel/", views.cancel, name="cancel"),
    path("<slug:slug>/rsvp/", views.rsvp, name="rsvp"),
    path("<slug:slug>/export/", views.export, name="export"),
]
