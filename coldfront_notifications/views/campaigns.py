"""Campaign views: list, detail, progress polling, resend, resend-compose."""
from __future__ import annotations

import json
import logging
from typing import Any

from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.views import View
from django.views.generic import DetailView, ListView, TemplateView

from ..models import NotificationCampaign, NotificationLog, SenderConfig
from .helpers import StaffRequiredMixin, dispatch_send

logger = logging.getLogger(__name__)


class CampaignListView(StaffRequiredMixin, ListView):
    """List all campaigns, optionally filtered by status."""

    model = NotificationCampaign
    template_name = "coldfront_notifications/campaign_list.html"
    context_object_name = "campaigns"

    def get_queryset(self):
        queryset = super().get_queryset().select_related("template")
        status_filter = self.request.GET.get("status", "")
        if status_filter:
            queryset = queryset.filter(status=status_filter)
        return queryset

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context["status_filter"] = self.request.GET.get("status", "")
        return context


class CampaignDetailView(StaffRequiredMixin, DetailView):
    """Campaign detail with delivery logs."""

    model = NotificationCampaign
    template_name = "coldfront_notifications/campaign_detail.html"
    context_object_name = "campaign"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context["recipient_logs"] = self.object.logs.all()
        return context


class CampaignProgressView(StaffRequiredMixin, View):
    """Lightweight JSON endpoint for AJAX progress polling."""

    def get(self, request, *args: Any, **kwargs: Any) -> JsonResponse:
        campaign = get_object_or_404(NotificationCampaign, pk=kwargs["pk"])
        return JsonResponse({
            "status": campaign.status,
            "status_display": campaign.get_status_display(),
            "recipient_count": campaign.recipient_count,
            "delivered_count": campaign.delivered_count,
            "failed_count": campaign.failed_count,
            "delivery_pct": campaign.delivery_pct,
        })


class ResendFailedView(StaffRequiredMixin, View):
    """Re-queue failed recipients for an existing campaign."""

    def post(self, request, *args: Any, **kwargs: Any):
        campaign = get_object_or_404(NotificationCampaign, pk=kwargs["pk"])
        failed_emails = list(
            campaign.logs
            .filter(status=NotificationLog.Status.FAILED)
            .values_list("email", flat=True)
        )

        if not failed_emails:
            messages.info(request, "No failed recipients to resend.")
            return redirect("notifications:campaign-detail", pk=campaign.pk)

        campaign.logs.filter(
            status=NotificationLog.Status.FAILED,
        ).update(status=NotificationLog.Status.QUEUED)
        campaign.status = NotificationCampaign.Status.QUEUED
        campaign.save(update_fields=["status"])

        dispatch_send(campaign.pk)
        messages.info(request, "Resend queued \u2014 delivery in progress.")
        return redirect("notifications:campaign-detail", pk=campaign.pk)


class ResendComposeView(StaffRequiredMixin, TemplateView):
    """Review-and-resend page pre-filled from an existing campaign."""

    template_name = "coldfront_notifications/resend_compose.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        source_campaign = get_object_or_404(NotificationCampaign, pk=kwargs["pk"])
        snapshot = source_campaign.filters_snapshot or {}
        context.update({
            "source": source_campaign,
            "snapshot": snapshot,
            "snapshot_json": json.dumps(snapshot),
            "senders": SenderConfig.objects.all(),
        })
        return context

    def post(self, request, *args: Any, **kwargs: Any):
        source_campaign = get_object_or_404(NotificationCampaign, pk=kwargs["pk"])
        snapshot = source_campaign.filters_snapshot or {}

        subject = request.POST.get("subject", "").strip()
        body = request.POST.get("body", "").strip()
        sender = request.POST.get("sender", "")
        reply_to = request.POST.get("reply_to", "")

        if not subject or not body:
            messages.error(request, "Subject and body are required.")
            return redirect("notifications:resend-compose", pk=source_campaign.pk)

        campaign = NotificationCampaign.objects.create(
            template=source_campaign.template,
            subject=subject,
            body=body,
            sender=sender,
            reply_to=reply_to,
            status=NotificationCampaign.Status.QUEUED,
            filters_snapshot=snapshot,
            extra_context=source_campaign.extra_context or {},
            recipient_count=0,
            created_by=request.user,
        )
        dispatch_send(campaign.pk)
        messages.info(request, "Resend queued \u2014 sending in progress.")
        return redirect("notifications:campaign-detail", pk=campaign.pk)
