"""Queue matching publication alerts inside the existing ledger transaction."""
from django.utils import timezone

from inbox.models import MessageBlock

from .models import HOST_COLLEGES, FormalAlert, FormalAlertDelivery


def notify_matching_members(listing):
    # Publication is the only trigger: no backfill, reservation-change spam or
    # external mail delivery while the ledger is locked.
    from .services import _tell

    host = listing.host
    if not listing.is_open or not host.is_active or host.is_banned or host.is_shadow_banned:
        return 0
    dinner_date = timezone.localdate(listing.event.start)
    alerts = FormalAlert.objects.filter(
        active=True, member__is_active=True, member__is_banned=False,
        member__is_shadow_banned=False, min_places__lte=listing.capacity,
    ).exclude(member_id=host.pk).select_related("member")
    matched = set()
    delivered = 0
    for alert in alerts.iterator(chunk_size=200):
        member = alert.member
        if member.pk in matched:
            continue
        if any((alert.college and alert.college != listing.college,
                alert.invitation_type and alert.invitation_type != listing.invitation_type,
                alert.booking_mode and alert.booking_mode != listing.booking_mode,
                alert.after and dinner_date < alert.after,
                alert.before and dinner_date > alert.before)):
            continue
        if listing.invitation_type == "reciprocal" and (
            member.college not in dict(HOST_COLLEGES)
            or listing.allowed_return_colleges and member.college not in listing.allowed_return_colleges
        ):
            continue
        if MessageBlock.exists_between(host, member):
            continue
        matched.add(member.pk)
        _, created = FormalAlertDelivery.objects.get_or_create(member=member, listing=listing)
        if created:
            _tell([member], f"A formal matches your alert: {listing.event.title} at {listing.get_college_display()} on {dinner_date:%d %b}.", listing)
            delivered += 1
    return delivered
