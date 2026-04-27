// ── campaign_detail.js ──────────────────────────────────────────
// Expects globals set inline by the Django template:
//   CAMPAIGN_STATUS       - string ('queued', 'sending', 'sent', etc.)
//   CAMPAIGN_PROGRESS_URL - URL for the progress JSON endpoint

var STATUS_CLASSES = {
  sent:    'badge-success',
  sending: 'badge-primary',
  partial: 'badge-warning',
  queued:  'badge-info',
  draft:   'badge-secondary',
  failed:  'badge-danger'
};

function updateProgress(data) {
  CAMPAIGN_STATUS = data.status;
  $('#statusBadge')
    .text(data.status_display)
    .attr('class', 'badge ' + (STATUS_CLASSES[data.status] || 'badge-secondary'));
  $('#totalCount').text(data.recipient_count);
  $('#deliveredCount').text(data.delivered_count);
  $('#failedCount').text(data.failed_count);
  if (data.failed_count > 0) {
    $('#failedCount').css('color', '#dc3545');
  }
  $('#progressBar').css('width', data.delivery_pct + '%');
  $('#progressPct').text(data.delivery_pct + '% delivery rate');
  if (data.recipient_count > 0) {
    $('#progressWrap').show();
  }
}

function pollProgress() {
  if (CAMPAIGN_STATUS !== 'sending' && CAMPAIGN_STATUS !== 'queued') return;
  $.getJSON(CAMPAIGN_PROGRESS_URL, function(data) {
    updateProgress(data);
    if (data.status === 'sending' || data.status === 'queued') {
      setTimeout(pollProgress, 3000);
    } else {
      // Final state reached — reload to get the full log table.
      location.reload();
    }
  }).fail(function() {
    setTimeout(pollProgress, 5000);
  });
}

$(function(){
  // Only init DataTable when there are real data rows.
  var realRows = $('#logTable tbody tr td:not([colspan])').length;
  if (realRows > 0) {
    $('#logTable').DataTable({ order:[[2,'asc']], columnDefs:[{orderable:false,targets:[3]}] });
  }
  // Start polling if campaign is in-flight.
  if (CAMPAIGN_STATUS === 'sending' || CAMPAIGN_STATUS === 'queued') {
    setTimeout(pollProgress, 3000);
  }
});

$(document).on('click', '.view-email-btn', function() {
  $('#em-from').text($(this).attr('data-from'));
  $('#em-replyto').text($(this).attr('data-replyto') || '—');
  $('#em-to').text($(this).attr('data-email'));
  $('#em-subject').text($(this).attr('data-subject'));
  $('#em-body').text($(this).attr('data-body'));
  $('#emailViewModal').modal('show');
});
