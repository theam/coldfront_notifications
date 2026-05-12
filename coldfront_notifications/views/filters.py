"""
Filter computation engine for the compose page.

Provides:
  - build_filter_summaries()  — summary dicts for tooltip/detail display
  - get_filter_context()      — initial filter data for page load
  - compute_filter_options()  — full cross-filter computation for AJAX cascade
"""
from collections import Counter

from ..models import SenderConfig


def build_filter_summaries(projects_qs=None, allocations_qs=None,
                           resources_qs=None, dept_names=None,
                           status_names=None, role_names=None,
                           active_filters=None):
    """
    Build a summary dict for each filter's current option set.

    Each summary has: count (int), breakdown (str), filtered_by (str or "").
    The frontend renders these into a styled tooltip.

    active_filters: dict of filter labels -> list of selected display values,
                    e.g. {"Allocation Status": ["Denied"], "Project": ["Alpha"]}.
                    Empty or None means no filters applied.
    """
    af = active_filters or {}
    parts = {}

    def _filtered_by(exclude_label):
        """Format which OTHER filters are active (exclude our own label)."""
        lines = []
        for label, vals in af.items():
            if label == exclude_label or not vals:
                continue
            lines.append(f"{label}: {', '.join(str(v) for v in vals)}")
        return " | ".join(lines)

    def _summary(label, total, breakdown, exclude_label):
        return {
            "count": total,
            "breakdown": breakdown,
            "filtered_by": _filtered_by(exclude_label),
        }

    # Projects — group by project status
    if projects_qs is not None:
        total = projects_qs.count()
        if total:
            counts = Counter(projects_qs.values_list("status__name", flat=True))
            breakdown = ", ".join(f"{c} {n}" for n, c in counts.most_common())
        else:
            breakdown = ""
        parts["projects"] = _summary("Project", total, breakdown, "Project")

    # Allocations — group by allocation status
    if allocations_qs is not None:
        total = allocations_qs.distinct().count()
        if total:
            counts = Counter(allocations_qs.distinct().values_list("status__name", flat=True))
            breakdown = ", ".join(f"{c} {n}" for n, c in counts.most_common())
        else:
            breakdown = ""
        parts["allocations"] = _summary("Allocation", total, breakdown, "Allocation")

    # Resources — group by resource type
    if resources_qs is not None:
        total = resources_qs.count()
        if total:
            counts = Counter(resources_qs.values_list("resource_type__name", flat=True))
            breakdown = ", ".join(f"{c} {n}" for n, c in counts.most_common())
        else:
            breakdown = ""
        parts["resources"] = _summary("Resource", total, breakdown, "Resource")

    # Departments
    if dept_names is not None:
        names = sorted(set(dept_names))
        total = len(names)
        breakdown = ", ".join(names[:8])
        if total > 8:
            breakdown += f" (+{total - 8} more)"
        parts["departments"] = _summary("Department", total, breakdown, "Department")

    # Statuses
    if status_names is not None:
        names = sorted(set(n for n in status_names if n))
        total = len(names)
        parts["statuses"] = _summary("Allocation Status", total, ", ".join(names), "Allocation Status")

    # Roles
    if role_names is not None:
        names = sorted(set(n for n in role_names if n))
        total = len(names)
        parts["roles"] = _summary("User Role", total, ", ".join(names), "User Role")

    return parts


def get_filter_context():
    """Return the initial filter context for the compose page template."""
    from coldfront.core.project.models import Project, ProjectUserRoleChoice
    from coldfront.core.allocation.models import Allocation, AllocationStatusChoice
    from coldfront.core.resource.models import Resource
    from ifxuser.models import Organization

    projects_qs = Project.objects.order_by("title")
    allocations_qs = Allocation.objects.select_related("project").order_by("project__title", "pk")
    resources_qs = Resource.objects.order_by("name")
    dept_names = sorted(set(
        Organization.objects.filter(rank="department").values_list("name", flat=True)
    ))
    status_names = list(AllocationStatusChoice.objects.values_list("name", flat=True).order_by("name"))
    role_names = list(ProjectUserRoleChoice.objects.values_list("name", flat=True).order_by("name"))

    summaries = build_filter_summaries(
        projects_qs=projects_qs, allocations_qs=allocations_qs,
        resources_qs=resources_qs, dept_names=dept_names,
        status_names=status_names, role_names=role_names,
    )

    return {
        "projects":    projects_qs,
        "allocations": allocations_qs,
        "resources":   resources_qs,
        "departments": dept_names,
        "statuses":    status_names,
        "roles":       role_names,
        "senders":     SenderConfig.objects.all(),
        "reply_tos":   SenderConfig.objects.all(),
        "filter_summaries": summaries,
    }


