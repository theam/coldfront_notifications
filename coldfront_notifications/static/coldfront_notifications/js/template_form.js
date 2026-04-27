// ── template_form.js ────────────────────────────────────────────
// Expects the following global set inline by the Django template:
//   ALL_VARS - array of variable objects
//   IS_EDIT  - boolean, true when editing an existing template

function renderVars(filter) {
  var $list = $('#varsList').empty();
  var q = (filter || '').toLowerCase();
  var filtered = q
    ? ALL_VARS.filter(function(v) {
        return (v.key || '').toLowerCase().indexOf(q) !== -1
            || (v.label || '').toLowerCase().indexOf(q) !== -1; })
    : ALL_VARS;
  if (!filtered.length) {
    $list.append('<div class="vars-empty">No matches</div>');
    return;
  }
  filtered.forEach(function(v) {
    var token = '{{' + v.key + '}}';
    var tag = v.source === 'manual' ? ' [manual]' : '';
    var $btn = $('<button type="button" class="var-btn"></button>')
      .attr('data-var', token)
      .attr('title', (v.description || v.label || '') + (v.example ? ' — ' + v.example : ''))
      .html(
        '<span style="display:block;font-family:monospace;font-size:.7rem;">' + token + tag + '</span>' +
        '<span style="display:block;font-size:.65rem;color:#6c757d;margin-top:1px;">' + (v.example || '') + '</span>'
      );
    $list.append($btn);
  });
}

function renderVarRef() {
  var $body = $('#varRefBody').empty();
  if (!ALL_VARS.length) {
    $body.append('<tr><td colspan="3" class="text-muted text-center py-3">No variables defined.</td></tr>');
    return;
  }
  ALL_VARS.forEach(function(v) {
    var badgeClass = v.source === 'manual' ? 'badge-warning' : 'badge-success';
    var badgeText  = v.source === 'manual' ? 'Manual' : 'Query';
    $body.append(
      '<tr>'
      + '<td style="font-family:monospace;">{{' + v.key + '}}</td>'
      + '<td><span class="badge ' + badgeClass + '" style="font-size:.62rem;">' + badgeText + '</span></td>'
      + '<td>' + (v.example || '—') + '</td>'
      + '</tr>'
    );
  });
}

$(document).on('click', '.var-btn', function() {
  var ta    = document.getElementById('id_body');
  var token = $(this).attr('data-var');
  var s = ta.selectionStart, e = ta.selectionEnd;
  ta.value = ta.value.substring(0, s) + token + ta.value.substring(e);
  ta.selectionStart = ta.selectionEnd = s + token.length;
  ta.focus();
});

$(document).on('input', '#varSearch', function() {
  renderVars($(this).val());
});

$(document).ready(function() { renderVars(''); renderVarRef(); });

if (!IS_EDIT) {
  $(document).on('input', '#id_name', function() {
    var slug = $(this).val().toLowerCase()
      .replace(/[^a-z0-9]+/g, '-')
      .replace(/^-|-$/g, '');
    $('#id_slug').val(slug);
  });
}
