// ── notifications_shared.js ─────────────────────────────────────
// Shared utilities for compose and resend pages.
// Requires jQuery and Bootstrap modal. Expects URLS global with
// previewRender and recipientCount keys.

// ── HTML escape ─────────────────────────────────────────────────
function esc(s) {
  var d = document.createElement('div');
  d.appendChild(document.createTextNode(s || ''));
  return d.innerHTML;
}

// ── Email Preview modal ─────────────────────────────────────────
// Caller must provide getFilters() and getDedupeUsers() via opts.
var NotifPreview = (function() {
  var SAMPLES = [];

  function renderSample(idx) {
    var s = SAMPLES[idx];
    if (!s) {
      $('#prev-to').text('--');
      $('#prev-subject').text($('#id_subject').val() || '--');
      $('#prev-body').text($('#id_body').val() || '--');
      $('#prev-error').hide();
      $('#prev-footer').text('Showing template with literal placeholders');
      return;
    }
    $('#prev-to').text((s.recipient.name || s.recipient.username) + ' <' + s.recipient.email + '>');
    $('#prev-subject').text(s.subject || '--');
    $('#prev-body').text(s.body || '--');
    if (s.error) { $('#prev-error').text('Resolution error: ' + s.error).show(); }
    else { $('#prev-error').hide(); }
    var ctx = [];
    if (s.recipient.project) ctx.push('project: ' + s.recipient.project);
    if (s.recipient.allocation) ctx.push('allocation: ' + s.recipient.allocation);
    $('#prev-footer').text(ctx.join(' \u00b7 ') || 'recipient-only context');
  }

  function open(getFilters, getDedupeUsers) {
    var csrf = $('input[name=csrfmiddlewaretoken]').val();
    $('#prev-from').text($('#id_sender').val());
    $('#prev-replyto').text($('#id_reply_to').val());
    SAMPLES = [];
    $('#prev-recipient-picker').html(
      '<option value="">-- Template (raw placeholders) --</option>'
      + '<option disabled>Loading samples\u2026</option>'
    );
    renderSample(null);
    $('#prev-scope-badge').text('');
    $('#previewModal').modal('show');

    $.ajax({
      url: URLS.previewRender, type: 'POST',
      data: {
        csrfmiddlewaretoken: csrf,
        subject: $('#id_subject').val() || '',
        body: $('#id_body').val() || '',
        filters: JSON.stringify(getFilters()),
        dedupe_users: JSON.stringify(getDedupeUsers()),
      },
      success: function(data) {
        SAMPLES = data.samples || [];
        var opts = ['<option value="">-- Template (raw placeholders) --</option>'];
        SAMPLES.forEach(function(s, i) {
          var lbl = esc((s.recipient.name || s.recipient.username) + ' <' + s.recipient.email + '>');
          if (s.recipient.project) lbl += ' &middot; ' + esc(s.recipient.project);
          opts.push('<option value="' + i + '">' + lbl + '</option>');
        });
        if (!SAMPLES.length) opts.push('<option disabled>(no matching recipients)</option>');
        $('#prev-recipient-picker').html(opts.join(''));
        if (data.scope) $('#prev-scope-badge').text('scope: ' + data.scope + ' \u00b7 ' + (data.total_emails || 0) + ' total');
        if (SAMPLES.length) { $('#prev-recipient-picker').val('0'); renderSample(0); }
        else { renderSample(null); }
      },
      error: function() {
        $('#prev-recipient-picker').html('<option value="">-- Template (raw placeholders) --</option>');
        $('#prev-footer').text('Preview failed to load');
      },
    });
  }

  $(document).on('change', '#prev-recipient-picker', function() {
    var v = $(this).val();
    renderSample(v === '' ? null : parseInt(v, 10));
  });

  return { open: open };
})();


