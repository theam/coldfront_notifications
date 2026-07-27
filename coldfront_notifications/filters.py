"""
Filter engine for the compose page.

Each filter class owns:
- ``initial_options()`` — UI dropdown data for the frontend
- ``_apply(qs, values)`` — queryset narrowing for recipient resolution

``FilterDataBuilder`` assembles all filter data into a single JSON-ready
payload that the frontend uses for client-side cross-filter cascading.
"""
from __future__ import annotations

import json
from collections import defaultdict
from typing import Any

from django.contrib.auth import get_user_model
from django.db.models import Case, IntegerField, Value, When

from coldfront.core.allocation.models import (
    Allocation,
    AllocationStatusChoice,
    AllocationUser,
)
from coldfront.core.department.models import Department
from coldfront.core.project.models import Project, ProjectUser, ProjectUserRoleChoice
from ifxuser.models import OrgRelation

User = get_user_model()


class BaseFilter:
    """Abstract interface for all compose-page filters."""

    def initial_options(self) -> list[dict[str, Any]]:
        raise NotImplementedError

    def narrowed_by(self) -> list[str]:
        return []

    def to_dict(self) -> dict[str, Any]:
        return {
            "options": self.initial_options(),
            "narrowed_by": self.narrowed_by(),
        }

    def apply(self, queryset, values):
        if not values:
            return queryset
        return self._apply(queryset, values)

    def _apply(self, queryset, values):
        raise NotImplementedError


# Concrete filters

class DepartmentFilter(BaseFilter):
    """Departments linked to projects via OrgRelation → ProjectOrganization."""

    def initial_options(self) -> list[dict[str, Any]]:
        departments = list(
            Department.objects.order_by("name")
            .values_list("pk", "name")
        )
        if not departments:
            return []

        department_ids = {pk for pk, _ in departments}

        department_projects = (
            OrgRelation.objects
            .filter(
                parent_id__in=department_ids,
                child__rank="lab",
                child__projectorganization__isnull=False,
            )
            .values_list(
                "parent_id",
                "child__projectorganization__project_id",
            )
            .distinct()
        )

        project_ids_by_department: dict[int, set[int]] = defaultdict(set)
        for department_pk, project_id in department_projects:
            project_ids_by_department[department_pk].add(project_id)

        return [
            {
                "id": pk,
                "label": name,
                "project_ids": sorted(project_ids_by_department.get(pk, set())),
            }
            for pk, name in departments
        ]

    def _apply(self, project_users, department_pks):
        project_ids = (
            OrgRelation.objects
            .filter(
                parent_id__in=department_pks,
                child__rank="lab",
                child__projectorganization__isnull=False,
            )
            .values_list("child__projectorganization__project_id", flat=True)
            .distinct()
        )
        return project_users.filter(project_id__in=project_ids)


class ProjectFilter(BaseFilter):
    """All projects, narrowed by department selections."""

    def initial_options(self) -> list[dict[str, Any]]:
        return [
            {"id": pk, "label": title}
            for pk, title in (
                Project.objects.order_by("title").values_list("pk", "title")
            )
        ]

    def narrowed_by(self) -> list[str]:
        return ["departments"]

    def _apply(self, project_users, project_pks):
        return project_users.filter(project__pk__in=project_pks)


class ResourceFilter(BaseFilter):
    """Resources linked to projects through allocations."""

    def initial_options(self) -> list[dict[str, Any]]:
        resource_allocations = (
            Allocation.objects
            .values_list("resources__pk", "resources__name", "project_id")
            .distinct()
        )

        resources_by_pk: dict[int, dict[str, Any]] = {}
        for resource_pk, resource_name, project_id in resource_allocations:
            if resource_pk is None:
                continue
            if resource_pk not in resources_by_pk:
                resources_by_pk[resource_pk] = {
                    "id": resource_pk,
                    "label": resource_name,
                    "project_ids": set(),
                }
            if project_id is not None:
                resources_by_pk[resource_pk]["project_ids"].add(project_id)

        options = sorted(resources_by_pk.values(), key=lambda r: r["label"])
        for option in options:
            option["project_ids"] = sorted(option["project_ids"])

        return options

    def narrowed_by(self) -> list[str]:
        return ["projects"]

    def _apply(self, allocations, resource_pks):
        return allocations.filter(resources__pk__in=resource_pks)


class StatusFilter(BaseFilter):
    """Allocation status choices."""

    def initial_options(self) -> list[dict[str, Any]]:
        return [
            {"id": name, "label": name}
            for name in (
                AllocationStatusChoice.objects
                .order_by("name")
                .values_list("name", flat=True)
            )
        ]

    def _apply(self, allocations, status_names):
        return allocations.filter(status__name__in=status_names)


