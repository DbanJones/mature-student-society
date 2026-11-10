(function () {
  'use strict';
  var filter = document.querySelector('[data-calendar-formals]');
  if (filter) filter.addEventListener('change', function () {
    if (filter.form.requestSubmit) filter.form.requestSubmit();
    else filter.form.submit();
  });

  // Picking a tag reloads the page with tags=open#tag-filter, so the tag list
  // stays open and in view. Once the page has settled, take both out of the
  // address bar, so a copied link opens the calendar the ordinary way.
  window.addEventListener('load', function () {
    if (!window.URLSearchParams || !window.history || !history.replaceState) return;
    var params = new URLSearchParams(location.search);
    var atFilter = location.hash === '#tag-filter';
    if (params.get('tags') !== 'open' && !atFilter) return;
    params.delete('tags');
    var query = params.toString();
    var hash = atFilter ? '' : location.hash;
    setTimeout(function () {
      history.replaceState(history.state, '', location.pathname + (query ? '?' + query : '') + hash);
    }, 0);
  });
}());
