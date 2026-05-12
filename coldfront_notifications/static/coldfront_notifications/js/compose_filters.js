// ── compose_filters.js ──────────────────────────────────────────
// Event-driven cascading filter logic + shared filter helpers.
//
// Globals used: URLS, FILTER_SUMMARIES (from Django template)
// Globals exported: collectFilters, getDedupeUsers, setDedupeUsers

// ── Shared helpers (used by validate, preview, submit) ──────────
function collectFilters() {
  return {
    projects:    $('#f_project').val()    || [],
    allocations: $('#f_allocation').val() || [],
    resources:   $('#f_resource').val()   || [],
    departments: $('#f_dept').val()       || [],
    statuses:    $('#f_status').val()     || [],
    roles:       $('#f_role').val()       || [],
  };
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

// ── Filter summary tooltips + inline summary ────────────────────
// summary: {count: N, breakdown: "...", filtered_by: "..."}

var FILTER_LABELS = {
  projects: 'Projects', allocations: 'Allocations', departments: 'Departments',
  resources: 'Resources', statuses: 'Statuses', roles: 'Roles'
};
var LATEST_SUMMARIES = {};  // kept in sync for the detail modal

function _formatTooltip(s) {
  if (!s || typeof s === 'string') return s || '';
  var count = s.count || 0;
  var parts = '<strong>' + count + '</strong> available';
  if (s.filtered_by) {
    parts += '<br><span style="color:#a0a0a0;font-size:.75rem;">Filtered by: ' + s.filtered_by + '</span>';
  } else {
    parts += '<br><span style="color:#a0a0a0;font-size:.75rem;">No filters applied</span>';
  }
  if (s.breakdown && s.filtered_by) {
    parts += '<br><span style="font-size:.75rem;">' + s.breakdown + '</span>';
  }
  return parts;
}

function _renderInlineSummary(summaries) {
  var $bar = $('#inlineSummary').empty();
  var keys = ['projects','allocations','departments','resources','statuses','roles'];
  var chips = [];

  keys.forEach(function(k) {
    var s = summaries[k];
    if (!s) return;
    var count = s.count || 0;
    var label = FILTER_LABELS[k] || k;
    // Keep chips short — count + label only; breakdown lives in the modal.
    chips.push('<b>' + count + '</b> ' + label);
  });

  chips.forEach(function(html) {
    $bar.append('<span class="ris-chip">' + html + '</span>');
  });
}

function showFilterDetailModal() {
  var keys = ['projects','allocations','departments','resources','statuses','roles'];
  var html = '';
  keys.forEach(function(k) {
    var s = LATEST_SUMMARIES[k];
    if (!s) return;
    var label = FILTER_LABELS[k] || k;
    var count = s.count || 0;
    var breakdown = s.breakdown ? $('<span>').text(s.breakdown).html() : '—';
    var filtered = s.filtered_by ? $('<span>').text(s.filtered_by).html() : '<span class="text-muted">None</span>';
    html += '<tr>'
      + '<td style="font-weight:600;">' + label + '</td>'
      + '<td>' + count + '</td>'
      + '<td style="font-size:.8rem;">' + breakdown + '</td>'
      + '<td style="font-size:.8rem;">' + filtered + '</td>'
      + '</tr>';
  });
  $('#filterDetailRows').html(html);
  $('#filterDetailModal').modal('show');
}

// Blur focused element before modal hides to avoid aria-hidden warning.
$('#filterDetailModal').on('hide.bs.modal', function() {
  document.activeElement && document.activeElement.blur();
});

function updateFilterSummaries(summaries) {
  LATEST_SUMMARIES = summaries || {};

  // Update i-icon tooltips
  $('.filter-info').each(function() {
    var key = $(this).data('filter');
    var raw = summaries && summaries[key];
    var html = _formatTooltip(raw);
    if ($(this).data('ui-tooltip')) $(this).tooltip('destroy');
    $(this).attr('title', '').tooltip({ content: html, tooltipClass: 'filter-summary-tip' });
  });

  // Update inline summary bar
  _renderInlineSummary(summaries);
}

// ── Event-driven filter cascade ─────────────────────────────────
// Each filter maps to an event name. On change we POST the event + all
// current selections; the backend returns full {options, selected} for
// every filter.  Filters are disabled while a request is in flight to
// prevent stale/overlapping cascades.

var FILTER_EVENT = {
  f_project:    'PROJECT_UPDATED',
  f_allocation: 'ALLOCATION_UPDATED',
  f_dept:       'DEPARTMENT_UPDATED',
  f_resource:   'RESOURCE_UPDATED',
  f_status:     'ALLOCATION_STATUS_UPDATED',
  f_role:       'ROLE_UPDATED'
};

var FILTER_SELECTS = {
  projects:    '#f_project',
  allocations: '#f_allocation',
  departments: '#f_dept',
  resources:   '#f_resource',
  statuses:    '#f_status',
  roles:       '#f_role'
};

var _filterXhr = null;  // tracks in-flight request

function setFiltersLocked(locked) {
  $.each(FILTER_SELECTS, function(_key, sel) {
    $(sel).prop('disabled', locked);
    // Select2 reads the underlying <select> disabled state on trigger.
    $(sel).trigger('change.select2');
  });
}

function applyFilterState(data) {
  // data: { projects: {options, selected, summary}, allocations: {…}, … }
  // Rebuild each <select> from the server response without firing cascade
  // change events (use change.select2 to update the widget only).
  var summaries = {};
  $.each(FILTER_SELECTS, function(key, sel) {
    var state = data[key];
    if (!state) return;
    var $sel = $(sel);
    var html = (state.options || []).map(function(o) {
      var esc = $('<div>').text(o.label).html();
      return '<option value="' + o.id + '">' + esc + '</option>';
    }).join('');
    $sel.html(html).val(state.selected || []).trigger('change.select2');
    if (state.summary) summaries[key] = state.summary;
  });
  updateFilterSummaries(summaries);
}

function onFilterChange(eventName) {
  // Mark validation stale.
  $('#recipNum').text('?');
  $('#recipLbl').text('press Validate');
  $('#sendBtn').prop('disabled', true);
  $('#previewRecipBtn').prop('disabled', true);
  $('#sendWarn').hide();
  setDedupeUsers([]);

  // Abort any in-flight cascade request.
  if (_filterXhr) _filterXhr.abort();

  setFiltersLocked(true);

  _filterXhr = $.ajax({
    url: URLS.filterOptions,
    type: 'POST',
    contentType: 'application/json',
    headers: { 'X-CSRFToken': $('input[name=csrfmiddlewaretoken]').val() },
    data: JSON.stringify({
      event: eventName,
      selections: collectFilters()
    }),
    success: function(data) {
      applyFilterState(data);
    },
    error: function(xhr) {
      // Silently ignore aborted requests (we triggered a newer one).
      if (xhr.statusText === 'abort') return;
    },
    complete: function() {
      _filterXhr = null;
      setFiltersLocked(false);
    }
  });
}

// Bind a single change handler per filter select.
$.each(FILTER_EVENT, function(domId, eventName) {
  $(document).on('change', '#' + domId, function() {
    onFilterChange(eventName);
  });
});
