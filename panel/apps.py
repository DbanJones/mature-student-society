import os

from django.apps import AppConfig


class PanelConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'panel'

    def ready(self):
        # Web workers note which code they started with, so the Site health
        # page can tell when new code is waiting for a restart. run_on_srcf.sh
        # sets MSS_WEB_PROCESS=1 just before it starts gunicorn.
        if os.environ.get("MSS_WEB_PROCESS") == "1":
            from .healthchecks.deploy import record_boot

            try:
                record_boot()
            except Exception:  # never stop the site starting
                pass
