# Functional Test Protocol — Compose Filters

## Data Model

```
Department (18 available)
  └── Labs (via OrgRelation, child rank='lab')
       └── Projects (via ProjectOrganization) — 1,121 total
            ├── Allocations — 2,574 total
            │    ├── Resources — 1,912 total (M2M)
            │    └── Status (Active, Pending Deactivation, Denied, etc.)
            └── ProjectUser
                 ├── User — 5,539 active
                 └── Role (PI, General Manager, User, Storage Manager, Access Manager)
```

Selecting a department narrows projects to only those connected via
the department's labs. Resources, allocations, and statuses narrow
based on visible projects. Roles can auto-select departments bottom-up.

---

## Test Scenarios

### Scenario 1 — Chemistry + General Manager (Sarah's use case)

> "I want to email all the General Managers that belong to the Chemistry
> Department, to let them know about an upcoming change to their software."

**Data chain:**
- Department: Chemistry and Chemical Biology (pk=1880)
- Labs: 7 (aspuru_lab, kahne_lab, liau_lab, lieber_lab, shair_lab, gordon_lab, zheng_lab)
- Projects: 7
- General Managers across those projects: 6

**Steps:**
1. Open Compose Notification
2. Select **Department** → "Chemistry and Chemical Biology"
3. Verify: Projects dropdown narrows to **7 projects**
4. Verify: Resources and Allocations narrow accordingly
5. Verify: Detail card shows "Departments: 7 (of 1200) · 1 selected"
6. Select **User Role** → "General Manager"
7. Verify: Detail card shows role selected
8. Click **Validate**
9. Verify: **5 users** reported, button turns green

**Expected result:** 5 recipients

**Evidence:** [ ] Screenshot / [ ] Video

---

### Scenario 2 — bos-isilon/tier1 + PI (Sarah's use case)

> "I need to let all the PIs that have data on bos-isilon/tier1 resource
> that the storage will be down for an upgrade."

**Data chain:**
- Resource: bos-isilon/tier1 — linked to 113 projects
- PIs across those projects: 110 (project scope)
- PIs with AllocationUser records on those allocations: 88 (allocation scope)

**Steps:**
1. Open Compose Notification
2. Select **Resource** → "bos-isilon/tier1"
3. Verify: Allocations narrow to only bos-isilon allocations
4. Select **User Role** → "PI"
5. Verify: Departments do NOT auto-select (resource already picked)
6. Click **Validate**
7. Verify result depends on template:
   - **No template / project-scoped template:** 110 users
   - **Allocation-scoped template** (uses `{{allocation_path}}`, etc.): **88 users → 109 emails**

**Expected result:** 110 users (project scope) or 88 users / 109 emails (allocation scope)

**Note:** 22 PIs are on bos-isilon projects but don't have AllocationUser
records, so they disappear when the template requires allocation-level data.

**Evidence:** [ ] Screenshot / [ ] Video

---

### Scenario 3 — Economics + Pending Deactivation (Sarah's use case)

> "I'm doing some data cleanup work with the Economics department and I
> need to know what folders are pending deactivation."

**Data chain:**
- Department: Economics (pk=1883)
- Projects: 13
- Pending Deactivation allocations: 3 (manually set for testing)
- Users on those allocations: 2

**Steps:**
1. Open Compose Notification
2. Select **Department** → "Economics"
3. Verify: Projects narrow to **13 projects**
4. Select **Allocation Status** → "Pending Deactivation"
5. Verify: Allocations narrow to only Pending Deactivation
6. Verify: Detail card shows "Filtered by: Project, Alloc Status"
7. Click **Validate**
8. Verify: **2 users** reported

**Expected result:** 2 recipients

**Evidence:** [ ] Screenshot / [ ] Video

---

### Scenario 4 — No filters (all active users)

**Data chain:**
- All active ProjectUser records → all distinct users

**Steps:**
1. Open Compose Notification
2. Do NOT select any filters
3. Verify: All dropdowns show full counts (18 depts, 1200 projects, etc.)
4. Verify: No detail cards shown (nothing active)
5. Click **Validate**
6. Verify: **5,539 users** reported

**Expected result:** 5,539 recipients

**Evidence:** [ ] Screenshot / [ ] Video

---

### Scenario 5 — Single department only (Astronomy)

**Data chain:**
- Department: Astronomy (pk=1878)
- Labs: 9 (Alyssa Goodman, Charlie Conroy, Doug Finkbeiner, etc.)
- Projects: 9

**Steps:**
1. Open Compose Notification
2. Select **Department** → "Astronomy"
3. Verify: Projects narrow to **9 projects**
4. Verify: Resources narrow to only those linked to Astronomy allocations
5. Verify: Detail card shows "Departments: 9 (of 1200) · 1 selected"
6. Verify: Detail card shows "Filtered by: Department" on projects
7. Click **Validate**
8. Verify: **147 users** reported

**Expected result:** 147 recipients

**Evidence:** [ ] Screenshot / [ ] Video

