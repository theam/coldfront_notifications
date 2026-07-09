// ── compose_init.js ─────────────────────────────────────────────
// Select2 initialization, form submit serialization, pageshow reset.
// Must be loaded LAST (after all other compose_*.js files).
//
// Globals used: loadTemplate (from compose_templates.js),
//               setDedupeUsers (from compose_filters.js),
//               HAS_VALIDATED (from compose_validate.js)

// ── Serialize filter hidden fields before submit ─────────────────
$('#notifForm').on('submit', function() {
  if (typeof tinymce !== 'undefined') tinymce.triggerSave();
  $('#h_projects').val(JSON.stringify($('#f_project').val() || []));
  $('#h_allocations').val(JSON.stringify($('#f_allocation').val() || []));
  $('#h_depts').val(JSON.stringify($('#f_dept').val() || []));
  $('#h_resources').val(JSON.stringify($('#f_resource').val() || []));
  $('#h_statuses').val(JSON.stringify($('#f_status').val() || []));
  $('#h_roles').val(JSON.stringify($('#f_role').val() || []));
  if (typeof DRAFT_PK !== 'undefined' && DRAFT_PK) {
    $('#hidden_draft_pk').val(DRAFT_PK);
  }
});

// ── Select2 init ─────────────────────────────────────────────────
$(document).ready(function() {
  $('.s2').select2({
    allowClear: true,
    width: '100%',
    placeholder: function() { return $(this).data('placeholder'); }
  });

  // Auto-select template if ?template=ID is in the URL
  var params = new URLSearchParams(window.location.search);
  var templateId = params.get('template');
  if (templateId) {
    // Delay slightly to ensure TinyMCE is initialized
    setTimeout(function() {
      var $item = $('.tmpl-item[data-id="' + templateId + '"]');
      if ($item.length) {
        $('.tmpl-item').removeClass('active');
        $item.addClass('active');
        loadTemplate($item);
      }
    }, 500);
  }
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
