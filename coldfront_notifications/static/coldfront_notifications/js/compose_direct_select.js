// compose_direct_select.js — Direct user selection mode.
//
// Provides a search autocomplete, chip display, and bulk-paste
// resolution for selecting individual users as recipients.
//
// Globals used: URLS, _invalidateValidation (from compose_filters.js)
// Globals exported: SELECTION_MODE, SELECTED_USERS, switchSelectionMode

var SELECTION_MODE = 'filters';
var SELECTED_USERS = {};   // pk → {pk, username, email, full_name}

var _searchDebounce = null;
var _searchCache = null;   // cache for the initial (empty-query) results


// ── Shared accessors (used by compose_filters, compose_preview, etc.) ──

function getSelectionMode() {
  return SELECTION_MODE;
}

function getDirectUserPks() {
  return Object.keys(SELECTED_USERS).map(Number);
}


// ── Mode switching ─────────────────────────────────────────────────

function switchSelectionMode(mode) {
  SELECTION_MODE = mode;
  $('#h_selection_mode').val(mode);

  if (mode === 'direct') {
    $('#filterModePanel').hide();
    $('#directModePanel').show();
    $('#modeFilters').removeClass('active');
    $('#modeDirect').addClass('active');
    $('#clearFiltersBtn').hide();
    // Pre-populate search results on first switch
    if (!_searchCache) {
      _fetchUsers('', function(results) {
        _searchCache = results;
        _renderSearchResults(results);
        $('#directSearchResults').show();
      });
    }
  } else {
    $('#directModePanel').hide();
    $('#filterModePanel').show();
    $('#modeDirect').removeClass('active');
    $('#modeFilters').addClass('active');
  }
  _invalidateValidation();
}

$(document).on('click', '#modeFilters', function() {
  if (SELECTION_MODE !== 'filters') switchSelectionMode('filters');
});
$(document).on('click', '#modeDirect', function() {
  if (SELECTION_MODE !== 'direct') switchSelectionMode('direct');
});


// ── User search autocomplete ───────────────────────────────────────

function _fetchUsers(query, callback) {
  $.ajax({
    url: URLS.userSearch,
    type: 'GET',
    data: { q: query, limit: 100 },
    success: function(data) {
      callback(data.results || []);
    },
    error: function() {
      callback([]);
    }
  });
}

function _renderSearchResults(results) {
  var $container = $('#directSearchResults');
  if (!results.length) {
    $container.html('<div class="dsr-empty">No users found</div>');
    $container.show();
    return;
  }

  var html = '';
  for (var i = 0; i < results.length; i++) {
    var u = results[i];
    if (SELECTED_USERS[u.pk]) continue;  // skip already-selected
    var escaped_name = $('<span>').text(u.full_name).html();
    var escaped_user = $('<span>').text(u.username).html();
    var escaped_email = $('<span>').text(u.email).html();
    html += '<div class="dsr-item" data-pk="' + u.pk + '"'
      + ' data-username="' + u.username + '"'
      + ' data-email="' + u.email + '"'
      + ' data-fullname="' + $('<span>').text(u.full_name).html() + '">'
      + '<span class="dsr-name">' + escaped_name + '</span>'
      + '<span class="dsr-detail">' + escaped_user + ' &middot; ' + escaped_email + '</span>'
      + '</div>';
  }
  $container.html(html || '<div class="dsr-empty">All matching users already selected</div>');
  $container.show();
}

$(document).on('input', '#directUserSearch', function() {
  var q = $(this).val().trim();
  clearTimeout(_searchDebounce);

  if (!q) {
    // Show cached initial results
    if (_searchCache) {
      _renderSearchResults(_searchCache);
    } else {
      _fetchUsers('', function(results) {
        _searchCache = results;
        _renderSearchResults(results);
      });
    }
    return;
  }

  _searchDebounce = setTimeout(function() {
    _fetchUsers(q, function(results) {
      _renderSearchResults(results);
    });
  }, 300);
});

$(document).on('focus', '#directUserSearch', function() {
  var q = $(this).val().trim();
  if (!q && _searchCache) {
    _renderSearchResults(_searchCache);
  } else if (!q) {
    _fetchUsers('', function(results) {
      _searchCache = results;
      _renderSearchResults(results);
    });
  } else {
    // Re-show existing results
    $('#directSearchResults').show();
  }
});

// Close dropdown when clicking outside
$(document).on('mousedown', function(e) {
  if (!$(e.target).closest('.direct-search-wrap').length) {
    $('#directSearchResults').hide();
  }
});

// Select a user from search results
$(document).on('click', '.dsr-item', function() {
  var pk = parseInt($(this).data('pk'), 10);
  if (SELECTED_USERS[pk]) return;

  SELECTED_USERS[pk] = {
    pk: pk,
    username: $(this).data('username'),
    email: $(this).data('email'),
    full_name: $(this).data('fullname')
  };

  $(this).remove();
  _renderChips();
  _syncHiddenField();
  _invalidateValidation();
  $('#directUserSearch').val('').focus();
});


