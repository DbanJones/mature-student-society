from django.contrib import admin

from .models import Poll, PollOption, PollVote


class PollOptionInline(admin.TabularInline):
    model = PollOption
    extra = 0


@admin.register(Poll)
class PollAdmin(admin.ModelAdmin):
    list_display = ["question", "kind", "event", "status", "closes_at", "outcome_option"]
    list_filter = ["kind", "status"]
    inlines = [PollOptionInline]


@admin.register(PollVote)
class PollVoteAdmin(admin.ModelAdmin):
    list_display = ["poll", "option", "user", "created_at"]
