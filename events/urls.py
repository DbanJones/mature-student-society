from django.urls import path

from . import views

app_name = "events"

urlpatterns = [
    path("", views.calendar_view, name="calendar"),
    path("new/", views.create, name="create"),
    # Groups: one page per tag (Supper Club, History Club, …).
    path("groups/", views.groups, name="groups"),
    path("groups/<slug:slug>/", views.tag_page, name="tag_page"),
    path("groups/<slug:slug>/edit/", views.tag_edit, name="tag_edit"),
    # Tag pages used to live under tags/; keep old links working.
    path("tags/<slug:slug>/", views.tag_page_moved, name="tag_page_moved"),
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
