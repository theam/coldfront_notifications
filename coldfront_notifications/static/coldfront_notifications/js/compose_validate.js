// ── compose_validate.js ─────────────────────────────────────────
// Validation AJAX, send-bar UI state, and input invalidation.
//
// Globals used: URLS (from Django template),
//               collectFilters, getDedupeUsers (from compose_filters.js)
// Globals exported: HAS_VALIDATED, runValidation

var HAS_VALIDATED = false;

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

      // Filter summary bar
      var af = data.active_filters || [];
      if (af.length) {
        var chips = af.map(function(f) {
          var vals = f.values.map(function(v) { return '<strong>' + $('<span>').text(v).html() + '</strong>'; }).join(', ');
          return '<span class="fs-chip">' + $('<span>').text(f.label).html() + ': ' + vals + '</span>';
        }).join('');
        $('#filterSummaryBar')
          .html('<div class="filter-summary-bar"><span class="fs-label"><i class="fas fa-filter mr-1"></i>Filters:</span>' + chips + '</div>')
          .show();
      } else {
        $('#filterSummaryBar')
          .html('<div class="filter-summary-bar"><span class="fs-label"><i class="fas fa-filter mr-1"></i>Filters:</span><span class="fs-none">No filters applied — targeting all active users</span></div>')
          .show();
      }

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
