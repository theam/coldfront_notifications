"""Settings views: variables CRUD, senders CRUD, variables JSON, settings page."""
from __future__ import annotations

from typing import Any

from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.views import View
from django.views.generic import ListView, TemplateView

from ..forms import NotificationVariableForm, SenderConfigForm
from ..models import NotificationTemplate, NotificationVariable, SenderConfig
from ..template_variable_value_resolver import registry as resolver_registry
from .helpers import StaffRequiredMixin


class VariableListView(StaffRequiredMixin, ListView):
    """List all active notification variables."""

    model = NotificationVariable
    template_name = "coldfront_notifications/variable_list.html"
    context_object_name = "variables"

    def get_queryset(self):
        return super().get_queryset().filter(is_deleted=False)


class VariableFormView(StaffRequiredMixin, TemplateView):
    """Create or edit a notification variable."""

    template_name = "coldfront_notifications/variable_form.html"
    model = NotificationVariable

    def get_instance(self):
        pk = self.kwargs.get("pk")
        return get_object_or_404(self.model, pk=pk) if pk else None

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        instance = self.get_instance()
        form = kwargs.get("form") or NotificationVariableForm(instance=instance)

        context.update({
            "form": form,
            "variable": instance,
            "query_choices": resolver_registry.choices,
        })
        return context

    def post(self, request, *args: Any, **kwargs: Any):
        instance = self.get_instance()
        form = NotificationVariableForm(request.POST, instance=instance)

        if form.is_valid():
            saved_variable = form.save()
            action_word = "updated" if instance else "created"
            messages.success(
                request,
                f'Variable "{{{{{saved_variable.key}}}}}" {action_word}.',
            )
            return redirect("notifications:variable-list")

        return self.render_to_response(self.get_context_data(form=form))


class VariableDeleteView(StaffRequiredMixin, TemplateView):
    """Confirm and soft-delete a notification variable."""

    template_name = "coldfront_notifications/variable_delete_confirm.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        instance = get_object_or_404(NotificationVariable, pk=kwargs["pk"])
        affected_templates = [
            template
            for template in NotificationTemplate.objects.filter(is_deleted=False)
            if instance.key in template.variables
        ]
        context.update({
            "variable": instance,
            "affected_templates": affected_templates,
        })
        return context

    def post(self, request, *args: Any, **kwargs: Any):
        instance = get_object_or_404(NotificationVariable, pk=kwargs["pk"])
        instance.is_deleted = True
        instance.save(update_fields=["is_deleted"])
        messages.success(
            request,
            f'Variable "{{{{{instance.key}}}}}" deleted.',
        )
        return redirect("notifications:variable-list")


class VariablesJsonView(StaffRequiredMixin, View):
    """Return the full catalog of notification variables as JSON."""

    def get(self, request, *args: Any, **kwargs: Any) -> JsonResponse:
        variables = NotificationVariable.objects.filter(is_deleted=False)
        return JsonResponse({
            "variables": [
                {
                    "key": variable.key,
                    "label": variable.label,
                    "description": variable.description,
                    "example": variable.example,
                    "source": variable.source,
                    "input_widget": variable.input_widget,
                    "is_required": variable.is_required,
                }
                for variable in variables
            ]
        })


class SettingsView(StaffRequiredMixin, TemplateView):
    """Plugin settings page showing sender configurations."""

    template_name = "coldfront_notifications/settings.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context["senders"] = SenderConfig.objects.all()
        return context


class SenderFormView(StaffRequiredMixin, TemplateView):
    """Create or edit a sender email configuration."""

    template_name = "coldfront_notifications/sender_form.html"
    model = SenderConfig

    def get_instance(self):
        pk = self.kwargs.get("pk")
        return get_object_or_404(self.model, pk=pk) if pk else None

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        instance = self.get_instance()
        form = kwargs.get("form") or SenderConfigForm(instance=instance)
        context.update({
            "form": form,
            "sender": instance,
        })
        return context

    def post(self, request, *args: Any, **kwargs: Any):
        instance = self.get_instance()
        form = SenderConfigForm(request.POST, instance=instance)

        if form.is_valid():
            saved_sender = form.save()
            action_word = "updated" if instance else "added"
            messages.success(request, f'Address "{saved_sender.email}" {action_word}.')
            return redirect("notifications:settings")

        return self.render_to_response(self.get_context_data(form=form))


class SenderDeleteView(StaffRequiredMixin, View):
    """Delete a sender email configuration."""

    def post(self, request, *args: Any, **kwargs: Any):
        sender = get_object_or_404(SenderConfig, pk=kwargs["pk"])
        email = sender.email
        sender.delete()
        messages.success(request, f'Address "{email}" removed.')
        return redirect("notifications:settings")
