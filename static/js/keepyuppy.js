/* MSS keepy-uppy ⚽ — the hidden football.
 *
 * Type  b a l l  anywhere (outside a text box) and a football drops onto the
 * page you're on. Keep it off the floor with your cursor; every touch is a
 * kick. Real-ish physics: gravity, drag, restitution, spin, and kicks whose
 * direction and power depend on where and how fast you hit the ball.
 * Escape (or ×) puts it away. Best scores go to /me/game/scores/.
 */
(function () {
  "use strict";
  if (window.__mssKeepyUppy) return;
  window.__mssKeepyUppy = true;

  var SCORES_URL = "/me/game/scores/";
  var R = 26;                 // ball radius (px)
  var GRAVITY = 2400;         // px/s²
  var DRAG = 0.18;            // air drag per second
  var WALL_BOUNCE = 0.78;
  var CURSOR_R = 16;          // effective cursor radius
  var overlay = null, raf = null, state = null;

  /* --- secret knock: type "ball" ------------------------------------------ */
  var typed = "";
  document.addEventListener("keydown", function (e) {
    var tag = (document.activeElement && document.activeElement.tagName) || "";
    if (/INPUT|TEXTAREA|SELECT/.test(tag) || document.activeElement.isContentEditable) return;
    if (e.key === "Escape" && overlay) { teardown(); return; }
    if (e.key === " " && overlay && state && !state.live) { e.preventDefault(); serve(); return; }
    if (!e.key || e.key.length !== 1) return;
    typed = (typed + e.key.toLowerCase()).slice(-4);
    if (typed === "ball" && !overlay) setup();
  });

  /* --- DOM ----------------------------------------------------------------- */
  function setup() {
    injectStyles();
    overlay = document.createElement("div");
    overlay.id = "ku-overlay";
    overlay.innerHTML =
      '<div id="ku-ball" aria-hidden="true">⚽</div>' +
      '<div id="ku-hud">' +
      '  <button id="ku-close" title="Put the ball away (Esc)">×</button>' +
      '  <h3>Keepy-uppy</h3>' +
      '  <div id="ku-score">0</div>' +
      '  <div id="ku-sub">personal best: <span id="ku-best">…</span></div>' +
      '  <button id="ku-serve" class="ku-btn">Kick off</button>' +
      '  <div id="ku-lb"><em>fetching leaderboard…</em></div>' +
      '  <div id="ku-hint">keep it up with your mouse · esc to hide</div>' +
      "</div>";
    document.body.appendChild(overlay);
    document.getElementById("ku-close").addEventListener("click", teardown);
    document.getElementById("ku-serve").addEventListener("click", serve);
    document.addEventListener("mousemove", onMouse, { passive: true });

    state = {
      x: innerWidth / 2, y: innerHeight - 200, vx: 0, vy: 0, rot: 0,
      live: false, score: 0, best: 0,
      mouse: { x: -999, y: -999, vx: 0, vy: 0, t: 0 },
      touching: false,
    };
    fetchScores();
    raf = requestAnimationFrame(tick);
  }

  function teardown() {
    if (raf) cancelAnimationFrame(raf);
    document.removeEventListener("mousemove", onMouse);
    if (overlay) overlay.remove();
    overlay = null; state = null; raf = null;
  }

  function onMouse(e) {
    if (!state) return;
    var now = performance.now();
    var dt = Math.max(8, now - state.mouse.t) / 1000;
    state.mouse.vx = (e.clientX - state.mouse.x) / dt;
    state.mouse.vy = (e.clientY - state.mouse.y) / dt;
    state.mouse.x = e.clientX;
    state.mouse.y = e.clientY;
    state.mouse.t = now;
  }

  function serve() {
    state.x = Math.min(Math.max(state.mouse.x || innerWidth / 2, 80), innerWidth - 80);
    state.y = innerHeight - 140;
    state.vx = (Math.random() - 0.5) * 160;
    state.vy = -1250;
    state.score = 0;
    state.live = true;
    state.touching = false;
    setText("ku-score", "0");
    document.getElementById("ku-serve").style.display = "none";
  }

  /* --- physics -------------------------------------------------------------- */
  var last = null;
  function tick(now) {
    raf = requestAnimationFrame(tick);
    if (!state) return;
    if (last == null) last = now;
    var dt = Math.min((now - last) / 1000, 0.033);
    last = now;

    if (state.live) {
      state.vy += GRAVITY * dt;
      state.vx *= 1 - DRAG * dt;
      state.x += state.vx * dt;
      state.y += state.vy * dt;
      state.rot += state.vx * dt * 0.9;

      // walls & ceiling
      if (state.x < R) { state.x = R; state.vx = Math.abs(state.vx) * WALL_BOUNCE; }
      if (state.x > innerWidth - R) { state.x = innerWidth - R; state.vx = -Math.abs(state.vx) * WALL_BOUNCE; }
      if (state.y < R) { state.y = R; state.vy = Math.abs(state.vy) * WALL_BOUNCE; }

      // cursor kick
      var dx = state.x - state.mouse.x, dy = state.y - state.mouse.y;
      var dist = Math.hypot(dx, dy);
      if (dist < R + CURSOR_R) {
        if (!state.touching) {
          state.touching = true;
          kick(dx, dy, dist);
        }
      } else if (dist > (R + CURSOR_R) * 1.35) {
        state.touching = false;
      }

      // floor = game over
      if (state.y > innerHeight - R) {
        state.y = innerHeight - R;
        gameOver();
      }
    }

    var ball = document.getElementById("ku-ball");
    if (ball) {
      ball.style.transform =
        "translate(" + (state.x - R) + "px," + (state.y - R) + "px) rotate(" + state.rot + "deg)";
    }
  }

  function kick(dx, dy, dist) {
    var nx = dist ? dx / dist : 0;
    var ny = dist ? dy / dist : -1;
    var mouseSpeed = Math.min(Math.hypot(state.mouse.vx, state.mouse.vy), 2600);
    var power = 620 + mouseSpeed * 0.35;
    state.vx = nx * power * 0.75 + state.mouse.vx * 0.22;
    // always send it meaningfully upward — it's keepy-uppy, not billiards
    state.vy = Math.min(ny, -0.45) * power - 260;
    state.vx = Math.max(-1400, Math.min(1400, state.vx));
    state.vy = Math.max(-1900, state.vy);
    state.score += 1;
    setText("ku-score", String(state.score));
    bump("ku-score");
  }

  function gameOver() {
    state.live = false;
    state.vx = 0; state.vy = 0;
    var serveBtn = document.getElementById("ku-serve");
    serveBtn.textContent = "Again (space)";
    serveBtn.style.display = "";
    if (state.score > 0) submit(state.score);
  }

  /* --- server ---------------------------------------------------------------- */
  function csrf() {
    var m = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
    return m ? m[1] : "";
  }
  function fetchScores() {
    fetch(SCORES_URL, { credentials: "same-origin" })
      .then(function (r) { return r.json(); })
      .then(renderScores)
      .catch(function () { setHTML("ku-lb", "<em>leaderboard unavailable</em>"); });
  }
  function submit(score) {
    fetch(SCORES_URL, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrf() },
      body: JSON.stringify({ score: score }),
    })
      .then(function (r) { return r.json(); })
      .then(renderScores)
      .catch(function () {});
  }
  function renderScores(data) {
    state.best = data.best || 0;
    setText("ku-best", String(state.best));
    var rows = (data.leaderboard || []).map(function (row, i) {
      return "<li" + (row.me ? ' class="me"' : "") + "><span>" +
        (i + 1) + ". " + escapeHtml(row.name) + "</span><b>" + row.score + "</b></li>";
    });
    setHTML("ku-lb", rows.length
      ? "<ol>" + rows.join("") + "</ol>"
      : "<em>no scores yet — be first!</em>");
  }

  /* --- helpers ----------------------------------------------------------------- */
  function setText(id, s) { var n = document.getElementById(id); if (n) n.textContent = s; }
  function setHTML(id, s) { var n = document.getElementById(id); if (n) n.innerHTML = s; }
  function bump(id) {
    var n = document.getElementById(id);
    if (!n) return;
    n.classList.remove("ku-bump");
    void n.offsetWidth;
    n.classList.add("ku-bump");
  }
  function escapeHtml(s) {
    var d = document.createElement("div");
    d.textContent = s == null ? "" : String(s);
    return d.innerHTML;
  }

  function injectStyles() {
    if (document.getElementById("ku-styles")) return;
    var css =
      "#ku-overlay{position:fixed;inset:0;z-index:9999;pointer-events:none}" +
      "#ku-ball{position:fixed;left:0;top:0;width:52px;height:52px;font-size:46px;" +
      "line-height:52px;text-align:center;will-change:transform;" +
      "filter:drop-shadow(0 6px 8px rgba(0,0,0,.28))}" +
      "#ku-hud{position:fixed;top:76px;right:16px;width:210px;pointer-events:auto;" +
      "background:rgba(255,255,255,.96);border:1px solid #e2e8e2;border-radius:14px;" +
      "box-shadow:0 8px 28px rgba(20,53,42,.18);padding:.8rem 1rem;font-size:.85rem;color:#1a2420}" +
      "#ku-hud h3{margin:0 0 .1rem;font-size:1rem;color:#14352a}" +
      "#ku-score{font-size:2.2rem;font-weight:700;color:#14352a;line-height:1.1}" +
      "#ku-score.ku-bump{animation:kuBump .18s ease}" +
      "@keyframes kuBump{50%{transform:scale(1.25);color:#b82818}}" +
      "#ku-sub{color:#5f6d64;margin-bottom:.5rem}" +
      ".ku-btn{background:#1c4a39;color:#fff;border:0;border-radius:8px;padding:.4rem .9rem;" +
      "font:inherit;font-weight:600;cursor:pointer;width:100%;margin-bottom:.5rem}" +
      ".ku-btn:hover{background:#276349}" +
      "#ku-lb ol{margin:.2rem 0 0;padding:0;list-style:none;max-height:11rem;overflow-y:auto}" +
      "#ku-lb li{display:flex;justify-content:space-between;gap:.6rem;padding:.12rem 0;" +
      "border-bottom:1px dashed #e2e8e2}" +
      "#ku-lb li.me{color:#b82818;font-weight:700}" +
      "#ku-hint{margin-top:.5rem;color:#5f6d64;font-size:.72rem}" +
      "#ku-close{position:absolute;top:.35rem;right:.55rem;background:none;border:0;" +
      "font-size:1.15rem;color:#5f6d64;cursor:pointer;padding:0}" +
      "#ku-close:hover{color:#b82818}";
    var style = document.createElement("style");
    style.id = "ku-styles";
    style.textContent = css;
    document.head.appendChild(style);
  }
})();
