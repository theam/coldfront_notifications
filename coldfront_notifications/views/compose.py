"""
Compose notification views: compose form, filter cascade, recipient preview,
email preview render, and pre-flight validation.
"""
import json
import logging

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required as staff_required
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from ..models import NotificationCampaign, NotificationTemplate, NotificationVariable
from ..utils import recipient_count
from ..validators import validate_campaign
from .filters import get_filter_context, compute_filter_options

logger = logging.getLogger(__name__)


def _dispatch_send(campaign_pk: int):
    """
    Queue the send task on Celery. Falls back to a background thread when
    the broker is unreachable so the HTTP response returns immediately.
    """
    from ..tasks import send_notification_campaign
    try:
        send_notification_campaign.delay(campaign_pk)
    except Exception as exc:
        import threading
        logger.warning("Celery broker unreachable (%s) — sending in background thread", exc)

        def _run():
            from django import db
            try:
                db.close_old_connections()
                send_notification_campaign(campaign_pk)
            except Exception as thread_exc:
                logger.error("Background send thread crashed for campaign %s: %s",
                             campaign_pk, thread_exc, exc_info=True)

        t = threading.Thread(target=_run, daemon=True)
        t.start()


@staff_required
def compose(request):
    templates = NotificationTemplate.objects.filter(is_deleted=False)
    ctx = get_filter_context()

    templates_json = json.dumps([
        {
            "id":        t.pk,
            "slug":      t.slug,
            "name":      t.name,
            "subject":   t.subject,
            "body":      t.body,
            "variables": t.variables,
        }
        for t in templates
    ])

    variables_json = json.dumps([
        {
            "key":          v.key,
            "label":        v.label,
            "description":  v.description,
            "example":      v.example,
            "source":       v.source,
            "input_widget": v.input_widget,
            "is_required":  v.is_required,
        }
        for v in NotificationVariable.objects.filter(is_deleted=False)
    ])

    if request.method == "POST":
        action = request.POST.get("action", "send")

        def _filter(field):
            raw = request.POST.get(field, "")
            if not raw:
                return []
            try:
                val = json.loads(raw)
                return val if isinstance(val, list) else [val]
            except (ValueError, TypeError):
                return request.POST.getlist(field)

        filters = {
            "projects":    _filter("filter_projects"),
            "allocations": _filter("filter_allocations"),
            "resources":   _filter("filter_resources"),
            "departments": _filter("filter_departments"),
            "statuses":    _filter("filter_statuses"),
            "roles":       _filter("filter_roles"),
        }
        extra = [
            line.strip()
            for line in request.POST.get("extra_recipients", "").splitlines()
            if line.strip()
        ]

        subject  = request.POST.get("subject", "").strip()
        body     = request.POST.get("body", "").strip()
        sender   = request.POST.get("sender", "")
        reply_to = request.POST.get("reply_to", "")
        tmpl_id  = request.POST.get("template_id")

        try:
            dedupe_users = json.loads(request.POST.get("dedupe_users", "[]") or "[]")
            if not isinstance(dedupe_users, list):
                dedupe_users = []
        except (ValueError, TypeError):
            dedupe_users = []

        if not subject or not body:
            messages.error(request, "Subject and body are required.")
            ctx.update({
                "templates": templates,
                "templates_json": templates_json,
            })
            return render(request, "compose.html", ctx)

        if action == "send":
            v = validate_campaign(subject, body, filters, {},
                                  dedupe_users=dedupe_users)
            if v["errors"] or v["missing_tokens"]:
                messages.error(
                    request,
                    f"Cannot send: {len(v['errors'])} resolution error(s), "
                    f"{len(v['missing_tokens'])} unknown token(s).",
                )
                ctx.update({
                    "validation": v,
                    "templates": templates,
                    "templates_json": templates_json,
                })
                return render(request, "compose.html", ctx)

        filters["extra_recipients"] = extra
        filters["dedupe_users"]     = dedupe_users
        campaign = NotificationCampaign.objects.create(
            template_id=tmpl_id or None,
            subject=subject,
            body=body,
            sender=sender,
            reply_to=reply_to,
            status=(NotificationCampaign.STATUS_DRAFT
                    if action == "draft"
                    else NotificationCampaign.STATUS_QUEUED),
            filters_snapshot=filters,
            extra_context={},
            recipient_count=0,
            created_by=request.user,
        )

        if action == "send":
            _dispatch_send(campaign.pk)
            messages.info(request, "Notification queued — sending in progress. This page refreshes automatically.")
            return redirect("notifications:campaign-detail", pk=campaign.pk)

        messages.success(request, f"Draft \"{subject[:50]}\" saved.")
        return redirect("notifications:campaign-list")

    ctx.update({
        "templates":      templates,
        "templates_json": templates_json,
        "variables_json": variables_json,
        "filter_summaries_json": json.dumps(ctx.get("filter_summaries", {})),
    })
    return render(request, "compose.html", ctx)


