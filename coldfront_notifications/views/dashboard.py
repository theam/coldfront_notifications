from django.contrib.admin.views.decorators import staff_member_required as staff_required
from django.shortcuts import render
from django.utils import timezone

from ..models import NotificationCampaign, NotificationLog


@staff_required
def dashboard(request):
    from datetime import timedelta
    thirty_days_ago = timezone.now() - timedelta(days=30)

    recent = NotificationCampaign.objects.select_related("template")[:10]
    stats = {
        "total_sent":      NotificationLog.objects.filter(status="delivered").count(),
        "total_failed_30": NotificationCampaign.objects.filter(
            status=NotificationCampaign.STATUS_FAILED,
            created_at__gte=thirty_days_ago,
        ).count(),
        "sending_now":     NotificationCampaign.objects.filter(status="sending").count(),
        "total_campaigns": NotificationCampaign.objects.count(),
    }
    return render(request, "dashboard.html", {
        "recent_campaigns": recent,
        **stats,
    })
