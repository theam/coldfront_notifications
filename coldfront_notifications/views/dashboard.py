"""Dashboard view showing recent campaigns and delivery stats."""
from __future__ import annotations

from datetime import timedelta
from typing import Any

from django.utils import timezone
from django.views.generic import TemplateView

from ..models import NotificationCampaign, NotificationLog
from .helpers import StaffRequiredMixin


class DashboardView(StaffRequiredMixin, TemplateView):
    """Notification dashboard with recent campaigns and 30-day stats."""

    template_name = "coldfront_notifications/dashboard.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        thirty_days_ago = timezone.now() - timedelta(days=30)

        context.update({
            "recent_campaigns": (
                NotificationCampaign.objects
                .select_related("template")[:10]
            ),
            "total_sent": (
                NotificationLog.objects
                .filter(status=NotificationLog.Status.DELIVERED)
                .count()
            ),
            "total_failed_30": (
                NotificationCampaign.objects
                .filter(
                    status=NotificationCampaign.Status.FAILED,
                    created_at__gte=thirty_days_ago,
                )
                .count()
            ),
            "sending_now": (
                NotificationCampaign.objects
                .filter(status=NotificationCampaign.Status.SENDING)
                .count()
            ),
            "total_campaigns": NotificationCampaign.objects.count(),
        })
        return context
