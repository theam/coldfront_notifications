// compose_validate.js — Validation AJAX, send-bar UI, and input invalidation.
//
// Globals used: URLS (from Django template),
//               collectFilters, getDedupeUsers (from compose_filters.js)
// Globals exported: HAS_VALIDATED, runValidation,
//                   _setValidateButtonState, _hideTopValidationResult

var HAS_VALIDATED = false;

function _setValidateButtonState(state) {
  var classMap = {
    default: 'btn btn-outline-secondary btn-sm',
    success: 'btn btn-outline-success btn-sm',
    error: 'btn btn-outline-danger btn-sm'
  };
  var className = classMap[state] || classMap.default;
  $('#recalcBtn').attr('class', className);
  // Bottom validate button always stays outline-secondary
}

function _showTopValidationResult(html, isError) {
  var alertClass = isError ? 'alert-danger' : 'alert-success';
  $('#topValidationResult')
    .html('<div class="alert ' + alertClass + ' py-2 mb-0" style="font-size:.82rem;">' + html + '</div>')
    .show();
}

function _hideTopValidationResult() {
  $('#topValidationResult').hide().empty();
}

function _buildErrorRow(error) {
  var parts = ['<code>' + error.email + '</code>'];
  if (error.project) {
    parts.push('project <strong>' + $('<span>').text(error.project).html() + '</strong>');
  }
  if (error.allocation_label) {
    parts.push('allocation <strong>' + $('<span>').text(error.allocation_label).html() + '</strong>');
  }
  parts.push('<code>{{' + error.token + '}}</code> — ' + error.reason);
  return '<li>' + parts.join(' · ') + '</li>';
}

function _buildTopErrorMessage(errorTitle, errors) {
  var html = '<i class="fas fa-exclamation-triangle mr-1"></i><strong>' + errorTitle + '</strong>';
  html += '<ul style="margin:6px 0 0 20px;font-size:.78rem;max-height:200px;overflow-y:auto;">';
  var limit = Math.min(errors.length, 20);
  for (var i = 0; i < limit; i++) {
    html += _buildErrorRow(errors[i]);
  }
  if (errors.length > 20) {
    html += '<li>… and ' + (errors.length - 20) + ' more</li>';
  }
  html += '</ul>';
  return html;
}

function runValidation() {
  var $btn = $('#recalcBtn');
  $btn.prop('disabled', true).html('<i class="fas fa-spinner fa-spin"></i>');
  _setValidateButtonState('default');
  _hideTopValidationResult();

  $.ajax({
    url: URLS.validate,
    type: 'POST',
    data: {
      csrfmiddlewaretoken: $('input[name=csrfmiddlewaretoken]').val(),
      subject: $('#id_subject').val() || '',
      body: $('#id_body').val() || '',
      filters: JSON.stringify(collectFilters()),
      dedupe_users: JSON.stringify(getDedupeUsers()),
    },
    success: function(data) {
      var userCount = data.user_count || 0;
      var emailCount = data.email_count || 0;
      $('#recipNum').text(userCount);
      $('#recipLbl').text('users');
      $('#emailCount').text(emailCount);
      $('#warnCount').text(emailCount);
      $('#sendCount').text(emailCount);
      $('#hidden_recip_count').val(emailCount);

      // Filter summary bar
      var activeFilters = data.active_filters || [];
      if (activeFilters.length) {
        var chips = activeFilters.map(function(filter) {
          var values = filter.values.map(function(value) {
            return '<strong>' + $('<span>').text(value).html() + '</strong>';
          }).join(', ');
          return '<span class="fs-chip">' + $('<span>').text(filter.label).html() + ': ' + values + '</span>';
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
        var multiLines = data.multi_emails.map(function(entry) {
          return '<li><code>' + entry.username + '</code> — ' + entry.count + ' emails</li>';
        }).join('');
        $('#multiList').html(multiLines);
        $('#multiWarnText').text(
          data.multi_emails.length + ' user(s) will receive multiple emails ' +
          '(different project/allocation context each)'
        );
        $('#multiWarn').show();
      } else {
        $('#multiBadge').hide();
        $('#multiWarn').hide();
      }

      $('#previewRecipBtn').prop('disabled', userCount === 0);
      HAS_VALIDATED = true;

      // Errors and missing tokens
      $('#validationPanel').show();
      var errors = data.errors || [];
      var missingTokens = data.missing_tokens || [];

      if (errors.length || missingTokens.length) {
        _setValidateButtonState('error');

        var titleParts = [];
        if (missingTokens.length) titleParts.push(missingTokens.length + ' unknown token(s): ' + missingTokens.join(', '));
        if (errors.length) titleParts.push(errors.length + ' resolution error(s)');
        var errorTitle = titleParts.join('; ');

        $('#validationBadTitle').text('Blocking — ' + errorTitle);
        var $errorList = $('#validationErrors').empty();
        errors.slice(0, 30).forEach(function(error) {
          $errorList.append(_buildErrorRow(error));
        });
        if (errors.length > 30) {
          $errorList.append('<li>… and ' + (errors.length - 30) + ' more</li>');
        }
        $('#validationBad').show();
        $('#validationOK').hide();
        $('#sendBtn').prop('disabled', true);
        $('#sendWarn').hide();

        _showTopValidationResult(_buildTopErrorMessage(errorTitle, errors), true);

      } else {
        _setValidateButtonState('success');

        var successMessage = '<i class="fas fa-check-circle mr-1"></i>' +
          userCount + ' users → <strong>' + emailCount + '</strong> emails. All variables resolve.';

        $('#validationOK').html(successMessage).show();
        $('#validationBad').hide();
        $('#sendBtn').prop('disabled', emailCount === 0);
        if (emailCount > 0) $('#sendWarn').show();

        _showTopValidationResult(successMessage, false);
      }
    },
    error: function() {
      $('#recipNum').text('—');
      $('#recipLbl').text('Error — try again');
      _setValidateButtonState('error');
      _showTopValidationResult(
        '<i class="fas fa-exclamation-triangle mr-1"></i>Validation request failed. Please try again.',
        true
      );
    },
    complete: function() {
      // Restore button text but keep the colored class
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
  _setValidateButtonState('default');
  _hideTopValidationResult();
});