// ── Chip rendering ─────────────────────────────────────────────────

function _renderChips() {
  var pks = Object.keys(SELECTED_USERS);
  var html = '';
  for (var i = 0; i < pks.length; i++) {
    var u = SELECTED_USERS[pks[i]];
    var escaped = $('<span>').text(u.username).html();
    html += '<span class="user-chip" data-pk="' + u.pk + '" title="' + $('<span>').text(u.email).html() + '">'
      + escaped
      + ' <i class="fas fa-times user-chip-remove"></i>'
      + '</span>';
  }
  $('#directUserChips').html(html);
  $('#directUserCount').text(pks.length ? pks.length + ' user(s) selected' : '');
}

$(document).on('click', '.user-chip-remove', function(e) {
  e.stopPropagation();
  var pk = parseInt($(this).closest('.user-chip').data('pk'), 10);
  delete SELECTED_USERS[pk];
  _renderChips();
  _syncHiddenField();
  _invalidateValidation();
});


// ── Bulk paste ─────────────────────────────────────────────────────

$(document).on('click', '#directBulkResolveBtn', function() {
  var raw = $('#directBulkPaste').val().trim();
  if (!raw) return;

  var $btn = $(this);
  $btn.prop('disabled', true).html('<i class="fas fa-spinner fa-spin mr-1"></i>Resolving…');

  $.ajax({
    url: URLS.userBulkResolve,
    type: 'POST',
    data: {
      csrfmiddlewaretoken: $('input[name=csrfmiddlewaretoken]').val(),
      identifiers: raw
    },
    success: function(data) {
      var added = 0;
      var found = data.found || [];
      for (var i = 0; i < found.length; i++) {
        if (!SELECTED_USERS[found[i].pk]) {
          SELECTED_USERS[found[i].pk] = found[i];
          added++;
        }
      }
      _renderChips();
      _syncHiddenField();
      _invalidateValidation();
      _searchCache = null; // invalidate cache since selections changed

      var feedbackHtml = '';
      if (added) {
        feedbackHtml += '<span class="text-success">' + added + ' user(s) added.</span>';
      }
      var notFound = data.not_found || [];
      if (notFound.length) {
        feedbackHtml += ' <span class="text-danger">' + notFound.length + ' not found: '
          + notFound.map(function(id) { return '<code>' + $('<span>').text(id).html() + '</code>'; }).join(', ')
          + '</span>';
      }
      $('#directBulkFeedback').html(feedbackHtml);
      if (!notFound.length) {
        $('#directBulkPaste').val('');
      }
    },
    error: function() {
      $('#directBulkFeedback').html('<span class="text-danger">Request failed — please try again.</span>');
    },
    complete: function() {
      $btn.prop('disabled', false).html('<i class="fas fa-search mr-1"></i>Resolve');
    }
  });
});


// ── Hidden field sync ──────────────────────────────────────────────

function _syncHiddenField() {
  $('#h_direct_user_pks').val(JSON.stringify(getDirectUserPks()));
}


// ── Restore direct mode from draft ─────────────────────────────────

function restoreDirectMode(userPks) {
  if (!userPks || !userPks.length) return;

  // Fetch full details for the stored PKs via bulk resolve.
  // We send PKs as "identifiers" — the search endpoint won't match by PK,
  // so instead fetch all and match client-side, with a fallback request
  // for any PKs not found in the initial batch.
  var remaining = userPks.slice();

  function _resolveBySearch(query, callback) {
    $.ajax({
      url: URLS.userSearch,
      type: 'GET',
      data: { q: query, limit: 200 },
      success: function(data) { callback(data.results || []); },
      error: function() { callback([]); }
    });
  }

  _resolveBySearch('', function(results) {
    var byPk = {};
    for (var i = 0; i < results.length; i++) byPk[results[i].pk] = results[i];

    var unresolved = [];
    for (var j = 0; j < remaining.length; j++) {
      var pk = remaining[j];
      if (byPk[pk]) {
        SELECTED_USERS[pk] = byPk[pk];
      } else {
        unresolved.push(pk);
      }
    }

    // For any PKs not in the first batch, fetch individually
    if (unresolved.length) {
      var done = 0;
      for (var k = 0; k < unresolved.length; k++) {
        (function(upk) {
          _resolveBySearch(String(upk), function(hits) {
            for (var h = 0; h < hits.length; h++) {
              if (hits[h].pk === upk) { SELECTED_USERS[upk] = hits[h]; break; }
            }
            done++;
            if (done === unresolved.length) { _renderChips(); _syncHiddenField(); }
          });
        })(unresolved[k]);
      }
    }

    _renderChips();
    _syncHiddenField();
  });
}
