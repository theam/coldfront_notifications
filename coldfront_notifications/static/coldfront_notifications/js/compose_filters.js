// ── compose_filters.js ──────────────────────────────────────────
// Client-side state-driven cascading filter system.
//
// All filter data is loaded once on page load (FILTER_DATA from Django).
// Cross-filter narrowing happens entirely in the browser — zero AJAX
// round-trips.
//
// State model: each filter has {all, visible, selected, autoSelected}.
// - "all"          = full server dataset, never changes
// - "visible"      = subset of all after upstream (top-down) narrowing
// - "selected"     = user's manual picks
// - "autoSelected" = set by bottom-up propagation (e.g. role → departments)
//
// Two cascade directions:
// - Top-down (narrow): selecting a department narrows projects, resources,
//   allocations via the visible set.
// - Bottom-up (select): selecting a role auto-selects matching departments.
//   These auto-selections feed into the top-down cascade like manual ones.
//
// Globals used:    FILTER_DATA (from Django template)
// Globals exported: collectFilters, getDedupeUsers, setDedupeUsers

// ═══════════════════════════════════════════════════════════════════
// FILTER STORE — central state + event dispatch
// ═══════════════════════════════════════════════════════════════════

var FilterStore = {
  /** @type {Object.<string, {all: Array, visible: Array, selected: Array, autoSelected: Array}>} */
  state: {},

  /** @type {Object.<string, Array<FilterWidget>>} */
  listeners: {},

  register: function(widget, events) {
    for (var i = 0; i < events.length; i++) {
      var evt = events[i];
      if (!this.listeners[evt]) {
        this.listeners[evt] = [];
      }
      this.listeners[evt].push(widget);
    }
  },

  dispatch: function(eventName) {
    var chain = this.listeners[eventName] || [];
    for (var i = 0; i < chain.length; i++) {
      chain[i].narrow();
      chain[i].dispatchChanged();
    }
  },

  getSelected: function(name) {
    var s = this.state[name];
    return s ? s.selected : [];
  },

  /**
   * Return the combined manual + auto selections for a filter.
   */
  getEffective: function(name) {
    var s = this.state[name];
    if (!s) return [];
    var combined = s.selected.slice();
    for (var i = 0; i < s.autoSelected.length; i++) {
      if (combined.indexOf(s.autoSelected[i]) === -1) {
        combined.push(s.autoSelected[i]);
      }
    }
    return combined;
  },

  /**
   * Return effective IDs for constraining downstream filters.
   * Uses combined selections if any, otherwise visible (if narrowed), otherwise null.
   */
  getEffectiveIds: function(name) {
    var s = this.state[name];
    if (!s) return null;

    var effective = this.getEffective(name);
    if (effective.length) {
      return effective;
    }
    if (s.visible.length < s.all.length) {
      var ids = [];
      for (var i = 0; i < s.visible.length; i++) {
        ids.push(s.visible[i].id);
      }
      return ids;
    }
    return null;
  }
};


// ═══════════════════════════════════════════════════════════════════
// FILTER WIDGET — one instance per dropdown
// ═══════════════════════════════════════════════════════════════════

function FilterWidget(config) {
  this.name = config.name;
  this.selector = config.selector;
  this.emits = config.emits;
  this.narrowedBy = config.narrowedBy || [];
  this.narrowFn = config.narrowFn || null;
  this._isRendering = false;
}

FilterWidget.prototype.init = function(data) {
  FilterStore.state[this.name] = {
    all: data.options || [],
    visible: data.options || [],
    selected: [],
    autoSelected: [],
    autoSource: '',   // human-readable description of what triggered auto-select
    dismissed: []     // auto-selected IDs the user explicitly removed
  };

  FilterStore.register(this, this.narrowedBy);
  this.render();
  this._bindChange();
};

