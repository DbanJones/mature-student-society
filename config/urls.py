from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("", include("core.urls")),
    path("accounts/", include("accounts.urls")),
    path("events/", include("events.urls")),
    path("guide/", include("guide.urls")),
    path("supper-club/", include("supper.urls")),
    path("me/", include("dashboard.urls")),
    path("admin/", include("panel.urls")),
    # Django's low-level admin, superuser only; the society-facing admin panel
    # lives at /admin/.
    path("dj-admin/", admin.site.urls),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
