// ── compose.js ──────────────────────────────────────────────────
// Requires notifications_shared.js loaded first.
// Expects globals: TEMPLATES, VARIABLES, URLS

// Index VARIABLES by key for O(1) lookup
var VAR_BY_KEY = {};
VARIABLES.forEach(function(v) { VAR_BY_KEY[v.key] = v; });

// ── Render chip list ─────────────────────────────────────────────
function renderVars(vars, filter) {
  var $list = $('#varsList').empty();
  var filtered = filter
    ? vars.filter(function(v) { return v.toLowerCase().indexOf(filter.toLowerCase()) !== -1; })
    : vars;
  if (!filtered.length) {
    $list.append('<div class="vars-empty">No matches</div>');
    return;
  }
  filtered.forEach(function(v) {
    var token   = '{{' + v + '}}';
    var def     = VAR_BY_KEY[v] || {};
    var example = def.example || '';
    var badge   = def.source === 'manual' ? ' [manual]' : '';
    var $btn = $('<button type="button" class="var-btn"></button>')
      .attr('data-var', token)
      .attr('title', (def.description || def.label || '') + (example ? ' — ' + example : ''))
      .html(
        '<span style="display:block;font-family:monospace;font-size:.7rem;">' + token + badge + '</span>' +
        '<span style="display:block;font-size:.65rem;color:#6c757d;margin-top:1px;">' + example + '</span>'
      );
    $list.append($btn);
  });
}

// ── Extract tokens from subject + body ───────────────────────────
var TOKEN_RE = /\{\{(\w+)\}\}/g;
function extractTokens() {
  var text = ($('#id_subject').val() || '') + '\n' + ($('#id_body').val() || '');
  var seen = {};
  var out = [];
  var m;
  while ((m = TOKEN_RE.exec(text))) {
    if (!seen[m[1]]) { seen[m[1]] = true; out.push(m[1]); }
  }
  return out;
}

// ── Load template into compose fields ────────────────────────────
var ACTIVE_VARS = [];
function loadTemplate($el) {
  var id   = parseInt($el.attr('data-id') || 0);
  var slug = $el.attr('data-slug') || '';
  $('#active-slug').text(slug);
  $('#hidden_template_id').val(id);
  $('#varSearch').val('');
  $('#validationPanel').hide();
  $('#sendBtn').prop('disabled', true);
  $('#sendWarn').hide();
  setDedupeUsers([]);
  if (!id) { ACTIVE_VARS = []; renderVars([], ''); return; }
  $.getJSON('/notifications/templates/' + id + '/json/', function(t) {
    $('#id_subject').val(t.subject || '');
    $('#id_body').val(t.body || '');
    ACTIVE_VARS = t.variables || [];
    renderVars(ACTIVE_VARS, '');
    if (HAS_VALIDATED) runValidation();
  }).fail(function() {
    ACTIVE_VARS = [];
    renderVars([], '');
  });
}

// ── Template click ───────────────────────────────────────────────
$(document).on('click', '.tmpl-item', function() {
  $('.tmpl-item').removeClass('active');
  $(this).addClass('active');
  loadTemplate($(this));
});

// ── Template search ─────────────────────────────────────────────
$(document).on('input', '#tmplSearch', function() {
  var q = $(this).val().toLowerCase();
  $('.tmpl-item').each(function() {
    var text = $(this).text().toLowerCase();
    $(this).toggle(!q || text.indexOf(q) !== -1);
  });
});

// ── Variable search ──────────────────────────────────────────────
$(document).on('input', '#varSearch', function() {
  renderVars(ACTIVE_VARS, $(this).val());
});

// ── Variable insert at cursor ────────────────────────────────────
$(document).on('click', '.var-btn', function() {
  var ta    = document.getElementById('id_body');
  var token = $(this).attr('data-var');
  var s = ta.selectionStart, e = ta.selectionEnd;
  ta.value = ta.value.substring(0, s) + token + ta.value.substring(e);
  ta.selectionStart = ta.selectionEnd = s + token.length;
  ta.focus();
});

// ── Preview modal — delegates to shared NotifPreview ─────────────
$(document).on('click', '#previewBtn', function() {
  NotifPreview.open(collectFilters, getDedupeUsers);
});

