"""Raven / University Account authentication plumbing.

Three modes, selected by the RAVEN_MODE environment variable (see settings):

- ``dev``    (local development): a clearly-labelled dev login page lets you
  impersonate seeded demo users. Never enable in production.

- ``header`` (SRCF "Track A", zero registration): Apache's mod_ucam_webauth —
  which on SRCF authenticates against Nevar, the SRCF's Ucam-WebAuth bridge to
  real University Account (Entra + MFA) login — protects the /accounts/raven/
  path and forwards the authenticated CRSid to Django in the
  ``X-MSS-Remote-User`` request header. The middleware below trusts that
  header. SECURITY: it must ONLY be trusted when Apache is guaranteed to have
  set it — the deployment guide pins this down (socket permissions + the
  header being unconditionally overwritten in .htaccess).

- ``oidc``   (SRCF "Track B", first-party): OpenID Connect against the
  University's Microsoft Entra tenant via a UIS Toolkit app registration.
  NOT YET IMPLEMENTED — selecting this mode currently does nothing; the wire-up
  (mozilla-django-oidc, a ``/oidc/callback/`` route, and a claim mapping that
  derives the CRSid from ``upn``/``preferred_username`` only when the domain is
  exactly ``cam.ac.uk``) still has to be written. See DEPLOYMENT_SRCF.md §8.
"""

from django.contrib.auth.backends import RemoteUserBackend
from django.contrib.auth.middleware import PersistentRemoteUserMiddleware


class NevarRemoteUserMiddleware(PersistentRemoteUserMiddleware):
    """Reads the CRSid that Apache (mod_ucam_webauth via Nevar) injects.

    ``header`` is the CGI-style key for the X-MSS-Remote-User HTTP header.
    Only active when RAVEN_MODE=header.
    """

    header = "HTTP_X_MSS_REMOTE_USER"


class CrsidBackend(RemoteUserBackend):
    """Creates/updates portal users from an authenticated CRSid."""

    create_unknown_user = True

    def clean_username(self, username):
        return username.strip().lower()

    def configure_user(self, request, user, created=True):
        if created or not user.crsid:
            user.crsid = user.username
            user.account_type = user.AccountType.RAVEN
            if not user.email:
                user.email = f"{user.username}@cam.ac.uk"
            user.save(update_fields=["crsid", "account_type", "email"])
        return user

    def user_can_authenticate(self, user):
        # Banned members stay out even though Raven itself would let them in.
        return super().user_can_authenticate(user) and not user.is_banned
