// compose_draft.js — Draft auto-save, resume, and dirty tracking.
//
// Loaded after compose_filters.js and before compose_init.js.
//
// Globals used: DRAFT_DATA, URLS, FILTER_DATA, FILTERS, FilterStore,
//               collectFilters (from compose_filters.js)
// Globals exported: DRAFT_PK, isDraftDirty, restoreDraft

var DRAFT_PK = null;
var _draftDirty = false;
var _autoSaveInterval = null;
var AUTO_SAVE_DELAY_MS = 30000;

function isDraftDirty() {
  return _draftDirty;
}

function _markDirty() {
  _draftDirty = true;
  $('#draftStatus').text('Unsaved changes').css('color', '#856404');
}

function _markClean(timestamp) {
  _draftDirty = false;
  var time = timestamp || new Date().toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'});
  $('#draftStatus').text('Draft saved at ' + time).css('color', '#28a745');
}

function _collectDraftData() {
  return {
    csrfmiddlewaretoken: $('input[name=csrfmiddlewaretoken]').val(),
    draft_pk: DRAFT_PK || '',
    subject: $('#id_subject').val() || '',
    body: getBodyContent() || '',
    sender: $('#id_sender').val() || '',
    reply_to: $('#id_reply_to').val() || '',
    template_id: $('#hidden_template_id').val() || '',
    filters: JSON.stringify(collectFilters()),
    extra_context: JSON.stringify({})
  };
}

function saveDraft(callback) {
  var data = _collectDraftData();

  $('#saveDraftBtn').prop('disabled', true).html('<i class="fas fa-spinner fa-spin mr-1"></i>Saving...');

  $.ajax({
    url: URLS.draftSave,
    type: 'POST',
    data: data,
    success: function(response) {
      DRAFT_PK = response.pk;
      $('#hidden_draft_pk').val(DRAFT_PK);
      _markClean();
      if (callback) callback(true);
    },
    error: function() {
      $('#draftStatus').text('Save failed').css('color', '#dc3545');
      if (callback) callback(false);
    },
    complete: function() {
      $('#saveDraftBtn').prop('disabled', false).html('<i class="fas fa-save mr-1"></i>Save Draft');
    }
  });
}

function _autoSave() {
  if (_draftDirty) {
    saveDraft();
  }
}

function _startAutoSave() {
  if (_autoSaveInterval) return;
  _autoSaveInterval = setInterval(_autoSave, AUTO_SAVE_DELAY_MS);
}

function _stopAutoSave() {
  if (_autoSaveInterval) {
    clearInterval(_autoSaveInterval);
    _autoSaveInterval = null;
  }
}

function restoreDraft(draftData) {
  if (!draftData) return;

  DRAFT_PK = draftData.pk;
  $('#hidden_draft_pk').val(DRAFT_PK);

  // Restore form fields
  if (draftData.subject) $('#id_subject').val(draftData.subject);
  if (draftData.body) setBodyContent(draftData.body);
  if (draftData.sender) $('#id_sender').val(draftData.sender);
  if (draftData.reply_to) $('#id_reply_to').val(draftData.reply_to);
  if (draftData.template_id) $('#hidden_template_id').val(draftData.template_id);

  // Restore filter selections
  var filters = draftData.filters || {};
  var filterMap = {
    departments: '#f_dept',
    projects: '#f_project',
    resources: '#f_resource',
    statuses: '#f_status',
    allocations: '#f_allocation',
    roles: '#f_role'
  };

  // Wait for Select2 and FilterStore to be initialized
  setTimeout(function() {
    // Check if draft used direct mode
    if (filters.selection_mode === 'direct' && typeof switchSelectionMode === 'function') {
      switchSelectionMode('direct');
      var directPks = filters.direct_user_pks || [];
      if (directPks.length && typeof restoreDirectMode === 'function') {
        restoreDirectMode(directPks);
      }
    } else {
      for (var filterName in filterMap) {
        var values = filters[filterName] || [];
        if (!values.length) continue;

        var state = FilterStore.state[filterName];
        if (!state) continue;

        // Coerce types to match the option IDs
        var firstOption = state.all.length ? state.all[0] : null;
        var useInt = firstOption && typeof firstOption.id === 'number';
        if (useInt) {
          values = values.map(function(value) { return parseInt(value, 10); });
        }

        state.selected = values;
        if (FILTERS[filterName]) {
          FILTERS[filterName].render();
          FILTERS[filterName].dispatchChanged();
        }
      }
    }

    // Restore extra recipients
    if (filters.extra_recipients && filters.extra_recipients.length) {
      $('textarea[name="extra_recipients"]').val(filters.extra_recipients.join('\n'));
    }

    // Restore dedupe users
    if (filters.dedupe_users && filters.dedupe_users.length) {
      $('#h_dedupe_users').val(JSON.stringify(filters.dedupe_users));
    }

    // Highlight the matching template in the sidebar
    if (draftData.template_id) {
      var $templateItem = $('.tmpl-item[data-id="' + draftData.template_id + '"]');
      if ($templateItem.length) {
        $templateItem.addClass('active');
        $('#active-slug').text($templateItem.data('slug'));
      }
    }

    _markClean();
    _updateSummaries();
  }, 200);
}

// Dirty tracking — mark dirty on any form input change
$(document).on('input change', '#id_subject, #id_body, #id_sender, #id_reply_to, textarea[name="extra_recipients"]', _markDirty);
$(document).on('change', '.s2', function() {
  // Only mark dirty from user-driven changes (not programmatic)
  if (!$(this).data('restoring')) {
    _markDirty();
  }
});

// Save Draft button
$(document).on('click', '#saveDraftBtn', function() {
  saveDraft();
});

// Beforeunload warning
$(window).on('beforeunload', function() {
  if (_draftDirty) {
    return 'You have unsaved changes. Leave anyway?';
  }
});

// Clear beforeunload on form submit (send)
$('#notifForm').on('submit', function() {
  _draftDirty = false;
  _stopAutoSave();
});

// Initialize on document ready
$(document).ready(function() {
  if (typeof DRAFT_DATA !== 'undefined' && DRAFT_DATA) {
    restoreDraft(DRAFT_DATA);
  }
  _startAutoSave();
});