class AllocationFilter(BaseFilter):
    """Allocations with FK columns for cross-filtering."""

    def initial_options(self) -> list[dict[str, Any]]:
        allocations = (
            Allocation.objects
            .select_related("project", "status")
            .prefetch_related("resources")
            .order_by("project__title", "pk")
        )

        options = []
        for allocation in allocations:
            parent_resource = allocation.get_parent_resource
            status_name = allocation.status.name if allocation.status else ""
            label = f"{allocation.project.title} \u2014 {parent_resource or ''} [{status_name}] (#{allocation.pk})"
            options.append({
                "id": allocation.pk,
                "label": label,
                "project_id": allocation.project_id,
                "status": allocation.status.name if allocation.status else "",
                "resource_ids": sorted(
                    allocation.resources.values_list("pk", flat=True)
                ),
            })

        return options

    def narrowed_by(self) -> list[str]:
        return ["projects", "resources", "statuses"]

    def _apply(self, allocations, allocation_pks):
        return allocations.filter(pk__in=allocation_pks)


class RoleFilter(BaseFilter):
    """Project user role choices with project_ids for bottom-up selection."""

    def initial_options(self) -> list[dict[str, Any]]:
        roles = list(
            ProjectUserRoleChoice.objects
            .order_by("name")
            .values_list("name", flat=True)
        )
        if not roles:
            return []

        role_projects = (
            ProjectUser.objects
            .filter(status__name="Active")
            .values_list("role__name", "project_id")
            .distinct()
        )

        project_ids_by_role: dict[str, set[int]] = defaultdict(set)
        for role_name, project_id in role_projects:
            if role_name and project_id is not None:
                project_ids_by_role[role_name].add(project_id)

        return [
            {
                "id": name,
                "label": name,
                "project_ids": sorted(project_ids_by_role.get(name, set())),
            }
            for name in roles
        ]

    def _apply(self, project_users, role_names):
        return project_users.filter(role__name__in=role_names)


# Registry

FILTER_REGISTRY: dict[str, type[BaseFilter]] = {
    "departments": DepartmentFilter,
    "projects": ProjectFilter,
    "resources": ResourceFilter,
    "statuses": StatusFilter,
    "allocations": AllocationFilter,
    "roles": RoleFilter,
}

PROJECT_FILTERS = ("departments", "projects", "roles")
ALLOCATION_FILTERS = ("allocations", "resources", "statuses")

PI_PRIORITY_ANNOTATION = {
    "role_priority": Case(
        When(role__name="PI", then=Value(0)),
        default=Value(1),
        output_field=IntegerField(),
    )
}


# FilterDataBuilder

class FilterDataBuilder:
    """Assembles all filter data into a single JSON-ready payload."""

    def build(self) -> dict[str, dict[str, Any]]:
        return {
            name: cls().to_dict()
            for name, cls in FILTER_REGISTRY.items()
        }

    def build_json(self) -> str:
        return json.dumps(self.build())


# RecipientResolver