FilterWidget.prototype.narrow = function() {
  var state = FilterStore.state[this.name];
  if (!this.narrowFn) {
    state.visible = state.all;
    return;
  }

  var store = FilterStore;
  var visible = [];
  for (var i = 0; i < state.all.length; i++) {
    if (this.narrowFn(state.all[i], store)) {
      visible.push(state.all[i]);
    }
  }
  state.visible = visible;

  // Prune selected and autoSelected to only IDs still visible
  var visibleIds = _toLookup(visible.map(function(v) { return v.id; }));
  state.selected = state.selected.filter(function(id) {
    return visibleIds[id] === true;
  });
  state.autoSelected = state.autoSelected.filter(function(id) {
    return visibleIds[id] === true;
  });

  this.render();
};

FilterWidget.prototype.render = function() {
  this._isRendering = true;

  var state = FilterStore.state[this.name];
  var $sel = $(this.selector);
  var html = '';

  for (var i = 0; i < state.visible.length; i++) {
    var opt = state.visible[i];
    var escaped = $('<div>').text(opt.label).html();
    html += '<option value="' + opt.id + '">' + escaped + '</option>';
  }

  // Show combined manual + auto selections in the dropdown
  var effective = FilterStore.getEffective(this.name);
  $sel.html(html);
  $sel.val(effective.map(String));
  $sel.trigger('change.select2');

  this._isRendering = false;
};

FilterWidget.prototype.dispatchChanged = function() {
  if (this.emits) {
    FilterStore.dispatch(this.emits);
  }
};

FilterWidget.prototype._bindChange = function() {
  var self = this;
  $(document).on('change', this.selector, function() {
    if (self._isRendering) return;

    var raw = $(self.selector).val() || [];
    var state = FilterStore.state[self.name];
    var firstOpt = state.all.length ? state.all[0] : null;
    var useInt = firstOpt && typeof firstOpt.id === 'number';
    var newVals = useInt
      ? raw.map(function(v) { return parseInt(v, 10); })
      : raw;

    var autoLookup = _toLookup(state.autoSelected);
    var newLookup = _toLookup(newVals);

    // Track auto items the user explicitly removed
    for (var i = 0; i < state.autoSelected.length; i++) {
      var aid = state.autoSelected[i];
      if (!newLookup[aid] && state.dismissed.indexOf(aid) === -1) {
        state.dismissed.push(aid);
      }
    }

    // Manual = everything in newVals that isn't auto
    state.selected = newVals.filter(function(id) {
      return !autoLookup[id];
    });

    // Auto-selected gets pruned to only what's still in the dropdown
    state.autoSelected = state.autoSelected.filter(function(id) {
      return newLookup[id] === true;
    });

    _invalidateValidation();

    // Bottom-up propagation: if this is a bottom-up filter, propagate first
    _bottomUpPropagate();

    // Then top-down cascade
    self.dispatchChanged();

    _updateSummaries();
  });
};


// ═══════════════════════════════════════════════════════════════════
// BOTTOM-UP PROPAGATION — role → departments
// ═══════════════════════════════════════════════════════════════════

/**
 * Recalculate auto-selections from bottom-up filters.
 * Role selection → find matching project_ids → find departments
 * whose project_ids intersect → auto-select those departments.
 */
