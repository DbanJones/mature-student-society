from django.urls import path

from . import views

app_name = "core"

urlpatterns = [
    path("", views.home, name="home"),
    path("menu/", views.menu, name="menu"),
    path("preview/", views.preview_markdown, name="preview"),
    path("about/", views.about, name="about"),
    path("winter-ball/", views.winter_ball, name="winter_ball"),
    path("wellbeing/", views.wellbeing, name="wellbeing"),
    path("community-policies/", views.policies, name="policies"),
    path("terms/", views.terms, name="terms"),
    path("search/", views.search, name="search"),
    path("banner/dismiss/", views.dismiss_banner, name="dismiss_banner"),
    path("pages/<slug:slug>/", views.site_page, name="site_page"),
    path("pages/<slug:slug>/edit/", views.site_page_edit, name="site_page_edit"),
    path("pages/<slug:slug>/history/", views.site_page_history, name="site_page_history"),
    path(
        "pages/<slug:slug>/history/<int:revision_id>/",
        views.site_page_revision, name="site_page_revision",
    ),
    path(
        "pages/<slug:slug>/history/<int:revision_id>/restore/",
        views.site_page_restore, name="site_page_restore",
    ),
    path(
        "pages/<slug:slug>/history/<int:revision_id>/diff/",
        views.site_page_diff, name="site_page_diff",
    ),
]
