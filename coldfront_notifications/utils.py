"""
Recipient filter engine.

Two entry points:
  - build_recipient_queryset(filters)  → distinct User queryset (for counts)
  - enumerate_recipients(filters, scope)
        → yields (user, project, allocation) tuples; allocation may be None
          when scope doesn't require one.

`scope` is the broadest variable type referenced by the template:
  "allocation" → iterate per AllocationUser
  "project"    → iterate per ProjectUser
  "user"       → distinct users only
"""
from django.contrib.auth import get_user_model

User = get_user_model()


def _apply_project_filters(pu_qs, filters):
    projects    = filters.get("projects") or []
    roles       = filters.get("roles") or []
    departments = filters.get("departments") or []
    if projects:
        pu_qs = pu_qs.filter(project__pk__in=projects)
    if roles:
        pu_qs = pu_qs.filter(role__name__in=roles)
    if departments:
        pu_qs = pu_qs.filter(
            project__projectorganization__organization__rank="department",
            project__projectorganization__organization__name__in=departments,
        )
    return pu_qs


def _allocation_queryset(filters):
    from coldfront.core.allocation.models import Allocation
    allocations = filters.get("allocations") or []
    resources   = filters.get("resources") or []
    statuses    = filters.get("statuses") or []
    qs = Allocation.objects.all()
    if allocations:
        qs = qs.filter(pk__in=allocations)
    if resources:
        qs = qs.filter(resources__pk__in=resources)
    if statuses:
        qs = qs.filter(status__name__in=statuses)
    return qs


def _has_alloc_filters(filters):
    return bool(
        filters.get("allocations")
        or filters.get("resources")
        or filters.get("statuses")
    )


def build_recipient_queryset(filters: dict):
    """
    filters keys (all optional, all lists):
        projects     - Project pk list
        allocations  - Allocation pk list
        resources    - Resource pk list
        departments  - dept Organization name strings (deduped across org_trees)
        statuses     - AllocationStatusChoice name strings
        roles        - ProjectUserRoleChoice name strings

    Returns a QuerySet[User] deduplicated.
    """
    from coldfront.core.project.models import ProjectUser
    from coldfront.core.allocation.models import AllocationUser

    pu_qs = _apply_project_filters(
        ProjectUser.objects.select_related("user", "project").filter(status__name="Active"),
        filters,
    )

    if _has_alloc_filters(filters):
        alloc_user_pks = AllocationUser.objects.filter(
            allocation__in=_allocation_queryset(filters),
            status__name="Active",
        ).values_list("user__pk", flat=True)
        pu_qs = pu_qs.filter(user__pk__in=alloc_user_pks)

    user_pks = pu_qs.values_list("user__pk", flat=True).distinct()
    return User.objects.filter(pk__in=user_pks)


def recipient_count(filters: dict) -> int:
    return build_recipient_queryset(filters).count()


def recipient_emails(filters: dict, extra: list = None) -> list:
    emails = list(
        build_recipient_queryset(filters)
        .exclude(email="")
        .values_list("email", flat=True)
        .distinct()
    )
    if extra:
        for addr in extra:
            addr = addr.strip()
            if addr and addr not in emails:
                emails.append(addr)
    return emails


def enumerate_recipients_deduped(filters: dict, scope: str, dedupe_users):
    """
    Wraps enumerate_recipients. For any user whose username is in the
    dedupe_users set, only the first yielded tuple is kept.
    """
    dedupe = set(dedupe_users or [])
    if not dedupe:
        yield from enumerate_recipients(filters, scope)
        return
    seen = set()
    for user, project, allocation in enumerate_recipients(filters, scope):
        if user.username in dedupe:
            if user.pk in seen:
                continue
            seen.add(user.pk)
        yield (user, project, allocation)


def enumerate_recipients(filters: dict, scope: str):
    """
    Yield (user, project, allocation) tuples for sending.

    scope:
      "allocation" → one tuple per (user, project, allocation) match
      "project"    → one tuple per (user, project) match; allocation=None
      "user"       → one tuple per distinct user;        project=None, allocation=None

    With scope='allocation', if a user has multiple matching allocations under
    a project, one tuple (and therefore one email) is emitted for each.
    """
    from coldfront.core.project.models import ProjectUser
    from coldfront.core.allocation.models import AllocationUser

    if scope not in ("user", "project", "allocation"):
        raise ValueError(f"Unknown scope {scope!r}")

    if scope == "user":
        for u in build_recipient_queryset(filters).iterator():
            yield (u, None, None)
        return

    pu_qs = _apply_project_filters(
        ProjectUser.objects
        .select_related("user", "project", "project__pi", "project__status", "role")
        .filter(status__name="Active"),
        filters,
    ).order_by("user_id", "project_id", "pk")

    if scope == "project":
        if _has_alloc_filters(filters):
            alloc_qs = _allocation_queryset(filters)
            # Only keep PUs that have at least one active AllocationUser on a matching allocation.
            au_user_pks = AllocationUser.objects.filter(
                allocation__in=alloc_qs, status__name="Active",
            ).values_list("user__pk", flat=True).distinct()
            pu_qs = pu_qs.filter(user__pk__in=au_user_pks)
        for pu in pu_qs.iterator():
            yield (pu.user, pu.project, None)
        return

    # scope == "allocation"
    alloc_qs = _allocation_queryset(filters)
    # Pull AllocationUsers for the allocation scope; we'll cross-reference per PU.
    au_qs = (
        AllocationUser.objects
        .select_related("allocation", "allocation__status", "allocation__project")
        .filter(allocation__in=alloc_qs, status__name="Active")
        .order_by("user_id", "allocation_id", "pk")
    )
    # Index AllocationUsers by (user_id, project_id) → list of allocations
    from collections import defaultdict
    by_pair = defaultdict(list)
    for au in au_qs.iterator():
        by_pair[(au.user_id, au.allocation.project_id)].append(au.allocation)

    for pu in pu_qs.iterator():
        allocs = by_pair.get((pu.user_id, pu.project_id), [])
        for a in allocs:
            yield (pu.user, pu.project, a)
