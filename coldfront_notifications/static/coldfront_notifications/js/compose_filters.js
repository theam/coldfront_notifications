// compose_filters.js — Client-side state-driven cascading filter system.
//
// All filter data is loaded once on page load (FILTER_DATA from Django).
// Cross-filter narrowing happens entirely in the browser — zero AJAX.
//
// State model: each filter has {all, visible, selected}.
// - "all"      = full server dataset, never changes
// - "visible"  = subset of all after upstream (top-down) narrowing
// - "selected" = user's manual picks
//
// Cascade directions:
// - Top-down (narrow): department → projects → resources → allocations.
//   Resource also narrows projects (bidirectional with cycle guard).
// - Bottom-up (inform): selecting a child filter shows context in the
//   detail cards (e.g. which departments/projects an allocation belongs to)
//   without modifying upstream dropdowns (except resource → projects)
//
// Globals used:    FILTER_DATA (from Django template)
// Globals exported: collectFilters, getDedupeUsers, setDedupeUsers

var FilterStore = {
  state: {},
  listeners: {},
  _dispatching: {},

  register: function(widget, events) {
    for (var i = 0; i < events.length; i++) {
      var evt = events[i];
      if (!this.listeners[evt]) this.listeners[evt] = [];
      this.listeners[evt].push(widget);
    }
  },

  dispatch: function(eventName) {
    if (this._dispatching[eventName]) return;
    this._dispatching[eventName] = true;
    try {
      var chain = this.listeners[eventName] || [];
      for (var i = 0; i < chain.length; i++) {
        chain[i].narrow();
        chain[i].dispatchChanged();
      }
    } finally {
      this._dispatching[eventName] = false;
    }
  },

  getSelected: function(name) {
    var s = this.state[name];
    return s ? s.selected : [];
  },

  getEffectiveIds: function(name) {
    var s = this.state[name];
    if (!s) return null;
    if (s.selected.length) return s.selected;
    if (s.visible.length < s.all.length) {
      return s.visible.map(function(v) { return v.id; });
    }
    return null;
  }
};


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
    selected: []
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

  var visible = [];
  for (var i = 0; i < state.all.length; i++) {
    if (this.narrowFn(state.all[i], FilterStore)) {
      visible.push(state.all[i]);
    }
  }
  state.visible = visible;

  var visibleIds = _toLookup(visible.map(function(v) { return v.id; }));
  state.selected = state.selected.filter(function(id) {
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

  $sel.html(html);
  $sel.val(state.selected.map(String));
  $sel.trigger('change.select2');
  this._isRendering = false;
};

FilterWidget.prototype.dispatchChanged = function() {
  if (this.emits) FilterStore.dispatch(this.emits);
};

FilterWidget.prototype._bindChange = function() {
  var self = this;
  $(document).on('change', this.selector, function() {
    if (self._isRendering) return;

    var raw = $(self.selector).val() || [];
    var state = FilterStore.state[self.name];
    var firstOpt = state.all.length ? state.all[0] : null;
    var useInt = firstOpt && typeof firstOpt.id === 'number';
    state.selected = useInt
      ? raw.map(function(v) { return parseInt(v, 10); })
      : raw;

    _invalidateValidation();
    self.dispatchChanged();
    _updateSummaries();
  });
};


// Top-down narrowing functions

function narrowProjects(row, store) {
  var passedDepartment = true;
  var passedResource = true;

  // Department filter: check if this project belongs to a selected department
  var selectedDepts = store.getSelected('departments');
  if (selectedDepts.length) {
    passedDepartment = false;
    var deptState = store.state.departments;
    for (var i = 0; i < deptState.all.length; i++) {
      var dept = deptState.all[i];
      if (selectedDepts.indexOf(dept.id) === -1) continue;
      if (dept.project_ids && dept.project_ids.indexOf(row.id) !== -1) {
        passedDepartment = true;
        break;
      }
    }
  }

  // Resource filter: check if this project has at least one selected resource
  var selectedResources = store.getSelected('resources');
  if (selectedResources.length) {
    passedResource = false;
    var resState = store.state.resources;
    for (var i = 0; i < resState.all.length; i++) {
      var resource = resState.all[i];
      if (selectedResources.indexOf(resource.id) === -1) continue;
      if (resource.project_ids && resource.project_ids.indexOf(row.id) !== -1) {
        passedResource = true;
        break;
      }
    }
  }

  return passedDepartment && passedResource;
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
  if (projectIds && !_toLookup(projectIds)[row.project_id]) return false;

  var selResources = store.getSelected('resources');
  if (selResources.length) {
    var rids = row.resource_ids || [];
    var found = false;
    for (var i = 0; i < rids.length; i++) {
      if (selResources.indexOf(rids[i]) !== -1) { found = true; break; }
    }
    if (!found) return false;
  }

  var selStatuses = store.getSelected('statuses');
  if (selStatuses.length && selStatuses.indexOf(row.status) === -1) return false;

  return true;
}

function _toLookup(arr) {
  var m = {};
  for (var i = 0; i < arr.length; i++) m[arr[i]] = true;
  return m;
}


// Filter instances

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
    narrowedBy: ['DEPARTMENT_CHANGED', 'RESOURCE_CHANGED'],
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


// Shared helpers

function collectFilters() {
  var filters = {};
  var keys = ['departments', 'projects', 'resources', 'statuses', 'allocations', 'roles'];
  for (var i = 0; i < keys.length; i++) {
    var key = keys[i];
    var state = FilterStore.state[key];
    filters[key] = (state && state.selected.length) ? state.selected.map(String) : [];
  }
  return filters;
}

function getDedupeUsers() {
  try {
    var v = JSON.parse($('#h_dedupe_users').val() || '[]');
    return Array.isArray(v) ? v : [];
  } catch (e) { return []; }
}

function setDedupeUsers(list) {
  $('#h_dedupe_users').val(JSON.stringify(list || []));
}


function _invalidateValidation() {
  $('#recipNum').text('?');
  $('#recipLbl').text('press Validate');
  $('#sendBtn').prop('disabled', true);
  $('#previewRecipBtn').prop('disabled', true);
  $('#sendWarn').hide();
  setDedupeUsers([]);
  if (typeof _setValidateButtonState === 'function') _setValidateButtonState('default');
  if (typeof _hideTopValidationResult === 'function') _hideTopValidationResult();
}


// Filter details — inline breakdown cards with bottom-up context

var FILTER_LABELS = {
  departments: 'Departments',
  projects: 'Projects',
  resources: 'Resources',
  statuses: 'Alloc Status',
  allocations: 'Allocations',
  roles: 'Roles'
};

function _updateSummaries() {
  _renderFilterDetails();
  _updateClearButton();
}

function _getBreakdown(name, state) {
  if (name === 'allocations') {
    var byStatus = {};
    for (var i = 0; i < state.visible.length; i++) {
      var s = state.visible[i].status || 'Unknown';
      byStatus[s] = (byStatus[s] || 0) + 1;
    }
    var parts = [];
    for (var key in byStatus) parts.push(byStatus[key] + ' ' + key);
    return parts.join(', ');
  }

  if (state.selected.length && state.selected.length <= 5) {
    var labels = [];
    var selLookup = _toLookup(state.selected);
    for (var i = 0; i < state.all.length; i++) {
      if (selLookup[state.all[i].id]) labels.push(state.all[i].label);
    }
    return labels.join(', ');
  }

  return '';
}

function _getTopDownInfo(name) {
  var sources = {
    projects: ['Department', 'Resource'],
    resources: ['Project'],
    allocations: ['Project', 'Resource', 'Alloc Status']
  };
  var deps = sources[name];
  if (!deps) return '';

  var active = [];
  if (deps.indexOf('Department') !== -1 && FilterStore.getSelected('departments').length) {
    active.push('Department');
  }
  if (deps.indexOf('Project') !== -1 && FilterStore.getEffectiveIds('projects')) {
    active.push('Project');
  }
  if (deps.indexOf('Resource') !== -1 && FilterStore.getSelected('resources').length) {
    active.push('Resource');
  }
  if (deps.indexOf('Alloc Status') !== -1 && FilterStore.getSelected('statuses').length) {
    active.push('Alloc Status');
  }
  return active.length ? 'Filtered by: ' + active.join(', ') : '';
}

function _getBottomUpContext(name, state) {
  if (!state.selected.length) return '';

  if (name === 'allocations') {
    var deptNames = {};
    var projectNames = {};
    var resourceNames = {};
    var statuses = {};
    var deptState = FilterStore.state.departments;
    var projState = FilterStore.state.projects;
    var resState = FilterStore.state.resources;

    // Build resource ID → label lookup
    var resourceLabelById = {};
    if (resState) {
      for (var r = 0; r < resState.all.length; r++) {
        resourceLabelById[resState.all[r].id] = resState.all[r].label;
      }
    }

    for (var i = 0; i < state.selected.length; i++) {
      var alloc = null;
      for (var j = 0; j < state.all.length; j++) {
        if (state.all[j].id === state.selected[i]) { alloc = state.all[j]; break; }
      }
      if (!alloc) continue;

      if (alloc.status) statuses[alloc.status] = true;

      // Resolve resource names
      var rids = alloc.resource_ids || [];
      for (var r = 0; r < rids.length; r++) {
        var resLabel = resourceLabelById[rids[r]];
        if (resLabel) resourceNames[resLabel] = true;
      }

      // Find project label
      if (projState) {
        for (var j = 0; j < projState.all.length; j++) {
          if (projState.all[j].id === alloc.project_id) {
            projectNames[projState.all[j].label] = true;
            break;
          }
        }
      }

      // Find department for this project
      if (deptState) {
        for (var j = 0; j < deptState.all.length; j++) {
          var dept = deptState.all[j];
          if (dept.project_ids && dept.project_ids.indexOf(alloc.project_id) !== -1) {
            deptNames[dept.label] = true;
          }
        }
      }
    }

    var parts = [];
    var deptList = Object.keys(deptNames);
    if (deptList.length) parts.push('Dept: ' + (deptList.length <= 3 ? deptList.join(', ') : deptList.length + ' departments'));
    var projList = Object.keys(projectNames);
    if (projList.length) parts.push('Project: ' + (projList.length <= 3 ? projList.join(', ') : projList.length + ' projects'));
    var resList = Object.keys(resourceNames);
    if (resList.length) parts.push('Resource: ' + (resList.length <= 3 ? resList.join(', ') : resList.length + ' resources'));
    var statusList = Object.keys(statuses);
    if (statusList.length) parts.push('Status: ' + statusList.join(', '));
    return parts.length ? 'Context: ' + parts.join(' · ') : '';
  }

  if (name === 'projects') {
    var deptState = FilterStore.state.departments;
    if (!deptState) return '';

    var deptNames = {};
    var selLookup = _toLookup(state.selected);
    for (var i = 0; i < deptState.all.length; i++) {
      var dept = deptState.all[i];
      var deptPids = dept.project_ids || [];
      for (var j = 0; j < deptPids.length; j++) {
        if (selLookup[deptPids[j]]) {
          deptNames[dept.label] = true;
          break;
        }
      }
    }

    var deptList = Object.keys(deptNames);
    if (!deptList.length) return '';
    return 'Dept: ' + (deptList.length <= 3 ? deptList.join(', ') : deptList.length + ' departments');
  }

  if (name === 'roles') {
    var roleState = FilterStore.state.roles;
    var deptState = FilterStore.state.departments;
    if (!roleState || !deptState) return '';

    var roleProjectIds = {};
    for (var i = 0; i < roleState.all.length; i++) {
      var role = roleState.all[i];
      if (state.selected.indexOf(role.id) === -1) continue;
      var pids = role.project_ids || [];
      for (var j = 0; j < pids.length; j++) roleProjectIds[pids[j]] = true;
    }

    var matchedDepts = [];
    for (var i = 0; i < deptState.all.length; i++) {
      var dept = deptState.all[i];
      var deptPids = dept.project_ids || [];
      for (var j = 0; j < deptPids.length; j++) {
        if (roleProjectIds[deptPids[j]]) { matchedDepts.push(dept.label); break; }
      }
    }

    var projectCount = Object.keys(roleProjectIds).length;
    var parts = [];
    if (matchedDepts.length) parts.push(matchedDepts.length <= 3 ? matchedDepts.join(', ') : matchedDepts.length + ' departments');
    if (projectCount) parts.push(projectCount + ' projects');
    return parts.length ? 'Present in: ' + parts.join(' across ') : '';
  }

  if (name === 'resources') {
    var resState = FilterStore.state.resources;
    var deptState = FilterStore.state.departments;
    if (!resState || !deptState) return '';

    var resProjectIds = {};
    for (var i = 0; i < resState.all.length; i++) {
      var res = resState.all[i];
      if (state.selected.indexOf(res.id) === -1) continue;
      var pids = res.project_ids || [];
      for (var j = 0; j < pids.length; j++) resProjectIds[pids[j]] = true;
    }

    var matchedDepts = [];
    for (var i = 0; i < deptState.all.length; i++) {
      var dept = deptState.all[i];
      var deptPids = dept.project_ids || [];
      for (var j = 0; j < deptPids.length; j++) {
        if (resProjectIds[deptPids[j]]) { matchedDepts.push(dept.label); break; }
      }
    }

    var projectCount = Object.keys(resProjectIds).length;
    var parts = [];
    if (matchedDepts.length) parts.push(matchedDepts.length <= 3 ? matchedDepts.join(', ') : matchedDepts.length + ' departments');
    if (projectCount) parts.push(projectCount + ' projects');
    return parts.length ? 'Spans: ' + parts.join(' across ') : '';
  }

  if (name === 'statuses') {
    var allocState = FilterStore.state.allocations;
    if (!allocState) return '';
    var matchCount = 0;
    for (var i = 0; i < allocState.visible.length; i++) {
      if (state.selected.indexOf(allocState.visible[i].status) !== -1) matchCount++;
    }
    return matchCount ? matchCount + ' matching allocations' : '';
  }

  return '';
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
    var hasSelections = state.selected.length > 0;

    if (!isNarrowed && !hasSelections) continue;

    var countText = '<span class="count">' + state.visible.length + '</span>';
    if (isNarrowed) {
      countText += ' <span style="color:#888;">(of ' + state.all.length + ')</span>';
    }
    if (hasSelections) {
      countText += ' · <span style="color:#0c5460;">' + state.selected.length + ' selected</span>';
    }

    var breakdown = _getBreakdown(k, state);
    var topDownInfo = _getTopDownInfo(k);
    var bottomUpContext = _getBottomUpContext(k, state);

    var html = '<div class="recip-detail-card' + (isNarrowed ? ' narrowed' : '') + '">';
    html += '<div class="recip-detail-title">' + label + ': ' + countText + '</div>';
    if (breakdown) html += '<div class="recip-detail-breakdown">' + breakdown + '</div>';
    if (bottomUpContext) html += '<div class="recip-detail-filtered-by" style="color:#0d6efd;">' + bottomUpContext + '</div>';
    if (topDownInfo) html += '<div class="recip-detail-filtered-by">' + topDownInfo + '</div>';
    html += '</div>';

    $container.append(html);
  }
}