class RecipientResolver:
    """Resolves the selected filter values into actual recipients at send time.

    Accepts the filters dict from the compose POST and produces User
    querysets or (user, project, allocation) tuples for email rendering.

    Supports two modes (via ``selection_mode`` key in filters):
      - ``"filters"`` (default): narrow recipients through filter cascade
      - ``"direct"``: hand-pick users by PK via ``direct_user_pks``
    """

    def __init__(self, filters: dict):
        self.filters = filters

    def _is_direct_mode(self) -> bool:
        return self.filters.get("selection_mode") == "direct"

    # ── filter-mode helpers ─────────────────────────────────────────

    def _apply_project_filters(self, project_users):
        for name in PROJECT_FILTERS:
            values = self.filters.get(name) or []
            if values:
                project_users = FILTER_REGISTRY[name]().apply(project_users, values)
        return project_users

    def _apply_allocation_filters(self):
        allocations = Allocation.objects.all()
        for name in ALLOCATION_FILTERS:
            values = self.filters.get(name) or []
            if values:
                allocations = FILTER_REGISTRY[name]().apply(allocations, values)
        return allocations

    def _has_allocation_filters(self):
        return any(self.filters.get(name) for name in ALLOCATION_FILTERS)

    # ── public API ──────────────────────────────────────────────────

    def queryset(self):
        """Return a deduplicated User queryset matching the filters."""
        if self._is_direct_mode():
            pks = self.filters.get("direct_user_pks") or []
            return User.objects.filter(pk__in=pks)

        project_users = self._apply_project_filters(
            ProjectUser.objects.select_related("user", "project")
            .filter(status__name="Active"),
        )

        if self._has_allocation_filters():
            allocation_user_pks = (
                AllocationUser.objects
                .filter(
                    allocation__in=self._apply_allocation_filters(),
                    status__name="Active",
                )
                .values_list("user__pk", flat=True)
            )
            project_users = project_users.filter(user__pk__in=allocation_user_pks)

        user_pks = project_users.values_list("user__pk", flat=True).distinct()
        return User.objects.filter(pk__in=user_pks)

    def count(self) -> int:
        return self.queryset().count()

    def enumerate(self, scope: str):
        """Yield (user, project, allocation) tuples.

        scope:
          "allocation" — one tuple per (user, project, allocation) match
          "project"    — one tuple per (user, project); allocation=None
          "user"       — one tuple per distinct user; project=None, allocation=None
        """
        if scope not in ("user", "project", "allocation"):
            raise ValueError(f"Unknown scope {scope!r}")

        if self._is_direct_mode():
            yield from self._enumerate_direct(scope)
            return

        if scope == "user":
            for user in self.queryset().iterator():
                yield (user, None, None)
            return

        project_users = self._apply_project_filters(
            ProjectUser.objects
            .select_related(
                "user", "project", "project__pi", "project__status", "role",
            )
            .filter(status__name="Active"),
        ).annotate(**PI_PRIORITY_ANNOTATION).order_by(
            "user_id", "role_priority", "project_id", "pk",
        )

        if scope == "project":
            if self._has_allocation_filters():
                allocation_user_pks = (
                    AllocationUser.objects
                    .filter(
                        allocation__in=self._apply_allocation_filters(),
                        status__name="Active",
                    )
                    .values_list("user__pk", flat=True)
                    .distinct()
                )
                project_users = project_users.filter(user__pk__in=allocation_user_pks)
            for project_user in project_users.iterator():
                yield (project_user.user, project_user.project, None)
            return

        # scope == "allocation"
        matched_allocations = self._apply_allocation_filters()
        allocation_users = (
            AllocationUser.objects
            .select_related(
                "allocation", "allocation__status", "allocation__project",
            )
            .filter(allocation__in=matched_allocations, status__name="Active")
            .order_by("user_id", "allocation_id", "pk")
        )

        allocations_by_user_project = defaultdict(list)
        for allocation_user in allocation_users.iterator():
            key = (allocation_user.user_id, allocation_user.allocation.project_id)
            allocations_by_user_project[key].append(allocation_user.allocation)

        for project_user in project_users.iterator():
            key = (project_user.user_id, project_user.project_id)
            for allocation in allocations_by_user_project.get(key, []):
                yield (project_user.user, project_user.project, allocation)

    # ── direct-mode enumeration ─────────────────────────────────────

    def _enumerate_direct(self, scope: str):
        """Yield tuples for directly-selected users, expanding to their
        projects/allocations when the template scope demands it."""
        pks = self.filters.get("direct_user_pks") or []
        if not pks:
            return

        if scope == "user":
            for user in User.objects.filter(pk__in=pks).iterator():
                yield (user, None, None)
            return

        # Expand to active project memberships
        project_users = (
            ProjectUser.objects
            .select_related(
                "user", "project", "project__pi", "project__status", "role",
            )
            .filter(user__pk__in=pks, status__name="Active")
            .annotate(**PI_PRIORITY_ANNOTATION)
            .order_by("user_id", "role_priority", "project_id", "pk")
        )

        if scope == "project":
            for project_user in project_users.iterator():
                yield (project_user.user, project_user.project, None)
            return

        # scope == "allocation" — expand to active allocations
        allocation_users = (
            AllocationUser.objects
            .select_related(
                "allocation", "allocation__status", "allocation__project",
            )
            .filter(user__pk__in=pks, status__name="Active")
            .order_by("user_id", "allocation_id", "pk")
        )

        allocations_by_user_project = defaultdict(list)
        for allocation_user in allocation_users.iterator():
            key = (allocation_user.user_id, allocation_user.allocation.project_id)
            allocations_by_user_project[key].append(allocation_user.allocation)

        for project_user in project_users.iterator():
            key = (project_user.user_id, project_user.project_id)
            for allocation in allocations_by_user_project.get(key, []):
                yield (project_user.user, project_user.project, allocation)

    def enumerate_deduped(self, scope: str, dedupe_users=None,
                          dedupe_selections=None):
        """Wraps enumerate with per-user deduplication.

        dedupe_users: list of usernames — keep first tuple only (legacy).
        dedupe_selections: dict {username: [project_pk, ...]} — keep only
            tuples whose project_pk is in the list.  Takes precedence over
            dedupe_users for usernames present in both.
        """
        selections = dedupe_selections or {}
        legacy_dedupe = set(dedupe_users or []) - set(selections.keys())

        if not legacy_dedupe and not selections:
            yield from self.enumerate(scope)
            return

        seen_legacy = set()
        for user, project, allocation in self.enumerate(scope):
            username = user.username

            if username in selections:
                keep_pks = selections[username]
                if project and project.pk in keep_pks:
                    yield (user, project, allocation)
                elif not project:
                    yield (user, project, allocation)
                continue

            if username in legacy_dedupe:
                if user.pk in seen_legacy:
                    continue
                seen_legacy.add(user.pk)

            yield (user, project, allocation)
