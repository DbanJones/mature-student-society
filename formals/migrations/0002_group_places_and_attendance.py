"""Keep existing one-place bookings intact while enabling group reservations."""

import django.core.validators
from django.db import migrations, models


def preserve_attendance(apps, schema_editor):
    Booking = apps.get_model("formals", "FormalBooking")
    Exchange = apps.get_model("formals", "FormalExchange")
    alias = schema_editor.connection.alias
    for booking in Booking.objects.using(alias).iterator(chunk_size=200):
        attended = booking.status == "attended"
        booking.host_attendance = [0] if attended or booking.host_confirmed else None
        booking.guest_attendance = [0] if attended or booking.guest_confirmed else None
        if attended:
            booking.host_confirmed = booking.guest_confirmed = True
        snapshot = dict(booking.terms_snapshot or {})
        snapshot.setdefault("places", 1)
        if booking.return_college:
            snapshot.setdefault("agreed_return_places", 1)
        booking.terms_snapshot = snapshot
        booking.save(using=alias, update_fields=["host_attendance", "guest_attendance", "host_confirmed", "guest_confirmed", "terms_snapshot"])
    for exchange in Exchange.objects.using(alias).iterator(chunk_size=200):
        snapshot = dict(exchange.terms_snapshot or {})
        snapshot.setdefault("places", 1)
        snapshot.setdefault("agreed_return_places", 1)
        exchange.terms_snapshot = snapshot
        exchange.save(using=alias, update_fields=["terms_snapshot"])


class Migration(migrations.Migration):
    dependencies = [("formals", "0001_initial")]

    operations = [
        migrations.AddField(
            model_name="formallisting", name="return_places",
            field=models.PositiveSmallIntegerField(default=0, help_text="Zero means the same number as the booking; otherwise the host's requested return places.", validators=[django.core.validators.MaxValueValidator(3)]),
        ),
        migrations.AddField(
            model_name="formalbooking", name="places",
            field=models.PositiveSmallIntegerField(default=1, validators=[django.core.validators.MinValueValidator(1), django.core.validators.MaxValueValidator(3)]),
        ),
        migrations.AddField(model_name="formalbooking", name="companion_names", field=models.JSONField(blank=True, default=list)),
        migrations.AddField(model_name="formalbooking", name="host_attendance", field=models.JSONField(blank=True, default=None, null=True)),
        migrations.AddField(model_name="formalbooking", name="guest_attendance", field=models.JSONField(blank=True, default=None, null=True)),
        migrations.AddField(
            model_name="formalexchange", name="return_places",
            field=models.PositiveSmallIntegerField(default=1, validators=[django.core.validators.MinValueValidator(1), django.core.validators.MaxValueValidator(3)]),
        ),
        migrations.AlterField(
            model_name="formalbooking", name="status",
            field=models.CharField(choices=[("requested", "Awaiting host approval"), ("held", "Held for a return invitation"), ("reserved", "Reserved"), ("attended", "Attendance confirmed"), ("absent", "No guests attended"), ("cancelled", "Cancelled"), ("declined", "Declined"), ("withdrawn", "Withdrawn"), ("no_show", "Attendance disputed")], default="requested", max_length=12),
        ),
        migrations.AddConstraint(
            model_name="formallisting",
            constraint=models.CheckConstraint(condition=models.Q(return_places__lte=3), name="formal_requested_returns_0_to_3"),
        ),
        migrations.AddConstraint(
            model_name="formalbooking",
            constraint=models.CheckConstraint(condition=models.Q(places__gte=1, places__lte=3), name="formal_booking_places_1_to_3"),
        ),
        migrations.AddConstraint(
            model_name="formalexchange",
            constraint=models.CheckConstraint(condition=models.Q(return_places__gte=1, return_places__lte=3), name="formal_exchange_returns_1_to_3"),
        ),
        migrations.RunPython(preserve_attendance, migrations.RunPython.noop),
    ]
