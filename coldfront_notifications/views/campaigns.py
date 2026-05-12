import json
import logging

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required as staff_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from ..models import NotificationCampaign, NotificationLog, SenderConfig
from .compose import _dispatch_send

logger = logging.getLogger(__name__)


@staff_required
def campaign_list(request):
    status_filter = request.GET.get("status", "")
    qs = NotificationCampaign.objects.select_related("template")
    if status_filter:
        qs = qs.filter(status=status_filter)
    return render(request, "campaign_list.html", {
        "campaigns": qs,
        "status_filter": status_filter,
    })


@staff_required
def campaign_detail(request, pk):
    campaign = get_object_or_404(NotificationCampaign, pk=pk)
    logs = campaign.logs.all()
    return render(request, "campaign_detail.html", {
        "campaign": campaign,
        "recipient_logs": logs,
    })


@staff_required
def campaign_progress(request, pk):
    """Lightweight JSON endpoint for AJAX progress polling."""
    campaign = get_object_or_404(NotificationCampaign, pk=pk)
    return JsonResponse({
        "status":          campaign.status,
        "status_display":  campaign.get_status_display(),
        "recipient_count": campaign.recipient_count,
        "delivered_count": campaign.delivered_count,
        "failed_count":    campaign.failed_count,
        "delivery_pct":    campaign.delivery_pct,
    })


@staff_required
@require_POST
def resend_failed(request, pk):
    campaign = get_object_or_404(NotificationCampaign, pk=pk)
    failed_emails = list(
        campaign.logs.filter(status=NotificationLog.STATUS_FAILED)
        .values_list("email", flat=True)
    )
    if not failed_emails:
        messages.info(request, "No failed recipients to resend.")
        return redirect("notifications:campaign-detail", pk=pk)

    campaign.logs.filter(status=NotificationLog.STATUS_FAILED).update(
        status=NotificationLog.STATUS_QUEUED
    )
    campaign.status = NotificationCampaign.STATUS_QUEUED
    campaign.save(update_fields=["status"])

    _dispatch_send(campaign.pk)
    messages.info(request, "Resend queued — delivery in progress.")

    return redirect("notifications:campaign-detail", pk=pk)


@staff_required
def resend_compose(request, pk):
    """
    Show a review-and-resend page pre-filled from an existing campaign.
    POST creates a new campaign and dispatches it.
    """
    source = get_object_or_404(NotificationCampaign, pk=pk)
    snapshot = source.filters_snapshot or {}

    if request.method == "POST":
        subject = request.POST.get("subject", "").strip()
        body = request.POST.get("body", "").strip()
        sender = request.POST.get("sender", "")
        reply_to = request.POST.get("reply_to", "")

        if not subject or not body:
            messages.error(request, "Subject and body are required.")
            return redirect("notifications:resend-compose", pk=pk)

        campaign = NotificationCampaign.objects.create(
            template=source.template,
            subject=subject,
            body=body,
            sender=sender,
            reply_to=reply_to,
            status=NotificationCampaign.STATUS_QUEUED,
            filters_snapshot=snapshot,
            extra_context=source.extra_context or {},
            recipient_count=0,
            created_by=request.user,
        )
        _dispatch_send(campaign.pk)
        messages.info(request, "Resend queued — sending in progress.")
        return redirect("notifications:campaign-detail", pk=campaign.pk)

    return render(request, "resend_compose.html", {
        "source": source,
        "snapshot": snapshot,
        "snapshot_json": json.dumps(snapshot),
        "senders": SenderConfig.objects.all(),
    })
