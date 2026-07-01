"""Template CRUD views: list, create/edit, delete, and JSON endpoint."""
from __future__ import annotations

import json
from typing import Any

from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.utils.text import slugify
from django.views import View
from django.views.generic import ListView, TemplateView

from ..forms import NotificationTemplateForm
from ..models import NotificationCampaign, NotificationTemplate, NotificationVariable
from .helpers import StaffRequiredMixin


class TemplateListView(StaffRequiredMixin, ListView):
    """List all active notification templates."""

    model = NotificationTemplate
    template_name = "coldfront_notifications/template_list.html"
    context_object_name = "templates"

    def get_queryset(self):
        return super().get_queryset().filter(is_deleted=False)


class TemplateFormView(StaffRequiredMixin, TemplateView):
    """Create or edit a notification template."""

    template_name = "coldfront_notifications/template_form.html"
    model = NotificationTemplate

    def get_instance(self):
        pk = self.kwargs.get("pk")
        return get_object_or_404(self.model, pk=pk) if pk else None

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        instance = self.get_instance()
        form = kwargs.get("form") or NotificationTemplateForm(instance=instance)

        variables_json = json.dumps([
            {
                "key": variable.key,
                "label": variable.label,
                "description": variable.description,
                "example": variable.example,
                "source": variable.source,
            }
            for variable in NotificationVariable.objects.filter(is_deleted=False)
        ])

        context.update({
            "form": form,
            "template": instance,
            "variables_json": variables_json,
        })
        return context

    def post(self, request, *args: Any, **kwargs: Any):
        instance = self.get_instance()
        form = NotificationTemplateForm(request.POST, instance=instance)

        if form.is_valid():
            saved_template = form.save(commit=False)
            if not saved_template.slug:
                saved_template.slug = slugify(saved_template.name)
            saved_template.save()
            action_word = "updated" if instance else "created"
            messages.success(request, f'Template "{saved_template.name}" {action_word}.')
            return redirect("notifications:template-list")

        return self.render_to_response(self.get_context_data(form=form))


class TemplateDeleteView(StaffRequiredMixin, TemplateView):
    """Confirm and soft-delete a notification template."""

    template_name = "coldfront_notifications/template_delete_confirm.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        instance = get_object_or_404(NotificationTemplate, pk=kwargs["pk"])
        context.update({
            "template": instance,
            "notification_count": (
                NotificationCampaign.objects
                .filter(template=instance)
                .count()
            ),
        })
        return context

    def post(self, request, *args: Any, **kwargs: Any):
        instance = get_object_or_404(NotificationTemplate, pk=kwargs["pk"])
        instance.is_deleted = True
        instance.save(update_fields=["is_deleted"])
        messages.success(request, f'Template "{instance.name}" deleted.')
        return redirect("notifications:template-list")


class TemplateJsonView(StaffRequiredMixin, View):
    """Return a single template's live content as JSON for the compose sidebar."""

    def get(self, request, *args: Any, **kwargs: Any) -> JsonResponse:
        template = get_object_or_404(NotificationTemplate, pk=kwargs["pk"])
        return JsonResponse({
            "id": template.pk,
            "slug": template.slug,
            "name": template.name,
            "subject": template.subject,
            "body": template.body,
            "variables": template.variables,
        })
