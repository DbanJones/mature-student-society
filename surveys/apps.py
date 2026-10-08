from django.apps import AppConfig


class SurveysConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "surveys"
    verbose_name = "Surveys"

    def ready(self):
        from django.contrib.auth import get_user_model
        from django.db.models.signals import pre_delete

        from .models import forget_member

        pre_delete.connect(forget_member, sender=get_user_model(), dispatch_uid="surveys_forget_member")