// ── Recipient filters stale on change ────────────────────────────
$(document).on('change', '.s2', function() {
  $('#recipNum').text('?');
  $('#recipLbl').text('press Validate');
  $('#sendBtn').prop('disabled', true);
  $('#previewRecipBtn').prop('disabled', true);
  $('#sendWarn').hide();
  setDedupeUsers([]);
});

// ── Cascading filter options ─────────────────────────────────────
function rebuildSelect($sel, items) {
  var current = $sel.val() || [];
  var normalized = (items || []).map(function(it) {
    return (typeof it === 'string') ? { id: it, label: it } : it;
  });
  var validIds = {};
  normalized.forEach(function(i) { validIds[String(i.id)] = true; });
  var keep = current.filter(function(v) { return validIds[String(v)]; });
  var html = normalized.map(function(i) {
    return '<option value="' + i.id + '">' + esc(i.label) + '</option>';
  }).join('');
  $sel.html(html).val(keep).trigger('change.select2');
}

var DOWNSTREAM = {
  project:    { dept: true, alloc: true, resource: true, status: true, role: true },
  dept:       { alloc: true, resource: true, status: true, role: true },
  allocation: { resource: true, status: true },
  resource:   { status: true }
};

function cascadeFrom(source) {
  var targets = DOWNSTREAM[source];
  if (!targets) return;
  $.ajax({
    url: URLS.filterOptions, type: 'POST',
    data: {
      csrfmiddlewaretoken: $('input[name=csrfmiddlewaretoken]').val(),
      projects: $('#f_project').val() || [],
      allocations: $('#f_allocation').val() || [],
      resources: $('#f_resource').val() || [],
      departments: $('#f_dept').val() || []
    },
    traditional: true,
    success: function(data) {
      if (targets.alloc)    rebuildSelect($('#f_allocation'), data.allocations);
      if (targets.dept)     rebuildSelect($('#f_dept'),       data.departments);
      if (targets.resource) rebuildSelect($('#f_resource'),   data.resources);
      if (targets.status)   rebuildSelect($('#f_status'),     data.statuses);
      if (targets.role)     rebuildSelect($('#f_role'),       data.roles);
    }
  });
}

$(document).on('change', '#f_project',    function() { cascadeFrom('project'); });
$(document).on('change', '#f_dept',       function() { cascadeFrom('dept'); });
$(document).on('change', '#f_allocation', function() { cascadeFrom('allocation'); });
$(document).on('change', '#f_resource',   function() { cascadeFrom('resource'); });

// ── Validate & count (AJAX) ─────────────────────────────────────
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

var HAS_VALIDATED = false;
var ALL_MULTI_USERS = [];

