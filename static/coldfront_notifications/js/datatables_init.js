// ── datatables_init.js ──────────────────────────────────────────
// Shared DataTable initializer for list pages.
// Expects the following global set inline by the Django template:
//   DT_CONFIG - object with: { tableId, order, nonOrderableTargets, paging, searching, info }
// All fields except tableId are optional.

$(function() {
  if (typeof DT_CONFIG === 'undefined') return;

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
