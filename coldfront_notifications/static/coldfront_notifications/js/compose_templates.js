// ── compose_templates.js ────────────────────────────────────────
// Template list, search, loading, variable chips, and variable insert.
//
// Globals used: VARIABLES, VAR_BY_KEY, URLS (from Django template),
//               HAS_VALIDATED, runValidation (from compose_validate.js),
//               setDedupeUsers (from compose_filters.js)

// Index VARIABLES by key for O(1) lookup
var VAR_BY_KEY = {};
VARIABLES.forEach(function(v) { VAR_BY_KEY[v.key] = v; });

// ── Render chip list ─────────────────────────────────────────────
var ACTIVE_VARS = [];

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
  $.getJSON(URLS.templateJson.replace('{id}', id), function(t) {
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