function runValidation() {
  var $btn = $('#recalcBtn');
  $btn.prop('disabled', true).html('<i class="fas fa-spinner fa-spin"></i>');

  $.ajax({
    url: URLS.validate, type: 'POST',
    data: {
      csrfmiddlewaretoken: $('input[name=csrfmiddlewaretoken]').val(),
      subject: $('#id_subject').val() || '',
      body: $('#id_body').val() || '',
      filters: JSON.stringify(collectFilters()),
      dedupe_users: JSON.stringify(getDedupeUsers()),
    },
    success: function(data) {
      var users = data.user_count || 0;
      var emails = data.email_count || 0;
      $('#recipNum').text(users);
      $('#recipLbl').text('users');
      $('#emailCount').text(emails);
      $('#warnCount').text(emails);
      $('#sendCount').text(emails);
      $('#hidden_recip_count').val(emails);

      if (data.multi_emails && data.multi_emails.length) {
        $('#multiCount').text(data.multi_emails.length);
        $('#multiBadge').show();
        var lines = data.multi_emails.map(function(m) {
          return '<li><code>' + esc(m.username) + '</code> — ' + m.count + ' emails</li>';
        }).join('');
        $('#multiList').html(lines);
        $('#multiWarnText').text(
          data.multi_emails.length + ' user(s) will receive multiple emails '
          + '(different project/allocation context each)'
        );
        $('#multiWarn').show();
      } else {
        $('#multiBadge').hide();
        $('#multiWarn').hide();
      }

      $('#previewRecipBtn').prop('disabled', users === 0);
      HAS_VALIDATED = true;

      $('#validationPanel').show();
      var errs = data.errors || [];
      var miss = data.missing_tokens || [];
      if (errs.length || miss.length) {
        var title = [];
        if (miss.length) title.push(miss.length + ' unknown token(s): ' + miss.join(', '));
        if (errs.length) title.push(errs.length + ' resolution error(s)');
        $('#validationBadTitle').text('Blocking — ' + title.join('; '));
        var $ul = $('#validationErrors').empty();
        errs.slice(0, 30).forEach(function(e) {
          $ul.append('<li><code>' + esc(e.email) + '</code>: <code>{{' + esc(e.token) + '}}</code> — ' + esc(e.reason) + '</li>');
        });
        if (errs.length > 30) $ul.append('<li>… and ' + (errs.length - 30) + ' more</li>');
        $('#validationBad').show();
        $('#validationOK').hide();
        $('#sendBtn').prop('disabled', true);
        $('#sendWarn').hide();
      } else {
        $('#validationOK')
          .html('<i class="fas fa-check-circle mr-1"></i>'
                + users + ' users → <strong>' + emails + '</strong> emails. All variables resolve.')
          .show();
        $('#validationBad').hide();
        $('#sendBtn').prop('disabled', emails === 0);
        if (emails > 0) $('#sendWarn').show();
      }
    },
    error: function() {
      $('#recipNum').text('—');
      $('#recipLbl').text('Error — try again');
    },
    complete: function() {
      $btn.prop('disabled', false).html('<i class="fas fa-check-double mr-1"></i>Validate');
    }
  });
}

$(document).on('click', '#recalcBtn, #recalcBtn2', function() { runValidation(); });
$(document).on('click', '#multiShowBtn', function() { $('#multiList').toggle(); });

$(document).on('input', '#id_body, #id_subject', function() {
  $('#validationPanel').hide();
  $('#sendBtn').prop('disabled', true);
  $('#sendWarn').hide();
});

// ── Recipient preview — compose-specific row renderer ────────────
function composeRenderRow(r, opts) {
  var u = r.username;
  var isMulti = opts.isMulti;
  var isDeduped = opts.isDeduped;
  var first = opts.first;
  var muted = (isDeduped && !first);
  if (isDeduped && opts.groupLen === 1 && isMulti) muted = true;
  var styleAttr = muted
    ? ' style="color:#aab0b7;text-decoration:line-through;font-style:italic;"' : '';
  var cbCell = '';
  if (first && isMulti) {
    cbCell = '<td style="text-align:center;"><input type="checkbox" class="dedupe-cb" '
           + 'data-username="' + esc(u) + '"'
           + (isDeduped ? ' checked' : '')
           + ' title="send only one email to ' + esc(u) + '"></td>';
  } else {
    cbCell = '<td></td>';
  }
  return '<tr' + styleAttr + '>'
    + cbCell
    + '<td>' + esc(r.full_name || '') + (isMulti && first ? ' <span class="badge badge-warning ml-1" style="font-size:.6rem;">multi</span>' : '') + '</td>'
    + '<td style="font-family:monospace;font-size:.78rem;">' + esc(r.email || '') + '</td>'
    + '<td>' + esc(r.role || '') + '</td>'
    + '<td>' + esc(r.project || '') + '</td>'
    + '<td style="font-size:.78rem;">' + esc(r.allocation || '') + '</td>'
    + '</tr>';
}

function renderComposePreviewRows(rows, multiUsers) {
  var order = [], byUser = {};
  rows.forEach(function(r) {
    var u = r.username;
    if (!(u in byUser)) { order.push(u); byUser[u] = []; }
    byUser[u].push(r);
  });

  var dedupeSet = {};
  getDedupeUsers().forEach(function(u) { dedupeSet[u] = true; });
  var multiSet = {};
  multiUsers.forEach(function(u) { multiSet[u] = true; });

  var html = '';
  order.forEach(function(u) {
    byUser[u].forEach(function(r, idx) {
      html += composeRenderRow(r, {
        isMulti: !!multiSet[u],
        isDeduped: !!dedupeSet[u],
        first: idx === 0,
        groupLen: byUser[u].length,
      });
    });
  });
  $('#recipRows').html(html || '<tr><td colspan="6" class="text-center text-muted py-3">No recipients matched.</td></tr>');

  var effective = 0;
  order.forEach(function(u) { effective += dedupeSet[u] ? 1 : byUser[u].length; });
  $('#recipModalCount').text(
    effective + ' emails → ' + order.length + ' users'
    + (effective !== rows.length ? ' (was ' + rows.length + ' before dedupe)' : '')
  );
}

