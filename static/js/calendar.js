(function () {
  'use strict';
  var filter = document.querySelector('[data-calendar-formals]');
  if (filter) filter.addEventListener('change', function () {
    if (filter.form.requestSubmit) filter.form.requestSubmit();
    else filter.form.submit();
  });
}());
