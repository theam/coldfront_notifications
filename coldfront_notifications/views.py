import json
import logging

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required as staff_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.text import slugify
from django.views.decorators.http import require_POST

from .forms import ComposeForm, NotificationTemplateForm, NotificationVariableForm, SenderConfigForm


def _dispatch_send(campaign_pk: int):
    """
    Queue the send task on Celery. Falls back to a background thread when
    the broker is unreachable so the HTTP response returns immediately.
    """
    from .tasks import send_notification_campaign
    try:
        send_notification_campaign.delay(campaign_pk)
    except Exception as exc:
        import threading
        logger.warning("Celery broker unreachable (%s) — sending in background thread", exc)

        def _run():
            import django
            from django import db
            try:
                db.close_old_connections()
                send_notification_campaign(campaign_pk)
            except Exception as thread_exc:
                logger.error("Background send thread crashed for campaign %s: %s",
                             campaign_pk, thread_exc, exc_info=True)

        t = threading.Thread(target=_run, daemon=True)
        t.start()
from .models import (
    NotificationCampaign,
    NotificationLog,
    NotificationTemplate,
    NotificationVariable,
    SenderConfig,
)
from .utils import recipient_count, recipient_emails
from .validators import validate_campaign

logger = logging.getLogger(__name__)


def _get_filter_context():
    from coldfront.core.project.models import Project, ProjectUserRoleChoice, ProjectStatusChoice
    from coldfront.core.allocation.models import Allocation, AllocationStatusChoice
    from coldfront.core.resource.models import Resource
    from ifxuser.models import Organization

    return {
        "projects":    Project.objects.order_by("title"),
        "allocations": Allocation.objects.select_related("project").order_by("project__title", "pk"),
        "resources":   Resource.objects.order_by("name"),
        # Dedupe by name — same dept name often exists across multiple org_trees.
        "departments": sorted(set(
            Organization.objects.filter(rank="department").values_list("name", flat=True)
        )),
        "statuses":    AllocationStatusChoice.objects.values_list("name", flat=True).order_by("name"),
        "roles":       ProjectUserRoleChoice.objects.values_list("name", flat=True).order_by("name"),
        "senders":     SenderConfig.objects.all(),
        "reply_tos":   SenderConfig.objects.all(),
    }


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
def compose(request):
    templates = NotificationTemplate.objects.filter(is_deleted=False)
    ctx = _get_filter_context()

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
            # JS submits each filter as one JSON-encoded hidden field.
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

        # Per-user dedupe list (chosen in the preview modal)
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

        # Pre-flight validation; block send (and draft) on hard errors.
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

        filters["extra_recipients"] = extra  # preserved in snapshot
        filters["dedupe_users"]     = dedupe_users
        campaign = NotificationCampaign.objects.create(
            template_id      = tmpl_id or None,
            subject          = subject,
            body             = body,
            sender           = sender,
            reply_to         = reply_to,
            status           = (NotificationCampaign.STATUS_DRAFT
                                if action == "draft"
                                else NotificationCampaign.STATUS_QUEUED),
            filters_snapshot = filters,
            extra_context    = {},
            recipient_count  = 0,   # filled in during send
            created_by       = request.user,
        )

        # Logs are created per-tuple during send; drafts carry no logs.
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
    })
    return render(request, "compose.html", ctx)