// Init shared recipient preview with compose-specific behavior
NotifRecipients.init({
  colCount: 6,
  getFilterData: function() {
    return {
      projects: $('#f_project').val() || [],
      allocations: $('#f_allocation').val() || [],
      departments: $('#f_dept').val() || [],
      resources: $('#f_resource').val() || [],
      alloc_status: $('#f_status').val() || [],
      roles: $('#f_role').val() || [],
    };
  },
  getDedupeUsers: getDedupeUsers,
  renderRow: function() { return ''; }, // overridden by onSuccess
  onSuccess: function(data) {
    ALL_MULTI_USERS = (data.multi_users || []).map(function(m) { return m.username; });
    renderComposePreviewRows(data.recipients || [], ALL_MULTI_USERS);

    if (ALL_MULTI_USERS.length) {
      $('#dedupeAllRow').show();
      var allDeduped = ALL_MULTI_USERS.every(function(u) {
        return getDedupeUsers().indexOf(u) !== -1;
      });
      $('#dedupeAllCb').prop('checked', allDeduped);
    } else {
      $('#dedupeAllRow').hide();
    }

    var scope = data.scope || null;
    var scopeLbl = scope ? 'scope: ' + scope : '';
    if (scope) { $('#recipModalScope').text(scopeLbl).show(); } else { $('#recipModalScope').hide(); }
  },
});

// Bulk dedupe
$(document).on('change', '#dedupeAllCb', function() {
  if ($(this).is(':checked')) {
    setDedupeUsers(ALL_MULTI_USERS.slice());
  } else {
    setDedupeUsers([]);
  }
  NotifRecipients.fetchPage(1);
  runValidation();
});

$(document).on('change', '.dedupe-cb', function() {
  var username = $(this).attr('data-username');
  var cur = getDedupeUsers();
  var idx = cur.indexOf(username);
  if ($(this).is(':checked')) {
    if (idx === -1) cur.push(username);
  } else {
    if (idx !== -1) cur.splice(idx, 1);
  }
  setDedupeUsers(cur);
  NotifRecipients.fetchPage(1);
  runValidation();
});

// ── Serialize filter hidden fields before submit ─────────────────
$('#notifForm').on('submit', function() {
  $('#h_projects').val(JSON.stringify($('#f_project').val() || []));
  $('#h_allocations').val(JSON.stringify($('#f_allocation').val() || []));
  $('#h_depts').val(JSON.stringify($('#f_dept').val() || []));
  $('#h_resources').val(JSON.stringify($('#f_resource').val() || []));
  $('#h_statuses').val(JSON.stringify($('#f_status').val() || []));
  $('#h_roles').val(JSON.stringify($('#f_role').val() || []));
});

// ── Select2 init ─────────────────────────────────────────────────
$(document).ready(function() {
  $('.s2').select2({
    allowClear: true,
    width: '100%',
    placeholder: function() { return $(this).data('placeholder'); }
  });
  var $first = $('.tmpl-item').first();
  if ($first.length) loadTemplate($first);
});

// ── pageshow: reset stale state ──────────────────────────────────
$(window).on('pageshow', function() {
  $('#validationPanel').hide();
  $('#validationOK').hide();
  $('#validationBad').hide();
  $('#sendWarn').hide();
  $('#sendBtn').prop('disabled', true);
  $('#recipNum').text('?');
  $('#recipLbl').text('press Validate');
  $('#emailCount').text('—');
  $('#multiBadge').hide();
  $('#multiWarn').hide();
  setDedupeUsers([]);
  HAS_VALIDATED = false;
  var $active = $('.tmpl-item.active');
  if ($active.length) loadTemplate($active);
});