function updateFilterSummaries() { _updateSummaries(); }

function clearAllFilters() {
  var keys = ['departments', 'projects', 'resources', 'statuses', 'allocations', 'roles'];
  for (var i = 0; i < keys.length; i++) {
    var state = FilterStore.state[keys[i]];
    if (state) {
      state.selected = [];
      state.visible = state.all;
    }
    if (FILTERS[keys[i]]) FILTERS[keys[i]].render();
  }
  _invalidateValidation();
  _updateSummaries();
}

function _updateClearButton() {
  var keys = ['departments', 'projects', 'resources', 'statuses', 'allocations', 'roles'];
  var hasAny = false;
  for (var i = 0; i < keys.length; i++) {
    var state = FilterStore.state[keys[i]];
    if (state && state.selected.length) { hasAny = true; break; }
  }
  $('#clearFiltersBtn').toggle(hasAny);
}

$(document).on('click', '#clearFiltersBtn', clearAllFilters);


// Initialization

$(document).ready(function() {
  if (typeof FILTER_DATA === 'undefined') return;

  var keys = ['departments', 'projects', 'resources', 'statuses', 'allocations', 'roles'];
  for (var i = 0; i < keys.length; i++) {
    var k = keys[i];
    if (FILTERS[k] && FILTER_DATA[k]) FILTERS[k].init(FILTER_DATA[k]);
  }

  _updateSummaries();
});
