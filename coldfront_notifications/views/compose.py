"""
Compose notification views: compose form, recipient preview, email
preview render, and pre-flight validation.

All views use Django class-based views with LoginRequiredMixin +
UserPassesTestMixin following ColdFront core conventions.
"""
from __future__ import annotations

import json
import logging
from collections import Counter
from typing import Any

from django.contrib import messages
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import redirect
from django.views import View
from django.views.generic import TemplateView

from coldfront.core.project.models import Project, ProjectUser
from coldfront.core.resource.models import Resource

from ..campaign_sender import TemplateRenderer
from ..conf import PREVIEW_PAGE_SIZE, PREVIEW_RENDER_LIMIT
from ..filters import FilterDataBuilder, RecipientResolver
from ..models import (
    NotificationCampaign,
    NotificationTemplate,
    NotificationVariable,
    SenderConfig,
)
from ..notification_validator import NotificationValidator, extract_tokens, determine_scope
from ..template_variable_value_resolver import MissingValue
from .helpers import StaffRequiredMixin, dispatch_send

logger = logging.getLogger(__name__)


class ComposeView(StaffRequiredMixin, TemplateView):
    """Main compose page: GET renders the form, POST handles send/draft."""

    template_name = "coldfront_notifications/compose.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        templates = NotificationTemplate.objects.filter(is_deleted=False)

        templates_json = json.dumps([
            {
                "id": template.pk,
                "slug": template.slug,
                "name": template.name,
                "subject": template.subject,
                "body": template.body,
                "variables": template.variables,
            }
            for template in templates
        ])

        variables_json = json.dumps([
            {
                "key": variable.key,
                "label": variable.label,
                "description": variable.description,
                "example": variable.example,
                "source": variable.source,
                "input_widget": variable.input_widget,
                "is_required": variable.is_required,
            }
            for variable in NotificationVariable.objects.filter(is_deleted=False)
        ])

        draft_json = "null"
        draft_pk = self.request.GET.get("draft")
        if draft_pk:
            try:
                draft = NotificationCampaign.objects.get(
                    pk=draft_pk,
                    status=NotificationCampaign.Status.DRAFT,
                    created_by=self.request.user,
                )
                draft_json = json.dumps({
                    "pk": draft.pk,
                    "subject": draft.subject,
                    "body": draft.body,
                    "sender": draft.sender,
                    "reply_to": draft.reply_to,
                    "template_id": draft.template_id,
                    "filters": draft.filters_snapshot or {},
                    "extra_context": draft.extra_context or {},
                })
            except NotificationCampaign.DoesNotExist:
                pass

        context.update({
            "templates": templates,
            "templates_json": templates_json,
            "variables_json": variables_json,
            "filter_data_json": FilterDataBuilder().build_json(),
            "draft_json": draft_json,
            "senders": SenderConfig.objects.all(),
            "reply_tos": SenderConfig.objects.all(),
        })
        return context

    def post(self, request, *args: Any, **kwargs: Any):
        """Handle send or draft submission."""
        action = request.POST.get("action", "send")

        filters = self._parse_filters_from_post(request)
        extra_recipients = [
            line.strip()
            for line in request.POST.get("extra_recipients", "").splitlines()
            if line.strip()
        ]

        subject = request.POST.get("subject", "").strip()
        body = request.POST.get("body", "").strip()
        sender = request.POST.get("sender", "")
        reply_to = request.POST.get("reply_to", "")
        template_id = request.POST.get("template_id")
        draft_pk = request.POST.get("draft_pk")

        dedupe_users = self._parse_json(request.POST.get("dedupe_users", "[]"), [])
        if not isinstance(dedupe_users, list):
            dedupe_users = []

        if not subject or not body:
            messages.error(request, "Subject and body are required.")
            return self.render_to_response(self.get_context_data())

        if action == "send":
            validation_result = NotificationValidator(
                subject, body, filters,
                dedupe_users=dedupe_users,
            ).validate()
            if validation_result["errors"] or validation_result["missing_tokens"]:
                messages.error(
                    request,
                    f"Cannot send: {len(validation_result['errors'])} resolution error(s), "
                    f"{len(validation_result['missing_tokens'])} unknown token(s).",
                )
                context = self.get_context_data()
                context["validation"] = validation_result
                return self.render_to_response(context)

        filters["extra_recipients"] = extra_recipients
        filters["dedupe_users"] = dedupe_users

        if draft_pk:
            campaign = self._update_draft(draft_pk, request.user, action, {
                "template_id": template_id or None,
                "subject": subject,
                "body": body,
                "sender": sender,
                "reply_to": reply_to,
                "filters_snapshot": filters,
            })
            if campaign is None:
                messages.error(request, "Draft not found or not editable.")
                return redirect("notifications:campaign-list")
        else:
            campaign = NotificationCampaign.objects.create(
                template_id=template_id or None,
                subject=subject,
                body=body,
                sender=sender,
                reply_to=reply_to,
                status=(
                    NotificationCampaign.Status.DRAFT
                    if action == "draft"
                    else NotificationCampaign.Status.QUEUED
                ),
                filters_snapshot=filters,
                extra_context={},
                recipient_count=0,
                created_by=request.user,
            )

        if action == "send":
            dispatch_send(campaign.pk)
            messages.info(
                request,
                "Notification queued — sending in progress. "
                "This page refreshes automatically.",
            )
            return redirect("notifications:campaign-detail", pk=campaign.pk)

        messages.success(request, f'Draft "{subject[:50]}" saved.')
        return redirect("notifications:campaign-list")

    @staticmethod
    def _update_draft(draft_pk, user, action, fields):
        """Update an existing draft campaign. Returns the campaign or None."""
        try:
            campaign = NotificationCampaign.objects.get(
                pk=draft_pk,
                status=NotificationCampaign.Status.DRAFT,
                created_by=user,
            )
        except NotificationCampaign.DoesNotExist:
            return None

        for field_name, value in fields.items():
            setattr(campaign, field_name, value)

        campaign.status = (
            NotificationCampaign.Status.DRAFT
            if action == "draft"
            else NotificationCampaign.Status.QUEUED
        )
        campaign.save()
        return campaign

    @staticmethod
    def _parse_filters_from_post(request) -> dict:
        return {
            "projects": ComposeView._parse_filter_list(request.POST.get("filter_projects", "")),
            "allocations": ComposeView._parse_filter_list(request.POST.get("filter_allocations", "")),
            "resources": ComposeView._parse_filter_list(request.POST.get("filter_resources", "")),
            "departments": ComposeView._parse_filter_list(request.POST.get("filter_departments", "")),
            "statuses": ComposeView._parse_filter_list(request.POST.get("filter_statuses", "")),
            "roles": ComposeView._parse_filter_list(request.POST.get("filter_roles", "")),
        }

    @staticmethod
    def _parse_filter_list(raw: str) -> list:
        if not raw:
            return []
        try:
            value = json.loads(raw)
            return value if isinstance(value, list) else [value]
        except (ValueError, TypeError):
            return [raw]

    @staticmethod
    def _parse_json(raw: str, default: Any = None) -> Any:
        if not raw:
            return default
        try:
            return json.loads(raw)
        except (ValueError, TypeError):
            return default


