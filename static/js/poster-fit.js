/* Fit poster text. Every line was laid out with the bundled fonts; a viewer
   who gets a substitute font (static files missing on the server, say) would
   see wider text run off the sheet. Once the fonts have settled, any line
   wider than the width it was laid out for (data-max on its <text>) is
   squeezed to that width. A line that fits is never touched, so nothing is
   ever stretched. */
(function () {
  "use strict";
  var current = null;

  function fit(root) {
    root.querySelectorAll("text[data-max]").forEach(function (text) {
      var max = parseFloat(text.getAttribute("data-max"));
      if (!(max > 0)) return;
      text.querySelectorAll("tspan").forEach(function (span) {
        span.removeAttribute("textLength");
        span.removeAttribute("lengthAdjust");
        var natural;
        try { natural = span.getComputedTextLength(); } catch (e) { return; }
        if (natural > max * 1.01) {
          span.setAttribute("textLength", max.toFixed(3));
          span.setAttribute("lengthAdjust", "spacingAndGlyphs");
        }
      });
    });
  }

  function run() {
    if (!current) return;
    requestAnimationFrame(function () { fit(current); });
  }

  if (document.fonts && document.fonts.addEventListener) {
    document.fonts.addEventListener("loadingdone", run);  // the real font arrived: measure again
  }
  window.fitPosterText = function (root) {
    current = root || document;
    if (document.fonts && document.fonts.ready) { document.fonts.ready.then(run, run); } else { run(); }
  };
})();