function _bottomUpPropagate() {
  var roleState = FilterStore.state.roles;
  var deptState = FilterStore.state.departments;
  if (!roleState || !deptState) return;

  // If the user has manual department selections, don't auto-select —
  // manual picks take priority
  if (deptState.selected.length) {
    deptState.autoSelected = [];
    deptState.autoSource = '';
    return;
  }

  var selectedRoles = FilterStore.getEffective('roles');

  if (!selectedRoles.length) {
    deptState.autoSelected = [];
    deptState.autoSource = '';
    deptState.dismissed = [];
    FILTERS.departments.render();
    return;
  }

  // Collect project_ids from selected roles
  var roleProjectIds = {};
  for (var i = 0; i < roleState.all.length; i++) {
    var role = roleState.all[i];
    if (selectedRoles.indexOf(role.id) === -1) continue;
    var pids = role.project_ids || [];
    for (var j = 0; j < pids.length; j++) {
      roleProjectIds[pids[j]] = true;
    }
  }

  // Find departments whose project_ids intersect with role's project_ids,
  // skipping any the user explicitly dismissed
  var dismissedLookup = _toLookup(deptState.dismissed);
  var autoSelectedDepts = [];
  for (var i = 0; i < deptState.all.length; i++) {
    var dept = deptState.all[i];
    if (dismissedLookup[dept.id]) continue;
    var deptPids = dept.project_ids || [];
    for (var j = 0; j < deptPids.length; j++) {
      if (roleProjectIds[deptPids[j]]) {
        autoSelectedDepts.push(dept.id);
        break;
      }
    }
  }

  // Clean up dismissed: remove any that are no longer candidates
  var candidateLookup = {};
  for (var i = 0; i < autoSelectedDepts.length; i++) {
    candidateLookup[autoSelectedDepts[i]] = true;
  }
  // Check dismissed items too
  for (var i = 0; i < deptState.dismissed.length; i++) {
    var did = deptState.dismissed[i];
    for (var j = 0; j < deptState.all.length; j++) {
      if (deptState.all[j].id !== did) continue;
      var pids = deptState.all[j].project_ids || [];
      for (var k = 0; k < pids.length; k++) {
        if (roleProjectIds[pids[k]]) {
          candidateLookup[did] = true;
          break;
        }
      }
      break;
    }
  }
  deptState.dismissed = deptState.dismissed.filter(function(id) {
    return candidateLookup[id] === true;
  });

  // Build human-readable source description
  var roleLabels = [];
  for (var i = 0; i < roleState.all.length; i++) {
    if (selectedRoles.indexOf(roleState.all[i].id) !== -1) {
      roleLabels.push(roleState.all[i].label);
    }
  }
  deptState.autoSource = roleLabels.length
    ? 'Auto-selected by User Role: ' + roleLabels.join(', ')
    : '';

  deptState.autoSelected = autoSelectedDepts;
  FILTERS.departments.render();

  // Trigger the top-down cascade from departments
  FILTERS.departments.dispatchChanged();
}


// ═══════════════════════════════════════════════════════════════════
// NARROWING FUNCTIONS — top-down, all state-driven
// ═══════════════════════════════════════════════════════════════════

function narrowProjects(row, store) {
  var effective = store.getEffective('departments');
  if (!effective.length) return true;

  var deptState = store.state.departments;
  for (var i = 0; i < deptState.all.length; i++) {
    var dept = deptState.all[i];
    if (effective.indexOf(dept.id) === -1) continue;
    if (dept.project_ids && dept.project_ids.indexOf(row.id) !== -1) {
      return true;
    }
  }
  return false;
}

function narrowResources(row, store) {
  var projectIds = store.getEffectiveIds('projects');
  if (!projectIds) return true;

  var lookup = _toLookup(projectIds);
  var pids = row.project_ids || [];
  for (var i = 0; i < pids.length; i++) {
    if (lookup[pids[i]]) return true;
  }
  return false;
}

function narrowAllocations(row, store) {
  var projectIds = store.getEffectiveIds('projects');
  if (projectIds && !_toLookup(projectIds)[row.project_id]) {
    return false;
  }

  var selResources = store.getSelected('resources');
  if (selResources.length) {
    var rids = row.resource_ids || [];
    var found = false;
    for (var i = 0; i < rids.length; i++) {
      if (selResources.indexOf(rids[i]) !== -1) {
        found = true;
        break;
      }
    }
    if (!found) return false;
  }

  var selStatuses = store.getSelected('statuses');
  if (selStatuses.length && selStatuses.indexOf(row.status) === -1) {
    return false;
  }

  return true;
}

function _toLookup(arr) {
  var m = {};
  for (var i = 0; i < arr.length; i++) m[arr[i]] = true;
  return m;
}


// ═══════════════════════════════════════════════════════════════════
// FILTER INSTANCES
// ═══════════════════════════════════════════════════════════════════

