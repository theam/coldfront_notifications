// ── compose_preview.js ──────────────────────────────────────────
// Email preview modal and recipient preview modal (with dedupe + pagination).
//
// Globals used: URLS (from Django template),
//               collectFilters, getDedupeUsers, setDedupeUsers (from compose_filters.js),
//               runValidation (from compose_validate.js)

// Blur focused element before modals hide to avoid aria-hidden warning.
$('#previewModal, #recipModal').on('hide.bs.modal', function() {
  document.activeElement && document.activeElement.blur();
});

// ═══════════════════════════════════════════════════════════════════
// EMAIL PREVIEW MODAL
// ═══════════════════════════════════════════════════════════════════

var PREVIEW_SAMPLES = [];

function renderPreviewSample(idx) {
  var s = PREVIEW_SAMPLES[idx];
  if (!s) {
    // "Template" — no recipient, raw tokens
    $('#prev-to').text('—');
    $('#prev-subject').text($('#id_subject').val() || '—');
    $('#prev-body').html(getBodyContent() || '—');
    $('#prev-error').hide();
    $('#prev-footer').text('Showing template with literal placeholders');
    return;
  }
  $('#prev-to').text((s.recipient.name || s.recipient.username) + ' <' + s.recipient.email + '>');
  $('#prev-subject').text(s.subject || '—');
  $('#prev-body').html(s.body || '—');
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
      body:         getBodyContent()       || '',
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

// ═══════════════════════════════════════════════════════════════════
// RECIPIENT PREVIEW MODAL (with dedupe + pagination)
// ═══════════════════════════════════════════════════════════════════

var PREVIEW_ROWS  = [];
var PREVIEW_SCOPE = null;
var PREVIEW_PAGE  = 1;
var PREVIEW_TOTAL_PAGES = 1;
var PREVIEW_TOTAL = 0;
var PREVIEW_USER_COUNT = 0;
var ALL_MULTI_USERS = [];
var ALL_MULTI_COUNTS = {};  // username → email count (for dedup math)

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
    var isMulti = !!multiSet[u];
    var isDeduped = !!dedupeSet[u];
    group.forEach(function(r, idx) {
      var first = idx === 0;
      var muted = (isDeduped && !first);
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

  // Compute effective email count: deduped users count as 1 email each.
  var dedupeList = getDedupeUsers();
  var dedupeSet = {};
  dedupeList.forEach(function(u) { dedupeSet[u] = true; });

  // For each multi-user that's deduped, subtract (count - 1) from total.
  var effective = PREVIEW_TOTAL;
  if (dedupeList.length && PREVIEW_TOTAL > 0) {
    ALL_MULTI_USERS.forEach(function(u) {
      if (dedupeSet[u] && ALL_MULTI_COUNTS[u]) {
        effective -= (ALL_MULTI_COUNTS[u] - 1);
      }
    });
  }

  var countText = effective + ' emails, ' + PREVIEW_USER_COUNT + ' users';
  if (effective !== PREVIEW_TOTAL) {
    countText += ' (was ' + PREVIEW_TOTAL + ' before dedupe)';
  }
  $('#recipModalCount').text(countText);
  $('#recipModalScope').hide();
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
      body:         getBodyContent()         || '',
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
      PREVIEW_USER_COUNT  = data.user_count || 0;
      PREVIEW_TOTAL_PAGES = data.total_pages || 1;
      PREVIEW_PAGE        = data.page || 1;
      ALL_MULTI_USERS = (data.multi_users || []).map(function(m) { return m.username; });
      ALL_MULTI_COUNTS = {};
      (data.multi_users || []).forEach(function(m) { ALL_MULTI_COUNTS[m.username] = m.count; });
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
