"""
Query resolvers for NotificationVariable(source='query').

The admin UI shows QUERY_CHOICES as a dropdown. The chosen value is stored
as NotificationVariable.resolver_key and dispatches here at send time.

Each resolver takes a context dict:
    {"user": IfxUser, "project": Project|None, "allocation": Allocation|None}
and returns a str, or raises MissingValue if the value can't be produced.
"""


class MissingValue(Exception):
    """Raised by a resolver when it cannot produce a non-empty value."""


def _fmt(value):
    if value is None:
        raise MissingValue()
    s = str(value).strip()
    if not s:
        raise MissingValue()
    return s


def _need(ctx, key):
    v = ctx.get(key)
    if v is None:
        raise MissingValue()
    return v


def _primary_resource(allocation):
    """Returns the parent resource of an allocation, or None."""
    if allocation is None:
        return None
    return allocation.get_parent_resource  # @property


def _dept_name(project):
    from ifxuser.models import Organization
    return Organization.objects.filter(
        projectorganization__project=project,
        rank="department",
    ).values_list("name", flat=True).first()


def _project_role(user, project):
    from coldfront.core.project.models import ProjectUser
    pu = (
        ProjectUser.objects.filter(
            user=user, project=project, status__name="Active",
        )
        .select_related("role")
        .first()
    )
    return pu.role.name if pu and pu.role else None


# ─────────────────────────────────────────────────────────────────────────
# Resolver registry.
# Each entry: resolver_key → callable(ctx) -> str
# ─────────────────────────────────────────────────────────────────────────

QUERY_RESOLVERS = {
    # Recipient (user)
    "user.username": lambda c: _fmt(_need(c, "user").username),
    "user.email": lambda c: _fmt(_need(c, "user").email),
    "user.full_name": lambda c: _fmt(_need(c, "user").get_full_name() or _need(c, "user").username),
    "user.first_name": lambda c: _fmt(_need(c, "user").first_name),
    "user.last_name": lambda c: _fmt(_need(c, "user").last_name),
    "user.ifxid": lambda c: _fmt(getattr(_need(c, "user"), "ifxid", None)),

    # Project
    "project.title": lambda c: _fmt(_need(c, "project").title),
    "project.description": lambda c: _fmt(_need(c, "project").description),
    "project.status.name": lambda c: _fmt(_need(c, "project").status.name),
    "project.field_of_science.description": lambda c: _fmt(
        _need(c, "project").field_of_science.description if _need(c, "project").field_of_science_id else None
    ),
    "project.parent_project.title": lambda c: _fmt(
        _need(c, "project").parent_project.title if _need(c, "project").parent_project_id else None
    ),

    # PI (of project)
    "project.pi.full_name": lambda c: _fmt(
        _need(c, "project").pi.get_full_name() or _need(c, "project").pi.username if _need(c, "project").pi_id else None
    ),
    "project.pi.email": lambda c: _fmt(_need(c, "project").pi.email if _need(c, "project").pi_id else None),
    "project.pi.username": lambda c: _fmt(_need(c, "project").pi.username if _need(c, "project").pi_id else None),

    # Department (via ProjectOrganization)
    "project.department.name": lambda c: _fmt(_dept_name(_need(c, "project"))),

    # User's role on the project
    "projectuser.role.name": lambda c: _fmt(_project_role(_need(c, "user"), _need(c, "project"))),

    # Allocation
    "allocation.id": lambda c: _fmt(_need(c, "allocation").pk),
    "allocation.description": lambda c: _fmt(_need(c, "allocation").description),
    "allocation.justification": lambda c: _fmt(_need(c, "allocation").justification),
    "allocation.quantity": lambda c: _fmt(_need(c, "allocation").quantity),
    "allocation.start_date": lambda c: _fmt(_need(c, "allocation").start_date),
    "allocation.end_date": lambda c: _fmt(_need(c, "allocation").end_date),
    "allocation.status.name": lambda c: _fmt(_need(c, "allocation").status.name),

    # Resource (primary on matched allocation)
    "resource.name": lambda c: _fmt(getattr(_primary_resource(_need(c, "allocation")), "name", None)),
    "resource.description": lambda c: _fmt(getattr(_primary_resource(_need(c, "allocation")), "description", None)),
    "resource.resource_type.name": lambda c: _fmt(
        (_primary_resource(_need(c, "allocation")).resource_type.name
         if _primary_resource(_need(c, "allocation")) and _primary_resource(_need(c, "allocation")).resource_type_id
         else None)
    ),
    "resource.parent_resource.name": lambda c: _fmt(
        (_primary_resource(_need(c, "allocation")).parent_resource.name
         if _primary_resource(_need(c, "allocation")) and _primary_resource(_need(c, "allocation")).parent_resource_id
         else None)
    ),
    "resource.names_joined": lambda c: _fmt(
        ", ".join(r.name for r in _need(c, "allocation").resources.all()) or None
    ),
}