@staff_required
@require_POST
def filter_options(request):
    """
    Event-driven filter re computation.

    The frontend sends an event name identifying which filter changed and the
    current state of all selections.  The backend computes the full
    {options, selected, summary} state for every filter.
    """
    try:
        payload = json.loads(request.body)
    except (ValueError, TypeError):
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    event = payload.get("event", "")
    valid_events = {"PROJECT_UPDATED", "DEPARTMENT_UPDATED",
                    "ALLOCATION_UPDATED", "RESOURCE_UPDATED",
                    "ALLOCATION_STATUS_UPDATED", "ROLE_UPDATED"}
    if event not in valid_events:
        return JsonResponse({"error": f"Unknown event: {event}"}, status=400)

    selections = payload.get("selections", {})
    return JsonResponse(compute_filter_options(selections))


@staff_required
@require_POST
def recipient_count_view(request):
    filters = {
        "projects":    request.POST.getlist("projects"),
        "allocations": request.POST.getlist("allocations"),
        "resources":   request.POST.getlist("resources"),
        "departments": request.POST.getlist("departments"),
        "statuses":    request.POST.getlist("alloc_status"),
        "roles":       request.POST.getlist("roles"),
    }
    preview = request.POST.get("preview") == "true"
    subject = request.POST.get("subject", "")
    body    = request.POST.get("body", "")

    if not preview:
        return JsonResponse({"count": recipient_count(filters)})

    try:
        page = max(1, int(request.POST.get("page", 1)))
    except (ValueError, TypeError):
        page = 1
    from ..conf import PREVIEW_PAGE_SIZE
    try:
        page_size = max(10, min(100, int(request.POST.get("page_size", PREVIEW_PAGE_SIZE))))
    except (ValueError, TypeError):
        page_size = PREVIEW_PAGE_SIZE

    from ..utils import enumerate_recipients, _has_alloc_filters
    from ..validators import _extract_tokens, _required_scope
    from collections import Counter

    tokens = _extract_tokens(subject, body)
    used_vars = list(NotificationVariable.objects.filter(key__in=tokens))
    token_scope = _required_scope(used_vars)

    if _has_alloc_filters(filters):
        scope = "allocation"
    else:
        scope = max(token_scope, "project", key=["user", "project", "allocation"].index)

    from coldfront.core.project.models import ProjectUser

    # Two-pass streaming to avoid loading all rows into memory.
    # Pass 1: count total, collect user_counts + user_info (lightweight).
    user_info = {}
    user_counts = Counter()
    total = 0

    for user, project, allocation in enumerate_recipients(filters, scope):
        u = user.username
        user_counts[u] += 1
        if u not in user_info:
            user_info[u] = {
                "email": user.email,
                "full_name": user.get_full_name() or user.username,
            }
        total += 1

    # Clamp page BEFORE computing the slice window.
    total_pages = max(1, (total + page_size - 1) // page_size)
    page = min(page, total_pages)
    want_start = (page - 1) * page_size
    want_end   = want_start + page_size

    # Pass 2: stream again, collect only the page slice.
    page_rows = []
    idx = 0
    for user, project, allocation in enumerate_recipients(filters, scope):
        if idx >= want_end:
            break
        if idx >= want_start:
            page_rows.append({
                "username":      user.username,
                "full_name":     user.get_full_name() or user.username,
                "email":         user.email,
                "user_pk":       user.pk,
                "project_pk":    project.pk if project else None,
                "project_title": project.title if project else "",
                "allocation": (
                    f"{allocation.pk} — {allocation.get_parent_resource}"
                    if allocation else ""
                ),
            })
        idx += 1

    if page_rows:
        pairs = [(r["user_pk"], r["project_pk"]) for r in page_rows if r["project_pk"]]
        if pairs:
            from django.db.models import Q
            q = Q()
            for uid, pid in pairs:
                q |= Q(user_id=uid, project_id=pid)
            role_map = {}
            for pu in ProjectUser.objects.filter(q, status__name="Active").select_related("role"):
                role_map[(pu.user_id, pu.project_id)] = pu.role.name if pu.role else ""
        else:
            role_map = {}
        for r in page_rows:
            r["role"] = role_map.get((r["user_pk"], r["project_pk"]), "")
            r["project"] = r.pop("project_title")
            del r["user_pk"]
            del r["project_pk"]

    multi_users = sorted([
        {"username": u, "email": user_info[u]["email"],
         "full_name": user_info[u]["full_name"], "count": c}
        for u, c in user_counts.items() if c > 1
    ], key=lambda x: -x["count"])

    return JsonResponse({
        "count":       total,
        "user_count":  len(user_info),
        "recipients":  page_rows,
        "multi_users": multi_users,
        "scope":       scope,
        "page":        page,
        "page_size":   page_size,
        "total_pages": total_pages,
    })


@staff_required
@require_POST
def preview_render_view(request):
    """
    Render the subject+body for up to N sample recipient tuples so the admin
    can see substitution in action before sending.
    """
    from ..utils import enumerate_recipients_deduped
    from ..validators import _extract_tokens, _required_scope
    from ..resolvers import MissingValue
    from ..tasks import _build_values, _render

    def _json(field, default):
        raw = request.POST.get(field, "")
        if not raw:
            return default
        try:
            return json.loads(raw)
        except (ValueError, TypeError):
            return default

    subject      = request.POST.get("subject", "")
    body         = request.POST.get("body", "")
    filters      = _json("filters", {}) or {}
    dedupe_users = _json("dedupe_users", []) or []
    try:
        from ..conf import PREVIEW_RENDER_LIMIT
        limit = max(1, min(10, int(request.POST.get("limit", PREVIEW_RENDER_LIMIT))))
    except (ValueError, TypeError):
        limit = 3

    for k in ("projects", "allocations", "resources", "departments", "statuses", "roles"):
        filters.setdefault(k, [])
        if not isinstance(filters[k], list):
            filters[k] = [filters[k]]

    tokens = _extract_tokens(subject, body)
    vars_by_key = {v.key: v for v in NotificationVariable.objects.filter(key__in=tokens)}
    scope = _required_scope(list(vars_by_key.values()))

    samples = []
    total = 0
    for user, project, allocation in enumerate_recipients_deduped(filters, scope, dedupe_users):
        total += 1
        if len(samples) >= limit:
            continue
        ctx = {"user": user, "project": project, "allocation": allocation}
        try:
            values = _build_values(tokens, vars_by_key, ctx)
            rendered_subject = _render(subject, values)
            rendered_body    = _render(body,    values)
            error = None
        except MissingValue as exc:
            rendered_subject = subject
            rendered_body    = body
            error = str(exc)
        samples.append({
            "recipient": {
                "name":       user.get_full_name() or user.username,
                "email":      user.email,
                "username":   user.username,
                "project":    project.title if project else "",
                "allocation": (f"{allocation.pk} — {allocation.get_parent_resource}"
                               if allocation else ""),
            },
            "subject": rendered_subject,
            "body":    rendered_body,
            "error":   error,
        })

    return JsonResponse({
        "samples":      samples,
        "total_emails": total,
        "scope":        scope,
    })


@staff_required
@require_POST
def validate_view(request):
    """Pre-flight validation — called from compose JS before Send is enabled."""
    def _json(field, default):
        raw = request.POST.get(field, "")
        if not raw:
            return default
        try:
            return json.loads(raw)
        except (ValueError, TypeError):
            return default

    subject       = request.POST.get("subject", "")
    body          = request.POST.get("body", "")
    filters       = _json("filters", {}) or {}
    extra_context = _json("extra_context", {}) or {}
    dedupe_users  = _json("dedupe_users", []) or []
    for k in ("projects", "allocations", "resources", "departments", "statuses", "roles"):
        v = filters.get(k)
        if v is None:
            filters[k] = []
        elif not isinstance(v, list):
            filters[k] = [v]

    from ..utils import _has_alloc_filters
    from ..validators import _extract_tokens, _required_scope
    tokens = _extract_tokens(subject, body)
    used_vars = list(NotificationVariable.objects.filter(key__in=tokens))
    token_scope = _required_scope(used_vars)
    if _has_alloc_filters(filters):
        elevated_scope = "allocation"
    else:
        elevated_scope = max(token_scope, "project",
                             key=["user", "project", "allocation"].index)

    result = validate_campaign(subject, body, filters, extra_context,
                               dedupe_users=dedupe_users,
                               scope_override=elevated_scope)

    from coldfront.core.project.models import Project
    from coldfront.core.resource.models import Resource

    active = []
    if filters.get("projects"):
        names = list(Project.objects.filter(
            pk__in=filters["projects"]
        ).values_list("title", flat=True))
        if names:
            active.append({"label": "Project", "values": names})
    if filters.get("departments"):
        active.append({"label": "Department", "values": filters["departments"]})
    if filters.get("allocations"):
        active.append({"label": "Allocation", "values": [str(pk) for pk in filters["allocations"]]})
    if filters.get("resources"):
        names = list(Resource.objects.filter(
            pk__in=filters["resources"]
        ).values_list("name", flat=True))
        if names:
            active.append({"label": "Resource", "values": names})
    if filters.get("statuses"):
        active.append({"label": "Allocation Status", "values": filters["statuses"]})
    if filters.get("roles"):
        active.append({"label": "User Role", "values": filters["roles"]})
    result["active_filters"] = active

    return JsonResponse(result)
