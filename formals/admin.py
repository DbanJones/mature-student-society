"""The Django admin is an inspection surface; policy changes use member views."""
from django.contrib import admin

from .models import FormalActivity, FormalBooking, FormalEmail, FormalExchange, FormalListing, ReturnProposal


class LedgerAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(FormalListing)
class ListingAdmin(LedgerAdmin):
    list_display = ("event", "college", "invitation_type", "booking_mode", "capacity")
    list_filter = ("college", "invitation_type", "booking_mode")


@admin.register(FormalBooking)
class BookingAdmin(LedgerAdmin):
    list_display = ("listing", "guest", "places", "status", "purpose", "ticket_status")
    list_filter = ("status", "purpose", "ticket_status")


@admin.register(FormalExchange)
class ExchangeAdmin(LedgerAdmin):
    list_display = ("id", "creditor", "debtor", "return_college", "return_places", "return_by", "status")
    list_filter = ("status", "return_college")


@admin.register(FormalEmail)
class EmailAdmin(LedgerAdmin):
    list_display = ("id", "recipient", "attempts", "available_at", "sent_at", "last_error")
    list_filter = ("sent_at",)


admin.site.register(ReturnProposal, LedgerAdmin)
admin.site.register(FormalActivity, LedgerAdmin)
