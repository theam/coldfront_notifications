// ── datatables_init.js ──────────────────────────────────────────
// Shared DataTable initializer for list pages.
// Expects the following global set inline by the Django template:
//   DT_CONFIG - object with: { tableId, order, nonOrderableTargets, paging, searching, info }
// All fields except tableId are optional.

// Fix DataTables "Show entries" dropdown and search layout
$('<style>')
  .text(
    '.dataTables_length label { display:inline-flex; align-items:center; gap:6px; white-space:nowrap; font-size:.82rem; margin:8px 0 8px 8px; }' +
    '.dataTables_length select { width:52px !important; padding:2px 4px; font-size:.82rem; height:auto !important; }' +
    '.dataTables_filter label { display:inline-flex; align-items:center; gap:6px; white-space:nowrap; font-size:.82rem; margin:8px 8px 8px 0; }' +
    '.dataTables_filter input { width:auto !important; font-size:.82rem; }' +
    '.dataTables_info { margin-left:8px; }' +
    '.dataTables_paginate { flex-wrap:wrap; }'
  )
  .appendTo('head');

$(function() {
  if (typeof DT_CONFIG === 'undefined') return;

  // Skip init when the table has no real data rows (only a colspan empty-state row)
  var $table = $('#' + DT_CONFIG.tableId);
  var realRows = $table.find('tbody tr td:not([colspan])').length;
  if (!realRows) return;

  var opts = {
    order: DT_CONFIG.order || [[0, 'asc']]
  };

  if (DT_CONFIG.nonOrderableTargets) {
    opts.columnDefs = [{ orderable: false, targets: DT_CONFIG.nonOrderableTargets }];
  }

  if (DT_CONFIG.paging === false) opts.paging = false;
  if (DT_CONFIG.searching === false) opts.searching = false;
  if (DT_CONFIG.info === false) opts.info = false;

  $('#' + DT_CONFIG.tableId).DataTable(opts);
});
