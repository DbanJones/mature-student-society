import os

from django.apps import AppConfig


class PanelConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'panel'

    def ready(self):
        from django.contrib.auth import get_user_model
        from django.db.models.signals import post_delete

        from . import listsync

        # however an account is deleted, its address is queued to come off
        # the society's mailing list (Admin > People > Mailing list)
        post_delete.connect(listsync.account_deleted, sender=get_user_model(),
                            dispatch_uid="panel.listsync.account_deleted")

        # Web workers note which code they started with, so the Site health
        # page can tell when new code is waiting for a restart. run_on_srcf.sh
        # sets MSS_WEB_PROCESS=1 just before it starts gunicorn.
        if os.environ.get("MSS_WEB_PROCESS") == "1":
            from .healthchecks.deploy import record_boot

            try:
                record_boot()
            except Exception:  # never stop the site starting
                pass
