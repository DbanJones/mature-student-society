(function () {
  'use strict';
  var summary = document.querySelector('[data-error-summary]');
  if (!summary) return;
  summary.focus();
  summary.addEventListener('click', function (event) {
    var link = event.target.closest('a[href^="#"]');
    if (!link) return;
    var target = document.getElementById(link.hash.slice(1));
    if (!target) return;
    var parent = target.parentElement;
    while (parent) {
      if (parent.tagName === 'DETAILS') parent.open = true;
      parent = parent.parentElement;
    }
    var input = target.matches('input, select, textarea') ? target : target.querySelector('input, select, textarea');
    if (input) input.focus();
  });
}());
