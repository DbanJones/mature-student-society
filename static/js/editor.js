/* The Markdown editor: a toolbar above any textarea marked data-editor, a
   link to the Pictures library, and a live preview rendered by the server
   with the same sanitising as the page itself. Without JavaScript the
   textarea works as before. */
(function () {
  "use strict";
  var TOOLS = [
    ["Bold", "**", "**", "bold text"],
    ["Italic", "*", "*", "italic text"],
    ["Heading", "## ", "", "Heading"],
    ["List", "- ", "", "item"],
    ["Quote", "> ", "", "quotation"],
    ["Link", "[", "](https://)", "link text"],
    ["Picture", "![", "](/media/public/pictures/name.jpg)", "what the picture shows"]
  ];

  function csrfToken(area) {
    var input = area.form && area.form.querySelector("input[name=csrfmiddlewaretoken]");
    return input ? input.value : "";
  }

  function wrapSelection(area, before, after, placeholder) {
    var start = area.selectionStart, end = area.selectionEnd;
    var chosen = area.value.slice(start, end) || placeholder;
    // Headings, lists and quotes only work at the start of a line.
    var lineStart = /[#\->] $/.test(before);
    var prefix = lineStart && start > 0 && area.value[start - 1] !== "\n" ? "\n" : "";
    area.setRangeText(prefix + before + chosen + after, start, end, "select");
    area.setSelectionRange(start + prefix.length + before.length, start + prefix.length + before.length + chosen.length);
    area.focus();
    area.dispatchEvent(new Event("input", { bubbles: true }));
  }

  function build(area) {
    var bar = document.createElement("div");
    bar.className = "editor-toolbar";
    TOOLS.forEach(function (tool) {
      var button = document.createElement("button");
      button.type = "button";
      button.textContent = tool[0];
      button.addEventListener("click", function () { wrapSelection(area, tool[1], tool[2], tool[3]); });
      bar.appendChild(button);
    });
    if (area.dataset.pictures) {
      var pictures = document.createElement("a");
      pictures.className = "editor-pictures";
      pictures.href = area.dataset.pictures;
      pictures.target = "_blank";
      pictures.rel = "noopener";
      pictures.textContent = "Pictures library";
      bar.appendChild(pictures);
    }
    var toggle = document.createElement("button");
    toggle.type = "button";
    toggle.className = "editor-preview-toggle";
    toggle.textContent = "Show preview";
    bar.appendChild(toggle);
    area.parentNode.insertBefore(bar, area);

    var preview = document.createElement("div");
    preview.className = "editor-preview prose";
    preview.hidden = true;
    area.parentNode.insertBefore(preview, area.nextSibling);

    var timer = null, latest = 0;
    function refresh() {
      var ticket = ++latest;
      fetch(area.dataset.editor, {
        method: "POST",
        credentials: "same-origin",
        redirect: "error",  // a redirect means the session has gone: never show the login page here
        headers: { "X-CSRFToken": csrfToken(area), "Content-Type": "application/x-www-form-urlencoded" },
        body: "text=" + encodeURIComponent(area.value)
      }).then(function (r) {
        return r.text().then(function (body) { return { ok: r.ok, status: r.status, body: body }; });
      }).then(function (got) {
        if (ticket !== latest) return;  // an older render must not overwrite a newer one
        if (got.ok) { preview.innerHTML = got.body; return; }
        preview.textContent = got.status === 413 || got.status === 429 ? got.body : "Preview unavailable (are you still logged in?).";
      }).catch(function () {
        if (ticket === latest) preview.textContent = "Preview unavailable (are you still logged in?).";
      });
    }
    toggle.addEventListener("click", function () {
      preview.hidden = !preview.hidden;
      toggle.textContent = preview.hidden ? "Show preview" : "Hide preview";
      if (!preview.hidden) refresh();
    });
    area.addEventListener("input", function () {
      if (preview.hidden) return;
      clearTimeout(timer);
      timer = setTimeout(refresh, 400);
    });
  }

  document.querySelectorAll("textarea[data-editor]").forEach(build);
})();
