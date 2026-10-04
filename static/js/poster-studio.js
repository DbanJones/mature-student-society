/* The poster studio: live preview, a photo from this device, the focal
   point, and PNG export. Printing works without any of this. */
(function () {
  "use strict";
  var form = document.getElementById("poster-form");
  var preview = document.getElementById("preview");
  if (!form || !preview) return;
  var slug = preview.dataset.slug;
  var svgUrl = preview.dataset.svgUrl;
  var printUrl = preview.dataset.printUrl;
  var printLink = document.getElementById("print-link");
  var svgLink = document.getElementById("svg-link");
  var pngBtn = document.getElementById("png-btn");
  var saveForm = document.getElementById("save-form");
  var fileInput = document.getElementById("device-photo");
  var clearBtn = document.getElementById("clear-photo");
  var photoFlag = document.getElementById("device-photo-flag");
  var mapNote = document.getElementById("map-note");
  var photoNote = document.getElementById("photo-note");
  var storeKey = "poster-photo:" + slug;
  var devicePhoto = null;   // {dataUrl, w, h}
  var photoKept = true;     // false: the photo is in the preview but not saved for printing
  var timer = null;

  function query() { return new URLSearchParams(new FormData(form)).toString(); }

  function updateLinks() {
    var q = query();
    var printQuery = photoKept ? q : q.replace(/(^|&)device_photo=1(?=&|$)/, "$1device_photo=");
    printLink.href = printUrl + "?" + printQuery + "&print=1";
    if (svgLink) svgLink.href = svgUrl + "?" + q + "&download=1";
    if (saveForm) {
      saveForm.querySelectorAll("input[type=hidden]").forEach(function (h) {
        var el = form.elements[h.name];
        if (!el) return;
        if (el.type === "checkbox") h.value = el.checked ? "on" : "";
        else if (el.length && el[0] && el[0].type === "radio") { var c = form.querySelector('[name="' + h.name + '"]:checked'); h.value = c ? c.value : ""; }
        else h.value = el.value;
      });
    }
  }

  /* Place the device photo so it covers its panel, centred on the focal point. */
  function placePhoto() {
    var img = preview.querySelector("#photo");
    if (!img || !devicePhoto) return;
    var p = img.dataset.panel.split(" ").map(Number);
    var fx = parseFloat(form.elements.focal_x.value);
    var fy = parseFloat(form.elements.focal_y.value);
    if (isNaN(fx)) fx = 0.5;
    if (isNaN(fy)) fy = 0.5;
    var scale = Math.max(p[2] / devicePhoto.w, p[3] / devicePhoto.h);
    var w = devicePhoto.w * scale, h = devicePhoto.h * scale;
    var x = p[0] + p[2] / 2 - fx * w, y = p[1] + p[3] / 2 - fy * h;
    x = Math.min(p[0], Math.max(p[0] + p[2] - w, x));
    y = Math.min(p[1], Math.max(p[1] + p[3] - h, y));
    img.setAttribute("href", devicePhoto.dataUrl);
    img.setAttribute("x", x); img.setAttribute("y", y);
    img.setAttribute("width", w); img.setAttribute("height", h);
    img.removeAttribute("display");
  }

  /* Geoapify can take a while to draw a map the first time: if the map
     fails to arrive, ask again a few times before giving up. */
  function watchMap() {
    preview.querySelectorAll("image[data-map]").forEach(function (im) {
      var tries = 0;
      im.addEventListener("error", function () {
        if (tries >= 4 || !im.isConnected) return;
        tries += 1;
        setTimeout(function () {
          if (!im.isConnected) return;  // a refresh has replaced it
          var href = (im.getAttribute("href") || "").replace(/&retry=\d+$/, "");
          im.setAttribute("href", href + "&retry=" + tries);
        }, 4000);
      });
    });
  }

  function showNote(note) {
    if (!mapNote) return;
    mapNote.textContent = note;
    mapNote.hidden = !note;
  }

  function refresh() {
    preview.classList.add("busy");
    fetch(svgUrl + "?" + query(), { credentials: "same-origin" })
      .then(function (r) {
        var note = r.headers.get("X-Map-Note");
        showNote(note ? decodeURIComponent(note) : "");
        return r.text();
      })
      .then(function (svg) { preview.innerHTML = svg; placePhoto(); bindFocal(); watchMap(); })
      .finally(function () { preview.classList.remove("busy"); updateLinks(); });
  }

  /* The layout keeps a picture panel when there's a photo from this
     device (otherwise the map would take the panel). */
  function setPhotoFlag(on) {
    if (!photoFlag || photoFlag.value === (on ? "1" : "")) return false;
    photoFlag.value = on ? "1" : "";
    return true;
  }
  function scheduleRefresh() { clearTimeout(timer); timer = setTimeout(refresh, 250); }

  /* Click or drag on the picture to move its focal point. */
  function bindFocal() {
    var img = preview.querySelector("#photo");
    var svg = preview.querySelector("svg");
    if (!img || !svg) return;
    var dragging = false;
    function setFocal(ev) {
      var p = img.dataset.panel.split(" ").map(Number);
      var pt = svg.createSVGPoint(); pt.x = ev.clientX; pt.y = ev.clientY;
      var loc = pt.matrixTransform(svg.getScreenCTM().inverse());
      var fx = Math.min(1, Math.max(0, (loc.x - p[0]) / p[2]));
      var fy = Math.min(1, Math.max(0, (loc.y - p[1]) / p[3]));
      form.elements.focal_x.value = fx.toFixed(3); form.elements.focal_y.value = fy.toFixed(3);
      if (devicePhoto) { placePhoto(); updateLinks(); } else { scheduleRefresh(); }
    }
    img.addEventListener("pointerdown", function (ev) { dragging = true; img.setPointerCapture(ev.pointerId); setFocal(ev); });
    img.addEventListener("pointermove", function (ev) { if (dragging) setFocal(ev); });
    img.addEventListener("pointerup", function () { dragging = false; });
  }

  /* A phone photo can be several megabytes, more than the browser will
     keep: hold a copy no longer than 2000 px on its long edge instead. */
  function shrink(im, dataUrl) {
    var longest = Math.max(im.naturalWidth, im.naturalHeight);
    if (longest <= 2000 && dataUrl.length <= 1500000) {
      return { dataUrl: dataUrl, w: im.naturalWidth, h: im.naturalHeight };
    }
    var scale = Math.min(1, 2000 / longest);
    var canvas = document.createElement("canvas");
    canvas.width = Math.round(im.naturalWidth * scale);
    canvas.height = Math.round(im.naturalHeight * scale);
    canvas.getContext("2d").drawImage(im, 0, 0, canvas.width, canvas.height);
    return { dataUrl: canvas.toDataURL("image/jpeg", 0.85), w: canvas.width, h: canvas.height };
  }

  /* Keep the photo for the print page, making room by forgetting photos
     kept for other posters if need be. */
  function keep(photo) {
    var value = JSON.stringify(photo);
    for (var attempt = 0; attempt < 2; attempt++) {
      try { localStorage.setItem(storeKey, value); return true; } catch (e) {
        try {
          Object.keys(localStorage).forEach(function (key) {
            if (key.indexOf("poster-photo:") === 0 && key !== storeKey) localStorage.removeItem(key);
          });
        } catch (e2) { return false; }
      }
    }
    return false;
  }

  function showPhotoNote(text) {
    if (!photoNote) return;
    photoNote.textContent = text;
    photoNote.hidden = !text;
  }

  function loadPhoto(file) {
    var reader = new FileReader();
    reader.onload = function () {
      var im = new Image();
      im.onload = function () {
        devicePhoto = shrink(im, reader.result);
        photoKept = keep(devicePhoto);
        showPhotoNote(photoKept ? "" : "This photo is too large for this browser to keep, so Print won't include it. Save image will, or choose a smaller photo.");
        clearBtn.hidden = false;
        if (setPhotoFlag(true)) refresh(); else { placePhoto(); bindFocal(); updateLinks(); }
      };
      im.src = reader.result;
    };
    reader.readAsDataURL(file);
  }
  if (fileInput) fileInput.addEventListener("change", function () { if (fileInput.files[0]) loadPhoto(fileInput.files[0]); });
  if (clearBtn) clearBtn.addEventListener("click", function () {
    devicePhoto = null; try { localStorage.removeItem(storeKey); } catch (e) {}
    photoKept = true; showPhotoNote("");
    clearBtn.hidden = true; fileInput.value = ""; setPhotoFlag(false); refresh();
  });
  try {
    var kept = JSON.parse(localStorage.getItem(storeKey) || "null");
    if (kept && kept.dataUrl) {
      devicePhoto = kept; clearBtn.hidden = false;
      if (setPhotoFlag(true)) refresh(); else placePhoto();
    }
  } catch (e) { /* ignore */ }

  form.addEventListener("input", scheduleRefresh);
  form.addEventListener("change", scheduleRefresh);
  form.addEventListener("submit", function (ev) { ev.preventDefault(); refresh(); });
  bindFocal(); watchMap(); updateLinks();

  /* PNG export: inline the fonts and every image into a copy of the SVG,
     draw it on a canvas at the output size, and hand the file to the browser. */
  var FONTS = [
    ["Libre Caslon Text", "/static/fonts/LibreCaslonText-Variable.ttf", "400 700"],
    ["Source Sans 3", "/static/fonts/SourceSans3-Variable.ttf", "200 900"]
  ];
  var fontCss = null;
  function toDataUrl(url) {
    return fetch(url, { credentials: "same-origin" }).then(function (r) { return r.blob(); }).then(function (b) {
      return new Promise(function (resolve) { var fr = new FileReader(); fr.onload = function () { resolve(fr.result); }; fr.readAsDataURL(b); });
    });
  }
  function embeddedFonts() {
    if (fontCss) return Promise.resolve(fontCss);
    return Promise.all(FONTS.map(function (f) {
      return toDataUrl(f[1]).then(function (d) {
        return "@font-face{font-family:\"" + f[0] + "\";src:url(" + d + ") format(\"truetype\");font-weight:" + f[2] + ";}";
      });
    })).then(function (parts) { fontCss = parts.join(""); return fontCss; });
  }
  function exportPng() {
    var svg = preview.querySelector("svg");
    if (!svg) return;
    pngBtn.disabled = true; pngBtn.textContent = "Making the image…";
    var clone = svg.cloneNode(true);
    var images = Array.prototype.slice.call(clone.querySelectorAll("image"));
    var work = images.map(function (im) {
      var href = im.getAttribute("href") || "";
      if (!href || href.indexOf("data:") === 0) return Promise.resolve();
      return toDataUrl(href).then(function (d) { im.setAttribute("href", d); }).catch(function () { im.remove(); });
    });
    Promise.all(work.concat([embeddedFonts()])).then(function () {
      var style = document.createElementNS("http://www.w3.org/2000/svg", "style");
      style.textContent = fontCss;
      clone.insertBefore(style, clone.firstChild);
      var vb = (clone.getAttribute("viewBox") || "0 0 210 297").split(" ").map(Number);
      var sizeKey = (form.querySelector('[name="size"]:checked') || {}).value || "a4";
      var targetW = { a4: 2480, a3: 3508, square: 1080, story: 1080 }[sizeKey] || 2480;
      var scale = targetW / vb[2];
      var canvas = document.createElement("canvas");
      canvas.width = Math.round(vb[2] * scale); canvas.height = Math.round(vb[3] * scale);
      var ctx = canvas.getContext("2d");
      var blob = new Blob([new XMLSerializer().serializeToString(clone)], { type: "image/svg+xml;charset=utf-8" });
      var url = URL.createObjectURL(blob);
      var img = new Image();
      img.onload = function () {
        ctx.fillStyle = "#fbfaf5"; ctx.fillRect(0, 0, canvas.width, canvas.height);
        ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
        URL.revokeObjectURL(url);
        canvas.toBlob(function (out) {
          var a = document.createElement("a");
          a.href = URL.createObjectURL(out);
          a.download = slug + "-" + sizeKey + ".png";
          document.body.appendChild(a); a.click(); a.remove();
          setTimeout(function () { URL.revokeObjectURL(a.href); }, 5000);
          pngBtn.disabled = false; pngBtn.textContent = "Save image";
        }, "image/png");
      };
      img.onerror = function () { pngBtn.disabled = false; pngBtn.textContent = "Save image (failed, try again)"; };
      img.src = url;
    });
  }
  if (pngBtn) { pngBtn.hidden = false; pngBtn.addEventListener("click", exportPng); }
})();