# Human-readable labels for the admin dropdown, grouped.
# Order preserved; groups keyed for UI grouping.
QUERY_CHOICES = [
    # (resolver_key, label, group)
    ("user.username",   "User · Username",   "Recipient"),
    ("user.email",      "User · Email",      "Recipient"),
    ("user.full_name",  "User · Full Name",  "Recipient"),
    ("user.first_name", "User · First Name", "Recipient"),
    ("user.last_name",  "User · Last Name",  "Recipient"),
    ("user.ifxid",      "User · IFX ID",     "Recipient"),

    ("project.title",                        "Project · Title",             "Project"),
    ("project.description",                  "Project · Description",       "Project"),
    ("project.status.name",                  "Project · Status",            "Project"),
    ("project.field_of_science.description", "Project · Field of Science",  "Project"),
    ("project.parent_project.title",         "Project · Parent Title",      "Project"),

    ("project.pi.full_name", "PI · Full Name", "PI"),
    ("project.pi.email",     "PI · Email",     "PI"),
    ("project.pi.username",  "PI · Username",  "PI"),

    ("project.department.name", "Department · Name", "Department"),

    ("projectuser.role.name", "Role · Name", "Role"),

    ("allocation.id",            "Allocation · ID",             "Allocation"),
    ("allocation.description",   "Allocation · Description",    "Allocation"),
    ("allocation.justification", "Allocation · Justification",  "Allocation"),
    ("allocation.quantity",      "Allocation · Quantity",       "Allocation"),
    ("allocation.start_date",    "Allocation · Start Date",     "Allocation"),
    ("allocation.end_date",      "Allocation · End Date",       "Allocation"),
    ("allocation.status.name",   "Allocation · Status",         "Allocation"),

    ("resource.name",                "Resource · Name (primary)", "Resource"),
    ("resource.description",         "Resource · Description",    "Resource"),
    ("resource.resource_type.name",  "Resource · Type",           "Resource"),
    ("resource.parent_resource.name","Resource · Parent Name",    "Resource"),
    ("resource.names_joined",        "Resource · All Names",      "Resource"),
]


# Each resolver_key's required scope — used to decide whether to iterate
# AllocationUsers vs ProjectUsers vs just distinct users during enumeration.
def _scope_for(key):
    if key.startswith("allocation.") or key.startswith("resource."):
        return "allocation"
    if key.startswith("project.") or key.startswith("projectuser."):
        return "project"
    return "user"


QUERY_SCOPES = {k: _scope_for(k) for k, _label, _group in QUERY_CHOICES}


def resolve(resolver_key: str, ctx: dict) -> str:
    """Dispatch a resolver_key on a context. Raises MissingValue on failure."""
    fn = QUERY_RESOLVERS.get(resolver_key)
    if fn is None:
        raise MissingValue()
    try:
        return fn(ctx)
    except MissingValue:
        raise
    except Exception:
        # Any attribute traversal failure collapses to a missing value.
        raise MissingValue()