var FILTERS = {
  departments: new FilterWidget({
    name: 'departments',
    selector: '#f_dept',
    emits: 'DEPARTMENT_CHANGED',
    narrowedBy: [],
    narrowFn: null
  }),
  projects: new FilterWidget({
    name: 'projects',
    selector: '#f_project',
    emits: 'PROJECT_CHANGED',
    narrowedBy: ['DEPARTMENT_CHANGED'],
    narrowFn: narrowProjects
  }),
  resources: new FilterWidget({
    name: 'resources',
    selector: '#f_resource',
    emits: 'RESOURCE_CHANGED',
    narrowedBy: ['PROJECT_CHANGED'],
    narrowFn: narrowResources
  }),
  statuses: new FilterWidget({
    name: 'statuses',
    selector: '#f_status',
    emits: 'STATUS_CHANGED',
    narrowedBy: [],
    narrowFn: null
  }),
  allocations: new FilterWidget({
    name: 'allocations',
    selector: '#f_allocation',
    emits: 'ALLOCATION_CHANGED',
    narrowedBy: ['PROJECT_CHANGED', 'RESOURCE_CHANGED', 'STATUS_CHANGED'],
    narrowFn: narrowAllocations
  }),
  roles: new FilterWidget({
    name: 'roles',
    selector: '#f_role',
    emits: 'ROLE_CHANGED',
    narrowedBy: [],
    narrowFn: null
  })
};


// ═══════════════════════════════════════════════════════════════════
// SHARED HELPERS (consumed by validate, preview, submit)
// ═══════════════════════════════════════════════════════════════════

function collectFilters() {
  return {
    projects:    $('#f_project').val()    || [],
    allocations: $('#f_allocation').val() || [],
    resources:   $('#f_resource').val()   || [],
    departments: $('#f_dept').val()       || [],
    statuses:    $('#f_status').val()     || [],
    roles:       $('#f_role').val()       || []
  };
}

function getDedupeUsers() {
  try {
    var v = JSON.parse($('#h_dedupe_users').val() || '[]');
    return Array.isArray(v) ? v : [];
  } catch (e) {
    return [];
  }
}

function setDedupeUsers(list) {
  $('#h_dedupe_users').val(JSON.stringify(list || []));
}


// ═══════════════════════════════════════════════════════════════════
// VALIDATION INVALIDATION
// ═══════════════════════════════════════════════════════════════════

function _invalidateValidation() {
  $('#recipNum').text('?');
  $('#recipLbl').text('press Validate');
  $('#sendBtn').prop('disabled', true);
  $('#previewRecipBtn').prop('disabled', true);
  $('#sendWarn').hide();
  setDedupeUsers([]);
  // Reset validate button and top result if they exist
  if (typeof _setValidateButtonState === 'function') _setValidateButtonState('default');
  if (typeof _hideTopValidationResult === 'function') _hideTopValidationResult();
}


// ═══════════════════════════════════════════════════════════════════
// FILTER DETAILS — inline breakdown cards
// ═══════════════════════════════════════════════════════════════════

var FILTER_LABELS = {
  departments: 'Departments',
  projects: 'Projects',
  resources: 'Resources',
  statuses: 'Alloc Status',
  allocations: 'Allocations',
  roles: 'Roles'
};

var NARROWED_BY_LABELS = {
  projects: ['Department'],
  resources: ['Project'],
  allocations: ['Project', 'Resource', 'Alloc Status']
};

function _updateSummaries() {
  _renderFilterDetails();
  _updateClearButton();
}

function _getBreakdown(name, state) {
  var selected = state.selected;
  var visible = state.visible;

  if (name === 'allocations') {
    var byStatus = {};
    for (var i = 0; i < visible.length; i++) {
      var s = visible[i].status || 'Unknown';
      byStatus[s] = (byStatus[s] || 0) + 1;
    }
    var parts = [];
    for (var key in byStatus) {
      parts.push(byStatus[key] + ' ' + key);
    }
    return parts.join(', ');
  }

  var effective = FilterStore.getEffective(name);
  if (effective.length && effective.length <= 5) {
    var labels = [];
    var effectiveLookup = _toLookup(effective);
    for (var i = 0; i < state.all.length; i++) {
      if (effectiveLookup[state.all[i].id]) {
        labels.push(state.all[i].label);
      }
    }
    return labels.join(', ');
  }

  return '';
}

