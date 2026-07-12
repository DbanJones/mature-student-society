from django.db import models


class SiteConfig(models.Model):
    """Singleton for society-wide settings, editable in the admin panel."""

    society_name = models.CharField(
        max_length=120, default="University of Cambridge Mature Student Society"
    )
    short_name = models.CharField(max_length=40, default="MSS")
    tagline = models.CharField(
        max_length=200,
        default="Welcoming Cambridge's mature students.",
    )
    about_text = models.TextField(blank=True, help_text="Markdown; shown on the public About page.")
    contact_email = models.EmailField(default="maturesoc@cambridgesu.co.uk")
    mailing_list_address = models.EmailField(
        default="soc-mss-members@srcf.net",
        help_text="Where the What's On mailer is sent (SRCF Mailman list).",
    )
    whatsapp_group_link = models.URLField(
        blank=True,
        help_text="The group invite link revealed once to each approved member.",
    )
    instagram_url = models.URLField(blank=True)
    facebook_url = models.URLField(blank=True)

    class Meta:
        verbose_name = "site configuration"

    def __str__(self):
        return self.society_name

    def save(self, *args, **kwargs):
        self.pk = 1  # enforce singleton
        super().save(*args, **kwargs)

    @classmethod
    def get(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj
