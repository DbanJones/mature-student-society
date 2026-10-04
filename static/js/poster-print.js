/* The printable poster: drop in a photo chosen in the studio (it lives in
   this browser's storage and is never uploaded), wait for every picture,
   the map included, and for the fonts; then print if the studio asked.
   Without JavaScript the page still prints, just without a device photo. */
(function () {
  "use strict";
  var sheet = document.querySelector(".sheet");
  var svg = sheet && sheet.querySelector("svg");
  if (!svg) return;

  function placeDevicePhoto() {
    var slot = svg.querySelector("#photo");
    if (!slot || !sheet.dataset.devicePhoto) return;
    var kept = null;
    try { kept = JSON.parse(localStorage.getItem("poster-photo:" + sheet.dataset.slug) || "null"); } catch (e) { kept = null; }
    if (!kept || !kept.dataUrl) return;
    var p = slot.dataset.panel.split(" ").map(Number);
    var fx = parseFloat(sheet.dataset.focalX), fy = parseFloat(sheet.dataset.focalY);
    if (isNaN(fx)) fx = 0.5;
    if (isNaN(fy)) fy = 0.5;
    var scale = Math.max(p[2] / kept.w, p[3] / kept.h);
    var w = kept.w * scale, h = kept.h * scale;
    var x = Math.min(p[0], Math.max(p[0] + p[2] - w, p[0] + p[2] / 2 - fx * w));
    var y = Math.min(p[1], Math.max(p[1] + p[3] - h, p[1] + p[3] / 2 - fy * h));
    slot.setAttribute("href", kept.dataUrl);
    slot.setAttribute("x", x); slot.setAttribute("y", y);
    slot.setAttribute("width", w); slot.setAttribute("height", h);
    slot.removeAttribute("display");
  }

  /* Resolves once the picture has loaded, or after a couple of retries for
     a map that Geoapify is still drawing. Never rejects. */
  function settle(image) {
    var href = image.getAttribute("href") || "";
    if (!href || href.indexOf("data:") === 0 || image.getAttribute("display") === "none") return Promise.resolve();
    var tries = image.hasAttribute("data-map") ? 2 : 0;
    return new Promise(function (resolve) {
      function attempt(url) {
        var probe = new Image();
        probe.onload = function () { if (url !== href) image.setAttribute("href", url); resolve(); };
        probe.onerror = function () {
          if (tries-- > 0) { setTimeout(function () { attempt(href + "&retry=" + (2 - tries)); }, 4000); }
          else resolve();
        };
        probe.src = url;
      }
      attempt(href);
    });
  }

  placeDevicePhoto();
  var images = Array.prototype.slice.call(svg.querySelectorAll("image"));
  var ready = Promise.all(images.map(settle).concat([document.fonts ? document.fonts.ready : Promise.resolve()]));
  var limit = new Promise(function (resolve) { setTimeout(resolve, 30000); });
  if (sheet.dataset.autoPrint) {
    Promise.race([ready, limit]).then(function () { setTimeout(function () { window.print(); }, 300); });
  }
})();
