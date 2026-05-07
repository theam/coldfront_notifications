// ── compose.js ──────────────────────────────────────────────────
// Expects the following globals set inline by the Django template:
//   TEMPLATES          - array of template objects
//   VARIABLES          - array of variable objects
//   URLS.previewRender - URL for preview-render endpoint
//   URLS.filterOptions - URL for filter-options endpoint
//   URLS.validate      - URL for validate endpoint
//   URLS.recipientCount - URL for recipient-count endpoint

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
// Always fetch fresh content from the server — avoids stale data when
// an admin edits a template in another tab and returns to compose.
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

// ── Preview modal ────────────────────────────────────────────────
// Samples returned from /preview-render/; indexed by select option value.
var PREVIEW_SAMPLES = [];

function renderPreviewSample(idx) {
  var s = PREVIEW_SAMPLES[idx];
  if (!s) {
    // "Template" — no recipient, raw tokens
    $('#prev-to').text('—');
    $('#prev-subject').text($('#id_subject').val() || '—');
    $('#prev-body').text($('#id_body').val() || '—');
    $('#prev-error').hide();
    $('#prev-footer').text('Showing template with literal placeholders');
    return;
  }
  $('#prev-to').text((s.recipient.name || s.recipient.username) + ' <' + s.recipient.email + '>');
  $('#prev-subject').text(s.subject || '—');
  $('#prev-body').text(s.body || '—');
  if (s.error) {
    $('#prev-error').text('Resolution error: ' + s.error).show();
  } else {
    $('#prev-error').hide();
  }
  var ctx = [];
  if (s.recipient.project)    ctx.push('project: ' + s.recipient.project);
  if (s.recipient.allocation) ctx.push('allocation: ' + s.recipient.allocation);
  $('#prev-footer').text(ctx.join(' · ') || 'recipient-only context');
}

$(document).on('click', '#previewBtn', function() {
  $('#prev-from').text($('#id_sender').val());
  $('#prev-replyto').text($('#id_reply_to').val());
  // Show the raw template immediately while samples load
  PREVIEW_SAMPLES = [];
  $('#prev-recipient-picker').html('<option value="">— Template (raw placeholders) —</option>'
    + '<option disabled>Loading samples…</option>');
  renderPreviewSample(null);
  $('#prev-scope-badge').text('');
  $('#previewModal').modal('show');

  $.ajax({
    url: URLS.previewRender,
    type: 'POST',
    data: {
      csrfmiddlewaretoken: $('input[name=csrfmiddlewaretoken]').val(),
      subject:      $('#id_subject').val() || '',
      body:         $('#id_body').val()    || '',
      filters:      JSON.stringify(collectFilters()),
      dedupe_users: JSON.stringify(getDedupeUsers()),
    },
    success: function(data) {
      PREVIEW_SAMPLES = data.samples || [];
      var opts = ['<option value="">— Template (raw placeholders) —</option>'];
      PREVIEW_SAMPLES.forEach(function(s, i) {
        var lbl = (s.recipient.name || s.recipient.username) + ' <' + s.recipient.email + '>';
        if (s.recipient.project) lbl += ' · ' + s.recipient.project;
        opts.push('<option value="' + i + '">' + lbl + '</option>');
      });
      if (!PREVIEW_SAMPLES.length) {
        opts.push('<option disabled>(no matching recipients)</option>');
      }
      $('#prev-recipient-picker').html(opts.join(''));
      if (data.scope) $('#prev-scope-badge').text('scope: ' + data.scope + ' · ' + (data.total_emails || 0) + ' total');

      // Auto-select the first real sample if available
      if (PREVIEW_SAMPLES.length) {
        $('#prev-recipient-picker').val('0');
        renderPreviewSample(0);
      } else {
        renderPreviewSample(null);
      }
    },
    error: function() {
      $('#prev-recipient-picker').html('<option value="">— Template (raw placeholders) —</option>');
      $('#prev-footer').text('Preview failed to load — showing raw template');
    },
  });
});

$(document).on('change', '#prev-recipient-picker', function() {
  var v = $(this).val();
  renderPreviewSample(v === '' ? null : parseInt(v, 10));
});

// ── Recipient filters stale on change ────────────────────────────
$(document).on('change', '.s2', function() {
  $('#recipNum').text('?');
  $('#recipLbl').text('press Validate');
  $('#sendBtn').prop('disabled', true);
  $('#previewRecipBtn').prop('disabled', true);
  $('#sendWarn').hide();
  // Changing filters may change the user pool — dedupe choices are user-scoped,
  // so reset them to avoid stale selections.
  setDedupeUsers([]);
});

