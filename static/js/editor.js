/* The Markdown editor: a toolbar above any textarea marked data-editor, a
   link to the Pictures library, and a live preview rendered by the server
   with the same sanitising as the page itself. Without JavaScript the
   textarea works as before.

   Pictures: a textarea with data-upload="/pictures/upload/" gets an "Add a
   picture" button that uploads a file and puts its line in the text
   (data-upload-max is the size limit in MB). Without data-upload the button
   reads "Picture" and only inserts the shape of the line.

   Two optional attributes, both used by the What's On mailer:
   data-editor-tools="Bold,Link"  shows only those buttons (so no picture
   button unless "Picture" is listed; email carries no pictures).
   data-editor-frame="subject,audience"  says the server answers with a whole
   page (an email). It is shown in a frame that can run nothing, open from
   the start; the text is sent under the textarea's own name, with the named
   fields of the same form beside it. */
(function () {
  "use strict";
  var TOOLS = [
    ["Bold", "**", "**", "bold text"],
    ["Italic", "*", "*", "italic text"],
    ["Heading", "## ", "", "Heading"],
    ["List", "- ", "", "item"],
    ["Quote", "> ", "", "quotation"],
    ["Link", "[", "](https://)", "link text"]
  ];
  var GONE = "Preview unavailable (are you still logged in?).";
  var FAILED = "The picture could not be added. Are you still logged in?";
  var uploadCount = 0;

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

  // Put a line of its own (a picture) where the cursor is.
  function insertBlock(area, text) {
    var start = area.selectionStart;
    var before = area.value.slice(0, start);
    var lead = !before || /\n\n$/.test(before) ? "" : (/\n$/.test(before) ? "\n" : "\n\n");
    area.focus();
    area.setRangeText(lead + text + "\n\n", start, area.selectionEnd, "end");
    area.dispatchEvent(new Event("input", { bubbles: true }));
  }

  // The "Add a picture" panel. Its two boxes have no name, so they are never
  // sent with the page's own form.
  function uploader(area, toggle) {
    var max = parseFloat(area.dataset.uploadMax || "5");
    var id = "editor-upload-" + (++uploadCount);
    var panel = document.createElement("div");
    panel.className = "editor-upload";
    panel.hidden = true;
    panel.innerHTML =
      '<label for="' + id + '-file">Picture <input type="file" id="' + id + '-file" accept="image/jpeg,image/png,image/webp,image/gif"></label>' +
      '<label for="' + id + '-alt">What the picture shows <input type="text" id="' + id + '-alt" maxlength="140"></label>' +
      '<button type="button" class="btn btn-small">Add it</button>' +
      '<button type="button" class="btn btn-ghost btn-small">Cancel</button>' +
      '<p class="small muted" role="status"></p>';
    var file = panel.querySelector("input[type=file]");
    var alt = panel.querySelector("input[type=text]");
    var add = panel.querySelectorAll("button")[0];
    var cancel = panel.querySelectorAll("button")[1];
    var status = panel.querySelector("[role=status]");

    function close() {
      file.value = ""; alt.value = ""; status.textContent = "";
      panel.hidden = true;
      toggle.setAttribute("aria-expanded", "false");
    }
    function send() {
      var picked = file.files && file.files[0];
      if (!picked) { status.textContent = "Choose a picture first."; return; }
      if (picked.size > max * 1024 * 1024) {
        status.textContent = "That picture is over " + max + " MB. Please choose a smaller one."; return;
      }
      if (!alt.value.trim()) { status.textContent = "Please describe the picture."; alt.focus(); return; }
      var data = new FormData();
      data.append("image", picked);
      data.append("alt", alt.value.trim());
      status.textContent = "Adding the picture…";
      add.disabled = true;
      // No Content-Type header: the browser writes the multipart one itself.
      fetch(area.dataset.upload, {
        method: "POST", body: data, credentials: "same-origin", redirect: "error",
        headers: { "X-CSRFToken": csrfToken(area) }
      }).then(function (response) {
        return response.json().then(function (body) { return { ok: response.ok, body: body }; });
      }).then(function (result) {
        add.disabled = false;
        if (!result.ok) { status.textContent = result.body.error || FAILED; return; }
        insertBlock(area, result.body.markdown);
        close();
      }).catch(function () { add.disabled = false; status.textContent = FAILED; });
    }
    toggle.setAttribute("aria-expanded", "false");
    toggle.addEventListener("click", function () {
      panel.hidden = !panel.hidden;
      toggle.setAttribute("aria-expanded", String(!panel.hidden));
      if (!panel.hidden) { file.focus(); }
    });
    add.addEventListener("click", send);
    cancel.addEventListener("click", function () { close(); area.focus(); });
    // Enter in the description would otherwise submit the page's own form.
    alt.addEventListener("keydown", function (event) {
      if (event.key === "Enter") { event.preventDefault(); send(); }
    });
    return panel;
  }

  function build(area) {
    var only = (area.dataset.editorTools || "").split(",").filter(Boolean);
    var framed = area.dataset.editorFrame !== undefined;
    var extra = (area.dataset.editorFrame || "").split(",").filter(Boolean);

    var bar = document.createElement("div");
    bar.className = "editor-toolbar";
    TOOLS.forEach(function (tool) {
      if (only.length && only.indexOf(tool[0]) === -1) return;
      var button = document.createElement("button");
      button.type = "button";
      button.textContent = tool[0];
      button.addEventListener("click", function () { wrapSelection(area, tool[1], tool[2], tool[3]); });
      bar.appendChild(button);
    });
    // Pictures: never in an email (framed), nor where the tool list leaves it out.
    var uploadPanel = null;
    if (!framed && (!only.length || only.indexOf("Picture") !== -1)) {
      var picture = document.createElement("button");
      picture.type = "button";
      if (area.dataset.upload) {
        picture.textContent = "Add a picture";
        uploadPanel = uploader(area, picture);
      } else {
        picture.textContent = "Picture";
        picture.addEventListener("click", function () {
          wrapSelection(area, "![", "](https://)", "what the picture shows");
        });
      }
      bar.appendChild(picture);
    }
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
    toggle.textContent = framed ? "Hide preview" : "Show preview";
    bar.appendChild(toggle);
    area.parentNode.insertBefore(bar, area);
    if (uploadPanel) area.parentNode.insertBefore(uploadPanel, area);

    var preview;
    if (framed) {
      preview = document.createElement("iframe");
      preview.className = "editor-preview-frame";
      preview.title = "Email preview";
      // No allow-scripts: nothing in the frame can run. allow-same-origin only
      // lets this script read how far down the admin has scrolled.
      preview.setAttribute("sandbox", "allow-same-origin allow-popups allow-popups-to-escape-sandbox");
      area.parentNode.appendChild(preview);  // under the help text, not between it and the box
    } else {
      preview = document.createElement("div");
      preview.className = "editor-preview prose";
      preview.hidden = true;
      area.parentNode.insertBefore(preview, area.nextSibling);
    }

    function show(html) {
      if (!framed) { preview.innerHTML = html; return; }
      var top = 0;
      try { top = preview.contentWindow.scrollY || 0; } catch (e) { top = 0; }
      preview.onload = function () {
        try { preview.contentWindow.scrollTo(0, top); } catch (e) { /* stays at the top */ }
      };
      preview.srcdoc = html;
    }
    function say(message) {
      if (!framed) { preview.textContent = message; return; }
      var line = document.createElement("p");
      line.textContent = message;
      show('<body style="margin:16px;font-family:sans-serif">' + line.outerHTML);
    }
    function payload() {
      if (!framed) return "text=" + encodeURIComponent(area.value);
      var parts = [encodeURIComponent(area.name) + "=" + encodeURIComponent(area.value)];
      extra.forEach(function (name) {
        var field = area.form && area.form.elements[name];
        if (field) parts.push(encodeURIComponent(name) + "=" + encodeURIComponent(field.value || ""));
      });
      return parts.join("&");
    }

    var timer = null, latest = 0;
    function refresh() {
      var ticket = ++latest;
      fetch(area.dataset.editor, {
        method: "POST",
        credentials: "same-origin",
        redirect: "error",  // a redirect means the session has gone: never show the login page here
        headers: { "X-CSRFToken": csrfToken(area), "Content-Type": "application/x-www-form-urlencoded" },
        body: payload()
      }).then(function (r) {
        return r.text().then(function (body) { return { ok: r.ok, status: r.status, body: body }; });
      }).then(function (got) {
        if (ticket !== latest) return;  // an older render must not overwrite a newer one
        if (got.ok) { show(got.body); return; }
        say(got.status === 413 || got.status === 429 ? got.body : GONE);
      }).catch(function () {
        if (ticket === latest) say(GONE);
      });
    }
    function later() {
      if (preview.hidden) return;
      clearTimeout(timer);
      timer = setTimeout(refresh, 400);
    }
    toggle.addEventListener("click", function () {
      preview.hidden = !preview.hidden;
      toggle.textContent = preview.hidden ? "Show preview" : "Hide preview";
      if (!preview.hidden) refresh();
    });
    area.addEventListener("input", later);
    if (framed) {
      // The subject and the audience change the email too.
      ["input", "change"].forEach(function (kind) {
        area.form && area.form.addEventListener(kind, function (e) {
          if (e.target !== area && extra.indexOf(e.target.name) !== -1) later();
        });
      });
      refresh();
    }
  }

  document.querySelectorAll("textarea[data-editor]").forEach(build);
})();
