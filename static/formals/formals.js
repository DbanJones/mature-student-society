/* Progressive enhancement: every field remains usable without JavaScript. */
(function () {
  'use strict';
  document.querySelectorAll('.formal-application').forEach(function (form) {
    var places = form.querySelector('select[name="places"]');
    var names = form.querySelector('[data-guest-names]');
    var submit = form.querySelector('button[type="submit"]');
    if (!places) return;
    function updateGroup() {
      var count = Number(places.value) || 1;
      var nameInput = names && names.querySelector('textarea');
      if (names) names.hidden = count === 1 && !(nameInput && nameInput.value.trim()) && !names.querySelector('.has-error');
      if (submit) submit.textContent = form.dataset.bookingVerb + ' ' + count + (count === 1 ? ' place' : ' places');
    }
    places.addEventListener('change', updateGroup);
    updateGroup();
  });
  var options = document.getElementById('reciprocal-options');
  if (options) {
    var radios = document.querySelectorAll('input[name="invitation_type"]');
    function updateInvitation() {
      var selected = document.querySelector('input[name="invitation_type"]:checked');
      options.hidden = Boolean(selected && selected.value !== 'reciprocal');
      var deadline = options.querySelector('input[name="return_by"]');
      if (deadline) deadline.required = Boolean(selected && selected.value === 'reciprocal');
    }
    radios.forEach(function (radio) { radio.addEventListener('change', updateInvitation); });
    updateInvitation();
  }
  var dateRows = document.getElementById('additional-date-rows');
  var dateTemplate = document.getElementById('additional-date-template');
  var addDate = document.querySelector('[data-add-formal-date]');
  var totalDates = document.getElementById('id_dates-TOTAL_FORMS');
  if (dateRows && dateTemplate && addDate && totalDates) {
    var maxDates = 19;
    function prepareDateRow(row) {
      var remove = row.querySelector('[data-remove-formal-date]');
      if (remove) {
        remove.hidden = false;
        remove.addEventListener('click', function () {
          row.querySelectorAll('input').forEach(function (input) { input.value = ''; });
          row.hidden = true;
          addDate.hidden = false;
          addDate.focus();
        });
      }
    }
    dateRows.querySelectorAll('.formal-date-row').forEach(function (row, index) {
      var input = row.querySelector('input[type="date"]');
      row.hidden = index > 0 && input && !input.value && !row.classList.contains('has-error');
      prepareDateRow(row);
    });
    addDate.hidden = false;
    addDate.addEventListener('click', function () {
      var row = dateRows.querySelector('.formal-date-row[hidden]');
      if (!row) {
        var count = Number(totalDates.value);
        if (count >= maxDates) return;
        var holder = document.createElement('div');
        holder.innerHTML = dateTemplate.innerHTML.replace(/__prefix__/g, String(count));
        row = holder.firstElementChild;
        dateRows.appendChild(row);
        totalDates.value = String(count + 1);
        prepareDateRow(row);
      }
      row.hidden = false;
      var input = row.querySelector('input[type="date"]');
      if (input) input.focus();
      addDate.hidden = dateRows.querySelectorAll('.formal-date-row:not([hidden])').length >= maxDates;
    });
  }
  var errors = document.querySelector('[data-formal-errors]');
  if (errors) {
    errors.focus();
    errors.addEventListener('click', function (event) {
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
  }
}());