// ── Cascading filter options ─────────────────────────────────────
// Rebuild a <select> from an option list, keeping still-valid user picks.
// items: [{id, label}] or ['name', 'name', ...]
function rebuildSelect($sel, items) {
  var current = $sel.val() || [];
  var normalized = (items || []).map(function(it) {
    return (typeof it === 'string') ? { id: it, label: it } : it;
  });
  var validIds = {};
  normalized.forEach(function(i) { validIds[String(i.id)] = true; });
  var keep = current.filter(function(v) { return validIds[String(v)]; });
  var html = normalized.map(function(i) {
    var esc = $('<div>').text(i.label).html();
    return '<option value="' + i.id + '">' + esc + '</option>';
  }).join('');
  // change.select2 updates the widget without firing a generic 'change',
  // so the stale handler above doesn't loop on programmatic rebuilds.
  $sel.html(html).val(keep).trigger('change.select2');
}

// Downstream targets for each source filter (top-down cascade).
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
    url: URLS.filterOptions,
    type: 'POST',
    data: {
      csrfmiddlewaretoken: $('input[name=csrfmiddlewaretoken]').val(),
      projects:    $('#f_project').val()    || [],
      allocations: $('#f_allocation').val() || [],
      resources:   $('#f_resource').val()   || [],
      departments: $('#f_dept').val()       || []
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

function runValidation() {
  var $btn = $('#recalcBtn');
  $btn.prop('disabled', true).html('<i class="fas fa-spinner fa-spin"></i>');

  $.ajax({
    url: URLS.validate,
    type: 'POST',
    data: {
      csrfmiddlewaretoken: $('input[name=csrfmiddlewaretoken]').val(),
      subject:      $('#id_subject').val() || '',
      body:         $('#id_body').val()    || '',
      filters:      JSON.stringify(collectFilters()),
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

      // Multi-email users
      if (data.multi_emails && data.multi_emails.length) {
        $('#multiCount').text(data.multi_emails.length);
        $('#multiBadge').show();
        var lines = data.multi_emails.map(function(m) {
          return '<li><code>' + m.username + '</code> — ' + m.count + ' emails</li>';
        }).join('');
        $('#multiList').html(lines);
        $('#multiWarnText').text(
          data.multi_emails.length + ' user(s) will receive multiple emails ' +
          '(different project/allocation context each)'
        );
        $('#multiWarn').show();
      } else {
        $('#multiBadge').hide();
        $('#multiWarn').hide();
      }

      // Preview recipients is independent of variable resolution — as
      // long as the filter matches anyone, admins can see who's in scope.
      $('#previewRecipBtn').prop('disabled', users === 0);
      HAS_VALIDATED = true;

      // Errors and missing tokens
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
          $ul.append('<li><code>' + e.email + '</code>: <code>{{' + e.token + '}}</code> — ' + e.reason + '</li>');
        });
        if (errs.length > 30) $ul.append('<li>… and ' + (errs.length - 30) + ' more</li>');
        $('#validationBad').show();
        $('#validationOK').hide();
        $('#sendBtn').prop('disabled', true);
        $('#sendWarn').hide();
      } else {
        $('#validationOK')
          .html('<i class="fas fa-check-circle mr-1"></i>' +
                users + ' users → <strong>' + emails + '</strong> emails. All variables resolve.')
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

$(document).on('click', '#multiShowBtn', function() {
  $('#multiList').toggle();
});

// Body/subject input changes invalidate previous validation.
$(document).on('input', '#id_body, #id_subject', function() {
  $('#validationPanel').hide();
  $('#sendBtn').prop('disabled', true);
  $('#sendWarn').hide();
});

// ── Recipient preview modal ──────────────────────────────────────
// Tracks the full set of tuples fetched from the server for this session.
var PREVIEW_ROWS  = [];   // current page of rows from server
var PREVIEW_SCOPE = null; // "user" | "project" | "allocation"
var PREVIEW_PAGE  = 1;
var PREVIEW_TOTAL_PAGES = 1;
var PREVIEW_TOTAL = 0;
var HAS_VALIDATED = false; // true after first validate; triggers auto-revalidation on changes

function renderPreviewRows() {
  // Group rows by username preserving order.
  var order = [], byUser = {};
  PREVIEW_ROWS.forEach(function(r) {
    var u = r.username;
    if (!(u in byUser)) { order.push(u); byUser[u] = []; }
    byUser[u].push(r);
  });

  var dedupeSet = {};
  getDedupeUsers().forEach(function(u) { dedupeSet[u] = true; });

  // Build a set from the server's cross-page multi-user list for O(1) lookup.
  var multiSet = {};
  ALL_MULTI_USERS.forEach(function(u) { multiSet[u] = true; });

  var html = '';
  order.forEach(function(u) {
    var group = byUser[u];
    var isMulti = !!multiSet[u];  // uses cross-page list, not just this page
    var isDeduped = !!dedupeSet[u];
    group.forEach(function(r, idx) {
      var first = idx === 0;
      // Strike-through rows after the first for deduped users (on this page)
      var muted = (isDeduped && !first);
      // If deduped and only 1 row on this page, still show strike-through
      if (isDeduped && group.length === 1 && isMulti) muted = true;
      var styleAttr = muted
        ? ' style="color:#aab0b7;text-decoration:line-through;font-style:italic;"'
        : '';
      var cbCell = '';
      if (first && isMulti) {
        cbCell = '<td style="text-align:center;"><input type="checkbox" class="dedupe-cb" '
               + 'data-username="' + u + '"'
               + (isDeduped ? ' checked' : '')
               + ' title="send only one email to ' + u + '"></td>';
      } else {
        cbCell = '<td></td>';
      }
      html += '<tr' + styleAttr + '>'
        + cbCell
        + '<td>' + (r.full_name||'') + (isMulti && first ? ' <span class="badge badge-warning ml-1" style="font-size:.6rem;">multi</span>' : '') + '</td>'
        + '<td style="font-family:monospace;font-size:.78rem;">' + (r.email||'') + '</td>'
        + '<td>' + (r.role||'') + '</td>'
        + '<td>' + (r.project||'') + '</td>'
        + '<td style="font-size:.78rem;">' + (r.allocation||'') + '</td>'
        + '</tr>';
    });
  });
  $('#recipRows').html(html || '<tr><td colspan="6" class="text-center text-muted py-3">No recipients matched.</td></tr>');

  // Effective email count = sum of group length, capped at 1 when deduped
  var effective = 0;
  order.forEach(function(u) {
    effective += dedupeSet[u] ? 1 : byUser[u].length;
  });
  var scopeLbl = PREVIEW_SCOPE ? 'scope: ' + PREVIEW_SCOPE : '';
  $('#recipModalCount').text(
    effective + ' emails → ' + order.length + ' users'
    + (effective !== PREVIEW_ROWS.length ? ' (was ' + PREVIEW_ROWS.length + ' before dedupe)' : '')
  );
  if (PREVIEW_SCOPE) { $('#recipModalScope').text(scopeLbl).show(); } else { $('#recipModalScope').hide(); }

}

function getPageSize() {
  return parseInt($('#recipPageSize').val(), 10) || 10;
}

function updatePaginationControls() {
  var atFirst = PREVIEW_PAGE <= 1;
  var atLast = PREVIEW_PAGE >= PREVIEW_TOTAL_PAGES;
  $('#recipPageInfo').text('Page ' + PREVIEW_PAGE + ' of ' + PREVIEW_TOTAL_PAGES);
  $('#recipFirstBtn').prop('disabled', atFirst);
  $('#recipPrevBtn').prop('disabled', atFirst);
  $('#recipNextBtn').prop('disabled', atLast);
  $('#recipLastBtn').prop('disabled', atLast);
}

function fetchPreviewPage(page) {
  PREVIEW_PAGE = page || 1;
  $('#recipSearch').val('');
  $('#recipRows').html('<tr><td colspan="6" class="text-center text-muted py-3"><i class="fas fa-spinner fa-spin mr-1"></i>Loading…</td></tr>');

  $.ajax({
    url: URLS.recipientCount,
    type: 'POST',
    data: {
      csrfmiddlewaretoken: $('input[name=csrfmiddlewaretoken]').val(),
      projects:     $('#f_project').val()    || [],
      allocations:  $('#f_allocation').val() || [],
      departments:  $('#f_dept').val()       || [],
      resources:    $('#f_resource').val()   || [],
      alloc_status: $('#f_status').val()     || [],
      roles:        $('#f_role').val()       || [],
      subject:      $('#id_subject').val()   || '',
      body:         $('#id_body').val()      || '',
      dedupe_users: JSON.stringify(getDedupeUsers()),
      preview:      'true',
      page:         PREVIEW_PAGE,
      page_size:    getPageSize(),
    },
    traditional: true,
    success: function(data) {
      PREVIEW_ROWS        = data.recipients || [];
      PREVIEW_SCOPE       = data.scope || null;
      PREVIEW_TOTAL       = data.count || 0;
      PREVIEW_TOTAL_PAGES = data.total_pages || 1;
      PREVIEW_PAGE        = data.page || 1;
      ALL_MULTI_USERS = (data.multi_users || []).map(function(m) { return m.username; });
      renderPreviewRows();
      // Show dedupe-all row if any multi-email users exist across all pages
      if (ALL_MULTI_USERS.length) {
        $('#dedupeAllRow').show();
        var allDeduped = ALL_MULTI_USERS.every(function(u) {
          return getDedupeUsers().indexOf(u) !== -1;
        });
        $('#dedupeAllCb').prop('checked', allDeduped);
      } else {
        $('#dedupeAllRow').hide();
      }
      updatePaginationControls();
    },
    error: function() {
      $('#recipRows').html('<tr><td colspan="6" class="text-center text-danger py-3">Failed to load.</td></tr>');
    }
  });
}

$(document).on('click', '#previewRecipBtn', function() {
  $('#recipModal').modal('show');
  fetchPreviewPage(1);
});
$(document).on('click', '#recipFirstBtn', function() {
  if (PREVIEW_PAGE > 1) fetchPreviewPage(1);
});
$(document).on('click', '#recipPrevBtn', function() {
  if (PREVIEW_PAGE > 1) fetchPreviewPage(PREVIEW_PAGE - 1);
});
$(document).on('click', '#recipNextBtn', function() {
  if (PREVIEW_PAGE < PREVIEW_TOTAL_PAGES) fetchPreviewPage(PREVIEW_PAGE + 1);
});
$(document).on('click', '#recipLastBtn', function() {
  if (PREVIEW_PAGE < PREVIEW_TOTAL_PAGES) fetchPreviewPage(PREVIEW_TOTAL_PAGES);
});
$(document).on('change', '#recipPageSize', function() {
  fetchPreviewPage(1);
});

// Client-side search: filter visible rows in the current page
$(document).on('input', '#recipSearch', function() {
  var q = $(this).val().toLowerCase();
  $('#recipRows tr').each(function() {
    var text = $(this).text().toLowerCase();
    $(this).toggle(!q || text.indexOf(q) !== -1);
  });
});

// All multi-email usernames across all pages (from server response).
var ALL_MULTI_USERS = [];

// Bulk dedupe: toggle ALL multi-email users across all pages.
$(document).on('change', '#dedupeAllCb', function() {
  if ($(this).is(':checked')) {
    setDedupeUsers(ALL_MULTI_USERS.slice());
  } else {
    setDedupeUsers([]);
  }
  renderPreviewRows();
  runValidation();
});

// Per-user dedupe toggle inside the modal.
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
  renderPreviewRows();
  runValidation();
});


// ── Serialize filter hidden fields before submit ─────────────────
$('#notifForm').on('submit', function() {
  $('#h_projects').val(JSON.stringify($('#f_project').val()    || []));
  $('#h_allocations').val(JSON.stringify($('#f_allocation').val() || []));
  $('#h_depts').val(JSON.stringify($('#f_dept').val()          || []));
  $('#h_resources').val(JSON.stringify($('#f_resource').val()  || []));
  $('#h_statuses').val(JSON.stringify($('#f_status').val()     || []));
  $('#h_roles').val(JSON.stringify($('#f_role').val()          || []));
});

// ── Select2 init ─────────────────────────────────────────────────
$(document).ready(function() {
  $('.s2').select2({
    allowClear: true,
    width: '100%',
    placeholder: function() { return $(this).data('placeholder'); }
  });

  // Load first template
  var $first = $('.tmpl-item').first();
  if ($first.length) loadTemplate($first);
});

// ── pageshow: reset validation state + refresh active template ────
// When a user edits a template in another tab and comes back, bfcache
// restores the DOM with the OLD textarea content and any stale errors.
// Force-hide the panel and re-fetch the active template so the textarea
// reflects the latest body before the next Validate click.
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
