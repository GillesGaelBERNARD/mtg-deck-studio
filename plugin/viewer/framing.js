(function () {
  "use strict";
  var V = window.mtgViewer;
  var MAX_ATTEMPTS = 20;
  // Below this the deck reads as a field of dots; a narrow pane cannot fit 100 cards
  // legibly, so the table opens at a readable scale anchored on real cards instead.
  var READABLE_SCALE = 0.42;
  var MIN_SCALE = 0.08;
  var MAX_SCALE = 1.35;

  function controls() {
    return document.getElementById("controls");
  }

  function viewport() {
    return document.getElementById("viewport").getBoundingClientRect();
  }

  // Area the table may use: right of the panel, above the tabs when they sit at the
  // bottom of a narrow pane.
  function safeRect() {
    var panel = controls().getBoundingClientRect();
    var tabs = document.getElementById("global-view-tabs").getBoundingClientRect();
    var collapsed = controls().classList.contains("collapsed");
    var left = Math.min(window.innerWidth - 160, collapsed ? panel.right + 12 : panel.right + 16);
    var tabsAtTop = tabs.height > 0 && tabs.top < window.innerHeight * 0.4;
    var top = Math.max(12, tabsAtTop ? tabs.bottom + 12 : 12);
    var bottom = tabsAtTop ? window.innerHeight - 12 : Math.max(top + 120, tabs.top - 12);
    return { left: left, top: top, width: Math.max(180, window.innerWidth - left - 12), height: Math.max(120, bottom - top) };
  }

  // Only ever close the panel automatically (a narrow pane needs the room); never
  // open it again, or a reframe would undo the reader's click on the close button.
  function setPanelCollapsed(want) {
    if (!want) return;
    var toggle = document.getElementById("toggle-panel");
    if (toggle && !controls().classList.contains("collapsed")) toggle.click();
  }

  function applyScale(scale) {
    if (!isFinite(scale) || Math.abs(scale - V.state.camera.scale) < 0.002) return;
    V.state.camera.scale = Math.max(MIN_SCALE, Math.min(MAX_SCALE, scale));
    V.applyCamera();
    V.layoutCards();
  }

  function cardBoxes() {
    return Array.prototype.map.call(document.querySelectorAll(".card"), function (card) {
      var r = card.getBoundingClientRect();
      return r.width > 1 && r.height > 1 ? r : null;
    }).filter(Boolean);
  }

  // Section and mana labels are drawn above the cards; framing to the cards alone
  // pushes the writing over the top edge of the pane.
  function labelBoxes() {
    return Array.prototype.map.call(document.querySelectorAll(".category-label, .mana-label"), function (node) {
      if (node.hidden) return null;
      var r = node.getBoundingClientRect();
      return r.width > 1 && r.height > 1 ? r : null;
    }).filter(Boolean);
  }

  function cardBounds() {
    var boxes = cardBoxes().concat(labelBoxes());
    if (!boxes.length) return null;
    return {
      left: Math.min.apply(null, boxes.map(function (r) { return r.left; })),
      top: Math.min.apply(null, boxes.map(function (r) { return r.top; })),
      right: Math.max.apply(null, boxes.map(function (r) { return r.right; })),
      bottom: Math.max.apply(null, boxes.map(function (r) { return r.bottom; }))
    };
  }

  function visibleCards() {
    var vp = viewport();
    return cardBoxes().filter(function (r) {
      return r.right > vp.left && r.left < vp.right && r.bottom > vp.top && r.top < vp.bottom;
    }).length;
  }

  function cardsOutside() {
    var vp = viewport();
    return cardBoxes().some(function (r) {
      return r.left < vp.left - 2 || r.top < vp.top - 2 || r.right > vp.right + 2 || r.bottom > vp.bottom + 2;
    });
  }

  // Put the box at the safe area's top-left, centred when it is narrower than the area.
  function shiftInto(box, safe, pad) {
    if (!box) return false;
    var wide = box.right - box.left;
    var wantX = wide > safe.width - pad * 2 ? safe.left + pad : safe.left + (safe.width - wide) / 2;
    var wantY = safe.top + pad;
    var dx = Math.round(wantX - box.left);
    var dy = Math.round(wantY - box.top);
    if (!dx && !dy) return false;
    V.state.camera.x += dx;
    V.state.camera.y += dy;
    V.applyCamera();
    return true;
  }

  function fitBox(box, safe, pad, ceiling) {
    var wide = Math.max(1, box.right - box.left);
    var tall = Math.max(1, box.bottom - box.top);
    var current = V.state.camera.scale || 1;
    var fitted = Math.min((safe.width - pad * 2) / wide, (safe.height - pad * 2) / tall);
    applyScale(Math.min(ceiling || 1, current * fitted));
    return true;
  }

  function frameTable() {
    var safe = safeRect();
    V.fitLoadedCards();
    var box = cardBounds();
    if (!box) return false;
    if (box.right - box.left < 4 || box.bottom - box.top < 4) return false;
    // Fit-all only when it stays readable; otherwise keep cards legible and let the
    // table pan from its top-left corner.
    if (V.state.camera.scale < READABLE_SCALE) {
      applyScale(READABLE_SCALE);
      box = cardBounds();
      if (!box) return false;
    }
    shiftInto(box, safe, 16);
    for (var pass = 0; pass < 2; pass++) {
      box = cardBounds();
      if (!box || !cardsOutside()) break;
      shiftInto(box, safe, 16);
    }
    return true;
  }

  function target(mode) {
    var table = document.getElementById("table");
    // The whole dashboard scrolls, not just the overview block at its top.
    if (mode === "stats") return table.querySelector(".stats-dashboard-board") || table.querySelector('[data-stats-focus="overview"]');
    if (mode === "buckets") return table.querySelector(".bucket-category-board");
    return null;
  }

  // Scale and shift until the element sits inside the safe area. The board is laid out
  // again after every camera change, so measure fresh each pass instead of trusting the
  // rect that was measured before the scale moved.
  function frameElement(element, safe, pad, maxScale) {
    if (!element) return false;
    var scale = V.state.camera.scale || 1;
    for (var pass = 0; pass < 6; pass++) {
      var r = element.getBoundingClientRect();
      if (r.width < 2 || r.height < 2) return false;
      var roomW = safe.width - pad * 2;
      var roomH = safe.height - pad * 2;
      var overW = r.width > roomW;
      var overH = r.height > roomH;
      var inside = r.left >= safe.left - 1 && r.top >= safe.top - 1 &&
        r.right <= safe.left + safe.width + 1 && r.bottom <= safe.top + safe.height + 1;
      if (inside && !overW && !overH) return true;
      if (overW || overH) {
        var factor = Math.min(overW ? roomW / r.width : 1, overH ? roomH / r.height : 1);
        var next = Math.max(MIN_SCALE, Math.min(maxScale || 1, scale * factor));
        if (Math.abs(next - scale) >= 0.002) {
          scale = next;
          V.state.camera.scale = scale;
          V.applyCamera();
          V.layoutCards();
          continue;
        }
      }
      var wantTop = r.height < roomH - 8 ? safe.top + pad + (roomH - r.height) / 2 : safe.top + pad;
      V.state.camera.x += Math.round(safe.left + pad - r.left);
      V.state.camera.y += Math.round(wantTop - r.top);
      V.applyCamera();
    }
    var end = element.getBoundingClientRect();
    return end.left >= safe.left - 2 && end.top >= safe.top - 2;
  }

  // The role board container keeps a fixed pixel box while the cards inside scale, so
  // framing it shrinks the cards to dots. Frame the cards and leave room for the
  // category header above them.
  // The role header block is laid out outside the scaled card layer, so it keeps its
  // pixel size while the cards scale. Measure the gap between the two instead of
  // treating the header as part of the card bounds.
  // The roles view stacks a header, a role profile and a toolbar above the cards, in
  // containers whose own box does not always cover that writing; take the highest
  // visible piece so the framing starts at the writing, not at the first card.
  function bucketHeaderTop() {
    var table = document.getElementById("table");
    var nodes = table.querySelectorAll(
      ".bucket-category-board, .bucket-profile-board, .bucket-profile-header, " +
      ".bucket-category-header, .bucket-toolbar, .bucket-config, .bucket-overview-controls"
    );
    var top = null;
    for (var i = 0; i < nodes.length; i++) {
      var rect = nodes[i].getBoundingClientRect();
      if (rect.height < 2 || rect.width < 2) continue;
      top = top === null ? rect.top : Math.min(top, rect.top);
    }
    if (top === null) {
      var board = target("buckets");
      top = board ? board.getBoundingClientRect().top : null;
    }
    return top;
  }

  // Topmost piece of writing in the view, ignoring the panel and the tab strip. Used
  // as a last check: whatever container holds the role header, it must start inside
  // the pane.
  function topmostWritingTop() {
    var table = document.getElementById("table");
    if (!table) return null;
    var nodes = table.querySelectorAll("h1, h2, h3, h4, p, span, button, .card, .category-label, .mana-label");
    var top = null;
    for (var i = 0; i < nodes.length; i++) {
      var el = nodes[i];
      if (el.closest("#controls, #global-view-tabs, #strategy-workspace, #deck-title-banner")) continue;
      var rect = el.getBoundingClientRect();
      if (rect.width < 2 || rect.height < 2) continue;
      if (rect.bottom < 0 || rect.top > window.innerHeight) continue;
      top = top === null ? rect.top : Math.min(top, rect.top);
    }
    return top;
  }

  function bucketHeaderGap() {
    var top = bucketHeaderTop();
    var cards = cardBounds();
    if (top === null || !cards) return 0;
    return Math.max(0, Math.round(cards.top - top));
  }

  // Roles keeps its category header, description and sub-role chips above the cards;
  // framing the cards alone pushed that writing over the top edge. Frame the whole
  // block from its top-left corner and let it run past the bottom of the pane.
  function frameRoleCards() {
    var safe = safeRect();
    // The role board re-lays out on every camera change, so fit by measuring again
    // each pass instead of trusting one scale computation.
    for (var pass = 0; pass < 4; pass++) {
      var box = cardBounds();
      if (!box) return false;
      var wide = Math.max(1, box.right - box.left);
      if (wide <= safe.width - 26 || (V.state.camera.scale || 1) <= MIN_SCALE + 0.001) break;
      var factor = (safe.width - 26) / wide;
      V.state.camera.scale = Math.max(MIN_SCALE, Math.min(1, (V.state.camera.scale || 1) * factor));
      V.applyCamera();
      V.layoutCards();
    }
    // Placing is iterative for the same reason: the board re-lays out after each
    // camera change, so the header lands short of the top on the first move.
    for (var settle = 0; settle < 4; settle++) {
      var bounds = cardBounds();
      if (!bounds) return false;
      var width = bounds.right - bounds.left;
      var wantX = width >= safe.width - 26 ? safe.left + 13 : safe.left + (safe.width - width) / 2;
      var dx = Math.round(wantX - bounds.left);
      var dy = Math.round(safe.top + 12 + bucketHeaderGap() - bounds.top);
      if (!dx && !dy) break;
      V.state.camera.x += dx;
      V.state.camera.y += dy;
      V.applyCamera();
    }
    clampPan();
    return true;
  }

  function frameStats() {
    var safe = safeRect();
    var board = target("stats");
    if (!board) return false;
    var pad = 14;
    var r = board.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) return false;
    var fit = (safe.width - pad * 2) / r.width;
    var scale = Math.max(MIN_SCALE, Math.min(1, fit));
    if (Math.abs(scale - (V.state.camera.scale || 1)) >= 0.002) {
      V.state.camera.scale = scale;
      V.applyCamera();
      V.layoutCards();
    }
    r = board.getBoundingClientRect();
    V.state.camera.x += Math.round(safe.left + pad - r.left);
    V.state.camera.y += Math.round(safe.top + pad - r.top);
    V.applyCamera();
    clampPan();
    return true;
  }

  function place(element, maxScale) {
    if (!element) return false;
    var safe = safeRect();
    var ok = frameElement(element, safe, 14, maxScale || 1);
    // A role board is a container whose cards are positioned absolutely; when the
    // container is framed but the cards sit elsewhere, frame the cards themselves.
    if (visibleCards() === 0) {
      var cards = cardBounds();
      if (cards) {
        fitBox(cards, safe, 14, maxScale || 1);
        shiftInto(cardBounds(), safe, 14);
        shiftInto(cardBounds(), safe, 14);
      }
    }
    return ok;
  }

  // Each view keeps the camera the user left it at, so a tab switch lands where the
  // work was instead of between two scales.
  var saved = {};

  function saveCamera(mode) {
    if (!mode || mode === "strategy") return;
    var c = V.state.camera;
    saved[mode] = { scale: c.scale, x: c.x, y: c.y };
  }

  function restoreCamera(mode) {
    var c = saved[mode];
    if (!c || !isFinite(c.scale)) return false;
    var before = { scale: V.state.camera.scale, x: V.state.camera.x, y: V.state.camera.y };
    V.state.camera.scale = c.scale;
    V.state.camera.x = c.x;
    V.state.camera.y = c.y;
    V.applyCamera();
    V.layoutCards();
    if (mode === "table" && visibleCards() === 0) {
      V.state.camera.scale = before.scale;
      V.state.camera.x = before.x;
      V.state.camera.y = before.y;
      V.applyCamera();
      V.layoutCards();
      return false;
    }
    return true;
  }

  // Panning must stop at the content's edge: the deck can never be dragged past the
  // top or bottom of the pane, and a board smaller than the pane stays centred.
  function contentBox() {
    var mode = V.state.viewMode;
    if (mode === "stats") {
      var board = target("stats");
      return board ? board.getBoundingClientRect() : null;
    }
    return cardBounds();
  }

  function clampPan() {
    if (V.state.viewMode === "strategy") return;
    var safe = safeRect();
    var box = contentBox();
    if (!box) return;
    var m = 12;
    var wide = box.right - box.left;
    var tall = box.bottom - box.top;
    var dx = 0;
    var dy = 0;
    if (wide <= safe.width - m * 2) {
      dx = Math.round(safe.left + (safe.width - wide) / 2 - box.left);
    } else {
      if (box.left > safe.left + m) dx = Math.round(safe.left + m - box.left);
      if (box.right < safe.left + safe.width - m) dx = Math.round(safe.left + safe.width - m - box.right);
    }
    var top = safe.top + (V.state.viewMode === "buckets" ? bucketHeaderGap() : 0);
    var room = Math.max(80, safe.height - (top - safe.top));
    if (V.state.viewMode === "stats" || V.state.viewMode === "buckets") {
      // A page, not a canvas: the board sits at the top edge and only scrolls. Roles
      // keeps its header above the cards, so free space never lifts the content back
      // into the middle where the header would be cut.
      if (tall <= room - m * 2) dy = Math.round(top + m - box.top);
      else {
        if (box.top > top + m) dy = Math.round(top + m - box.top);
        if (box.bottom < top + room - m) dy = Math.round(top + room - m - box.bottom);
      }
    } else if (tall <= room - m * 2) {
      dy = Math.round(top + (room - tall) / 2 - box.top);
    } else {
      if (box.top > top + m) dy = Math.round(top + m - box.top);
      if (box.bottom < top + room - m) dy = Math.round(top + room - m - box.bottom);
    }
    if (!dx && !dy) return;
    V.state.camera.x += dx;
    V.state.camera.y += dy;
    V.applyCamera();
  }

  var clampQueued = false;
  function queueClamp() {
    if (clampQueued) return;
    clampQueued = true;
    window.requestAnimationFrame(function () {
      clampQueued = false;
      clampPan();
    });
  }

  // The panel resizes itself (collapse, width clamp); follow it.
  if (window.ResizeObserver) {
    try { new ResizeObserver(queueInsets).observe(controls()); } catch (error) { /* older engine */ }
  }
  new MutationObserver(queueInsets).observe(controls(), { attributes: true, attributeFilter: ["class", "style"] });
  window.addEventListener("resize", queueInsets);

  ["pointerup", "pointercancel", "wheel", "touchend", "keyup"].forEach(function (type) {
    window.addEventListener(type, queueClamp, { passive: true, capture: true });
  });

  var publishedInset = null;
  var refitting = false;

  function publishInsets() {
    // The panel floats over the table; clip the table at its edge so cards never
    // render underneath the menu. When the panel collapses or opens, the content
    // moves with its edge instead of leaving dead space behind.
    var panel = controls();
    var inset = Math.round(
      panel.classList.contains("collapsed") ? panel.getBoundingClientRect().right + 10 : panel.getBoundingClientRect().right + 14
    );
    document.documentElement.style.setProperty("--wb-left-inset", inset + "px");
    if (publishedInset !== null && inset !== publishedInset && V.state.viewMode !== "strategy" && !refitting) {
      V.state.camera.x += inset - publishedInset;
      V.applyCamera();
      clampPan();
      // Sliding keeps the scale and the vertical position; then the left edge is
      // re-anchored so the content lines up with the panel's new edge instead of
      // leaving a gap or hanging off the side.
      var box = contentBox();
      var safe = safeRect();
      if (box) {
        var target = safe.left + 13;
        if (box.left < safe.left - 2 || box.left > target + 24) {
          V.state.camera.x += Math.round(target - box.left);
          V.applyCamera();
        }
      }
    }
    publishedInset = inset;
  }

  var insetQueued = false;
  function queueInsets() {
    if (insetQueued) return;
    insetQueued = true;
    window.requestAnimationFrame(function () {
      insetQueued = false;
      publishInsets();
    });
  }

  function frameCurrentView(force) {
    var mode = V.state.viewMode;
    document.body.dataset.view = mode;
    publishInsets();
    if (mode === "strategy") return;
    setPanelCollapsed(window.innerWidth < 720 || (mode !== "table" && window.innerWidth < 900));
    publishInsets();
    if (!force && restoreCamera(mode)) return;
    var attempts = 0;
    function attempt() {
      if (mode !== V.state.viewMode) return;
      var ok = mode === "table" ? frameTable()
        : mode === "buckets" ? frameRoleCards()
        : mode === "stats" ? frameStats()
        : place(target(mode), 1);
      if (ok && (mode !== "table" || !cardsOutside())) return;
      if (++attempts < MAX_ATTEMPTS) window.requestAnimationFrame(attempt);
    }
    attempt();
  }

  Array.prototype.forEach.call(document.querySelectorAll(".view-tab"), function (tab) {
    tab.addEventListener("click", function () {
      window.setTimeout(frameCurrentView, 0);
    });
  });

  var setViewMode = V.setViewMode;
  V.setViewMode = function (mode) {
    saveCamera(V.state.viewMode);
    setViewMode.call(V, mode);
    frameCurrentView();
  };

  var restore = V.restoreSaveBundle;
  V.restoreSaveBundle = function (document_) {
    saved = {};
    var result = restore.call(V, document_);
    [0, 120, 350, 800].forEach(function (delay) {
      window.setTimeout(function () { frameCurrentView(true); }, delay);
    });
    return result;
  };

  // The frame can mount at zero size (it is docked after the first paint); refit when
  // the real size arrives, and whenever the pane is resized by the reader.
  if (window.ResizeObserver) {
    var lastSize = [window.innerWidth, window.innerHeight];
    try {
      new ResizeObserver(function () {
        var w = window.innerWidth, h = window.innerHeight;
        if (!w || !h) return;
        if (Math.abs(w - lastSize[0]) < 40 && Math.abs(h - lastSize[1]) < 40) return;
        lastSize = [w, h];
        if (!window.mtgDeckStudio || !window.mtgDeckStudio.state().deckId) return;
        if (V.state.viewMode === "strategy") return;
        frameCurrentView(true);
      }).observe(document.documentElement);
    } catch (error) { /* older engine */ }
  }

  // A resized pane keeps the user's camera when the view has one already.
  var resizeTimer = 0;
  window.addEventListener("resize", function () {
    window.clearTimeout(resizeTimer);
    resizeTimer = window.setTimeout(function () {
      if (!window.mtgDeckStudio || !window.mtgDeckStudio.state().deckId) return;
      if (V.state.viewMode === "strategy") return;
      if (saved[V.state.viewMode]) shiftInto(cardBounds(), safeRect(), 16);
      else frameCurrentView(true);
    }, 220);
  });

  // Frame a specific world rectangle (the studio asking to show a card together with its
  // links). The camera is ours to keep here: refits restore the saved camera, so this
  // framing is saved too.
  function focus(rect) {
    if (!rect) return false;
    V.focusWorldRect(rect, { fit: true, minScale: 0.08, maxScale: 1.0 });
    saveCamera(V.state.viewMode);
    clampPan();
    report("focus");
    return true;
  }

  function report(label) {
    var box = cardBounds();
    try {
      parent.postMessage({
        type: "wb.frame",
        label: label,
        view: V.state.viewMode,
        inner: [window.innerWidth, window.innerHeight],
        scale: Number((V.state.camera.scale || 0).toFixed(3)),
        camera: [Math.round(V.state.camera.x), Math.round(V.state.camera.y)],
        safe: safeRect(),
        cards: box ? [Math.round(box.left), Math.round(box.top), Math.round(box.right), Math.round(box.bottom)] : null,
        visible: visibleCards()
      }, "*");
    } catch (error) { /* host not listening */ }
  }

  window.mtgFrame = { frame: frameCurrentView, focus: focus, place: place, safeRect: safeRect, report: report, saved: saved, clamp: clampPan };
})();

// build bump: keep the pane on the latest viewer
// nudge
// nudge2
