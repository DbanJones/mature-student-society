from django.conf import settings
from django.db import models


class KeepyUppyScore(models.Model):
    """Best keepy-uppy score per member — the hidden football easter egg.

    (Type b-a-l-l anywhere on the site while logged in.)
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name="keepy_uppy_score",
    )
    best = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-best", "updated_at"]

    def __str__(self):
        return f"{self.user} — {self.best} keepy-uppies"
