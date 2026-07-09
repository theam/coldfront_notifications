// tinymce_init.js — Shared TinyMCE configuration for email body editors.
//
// Initializes TinyMCE on #id_body with a limited toolbar suitable for
// email content (bold, italic, underline, links, lists).
//
// Provides getBodyContent() and setBodyContent(html) helpers for other
// modules that need to read/write the editor programmatically.

function getBodyContent() {
  var editor = tinymce.get('id_body');
  return editor ? editor.getContent() : ($('#id_body').val() || '');
}

function setBodyContent(html) {
  var editor = tinymce.get('id_body');
  if (editor) {
    editor.setContent(html || '');
  } else {
    $('#id_body').val(html || '');
  }
}

$(document).ready(function() {
  if (typeof tinymce === 'undefined') return;

  tinymce.init({
    selector: '#id_body',
    menubar: false,
    statusbar: false,
    toolbar: 'bold italic underline | link unlink | bullist numlist | undo redo | removeformat',
    plugins: 'link lists',
    width: '100%',
    height: 350,
    content_style: 'body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; font-size: 14px; }',
    link_default_target: '_blank',
    forced_root_block: 'p',
    setup: function(editor) {
      editor.on('change keyup', function() {
        editor.save();
      });
    }
  });
});
