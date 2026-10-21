import django.core.validators
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("formals", "0002_group_places_and_attendance"), migrations.swappable_dependency(settings.AUTH_USER_MODEL)]

    operations = [
        migrations.CreateModel(
            name="FormalAlert",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("college", models.CharField(blank=True, choices=[("christs", "Christ's"), ("churchill", "Churchill"), ("clare", "Clare"), ("clare-hall", "Clare Hall"), ("corpus", "Corpus Christi"), ("darwin", "Darwin"), ("downing", "Downing"), ("emmanuel", "Emmanuel"), ("fitzwilliam", "Fitzwilliam"), ("girton", "Girton"), ("gonville-caius", "Gonville & Caius"), ("homerton", "Homerton"), ("hughes-hall", "Hughes Hall"), ("jesus", "Jesus"), ("kings", "King's"), ("lucy-cavendish", "Lucy Cavendish"), ("magdalene", "Magdalene"), ("murray-edwards", "Murray Edwards"), ("newnham", "Newnham"), ("pembroke", "Pembroke"), ("peterhouse", "Peterhouse"), ("queens", "Queens'"), ("robinson", "Robinson"), ("selwyn", "Selwyn"), ("sidney-sussex", "Sidney Sussex"), ("st-catharines", "St Catharine's"), ("st-edmunds", "St Edmund's"), ("st-johns", "St John's"), ("trinity", "Trinity"), ("trinity-hall", "Trinity Hall"), ("wolfson", "Wolfson")], max_length=32)),
                ("invitation_type", models.CharField(blank=True, choices=[("open", "Open invitation (no return promise)"), ("reciprocal", "Reciprocal swap")], max_length=12)),
                ("booking_mode", models.CharField(blank=True, choices=[("approval", "Host chooses guests"), ("instant", "Instant reservation")], max_length=12)),
                ("after", models.DateField(blank=True, null=True)),
                ("before", models.DateField(blank=True, null=True)),
                ("min_places", models.PositiveSmallIntegerField(default=1, validators=[django.core.validators.MinValueValidator(1), django.core.validators.MaxValueValidator(3)])),
                ("active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("member", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="formal_alerts", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-created_at", "-pk"], "constraints": [models.CheckConstraint(condition=models.Q(min_places__gte=1, min_places__lte=3), name="formal_alert_places_1_to_3")]},
        ),
        migrations.CreateModel(
            name="FormalAlertDelivery",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("listing", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="alert_deliveries", to="formals.formallisting")),
                ("member", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="formal_alert_deliveries", to=settings.AUTH_USER_MODEL)),
            ],
            options={"constraints": [models.UniqueConstraint(fields=("member", "listing"), name="formal_alert_once_per_member")]},
        ),
    ]