function _getFilteredByText(name) {
  var sources = NARROWED_BY_LABELS[name];
  if (!sources) return '';

  var active = [];
  if (sources.indexOf('Department') !== -1 && FilterStore.getEffective('departments').length) {
    active.push('Department');
  }
  if (sources.indexOf('Project') !== -1) {
    var effProj = FilterStore.getEffectiveIds('projects');
    if (effProj) active.push('Project');
  }
  if (sources.indexOf('Resource') !== -1 && FilterStore.getSelected('resources').length) {
    active.push('Resource');
  }
  if (sources.indexOf('Alloc Status') !== -1 && FilterStore.getSelected('statuses').length) {
    active.push('Alloc Status');
  }

  return active.length ? 'Filtered by: ' + active.join(', ') : '';
}

function _renderFilterDetails() {
  var $container = $('#filterDetails').empty();
  var keys = ['departments', 'projects', 'resources', 'statuses', 'allocations', 'roles'];

  for (var i = 0; i < keys.length; i++) {
    var k = keys[i];
    var state = FilterStore.state[k];
    if (!state) continue;

    var label = FILTER_LABELS[k] || k;
    var isNarrowed = state.visible.length < state.all.length;
    var effective = FilterStore.getEffective(k);
    var hasSelections = effective.length > 0;

    if (!isNarrowed && !hasSelections) continue;

    var countText = '<span class="count">' + state.visible.length + '</span>';
    if (isNarrowed) {
      countText += ' <span style="color:#888;">(of ' + state.all.length + ')</span>';
    }
    if (hasSelections) {
      var selLabel = state.selected.length + ' selected';
      if (state.autoSelected.length) {
        selLabel += ', ' + state.autoSelected.length + ' auto';
      }
      countText += ' · <span style="color:#0c5460;">' + selLabel + '</span>';
    }

    var breakdown = _getBreakdown(k, state);
    var filteredBy = _getFilteredByText(k);
    var autoSource = state.autoSource || '';

    var html = '<div class="recip-detail-card' + (isNarrowed ? ' narrowed' : '') + '">';
    html += '<div class="recip-detail-title">' + label + ': ' + countText + '</div>';
    if (breakdown) {
      html += '<div class="recip-detail-breakdown">' + breakdown + '</div>';
    }
    if (autoSource) {
      html += '<div class="recip-detail-filtered-by">' + autoSource + '</div>';
    }
    if (filteredBy) {
      html += '<div class="recip-detail-filtered-by">' + filteredBy + '</div>';
    }
    html += '</div>';

    $container.append(html);
  }
}

function updateFilterSummaries() {
  _updateSummaries();
}

function clearAllFilters() {
  var keys = ['departments', 'projects', 'resources', 'statuses', 'allocations', 'roles'];
  for (var i = 0; i < keys.length; i++) {
    var state = FilterStore.state[keys[i]];
    if (state) {
      state.selected = [];
      state.autoSelected = [];
      state.autoSource = '';
      state.dismissed = [];
      state.visible = state.all;
    }
    if (FILTERS[keys[i]]) {
      FILTERS[keys[i]].render();
    }
  }
  _invalidateValidation();
  _updateSummaries();
}

function _updateClearButton() {
  var keys = ['departments', 'projects', 'resources', 'statuses', 'allocations', 'roles'];
  var hasAny = false;
  for (var i = 0; i < keys.length; i++) {
    var state = FilterStore.state[keys[i]];
    if (state && (state.selected.length || state.autoSelected.length)) {
      hasAny = true;
      break;
    }
  }
  $('#clearFiltersBtn').toggle(hasAny);
}

$(document).on('click', '#clearFiltersBtn', clearAllFilters);


// ═══════════════════════════════════════════════════════════════════
// INITIALIZATION
// ═══════════════════════════════════════════════════════════════════

$(document).ready(function() {
  if (typeof FILTER_DATA === 'undefined') return;

  var keys = ['departments', 'projects', 'resources', 'statuses', 'allocations', 'roles'];
  for (var i = 0; i < keys.length; i++) {
    var k = keys[i];
    if (FILTERS[k] && FILTER_DATA[k]) {
      FILTERS[k].init(FILTER_DATA[k]);
    }
  }

  _updateSummaries();
});
