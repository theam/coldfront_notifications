from django import forms
from django.contrib import admin

from .models import (
    NotificationCampaign,
    NotificationLog,
    NotificationTemplate,
    NotificationVariable,
    SenderConfig,
)
from .template_variable_value_resolver import registry as resolver_registry


class NotificationVariableForm(forms.ModelForm):
    """
    Force resolver_key into a whitelisted dropdown built from resolver_registry.choices.
    When source=manual, the field is hidden/cleared.
    """
    resolver_key = forms.ChoiceField(
        required=False,
        choices=(
            [("", "— choose a query path —")]
            + [(k, f"{group}: {label}") for (k, label, group) in resolver_registry.choices]
        ),
        help_text="Only used when Source = Query.",
    )

    class Meta:
        model = NotificationVariable
        fields = [
            "key", "label", "description", "example",
            "source", "resolver_key",
            "input_widget", "is_required",
        ]

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("source") == NotificationVariable.Source.MANUAL:
            cleaned["resolver_key"] = ""
        elif cleaned.get("source") == NotificationVariable.Source.QUERY:
            if not cleaned.get("resolver_key"):
                raise forms.ValidationError(
                    "Pick a query source when Source = Query."
                )
        return cleaned


@admin.register(NotificationVariable)
class NotificationVariableAdmin(admin.ModelAdmin):
    form = NotificationVariableForm
    list_display  = ("key", "label", "source", "resolver_key", "input_widget", "is_required")
    list_filter   = ("source", "input_widget", "is_required")
    search_fields = ("key", "label", "resolver_key")
    ordering      = ("key",)


@admin.register(NotificationTemplate)
class NotificationTemplateAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "updated_at")
    search_fields = ("name", "slug", "subject")


@admin.register(SenderConfig)
class SenderConfigAdmin(admin.ModelAdmin):
    list_display = ("label", "email", "is_default", "is_reply_to")
    list_filter  = ("is_default", "is_reply_to")


@admin.register(NotificationCampaign)
class NotificationCampaignAdmin(admin.ModelAdmin):
    list_display = ("subject", "status", "recipient_count", "delivered_count", "failed_count", "created_at")
    list_filter  = ("status",)
    readonly_fields = ("created_at", "sent_at", "completed_at")


@admin.register(NotificationLog)
class NotificationLogAdmin(admin.ModelAdmin):
    list_display = ("campaign", "email", "status", "project_id", "allocation_id", "timestamp")
    list_filter  = ("status",)
    search_fields = ("email",)
