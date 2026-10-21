from django.contrib import admin
from django.utils.html import format_html

from .models import Poll, PollOption, PollVote


class PollOptionInline(admin.TabularInline):
    model = PollOption
    extra = 0
    readonly_fields = ("label", "start", "capacity", "is_opt_out", "sort_order")

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class LedgerAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Poll)
class PollAdmin(LedgerAdmin):
    list_display = ["question", "kind", "event", "status", "closes_at", "outcome_option"]
    list_filter = ["kind", "status"]
    inlines = [PollOptionInline]
    readonly_fields = ["manage_poll"]

    @admin.display(description="Manage this poll")
    def manage_poll(self, obj):
        return format_html('<a href="{}">Open the poll to vote, close it or manage its result</a>', obj.get_absolute_url())

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "event":
            from events.models import Event

            kwargs["queryset"] = Event.objects.filter(formal_listing__isnull=True)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


@admin.register(PollVote)
class PollVoteAdmin(LedgerAdmin):
    list_display = ["poll", "option", "user", "created_at"]
