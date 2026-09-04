from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    path("login/", views.login_view, name="login"),
    path("login/dev/", views.dev_login, name="dev_login"),
    path("logout/", views.LogoutView.as_view(), name="logout"),
    path("raven/", views.raven, name="raven"),
    path("terms/", views.terms, name="terms"),
    path("profile/setup/", views.profile_setup, name="profile_setup"),
    path("profile/", views.profile, name="profile"),
    path("profile/password/", views.PasswordChangeView.as_view(), name="password_change"),
    path("waitlist/", views.waitlist, name="waitlist"),
    path("waitlist/thanks/", views.waitlist_done, name="waitlist_done"),
    path("set-password/<uidb64>/<token>/", views.SetPasswordView.as_view(), name="set_password"),
    path("whatsapp/", views.whatsapp, name="whatsapp"),
]