---

### Scenario 6 — Bottom-up: Role auto-selects departments

**Data chain:**
- Role: Storage Manager — present in 57 projects across multiple departments

**Steps:**
1. Open Compose Notification
2. Select **User Role** → "Storage Manager"
3. Verify: Departments are **auto-selected** (blue pills appear in dropdown)
4. Verify: Detail card shows "Auto-selected by User Role: Storage Manager"
5. Verify: Projects narrow based on the auto-selected departments
6. Click **Validate**
7. Verify: **66 users** reported
8. **Deselect** the role
9. Verify: Auto-selected departments clear
10. Verify: All filters reset to full counts

**Expected result:** 66 recipients; auto-selection clears on role deselect

**Evidence:** [ ] Screenshot / [ ] Video

---

### Scenario 7 — Resource + Status combo (active isilon users)

**Data chain:**
- Resource: bos-isilon/tier1 → 113 projects
- Status: Active → intersection with isilon allocations

**Steps:**
1. Open Compose Notification
2. Select **Resource** → "bos-isilon/tier1"
3. Verify: Allocations narrow
4. Select **Allocation Status** → "Active"
5. Verify: Allocations narrow further (only Active + isilon)
6. Verify: Detail card shows allocation breakdown with Active count
7. Click **Validate**
8. Verify: **548 users** reported

**Expected result:** 548 recipients

**Evidence:** [ ] Screenshot / [ ] Video

---

### Scenario 8 — Multiple departments

**Data chain:**
- Chemistry (7 projects) + Earth and Planetary Sciences (7 projects)
- Combined: 14 projects (no overlap)

**Steps:**
1. Open Compose Notification
2. Select **Department** → "Chemistry and Chemical Biology"
3. Verify: Projects narrow to 7
4. Add **Department** → "Earth and Planetary Sciences"
5. Verify: Projects expand to **14** (union of both)
6. Verify: Resources and Allocations reflect the combined scope
7. Click **Validate**
8. Verify: **145 users** reported
9. **Remove** "Earth and Planetary Sciences"
10. Verify: Projects narrow back to 7

**Expected result:** 145 recipients with both; narrowing works on remove

**Evidence:** [ ] Screenshot / [ ] Video

---

### Scenario 9 — FASRC Cluster + PI (cluster notification)

**Data chain:**
- Resource: FASRC Cluster — linked to most projects
- PIs on FASRC Cluster: 127

**Steps:**
1. Open Compose Notification
2. Select **Resource** → "FASRC Cluster"
3. Select **User Role** → "PI"
4. Verify: Role triggers bottom-up auto-select on departments
5. Verify: But since resource was selected first, departments should NOT auto-select (manual resource selection takes priority in the cascade)
6. Click **Validate**
7. Verify: **127 users** reported

**Expected result:** 127 recipients

**Evidence:** [ ] Screenshot / [ ] Video

---

### Scenario 10 — Three-filter combo (SEAS + Active + PI)

**Data chain:**
- Department: School of Engineering and Applied Sciences (pk=1956) → 80 projects
- Status: Active → narrows allocations
- Role: PI → narrows to PIs only
- Intersection: 54 users

**Steps:**
1. Open Compose Notification
2. Select **Department** → "School of Engineering and Applied Sciences"
3. Verify: Projects narrow to **80**
4. Select **Allocation Status** → "Active"
5. Verify: Allocations narrow to Active only within SEAS
6. Select **User Role** → "PI"
7. Verify: No bottom-up auto-select (department already manually selected)
8. Verify: Detail cards show all three filters active
9. Click **Validate**
10. Verify: **54 users** reported

**Expected result:** 54 recipients

**Evidence:** [ ] Screenshot / [ ] Video

---

## Cascade Verification Checklist

For each scenario, verify the following cascade behavior:

| Check | How to verify |
|---|---|
| Top-down narrowing | Select a department → projects dropdown count decreases |
| Resource narrowing from projects | Select a department → resources dropdown count decreases |
| Allocation narrowing | Select department + status → allocations show fewer options |
| Bottom-up auto-select | Select a role with no dept selected → departments auto-populate |
| Manual overrides auto | Select a dept first, then a role → dept stays, no auto-select |
| Clear Filters | Click "Clear Filters" → all dropdowns reset to full counts |
| Detail cards update | Every filter change → detail cards reflect current state |
| Validate button state | Green on success, red on error, gray on change |
| Validation at top | Error/success message appears under filters AND at send bar |
| Draft save | Click Save Draft → appears in campaign list with Draft badge |

---

## Test Environment

- **URL:** http://127.0.0.1:2444/notifications/compose/
- **Login:** antonio / antonio (superuser)
- **Database:** MySQL in Docker (mysql-custom), database cf-contractor
- **Data:** cf_contractor_dump.sql + test modifications (Pending Deactivation on Economics, extra General Managers on Chemistry)
- **Email:** MailHog at http://127.0.0.1:8025 (catches all sent emails)