@staff_required
@require_POST
def filter_options(request):
    """
    Return downstream filter option lists given the current selection.

    Cascade direction: (project | department) → allocation → resource → status.
    Roles are narrowed by projects-linked-to-dept only.
    """
    from coldfront.core.project.models import Project, ProjectUser, ProjectUserRoleChoice
    from coldfront.core.allocation.models import Allocation
    from coldfront.core.resource.models import Resource
    from ifxuser.models import Organization

    projects    = request.POST.getlist("projects")
    allocations = request.POST.getlist("allocations")
    resources   = request.POST.getlist("resources")
    departments = request.POST.getlist("departments")  # names

    # Eligible project set given project + dept filters.
    project_scope = Project.objects.all()
    if projects:
        project_scope = project_scope.filter(pk__in=projects)
    if departments:
        project_scope = project_scope.filter(
            projectorganization__organization__rank="department",
            projectorganization__organization__name__in=departments,
        )

    # Narrow allocations progressively so each facet only sees its upstream.
    alloc_by_p = Allocation.objects.filter(project__in=project_scope)
    alloc_by_pa = alloc_by_p.filter(pk__in=allocations) if allocations else alloc_by_p
    alloc_by_par = alloc_by_pa.filter(resources__pk__in=resources) if resources else alloc_by_pa

    allocations_out = [
        {"id": a.pk, "label": f"{a.project.title} — {a.get_parent_resource or ''}"}
        for a in alloc_by_p.select_related("project").distinct().order_by("project__title", "pk")
    ]

    resource_pks = alloc_by_pa.values_list("resources__pk", flat=True).distinct()
    resources_out = [
        {"id": r.pk, "label": r.name}
        for r in Resource.objects.filter(pk__in=resource_pks).order_by("name")
    ]

    status_names = alloc_by_par.values_list("status__name", flat=True).distinct()
    statuses_out = sorted({n for n in status_names if n})

    role_pks = (
        ProjectUser.objects.filter(project__in=project_scope)
        .values_list("role__pk", flat=True)
        .distinct()
    )
    roles_out = sorted(
        ProjectUserRoleChoice.objects.filter(pk__in=role_pks).values_list("name", flat=True)
    )

    # Departments narrow by project scope but ignore their own selection so users
    # can switch between depts without the others disappearing.
    dept_project_scope = Project.objects.all()
    if projects:
        dept_project_scope = dept_project_scope.filter(pk__in=projects)
    dept_names = (
        Organization.objects.filter(
            rank="department",
            projectorganization__project__in=dept_project_scope,
        )
        .values_list("name", flat=True)
        .distinct()
    )
    departments_out = sorted(set(dept_names))

    return JsonResponse({
        "allocations": allocations_out,
        "resources":   resources_out,
        "statuses":    statuses_out,
        "roles":       roles_out,
        "departments": departments_out,
    })


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
    try:
        dedupe_users = json.loads(request.POST.get("dedupe_users", "[]") or "[]")
    except (ValueError, TypeError):
        dedupe_users = []

    if not preview:
        return JsonResponse({"count": recipient_count(filters)})

    try:
        page = max(1, int(request.POST.get("page", 1)))
    except (ValueError, TypeError):
        page = 1
    from .conf import PREVIEW_PAGE_SIZE
    page_size = PREVIEW_PAGE_SIZE

    # Determine scope the same way the validator/sender will.
    from .utils import enumerate_recipients_deduped
    from .validators import _extract_tokens, _required_scope
    from collections import Counter

    tokens = _extract_tokens(subject, body)
    used_vars = list(NotificationVariable.objects.filter(key__in=tokens))
    scope = _required_scope(used_vars)

    # Single pass: collect counts + page rows in one iteration.
    from coldfront.core.project.models import ProjectUser

    user_info = {}       # username → {email, full_name}
    user_counts = Counter()
    page_rows = []
    total = 0
    want_start = (page - 1) * page_size
    want_end   = want_start + page_size

    for user, project, allocation in enumerate_recipients_deduped(filters, scope, []):
        u = user.username
        user_counts[u] += 1
        if u not in user_info:
            user_info[u] = {
                "email": user.email,
                "full_name": user.get_full_name() or user.username,
            }

        # Only collect details for rows in the requested page.
        if want_start <= total < want_end:
            page_rows.append({
                "username":      u,
                "full_name":     user.get_full_name() or u,
                "email":         user.email,
                "user_pk":       user.pk,
                "project_pk":    project.pk if project else None,
                "project_title": project.title if project else "",
                "allocation":    (f"{allocation.pk} — {allocation.get_parent_resource}"
                                 if allocation else ""),
            })

        total += 1

    total_pages = max(1, (total + page_size - 1) // page_size)
    page = min(page, total_pages)

    # Batch-load roles for the page's rows in one query.
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
def template_list(request):
    return render(request, "template_list.html", {
        "templates": NotificationTemplate.objects.filter(is_deleted=False),
    })


@staff_required
def template_form(request, pk=None):
    instance = get_object_or_404(NotificationTemplate, pk=pk) if pk else None
    if request.method == "POST":
        form = NotificationTemplateForm(request.POST, instance=instance)
        if form.is_valid():
            obj = form.save(commit=False)
            if not obj.slug:
                obj.slug = slugify(obj.name)
            obj.save()
            action_word = "updated" if instance else "created"
            messages.success(request, f'Template "{obj.name}" {action_word}.')
            return redirect("notifications:template-list")
    else:
        form = NotificationTemplateForm(instance=instance)

    variables_json = json.dumps([
        {
            "key":         v.key,
            "label":       v.label,
            "description": v.description,
            "example":     v.example,
            "source":      v.source,
        }
        for v in NotificationVariable.objects.filter(is_deleted=False)
    ])

    return render(request, "template_form.html", {
        "form":     form,
        "template": instance,
        "variables_json": variables_json,
    })


@staff_required
def template_delete(request, pk):
    instance = get_object_or_404(NotificationTemplate, pk=pk)
    # Count notifications that used this template
    notification_count = NotificationCampaign.objects.filter(template=instance).count()
    if request.method == "POST":
        instance.is_deleted = True
        instance.save(update_fields=["is_deleted"])
        messages.success(request, f'Template "{instance.name}" deleted.')
        return redirect("notifications:template-list")
    return render(request, "template_delete_confirm.html", {
        "template": instance,
        "notification_count": notification_count,
    })


@staff_required
def variable_list(request):
    return render(request, "variable_list.html", {
        "variables": NotificationVariable.objects.filter(is_deleted=False),
    })


@staff_required
def variable_form(request, pk=None):
    instance = get_object_or_404(NotificationVariable, pk=pk) if pk else None
    if request.method == "POST":
        form = NotificationVariableForm(request.POST, instance=instance)
        if form.is_valid():
            obj = form.save()
            action_word = "updated" if instance else "created"
            messages.success(request, f'Variable "{{{{{obj.key}}}}}" {action_word}.')
            return redirect("notifications:variable-list")
    else:
        form = NotificationVariableForm(instance=instance)

    # Dropdown metadata for conditional UI rendering
    from .resolvers import QUERY_CHOICES
    return render(request, "variable_form.html", {
        "form":     form,
        "variable": instance,
        "query_choices": QUERY_CHOICES,
    })


@staff_required
def variable_delete(request, pk):
    instance = get_object_or_404(NotificationVariable, pk=pk)
    # Find templates that reference this variable's key
    affected = [
        t for t in NotificationTemplate.objects.filter(is_deleted=False)
        if instance.key in t.variables
    ]
    if request.method == "POST":
        instance.is_deleted = True
        instance.save(update_fields=["is_deleted"])
        messages.success(request, f'Variable "{{{{{instance.key}}}}}" deleted.')
        return redirect("notifications:variable-list")
    return render(request, "variable_delete_confirm.html", {
        "variable": instance,
        "affected_templates": affected,
    })


@staff_required
def settings_view(request):
    senders = SenderConfig.objects.all()
    return render(request, "settings.html", {"senders": senders})


@staff_required
def sender_form(request, pk=None):
    instance = get_object_or_404(SenderConfig, pk=pk) if pk else None
    if request.method == "POST":
        form = SenderConfigForm(request.POST, instance=instance)
        if form.is_valid():
            obj = form.save()
            action_word = "updated" if instance else "added"
            messages.success(request, f'Address "{obj.email}" {action_word}.')
            return redirect("notifications:settings")
    else:
        form = SenderConfigForm(instance=instance)
    return render(request, "sender_form.html", {
        "form": form,
        "sender": instance,
    })


@staff_required
@require_POST
def sender_delete(request, pk):
    obj = get_object_or_404(SenderConfig, pk=pk)
    email = obj.email
    obj.delete()
    messages.success(request, f'Address "{email}" removed.')
    return redirect("notifications:settings")


@staff_required
def template_json(request, pk):
    """Return a single template's live content for the compose-page sidebar."""
    t = get_object_or_404(NotificationTemplate, pk=pk)
    return JsonResponse({
        "id":        t.pk,
        "slug":      t.slug,
        "name":      t.name,
        "subject":   t.subject,
        "body":      t.body,
        "variables": t.variables,
    })


@staff_required
def variables_view(request):
    """Return the full catalog of NotificationVariables as JSON."""
    return JsonResponse({
        "variables": [
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
        ]
    })


@staff_required
@require_POST
def preview_render_view(request):
    """
    Render the subject+body for up to N sample recipient tuples so the admin
    can see substitution in action before sending. Does NOT hard-fail on
    resolution errors — it annotates the sample instead.
    """
    from .utils import enumerate_recipients_deduped
    from .validators import _extract_tokens, _required_scope
    from .resolvers import MissingValue
    from .tasks import _build_values, _render

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
        from .conf import PREVIEW_RENDER_LIMIT
        limit = max(1, min(10, int(request.POST.get("limit", PREVIEW_RENDER_LIMIT))))
    except (ValueError, TypeError):
        limit = 3

    for k in ("projects","allocations","resources","departments","statuses","roles"):
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
    """
    Pre-flight validation — called from compose JS before Send is enabled.
    POST payload:
      subject, body (str)
      filters (JSON-encoded dict with projects/allocations/resources/departments/statuses/roles)
      extra_context (JSON-encoded dict {var_key: user_value})
    """
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
    # Normalize every filter key to a list (validate_campaign expects lists)
    for k in ("projects","allocations","resources","departments","statuses","roles"):
        v = filters.get(k)
        if v is None:
            filters[k] = []
        elif not isinstance(v, list):
            filters[k] = [v]

    result = validate_campaign(subject, body, filters, extra_context,
                               dedupe_users=dedupe_users)
    return JsonResponse(result)