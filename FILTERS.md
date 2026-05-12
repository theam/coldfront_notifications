# Compose Notification — Filter System

## Overview

The compose notification page has 6 recipient filters that are interdependent.
Every filter change sends a single AJAX request to the backend with the current
state of all selections. The backend computes the full option list for every
filter in one pass and returns the complete state — the frontend blindly applies
it with no local cascade logic.

Each filter's options are computed considering all **other** active selections
(no self-narrowing). This prevents circular pruning while keeping all filters
consistent with each other.

## Filters

| Filter            | DOM ID          | Value type         | DB Source                              |
|-------------------|-----------------|--------------------|----------------------------------------|
| Project           | `#f_project`    | Project PKs        | `coldfront.core.project.Project`       |
| Allocation        | `#f_allocation` | Allocation PKs     | `coldfront.core.allocation.Allocation` |
| Department        | `#f_dept`       | Dept names (str)   | `ifxuser.Organization` (rank=dept)     |
| Resource          | `#f_resource`   | Resource PKs       | `coldfront.core.resource.Resource`     |
| Allocation Status | `#f_status`     | Status names (str) | `AllocationStatusChoice`               |
| User Role         | `#f_role`       | Role names (str)   | `ProjectUserRoleChoice`                |

## Database Relationships

```
Project  <──M2M──>  Department/Organization
   │                  (via ProjectOrganization bridge table,
   │                   FKs to both Project and Organization)
   │
   ├── FK ── Allocation
   │            ├── M2M ── Resource   (Allocation.resources ManyToManyField)
   │            └── FK  ── AllocationStatusChoice  (Allocation.status)
   │
   └── FK ── ProjectUser
                ├── FK ── User
                └── FK ── ProjectUserRoleChoice  (ProjectUser.role)
```

Key details:
- **Allocation → Resource is M2M** (one allocation can reference multiple resources).
- **Project ↔ Department** is M2M via `ProjectOrganization` (in `coldfront.plugins.ifx.models`).
- **Role** is reached through `ProjectUser`, which has `unique_together = ('user', 'project')`.
- **Department** is actually an `Organization` with `rank="department"` (proxy model).

## Filter Tiers

Filters are organized into tiers that determine narrowing direction:

```
Tier 1 (top):    Project  <-->  Department     (mutual peers)
Tier 2 (mid):    Allocation  ·  Resource  ·  Status   (peers under Tier 1)
Tier 3 (leaf):   Role
```

- **Between tiers**: top-down only (Tier 1 narrows Tier 2, never the reverse).
- **Within Tier 1**: mutual peers — each narrows the other.
- **Within Tier 2**: each filter's options are scoped by the other Tier 2
  selections, with one exception: **Status is independent of Allocation
  selection** to prevent circular pruning (selecting an allocation would drop
  statuses, which would cascade-remove other allocations).
- **Tier 3**: Role is scoped by the Tier 1 project scope but does not narrow
  any other filter.

## How Each Filter's Options Are Computed

All filters are always computed on every request, regardless of which filter
changed. The event name is used only for request validation.

### Tier 1

| Filter     | Scoped by              | Excludes own selection? |
|------------|------------------------|-------------------------|
| Project    | `sel_departments`      | Yes (not `sel_projects`) |
| Department | `sel_projects`         | Yes (not `sel_departments`) |

### Full Project Scope (for Tier 2)

Both `sel_projects` and `sel_departments` are applied to create the project
scope used by all Tier 2 filters.

### Tier 2

All Tier 2 filters start from `Allocation.filter(project__in=project_scope)`.

| Filter            | Additional scoping                          | Excludes                    |
|-------------------|---------------------------------------------|-----------------------------|
| Allocation        | `sel_statuses` + `sel_resources`            | `sel_allocations`           |
| Resource          | `sel_statuses` + `sel_allocations`          | `sel_resources`             |
| Allocation Status | `sel_resources` only                        | `sel_allocations` + `sel_statuses` |

**Why Status excludes `sel_allocations`**: if the user selects statuses
"Active" + "Denied" then picks an Active allocation, computing statuses from
that allocation alone would drop "Denied" — removing the other allocations the
user hasn't selected yet.

### Tier 3

| Filter | Scoped by      |
|--------|----------------|
| Role   | `project_scope` |

## Protocol

### Request

```
POST /filter-options/
Content-Type: application/json
X-CSRFToken: <token>

{
  "event": "ALLOCATION_STATUS_UPDATED",
  "selections": {
    "projects":    [],
    "allocations": [],
    "departments": [],
    "resources":   [],
    "statuses":    ["Active", "Denied"],
    "roles":       []
  }
}
```

Valid events: `PROJECT_UPDATED`, `DEPARTMENT_UPDATED`, `ALLOCATION_UPDATED`,
`RESOURCE_UPDATED`, `ALLOCATION_STATUS_UPDATED`, `ROLE_UPDATED`.

### Response

```json
{
  "projects": {
    "options":  [{"id": 1, "label": "Project Alpha"}, ...],
    "selected": [1],
    "summary":  {"count": 11, "breakdown": "11 Active", "filtered_by": ""}
  },
  "allocations": { ... },
  "departments": { ... },
  "resources":   { ... },
  "statuses":    { ... },
  "roles":       { ... }
}
```

- `options` — full list of valid choices for the dropdown.
- `selected` — the user's prior selections pruned to only values still in `options`.
- `summary` — structured data for the filter info tooltip and details modal.
  - `count` — number of options available.
  - `breakdown` — grouped description (e.g., "7 Active, 1 Expired").
  - `filtered_by` — which other filters narrowed this one. Empty string if the
    filter shows its full unfiltered set.

## Frontend Architecture

The compose page JS is split into 5 modules loaded in order:

1. **`compose_filters.js`** — filter cascade, `collectFilters()`,
   `getDedupeUsers()`, `setDedupeUsers()`, summary tooltips, inline summary
   chips, filter details modal, filter locking during AJAX.
2. **`compose_validate.js`** — validation AJAX, send-bar UI, input
   invalidation, filter summary bar in validation panel.
3. **`compose_templates.js`** — template list/search/load, variable chip
   rendering, variable insert at cursor.
4. **`compose_preview.js`** — email preview modal, recipient preview modal
   (with dedupe and pagination).
5. **`compose_init.js`** — Select2 init, form submit serialization, pageshow
   reset. Must load last.

## Backend Architecture

Filter logic lives in `views/filters.py`:

- `build_filter_summaries()` — generates `{count, breakdown, filtered_by}`
  dicts for each filter, grouped by meaningful attributes (projects by status,
  allocations by status, resources by type, etc.).
- `get_filter_context()` — initial filter data for the compose page template
  (options + summaries for all 6 filters, senders, reply-tos).
- `compute_filter_options(selections)` — the core cross-filter computation.
  Called by the `filter_options` view. Returns the full response dict ready for
  `JsonResponse`.
