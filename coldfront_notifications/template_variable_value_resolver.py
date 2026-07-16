"""
Query resolvers for NotificationVariable(source='query').

Each resolver group is a class that registers its resolver keys, labels,
scope, and resolve methods. The ResolverRegistry collects all groups and
provides the public API: resolve(), choices, scopes, and key validation.

Context dict passed to each resolver:
    {"user": IfxUser, "project": Project|None, "allocation": Allocation|None}
"""
from coldfront.core.project.models import ProjectUser
from ifxuser.models import Organization


class MissingValue(Exception):
    """Raised when a resolver cannot produce a non-empty value."""


class ResolverContext:
    """Wraps the raw context dict with typed accessors that raise MissingValue."""

    def __init__(self, context: dict):
        self._context = context

    @property
    def user(self):
        user = self._context.get("user")
        if user is None:
            raise MissingValue("user is required")
        return user

    @property
    def project(self):
        project = self._context.get("project")
        if project is None:
            raise MissingValue("project is required")
        return project

    @property
    def allocation(self):
        allocation = self._context.get("allocation")
        if allocation is None:
            raise MissingValue("allocation is required")
        return allocation

    @property
    def primary_resource(self):
        resource = self.allocation.get_parent_resource
        if resource is None:
            raise MissingValue("allocation has no parent resource")
        return resource


def format_value(value):
    """Convert a value to a non-empty stripped string, or raise MissingValue."""
    if value is None:
        raise MissingValue()
    formatted = str(value).strip()
    if not formatted:
        raise MissingValue()
    return formatted


class BaseResolverGroup:
    """Each subclass defines resolvers for a scope (user, project, allocation).

    Subclasses populate ``resolvers`` — a list of (key, label, method_name)
    tuples — and implement the corresponding methods.
    """
    scope = None
    group_label = None
    resolvers = []

    def get_registry_entries(self):
        """Return list of (key, label, group, scope, callable) tuples."""
        entries = []
        for key, label, method_name in self.resolvers:
            method = getattr(self, method_name)
            entries.append((key, label, self.group_label, self.scope, method))
        return entries


class UserResolverGroup(BaseResolverGroup):
    scope = "user"
    group_label = "Recipient"
    resolvers = [
        ("user.username", "User · Username", "resolve_username"),
        ("user.email", "User · Email", "resolve_email"),
        ("user.full_name", "User · Full Name", "resolve_full_name"),
        ("user.first_name", "User · First Name", "resolve_first_name"),
        ("user.last_name", "User · Last Name", "resolve_last_name"),
    ]

    def resolve_username(self, context):
        return format_value(context.user.username)

    def resolve_email(self, context):
        return format_value(context.user.email)

    def resolve_full_name(self, context):
        full_name = context.user.get_full_name()
        return format_value(full_name or context.user.username)

    def resolve_first_name(self, context):
        return format_value(context.user.first_name)

    def resolve_last_name(self, context):
        return format_value(context.user.last_name)


class ProjectResolverGroup(BaseResolverGroup):
    scope = "project"
    group_label = "Project"
    resolvers = [
        ("project.title", "Project · Title", "resolve_title"),
        ("project.status.name", "Project · Status", "resolve_status"),
        ("project.field_of_science.description", "Project · Field of Science", "resolve_field_of_science"),
        ("project.parent_project.title", "Project · Parent Title", "resolve_parent_title"),
        ("project.user_count", "Users · Quantity", "resolve_user_count"),
        ("project.usernames", "Users · Usernames", "resolve_usernames"),
    ]

    def resolve_title(self, context):
        return format_value(context.project.title)

    def resolve_status(self, context):
        return format_value(context.project.status.name)

    def resolve_field_of_science(self, context):
        project = context.project
        if not project.field_of_science_id:
            raise MissingValue("project has no field of science")
        return format_value(project.field_of_science.description)

    def resolve_parent_title(self, context):
        project = context.project
        if not project.parent_project_id:
            raise MissingValue("project has no parent project")
        return format_value(project.parent_project.title)

    def resolve_user_count(self, context):
        active_users = context.project.projectuser_set.filter(status__name="Active")
        return format_value(active_users.count())

    def resolve_usernames(self, context):
        usernames = list(
            context.project.projectuser_set
            .filter(status__name="Active")
            .order_by("user__username")
            .values_list("user__username", flat=True)
        )
        return format_value(", ".join(usernames) or None)


class PIResolverGroup(BaseResolverGroup):
    scope = "project"
    group_label = "PI"
    resolvers = [
        ("project.pi.full_name", "PI · Full Name", "resolve_pi_full_name"),
        ("project.pi.email", "PI · Email", "resolve_pi_email"),
        ("project.pi.username", "PI · Username", "resolve_pi_username"),
    ]

    def _get_pi(self, context):
        project = context.project
        if not project.pi_id:
            raise MissingValue("project has no PI")
        return project.pi

    def resolve_pi_full_name(self, context):
        pi = self._get_pi(context)
        return format_value(pi.get_full_name() or pi.username)

    def resolve_pi_email(self, context):
        return format_value(self._get_pi(context).email)

    def resolve_pi_username(self, context):
        return format_value(self._get_pi(context).username)