def compute_filter_options(selections):
    """
    Compute all filter options from the given selections.

    Each filter excludes its own selection (no self-narrowing) but considers
    every other active selection.  Returns a dict ready for JsonResponse.
    """
    from coldfront.core.project.models import Project, ProjectUserRoleChoice
    from coldfront.core.allocation.models import Allocation, AllocationStatusChoice
    from coldfront.core.resource.models import Resource
    from ifxuser.models import Organization

    sel_projects    = [int(x) for x in selections.get("projects", []) if x]
    sel_allocations = [int(x) for x in selections.get("allocations", []) if x]
    sel_resources   = [int(x) for x in selections.get("resources", []) if x]
    sel_departments = [str(x) for x in selections.get("departments", []) if x]
    sel_statuses    = [str(x) for x in selections.get("statuses", []) if x]
    sel_roles       = [str(x) for x in selections.get("roles", []) if x]

    # ── Helpers ──────────────────────────────────────────────────────
    def _project_options(qs):
        return [{"id": pk, "label": title}
                for pk, title in qs.order_by("title").values_list("pk", "title")]

    def _allocation_options(qs):
        return [{"id": pk, "label": f"{proj} — {res or ''}"}
                for pk, proj, res in qs.distinct()
                .order_by("project__title", "pk")
                .values_list("pk", "project__title", "resources__name")]

    def _resource_options(qs):
        return [{"id": pk, "label": name}
                for pk, name in qs.order_by("name").values_list("pk", "name")]

    def _department_options(names):
        return [{"id": n, "label": n} for n in sorted(set(names))]

    def _string_options(names):
        return [{"id": n, "label": n} for n in sorted(set(n for n in names if n))]

    def _prune(options, selected):
        valid = {o["id"] for o in options}
        return [s for s in selected if s in valid]

    # ── Full counts — small lookup tables, fetched once for
    #    "no narrowing" detection. No full queryset materialized. ─────
    full_counts = {
        "projects":    Project.objects.count(),
        "allocations": Allocation.objects.distinct().count(),
        "resources":   Resource.objects.count(),
        "departments": (Organization.objects.filter(rank="department")
                        .values("name").distinct().count()),
        "statuses":    AllocationStatusChoice.objects.count(),
        "roles":       ProjectUserRoleChoice.objects.count(),
    }

    # ── Tier 1: project <-> department (mutual peers) ────────────────
    proj_opts_qs = Project.objects.all()
    if sel_departments:
        proj_opts_qs = proj_opts_qs.filter(
            projectorganization__organization__rank="department",
            projectorganization__organization__name__in=sel_departments,
        )
    projects_opts = _project_options(proj_opts_qs)

    dept_scope_qs = Project.objects.all()
    if sel_projects:
        dept_scope_qs = dept_scope_qs.filter(pk__in=sel_projects)
    dept_names = list(Organization.objects.filter(
        rank="department",
        projectorganization__project__in=dept_scope_qs,
    ).values_list("name", flat=True).distinct())
    departments_opts = _department_options(dept_names)

    # Full project scope (both selections applied) for Tier 2.
    project_scope = Project.objects.all()
    if sel_projects:
        project_scope = project_scope.filter(pk__in=sel_projects)
    if sel_departments:
        project_scope = project_scope.filter(
            projectorganization__organization__rank="department",
            projectorganization__organization__name__in=sel_departments,
        )
    alloc_base = Allocation.objects.filter(project__in=project_scope)

    # ── Tier 2: allocation, resource, status — each excludes itself ──
    alloc_opts_qs = alloc_base
    if sel_statuses:
        alloc_opts_qs = alloc_opts_qs.filter(status__name__in=sel_statuses)
    if sel_resources:
        alloc_opts_qs = alloc_opts_qs.filter(resources__pk__in=sel_resources)
    allocations_opts = _allocation_options(alloc_opts_qs)

    res_scope = alloc_base
    if sel_statuses:
        res_scope = res_scope.filter(status__name__in=sel_statuses)
    if sel_allocations:
        res_scope = res_scope.filter(pk__in=sel_allocations)
    res_opts_qs = Resource.objects.filter(
        pk__in=res_scope.values_list("resources__pk", flat=True).distinct()
    )
    resources_opts = _resource_options(res_opts_qs)

    # Status is independent of allocation selection to avoid circular pruning.
    stat_scope = alloc_base
    if sel_resources:
        stat_scope = stat_scope.filter(resources__pk__in=sel_resources)
    status_names = list(stat_scope.values_list("status__name", flat=True).distinct())
    statuses_opts = _string_options(status_names)

    # Roles: from project scope (single query with join).
    role_names = list(
        ProjectUserRoleChoice.objects.filter(
            projectuser__project__in=project_scope
        ).values_list("name", flat=True).distinct()
    )
    roles_opts = _string_options(role_names)

    # ── Prune selections ─────────────────────────────────────────────
    pruned = {
        "projects":    _prune(projects_opts, sel_projects),
        "allocations": _prune(allocations_opts, sel_allocations),
        "departments": _prune(departments_opts, sel_departments),
        "resources":   _prune(resources_opts, sel_resources),
        "statuses":    _prune(statuses_opts, sel_statuses),
        "roles":       _prune(roles_opts, sel_roles),
    }

    # ── Active-filter labels from already-computed options ────────────
    # Look up labels from the options lists instead of re-querying the DB.
    proj_labels = {o["id"]: o["label"] for o in projects_opts}
    res_labels  = {o["id"]: o["label"] for o in resources_opts}
    alloc_labels = {o["id"]: o["label"] for o in allocations_opts}

    active_filters = {}
    if pruned["projects"]:
        names = [proj_labels[pk] for pk in pruned["projects"] if pk in proj_labels]
        if names:
            active_filters["Project"] = names
    if pruned["allocations"]:
        names = [alloc_labels.get(pk, str(pk)) for pk in pruned["allocations"]]
        active_filters["Allocation"] = names
    if pruned["departments"]:
        active_filters["Department"] = pruned["departments"]
    if pruned["resources"]:
        names = [res_labels[pk] for pk in pruned["resources"] if pk in res_labels]
        if names:
            active_filters["Resource"] = names
    if pruned["statuses"]:
        active_filters["Allocation Status"] = pruned["statuses"]
    if pruned["roles"]:
        active_filters["User Role"] = pruned["roles"]

    # ── Summaries ────────────────────────────────────────────────────
    summaries = build_filter_summaries(
        projects_qs=proj_opts_qs, allocations_qs=alloc_opts_qs,
        resources_qs=res_opts_qs, dept_names=dept_names,
        status_names=status_names, role_names=role_names,
        active_filters=active_filters,
    )

    # Clear "filtered_by" when the option count matches the full set.
    for key, s in summaries.items():
        if isinstance(s, dict) and s.get("count", 0) == full_counts.get(key, -1):
            s["filtered_by"] = ""

    def _pack(options, pruned_sel, key):
        return {
            "options": options,
            "selected": pruned_sel,
            "summary": summaries.get(key, {}),
        }

    return {
        "projects":    _pack(projects_opts, pruned["projects"], "projects"),
        "allocations": _pack(allocations_opts, pruned["allocations"], "allocations"),
        "departments": _pack(departments_opts, pruned["departments"], "departments"),
        "resources":   _pack(resources_opts, pruned["resources"], "resources"),
        "statuses":    _pack(statuses_opts, pruned["statuses"], "statuses"),
        "roles":       _pack(roles_opts, pruned["roles"], "roles"),
    }
