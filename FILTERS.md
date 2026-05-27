# Compose Notification — Filter System

## Overview

The compose page has 6 recipient filters. All filter data is loaded once on
page load via `FilterDataBuilder`. The frontend `FilterStore` handles cascade
narrowing entirely client-side — zero AJAX round-trips for filter changes.

Two cascade directions:
- **Top-down (narrow):** selecting a department narrows projects, resources,
  and allocations via the visible set.
- **Bottom-up (select):** selecting a role auto-selects matching departments.
  Manual selections take priority over auto-selections.

## Filters

| Filter            | DOM ID          | Value type       | Class                |
|-------------------|-----------------|------------------|----------------------|
| Department        | `#f_dept`       | Dept PKs (int)   | `DepartmentFilter`   |
| Project           | `#f_project`    | Project PKs      | `ProjectFilter`      |
| Resource          | `#f_resource`   | Resource PKs     | `ResourceFilter`     |
| Allocation Status | `#f_status`     | Status names     | `StatusFilter`       |
| Allocation        | `#f_allocation` | Allocation PKs   | `AllocationFilter`   |
| User Role         | `#f_role`       | Role names       | `RoleFilter`         |

## Database Relationships

```
Department (Organization, org_tree='Research Computing Storage Billing')
  └── Lab (OrgRelation, child__rank='lab')
       └── Project (ProjectOrganization)
            ├── Allocation
            │    ├── Resource   (M2M via Allocation.resources)
            │    └── Status     (FK to AllocationStatusChoice)
            └── ProjectUser
                 ├── User
                 └── Role       (FK to ProjectUserRoleChoice)
```

Key: Department → Project traversal goes through OrgRelation and
ProjectOrganization (not a direct FK). The `DepartmentFilter._apply()` method
and `DepartmentFilter.initial_options()` both use this join chain.

## Filter Cascade

```
Top-down narrowing:

  Department → Projects → Resources
                       → Allocations ← Statuses
                                     ← Resources

Bottom-up selection:

  Role → auto-selects Departments (if no manual dept selections)
```

## State Model

Each filter in the `FilterStore` has:
- `all` — full server dataset, never changes after page load
- `visible` — subset of `all` after upstream narrowing
- `selected` — user's manual picks (subset of visible)
- `autoSelected` — set by bottom-up propagation (role → departments)
- `dismissed` — auto-selected items the user explicitly removed
- `autoSource` — human-readable description of what triggered auto-select

Downstream filters read `getEffectiveIds()` which returns:
1. `selected` if any exist, else
2. `visible` IDs if narrowed from `all`, else
3. `null` (no constraint)

## Backend Architecture

All filter logic lives in `filters.py`:

- **`BaseFilter`** — abstract class with `initial_options()` and `_apply()`
- **6 filter classes** — each owns both its UI options and queryset filtering
- **`FilterDataBuilder`** — assembles all filter data into JSON for page load
- **`RecipientResolver`** — resolves selected filters into actual recipients
  at send time

## Frontend Architecture

JS modules loaded in order:

1. **`compose_filters.js`** — FilterStore, FilterWidget, narrowing functions,
   bottom-up propagation, filter details cards, clear filters
2. **`compose_validate.js`** — validation AJAX, send-bar UI
3. **`compose_templates.js`** — template list/search/load, variable chips
4. **`compose_preview.js`** — email preview and recipient preview modals
5. **`compose_draft.js`** — draft auto-save, resume, dirty tracking
6. **`compose_init.js`** — Select2 init, form submit serialization (loads last)

## Filter Data Format

Each filter's options from `FilterDataBuilder.build()`:

```json
{
  "departments": {
    "options": [
      {"id": 1880, "label": "Chemistry and Chemical Biology", "project_ids": [55, 94, ...]}
    ],
    "narrowed_by": []
  },
  "projects": {
    "options": [{"id": 1, "label": "alpha_lab"}],
    "narrowed_by": ["departments"]
  },
  "resources": {
    "options": [{"id": 38, "label": "FASRC Cluster", "project_ids": [2, 3, ...]}],
    "narrowed_by": ["projects"]
  },
  "statuses": {
    "options": [{"id": "Active", "label": "Active"}],
    "narrowed_by": []
  },
  "allocations": {
    "options": [{"id": 100, "label": "alpha_lab — Storage", "project_id": 1, "status": "Active", "resource_ids": [38]}],
    "narrowed_by": ["projects", "resources", "statuses"]
  },
  "roles": {
    "options": [{"id": "PI", "label": "PI", "project_ids": [1, 2, ...]}],
    "narrowed_by": []
  }
}
```