class DepartmentResolverGroup(BaseResolverGroup):
    scope = "project"
    group_label = "Department"
    resolvers = [
        ("project.department.name", "Department · Name", "resolve_department_name"),
    ]

    def resolve_department_name(self, context):
        department_name = Organization.objects.filter(
            projectorganization__project=context.project,
            rank="department",
        ).values_list("name", flat=True).first()
        return format_value(department_name)


class RoleResolverGroup(BaseResolverGroup):
    scope = "project"
    group_label = "Role"
    resolvers = [
        ("projectuser.role.name", "Role · Name", "resolve_role_name"),
    ]

    def resolve_role_name(self, context):
        project_user = (
            ProjectUser.objects
            .filter(
                user=context.user,
                project=context.project,
                status__name="Active",
            )
            .select_related("role")
            .first()
        )
        if not project_user or not project_user.role:
            raise MissingValue("user has no active role on this project")
        return format_value(project_user.role.name)


class AllocationResolverGroup(BaseResolverGroup):
    scope = "allocation"
    group_label = "Allocation"
    resolvers = [
        ("allocation.quantity", "Allocation · Quantity", "resolve_quantity"),
        ("allocation.start_date", "Allocation · Start Date", "resolve_start_date"),
        ("allocation.end_date", "Allocation · End Date", "resolve_end_date"),
        ("allocation.status.name", "Allocation · Status", "resolve_status"),
        ("allocation.path", "Allocation · Path", "resolve_path"),
        ("allocation.quota", "Allocation · Quota (TB)", "resolve_quota"),
        ("allocation.usage", "Allocation · Usage", "resolve_usage"),
    ]

    def resolve_quantity(self, context):
        return format_value(context.allocation.quantity)

    def resolve_start_date(self, context):
        return format_value(context.allocation.start_date)

    def resolve_end_date(self, context):
        return format_value(context.allocation.end_date)

    def resolve_status(self, context):
        return format_value(context.allocation.status.name)

    def resolve_path(self, context):
        return format_value(context.allocation.path or None)

    def resolve_quota(self, context):
        unit_label = context.primary_resource.quantity_label
        if not unit_label:
            raise MissingValue("resource has no quantity_label")
        return format_value(context.allocation.get_attribute(f"Storage Quota ({unit_label})"))

    def resolve_usage(self, context):
        return format_value(context.allocation.usage)


class ResourceResolverGroup(BaseResolverGroup):
    scope = "allocation"
    group_label = "Resource"
    resolvers = [
        ("resource.name", "Resource · Name (primary)", "resolve_name"),
        ("resource.resource_type.name", "Resource · Type", "resolve_type"),
        ("resource.parent_resource.name", "Resource · Parent Name", "resolve_parent_name"),
        ("resource.names_joined", "Resource · All Names", "resolve_names_joined"),
    ]

    def resolve_name(self, context):
        return format_value(context.primary_resource.name)

    def resolve_type(self, context):
        resource = context.primary_resource
        if not resource.resource_type_id:
            raise MissingValue("resource has no type")
        return format_value(resource.resource_type.name)

    def resolve_parent_name(self, context):
        resource = context.primary_resource
        if not resource.parent_resource_id:
            raise MissingValue("resource has no parent resource")
        return format_value(resource.parent_resource.name)

    def resolve_names_joined(self, context):
        resource_names = ", ".join(
            resource.name for resource in context.allocation.resources.all()
        )
        return format_value(resource_names or None)


class ResolverRegistry:
    """Collects all resolver groups and provides the public lookup API.

    Builds three structures from the registered groups:
    - resolvers: key → callable
    - choices: [(key, label, group)] for admin dropdowns
    - scopes: key → "user" | "project" | "allocation"
    """

    def __init__(self, groups):
        self.resolvers = {}
        self.choices = []
        self.scopes = {}

        for group in groups:
            for key, label, group_label, scope, resolver_callable in group.get_registry_entries():
                self.resolvers[key] = resolver_callable
                self.choices.append((key, label, group_label))
                self.scopes[key] = scope

    def resolve(self, resolver_key: str, context: dict) -> str:
        """Dispatch a resolver_key against a context dict. Raises MissingValue on failure."""
        resolver_callable = self.resolvers.get(resolver_key)
        if resolver_callable is None:
            raise MissingValue(f"unknown resolver key: {resolver_key!r}")
        try:
            return resolver_callable(ResolverContext(context))
        except MissingValue:
            raise
        except Exception:
            raise MissingValue(f"failed to resolve {resolver_key!r}")


registry = ResolverRegistry([
    UserResolverGroup(),
    ProjectResolverGroup(),
    PIResolverGroup(),
    DepartmentResolverGroup(),
    RoleResolverGroup(),
    AllocationResolverGroup(),
    ResourceResolverGroup(),
])
