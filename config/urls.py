from django.contrib import admin
from django.urls import include, path, re_path

from config.media import protected_media
from posters.views import scan as poster_scan

urlpatterns = [
    path("", include("core.urls")),
    path("accounts/", include("accounts.urls")),
    path("events/", include("events.urls")),
    path("guide/", include("guide.urls")),
    path("faq/", include("faq.urls")),
    path("supper-club/", include("supper.urls")),
    path("testimonials/", include("testimonials.urls")),
    path("polls/", include("polls.urls")),
    path("notifications/", include("notifications.urls")),
    path("posters/", include("posters.urls")),
    path("surveys/", include("surveys.urls")),
    # The short link printed in poster QR codes: counts the scan, then sends
    # the phone on to the event page.
    path("p/<slug:slug>/", poster_scan, name="poster_scan"),
    path("members/", include("accounts.member_urls")),
    path("messages/", include("inbox.urls")),
    path("me/", include("dashboard.urls")),
    path("admin/", include("panel.urls")),
    # Django's low-level admin, superuser only; the society-facing admin panel
    # lives at /admin/.
    path("dj-admin/", admin.site.urls),
    # Uploads (member photos) are members-only in every environment.
    re_path(r"^media/(?P<path>.*)$", protected_media, name="protected_media"),
]