class RecipientCountView(StaffRequiredMixin, View):
    """Return recipient count or paginated preview as JSON."""

    def post(self, request, *args: Any, **kwargs: Any) -> JsonResponse:
        filters = {
            "projects": request.POST.getlist("projects"),
            "allocations": request.POST.getlist("allocations"),
            "resources": request.POST.getlist("resources"),
            "departments": request.POST.getlist("departments"),
            "statuses": request.POST.getlist("alloc_status"),
            "roles": request.POST.getlist("roles"),
        }
        is_preview = request.POST.get("preview") == "true"
        subject = request.POST.get("subject", "")
        body = request.POST.get("body", "")

        if not is_preview:
            return JsonResponse({"count": RecipientResolver(filters).count()})

        return self._build_paginated_preview(request, filters, subject, body)

    def _build_paginated_preview(self, request, filters, subject, body) -> JsonResponse:
        page, page_size = self._parse_pagination(request)
        scope = self._determine_preview_scope(filters, subject, body)
        resolver = RecipientResolver(filters)

        user_info, emails_per_user, total_count = self._collect_user_stats(resolver, scope)
        total_pages = max(1, (total_count + page_size - 1) // page_size)
        page = min(page, total_pages)

        page_rows = self._collect_page_rows(resolver, scope, page, page_size)
        self._enrich_with_roles(page_rows)
        multi_email_users = self._build_multi_email_list(emails_per_user, user_info)

        return JsonResponse({
            "count": total_count,
            "user_count": len(user_info),
            "recipients": page_rows,
            "multi_users": multi_email_users,
            "scope": scope,
            "page": page,
            "page_size": page_size,
            "total_pages": total_pages,
        })

    @staticmethod
    def _parse_pagination(request) -> tuple[int, int]:
        try:
            page = max(1, int(request.POST.get("page", 1)))
        except (ValueError, TypeError):
            page = 1
        try:
            page_size = max(10, min(100, int(request.POST.get("page_size", PREVIEW_PAGE_SIZE))))
        except (ValueError, TypeError):
            page_size = PREVIEW_PAGE_SIZE
        return page, page_size

    @staticmethod
    def _determine_preview_scope(filters, subject, body) -> str:
        tokens = extract_tokens(subject, body)
        used_variables = list(NotificationVariable.objects.filter(key__in=tokens))
        token_scope = determine_scope(used_variables)

        if RecipientResolver(filters)._has_allocation_filters():
            return "allocation"
        return max(token_scope, "project", key=["user", "project", "allocation"].index)

    @staticmethod
    def _collect_user_stats(resolver, scope) -> tuple[dict, Counter, int]:
        user_info = {}
        emails_per_user = Counter()
        total_count = 0

        for user, project, allocation in resolver.enumerate(scope):
            username = user.username
            emails_per_user[username] += 1
            if username not in user_info:
                user_info[username] = {
                    "email": user.email,
                    "full_name": user.get_full_name() or user.username,
                }
            total_count += 1

        return user_info, emails_per_user, total_count

    @staticmethod
    def _collect_page_rows(resolver, scope, page, page_size) -> list[dict]:
        start_index = (page - 1) * page_size
        end_index = start_index + page_size
        page_rows = []

        for index, (user, project, allocation) in enumerate(resolver.enumerate(scope)):
            if index >= end_index:
                break
            if index >= start_index:
                page_rows.append({
                    "username": user.username,
                    "full_name": user.get_full_name() or user.username,
                    "email": user.email,
                    "user_pk": user.pk,
                    "project_pk": project.pk if project else None,
                    "project_title": project.title if project else "",
                    "allocation": (
                        f"{allocation.pk} \u2014 {allocation.get_parent_resource}"
                        if allocation
                        else ""
                    ),
                })

        return page_rows

    @staticmethod
    def _enrich_with_roles(page_rows: list[dict]):
        user_project_pairs = [
            (row["user_pk"], row["project_pk"])
            for row in page_rows
            if row["project_pk"]
        ]
        role_lookup = {}
        if user_project_pairs:
            query = Q()
            for user_pk, project_pk in user_project_pairs:
                query |= Q(user_id=user_pk, project_id=project_pk)
            for project_user in (
                ProjectUser.objects
                .filter(query, status__name="Active")
                .select_related("role")
            ):
                role_lookup[(project_user.user_id, project_user.project_id)] = (
                    project_user.role.name if project_user.role else ""
                )

        for row in page_rows:
            row["role"] = role_lookup.get((row["user_pk"], row["project_pk"]), "")
            row["project"] = row.pop("project_title")
            del row["user_pk"]
            del row["project_pk"]

    @staticmethod
    def _build_multi_email_list(emails_per_user, user_info) -> list[dict]:
        return sorted(
            [
                {
                    "username": username,
                    "email": user_info[username]["email"],
                    "full_name": user_info[username]["full_name"],
                    "count": count,
                }
                for username, count in emails_per_user.items()
                if count > 1
            ],
            key=lambda entry: -entry["count"],
        )


class PreviewRenderView(StaffRequiredMixin, View):
    """Render subject+body for sample recipients so the admin can preview."""

    def post(self, request, *args: Any, **kwargs: Any) -> JsonResponse:
        subject = request.POST.get("subject", "")
        body = request.POST.get("body", "")
        filters = self._parse_json(request.POST.get("filters", ""), {}) or {}
        dedupe_users = self._parse_json(request.POST.get("dedupe_users", ""), []) or []

        try:
            limit = max(1, min(10, int(request.POST.get("limit", PREVIEW_RENDER_LIMIT))))
        except (ValueError, TypeError):
            limit = 3

        self._normalize_filter_keys(filters)

        tokens = extract_tokens(subject, body)
        variables_by_key = {
            variable.key: variable
            for variable in NotificationVariable.objects.filter(key__in=tokens)
        }
        scope = determine_scope(list(variables_by_key.values()))
        renderer = TemplateRenderer()

        samples = []
        total_count = 0
        for user, project, allocation in RecipientResolver(filters).enumerate_deduped(
            scope, dedupe_users,
        ):
            total_count += 1
            if len(samples) >= limit:
                continue
            context = {"user": user, "project": project, "allocation": allocation}
            try:
                values = renderer.build_values(tokens, variables_by_key, context)
                rendered_subject = renderer.render(subject, values)
                rendered_body = renderer.render(body, values)
                error = None
            except MissingValue as exception:
                rendered_subject = subject
                rendered_body = body
                error = str(exception)

            samples.append({
                "recipient": {
                    "name": user.get_full_name() or user.username,
                    "email": user.email,
                    "username": user.username,
                    "project": project.title if project else "",
                    "allocation": (
                        f"{allocation.pk} \u2014 {allocation.get_parent_resource}"
                        if allocation
                        else ""
                    ),
                },
                "subject": rendered_subject,
                "body": rendered_body,
                "error": error,
            })

        return JsonResponse({
            "samples": samples,
            "total_emails": total_count,
            "scope": scope,
        })

    @staticmethod
    def _parse_json(raw: str, default: Any = None) -> Any:
        if not raw:
            return default
        try:
            return json.loads(raw)
        except (ValueError, TypeError):
            return default

    @staticmethod
    def _normalize_filter_keys(filters: dict):
        for key in ("projects", "allocations", "resources", "departments", "statuses", "roles"):
            filters.setdefault(key, [])
            if not isinstance(filters[key], list):
                filters[key] = [filters[key]]


class ValidateView(StaffRequiredMixin, View):
    """Pre-flight validation — called from compose JS before Send."""

    def post(self, request, *args: Any, **kwargs: Any) -> JsonResponse:
        subject = request.POST.get("subject", "")
        body = request.POST.get("body", "")
        filters = PreviewRenderView._parse_json(request.POST.get("filters", ""), {}) or {}
        dedupe_users = PreviewRenderView._parse_json(request.POST.get("dedupe_users", ""), []) or []

        PreviewRenderView._normalize_filter_keys(filters)

        tokens = extract_tokens(subject, body)
        used_variables = list(NotificationVariable.objects.filter(key__in=tokens))
        token_scope = determine_scope(used_variables)

        if RecipientResolver(filters)._has_allocation_filters():
            elevated_scope = "allocation"
        else:
            elevated_scope = max(
                token_scope,
                "project",
                key=["user", "project", "allocation"].index,
            )

        result = NotificationValidator(
            subject,
            body,
            filters,
            dedupe_users=dedupe_users,
            scope_override=elevated_scope,
        ).validate()

        result["active_filters"] = self._build_active_filters(filters)
        return JsonResponse(result)

    @staticmethod
    def _build_active_filters(filters: dict) -> list[dict]:
        """Build human-readable active filter labels."""
        active = []

        if filters.get("projects"):
            project_names = list(
                Project.objects
                .filter(pk__in=filters["projects"])
                .values_list("title", flat=True)
            )
            if project_names:
                active.append({"label": "Project", "values": project_names})

        if filters.get("departments"):
            active.append({
                "label": "Department",
                "values": filters["departments"],
            })

        if filters.get("allocations"):
            active.append({
                "label": "Allocation",
                "values": [str(pk) for pk in filters["allocations"]],
            })

        if filters.get("resources"):
            resource_names = list(
                Resource.objects
                .filter(pk__in=filters["resources"])
                .values_list("name", flat=True)
            )
            if resource_names:
                active.append({"label": "Resource", "values": resource_names})

        if filters.get("statuses"):
            active.append({
                "label": "Allocation Status",
                "values": filters["statuses"],
            })

        if filters.get("roles"):
            active.append({
                "label": "User Role",
                "values": filters["roles"],
            })

        return active


class DraftSaveView(StaffRequiredMixin, View):
    """AJAX endpoint to create or update a draft campaign."""

    def post(self, request, *args: Any, **kwargs: Any) -> JsonResponse:
        draft_pk = request.POST.get("draft_pk")
        subject = request.POST.get("subject", "").strip()
        body = request.POST.get("body", "").strip()
        sender = request.POST.get("sender", "")
        reply_to = request.POST.get("reply_to", "")
        template_id = request.POST.get("template_id") or None

        filters = PreviewRenderView._parse_json(request.POST.get("filters", ""), {}) or {}
        extra_context = PreviewRenderView._parse_json(request.POST.get("extra_context", ""), {}) or {}

        draft_fields = {
            "subject": subject,
            "body": body,
            "sender": sender,
            "reply_to": reply_to,
            "template_id": template_id,
            "filters_snapshot": filters,
            "extra_context": extra_context,
        }

        if draft_pk:
            try:
                campaign = NotificationCampaign.objects.get(
                    pk=draft_pk,
                    status=NotificationCampaign.Status.DRAFT,
                    created_by=request.user,
                )
                for field_name, value in draft_fields.items():
                    setattr(campaign, field_name, value)
                campaign.save()
            except NotificationCampaign.DoesNotExist:
                return JsonResponse({"error": "Draft not found"}, status=404)
        else:
            campaign = NotificationCampaign.objects.create(
                **draft_fields,
                status=NotificationCampaign.Status.DRAFT,
                recipient_count=0,
                created_by=request.user,
            )

        return JsonResponse({"pk": campaign.pk})
