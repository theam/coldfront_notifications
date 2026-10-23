"""Campaign views: list, detail, progress polling, resend-compose."""
from __future__ import annotations

import json
import logging
from typing import Any

from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.views import View
from django.views.generic import DetailView, ListView, TemplateView

from django.contrib.auth import get_user_model
from django.db.models import Q

from coldfront.core.allocation.models import Allocation
from coldfront.core.project.models import Project, ProjectUser

from ..models import NotificationCampaign, SenderConfig
from .helpers import StaffRequiredMixin, dispatch_send

User = get_user_model()

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


class DraftDeleteView(StaffRequiredMixin, TemplateView):
    """Confirm and hard-delete a draft or stuck-sending campaign."""

    DELETABLE_STATUSES = (
        NotificationCampaign.Status.DRAFT,
        NotificationCampaign.Status.SENDING,
    )

    template_name = "coldfront_notifications/draft_delete_confirm.html"

    def _get_campaign(self, pk):
        return get_object_or_404(
            NotificationCampaign,
            pk=pk,
            status__in=self.DELETABLE_STATUSES,
        )

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context["campaign"] = self._get_campaign(kwargs["pk"])
        return context

    def post(self, request, *args: Any, **kwargs: Any):
        campaign = self._get_campaign(kwargs["pk"])
        status = campaign.get_status_display()
        subject = campaign.subject or "(no subject)"
        campaign.delete()
        messages.success(request, f'{status} Campaign "{subject}" Deleted.')
        return redirect("notifications:campaign-list")


class ResendPreviewView(StaffRequiredMixin, View):
    """Paginated recipient preview from the original campaign's delivery log."""

    def post(self, request, *args: Any, **kwargs: Any) -> JsonResponse:
        campaign = get_object_or_404(NotificationCampaign, pk=kwargs["pk"])

        try:
            page = max(1, int(request.POST.get("page", 1)))
        except (ValueError, TypeError):
            page = 1
        try:
            page_size = max(10, min(100, int(request.POST.get("page_size", 10))))
        except (ValueError, TypeError):
            page_size = 10
        search_query = request.POST.get("search", "").strip().lower()

        logs = list(campaign.logs.order_by("pk"))
        if not logs:
            return JsonResponse({
                "count": 0, "recipients": [], "page": 1,
                "page_size": page_size, "total_pages": 1,
            })

        emails = {log.email for log in logs}
        project_ids = {log.project_id for log in logs if log.project_id}
        allocation_ids = {log.allocation_id for log in logs if log.allocation_id}

        users_by_email = {
            u.email: u for u in User.objects.filter(email__in=emails)
        }
        projects_by_id = {
            p.pk: p for p in Project.objects.filter(pk__in=project_ids)
        } if project_ids else {}
        allocations_by_id = {
            a.pk: a
            for a in Allocation.objects.filter(pk__in=allocation_ids)
        } if allocation_ids else {}

        role_lookup = self._build_role_lookup(logs, users_by_email)

        all_rows = []
        for log in logs:
            user = users_by_email.get(log.email)
            project = projects_by_id.get(log.project_id)
            allocation = allocations_by_id.get(log.allocation_id)

            full_name = (
                user.get_full_name() or user.username
            ) if user else log.email.split("@")[0]

            row = {
                "full_name": full_name,
                "email": log.email,
                "role": role_lookup.get(
                    (user.pk if user else None, log.project_id), ""
                ),
                "project": project.title if project else "",
                "allocation": (
                    f"{allocation.pk} \u2014 {allocation.get_parent_resource}"
                    if allocation else ""
                ),
            }

            if search_query:
                searchable = (
                    f"{row['full_name']} {row['email']} {row['project']}"
                ).lower()
                if search_query not in searchable:
                    continue

            all_rows.append(row)

        total_count = len(all_rows)
        total_pages = max(1, (total_count + page_size - 1) // page_size)
        page = min(page, total_pages)
        start = (page - 1) * page_size
        page_rows = all_rows[start:start + page_size]

        return JsonResponse({
            "count": total_count,
            "recipients": page_rows,
            "page": page,
            "page_size": page_size,
            "total_pages": total_pages,
        })

    @staticmethod
    def _build_role_lookup(logs, users_by_email):
        pairs = [
            (users_by_email[log.email].pk, log.project_id)
            for log in logs
            if log.email in users_by_email and log.project_id
        ]
        if not pairs:
            return {}
        query = Q()
        for user_pk, project_pk in pairs:
            query |= Q(user_id=user_pk, project_id=project_pk)
        return {
            (pu.user_id, pu.project_id): pu.role.name if pu.role else ""
            for pu in ProjectUser.objects.filter(
                query, status__name="Active",
            ).select_related("role")
        }


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
