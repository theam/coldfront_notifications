// ── variable_form.js ────────────────────────────────────────────
// No server-rendered globals required.

// Example values per resolver_key
var QUERY_EXAMPLES = {
  "user.username":   "jdoe",
  "user.email":      "jdoe@example.com",
  "user.full_name":  "John Doe",
  "user.first_name": "John",
  "user.last_name":  "Doe",
  "user.ifxid":      "IFX000123",
  "project.title":                        "astro-gpu-2025",
  "project.description":                  "(project description)",
  "project.status.name":                  "Active",
  "project.field_of_science.description": "Physics",
  "project.parent_project.title":         "FASRC",
  "project.pi.full_name":    "Dr. Jane Smith",
  "project.pi.email":        "pi@example.com",
  "project.pi.username":     "jsmith",
  "project.department.name": "Astronomy",
  "projectuser.role.name":   "PI",
  "allocation.id":            "412",
  "allocation.description":   "(allocation description)",
  "allocation.justification": "(justification text)",
  "allocation.quantity":      "1",
  "allocation.start_date":    "2025-01-01",
  "allocation.end_date":      "2025-12-31",
  "allocation.status.name":   "Active",
  "resource.name":                 "Cannon Cluster",
  "resource.description":          "(resource description)",
  "resource.resource_type.name":   "Cluster",
  "resource.parent_resource.name": "Cannon Cluster",
  "resource.names_joined":         "Cannon Cluster, Holyoke GPU"
};

function syncSource() {
  var val = $('input[name=source]:checked').val();
  $('#card-manual, #card-query').removeClass('active');
  if (val === 'manual') {
    $('#card-manual').addClass('active');
    $('#query-config').removeClass('show');
    $('#manual-config').addClass('show');
    syncValueWidget();
  } else if (val === 'query') {
    $('#card-query').addClass('active');
    $('#query-config').addClass('show');
    $('#manual-config').removeClass('show');
    updateQueryExample();
  } else {
    $('#query-config').removeClass('show');
    $('#manual-config').removeClass('show');
  }
}

// Swap the Value input's type based on input_widget selection so the admin
// gets a proper date/url/textarea control while entering the stored value.
function syncValueWidget() {
  var widget = $('#id_input_widget').val() || 'text';
  var $cur = $('#id_value');
  if (!$cur.length) return;
  var current = $cur.val();
  var name = $cur.attr('name') || 'value';
  var $new;
  if (widget === 'textarea') {
    $new = $('<textarea rows="3"></textarea>');
  } else if (widget === 'date') {
    $new = $('<input type="date">');
  } else if (widget === 'datetime') {
    $new = $('<input type="datetime-local">');
  } else if (widget === 'url') {
    $new = $('<input type="url">');
  } else {
    $new = $('<input type="text">');
  }
  $new.attr({ id: 'id_value', name: name, class: 'form-control' }).val(current);
  $cur.replaceWith($new);
}

function updateQueryExample() {
  var k = $('#id_resolver_key').val();
  if (!k) { $('#query-example').hide(); return; }
  var ex = QUERY_EXAMPLES[k] || '(depends on recipient)';
  $('#query-example-text').html('<code>' + k + '</code> → <strong>' + ex + '</strong>');
  $('#query-example').show();
}

$(document).on('change', 'input[name=source]', syncSource);
$(document).on('change', '#id_resolver_key', updateQueryExample);
$(document).on('change', '#id_input_widget', syncValueWidget);
$(document).ready(syncSource);
