from django.contrib import admin
from django.urls import reverse
from django.utils.html import format_html

from .models import Choice, Question, Survey


class ChoiceInline(admin.TabularInline):
    model = Choice
    extra = 0
    readonly_fields = ("label", "sort_order")

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class SurveyInspectionAdmin(admin.ModelAdmin):
    """Use the survey builder, which serializes edits with member responses."""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Question)
class QuestionAdmin(SurveyInspectionAdmin):
    list_display = ("prompt", "survey", "kind", "required", "sort_order")
    list_filter = ("kind", "survey")
    inlines = [ChoiceInline]
    readonly_fields = ("edit_question",)

    @admin.display(description="Edit this question")
    def edit_question(self, obj):
        return format_html(
            '<a href="{}">Open this question in the survey builder</a>',
            reverse("surveys:question_edit", args=[obj.survey.slug, obj.pk]),
        )


@admin.register(Survey)
class SurveyAdmin(SurveyInspectionAdmin):
    list_display = ("title", "status", "opens_at", "closes_at", "created_by")
    list_filter = ("status",)
    search_fields = ("title",)
    filter_horizontal = ("admins",)
    readonly_fields = ("manage_survey",)

    @admin.display(description="Manage this survey")
    def manage_survey(self, obj):
        return format_html(
            '<a href="{}">Open the survey builder and controls</a> · <a href="{}">Survey settings</a>',
            obj.get_absolute_url(), reverse("surveys:edit", args=[obj.slug]),
        )
