from django.contrib import admin

from .models import Choice, Question, Survey


class ChoiceInline(admin.TabularInline):
    model = Choice
    extra = 0


@admin.register(Question)
class QuestionAdmin(admin.ModelAdmin):
    list_display = ("prompt", "survey", "kind", "required", "sort_order")
    list_filter = ("kind", "survey")
    inlines = [ChoiceInline]


@admin.register(Survey)
class SurveyAdmin(admin.ModelAdmin):
    list_display = ("title", "status", "opens_at", "closes_at", "created_by")
    list_filter = ("status",)
    search_fields = ("title",)
    filter_horizontal = ("admins",)