// ── Recipient Preview modal ─────────────────────────────────────
// Caller provides getFilterData(), getDedupeUsers(), renderRow(r),
// and colCount via NotifRecipients.init(opts).
var NotifRecipients = (function() {
  var PAGE = 1, TOTAL_PAGES = 1, ROWS = [], SCOPE = null;
  var _getFilterData, _getDedupeUsers, _renderRow, _colCount, _onSuccess;

  function getPageSize() { return parseInt($('#recipPageSize').val(), 10) || 10; }

  function updatePagination() {
    var atFirst = PAGE <= 1, atLast = PAGE >= TOTAL_PAGES;
    $('#recipPageInfo').text('Page ' + PAGE + ' of ' + TOTAL_PAGES);
    $('#recipFirstBtn').prop('disabled', atFirst);
    $('#recipPrevBtn').prop('disabled', atFirst);
    $('#recipNextBtn').prop('disabled', atLast);
    $('#recipLastBtn').prop('disabled', atLast);
  }

  function fetchPage(page) {
    PAGE = page || 1;
    $('#recipSearch').val('');
    $('#recipRows').html('<tr><td colspan="' + _colCount + '" class="text-center text-muted py-3">'
      + '<i class="fas fa-spinner fa-spin mr-1"></i>Loading\u2026</td></tr>');

    var data = $.extend({}, _getFilterData(), {
      csrfmiddlewaretoken: $('input[name=csrfmiddlewaretoken]').val(),
      subject: $('#id_subject').val() || '',
      body: $('#id_body').val() || '',
      dedupe_users: JSON.stringify(_getDedupeUsers()),
      preview: 'true',
      page: PAGE,
      page_size: getPageSize(),
    });

    $.ajax({
      url: URLS.recipientCount, type: 'POST',
      data: data,
      traditional: true,
      success: function(resp) {
        ROWS = resp.recipients || [];
        SCOPE = resp.scope || null;
        TOTAL_PAGES = resp.total_pages || 1;
        PAGE = resp.page || 1;

        if (_onSuccess) {
          _onSuccess(resp);
        } else {
          var html = '';
          ROWS.forEach(function(r) { html += _renderRow(r); });
          $('#recipRows').html(html || '<tr><td colspan="' + _colCount
            + '" class="text-center text-muted py-3">No recipients matched.</td></tr>');

          var scopeLbl = SCOPE ? 'scope: ' + SCOPE : '';
          $('#recipModalCount').text(ROWS.length + ' on this page');
          if (SCOPE) { $('#recipModalScope').text(scopeLbl).show(); } else { $('#recipModalScope').hide(); }
        }
        updatePagination();
      },
      error: function() {
        $('#recipRows').html('<tr><td colspan="' + _colCount
          + '" class="text-center text-danger py-3">Failed to load.</td></tr>');
      }
    });
  }

  function init(opts) {
    _getFilterData = opts.getFilterData;
    _getDedupeUsers = opts.getDedupeUsers || function() { return []; };
    _renderRow = opts.renderRow;
    _colCount = opts.colCount || 6;
    _onSuccess = opts.onSuccess || null;
  }

  // Event handlers
  $(document).on('click', '#previewRecipBtn', function() {
    $('#recipModal').modal('show');
    fetchPage(1);
  });
  $(document).on('click', '#recipFirstBtn', function() { if (PAGE > 1) fetchPage(1); });
  $(document).on('click', '#recipPrevBtn', function() { if (PAGE > 1) fetchPage(PAGE - 1); });
  $(document).on('click', '#recipNextBtn', function() { if (PAGE < TOTAL_PAGES) fetchPage(PAGE + 1); });
  $(document).on('click', '#recipLastBtn', function() { if (PAGE < TOTAL_PAGES) fetchPage(TOTAL_PAGES); });
  $(document).on('change', '#recipPageSize', function() { fetchPage(1); });

  $(document).on('input', '#recipSearch', function() {
    var q = $(this).val().toLowerCase();
    $('#recipRows tr').each(function() {
      $(this).toggle(!q || $(this).text().toLowerCase().indexOf(q) !== -1);
    });
  });

  return { init: init, fetchPage: fetchPage };
})();
