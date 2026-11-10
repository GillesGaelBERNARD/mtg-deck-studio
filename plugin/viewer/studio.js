(function () {
  "use strict";
  var V = window.mtgViewer;
  var S = V.state;
  var host = function (message) { parent.postMessage(message, "*"); };
  var doc = { deckId: null, rev: 0, persistedRev: 0, fingerprint: null, busy: false, inflight: false };
  var seq = 0;
  var lastContext = "";

  function tagHistory() {
    [S.undoStack, S.bucketUndoStack].forEach(function (stack) {
      var top = stack[stack.length - 1];
      if (top && top.__wbSeq === undefined) top.__wbSeq = ++seq;
    });
  }

  async function snapshot() {
    tagHistory();
    return V.createSaveBundle({ embedImages: false, quiet: true });
  }

  function fingerprintOf(bundle) {
    var layout = Object.assign({}, bundle.layout);
    delete layout.camera;
    delete layout.activeBucketFilter;
    delete layout.z;
    var cards = (bundle.cards || []).map(function (card) { var copy = Object.assign({}, card); delete copy.zIndex; return copy; });
    return JSON.stringify([bundle.deckTitle, bundle.strategyNotes, bundle.customBuckets, bundle.customTableSections,
      bundle.customStatsCategories, layout, cards]);
  }

  function foldName(value) {
    return String(value == null ? "" : value)
      .normalize("NFD").replace(/[\u0300-\u036f]/g, "")
      .toLowerCase().trim();
  }

  function cardsByRef(args) {
    var names = (args.names || []).map(foldName);
    var ids = args.ids || [];
    return S.cards.filter(function (c) {
      if (ids.indexOf(c.id) >= 0) return true;
      var aliases = [c.name, c.printedName, c.requestedName].filter(Boolean).map(foldName);
      var faces = String(c.name || "").split(" // ").map(foldName);
      return aliases.concat(faces).some(function (alias) { return names.indexOf(alias) >= 0; });
    });
  }

  function requireCards(args) {
    var cards = cardsByRef(args);
    if (!cards.length) throw outcomeError("invalid", "No card in this deck matches " + JSON.stringify(args.names || args.ids || []));
    return cards;
  }


  function cardSummary(c) {
    return { id: c.id, name: c.name, printed_name: c.printedName || null, commander: !!c.isCommander, alternate: !!c.isAlternateCommander,
      section: c.tableCategory || c.category, mana_value: c.manualManaValue == null ? c.manaValue : c.manualManaValue };
  }

  function matchDefinition(list, ref, what) {
    var key = String(ref || "").trim().toLowerCase();
    var found = list.find(function (d) { return d.id.toLowerCase() === key; }) || list.find(function (d) { return String(d.label || "").toLowerCase() === key; });
    if (!found) throw outcomeError("invalid", "Unknown " + what + " '" + ref + "'. Use op list_roles to see the available ones.");
    return found.id;
  }

  function resolveRole(ref) { return matchDefinition(V.allBucketDefinitions(), ref, "role"); }
  function resolveGroup(ref) { return matchDefinition(V.BUCKET_OVERVIEW_GROUPS, ref, "role group"); }
  function resolveStatsCategory(ref) { return matchDefinition(V.allStatsCategoryDefinitions(), ref, "stats category"); }
  function resolveSection(ref) {
    if (ref === "" || ref === null) return "";
    return matchDefinition(V.tableSectionDefinitions(), ref, "table section");
  }

  function clearBucketCaches() {
    S.bucketOverviewRowsCache = null;
    if (S.bucketLeafRowsCache && S.bucketLeafRowsCache.clear) S.bucketLeafRowsCache.clear();
  }

  function clickControl(id, result) {
    var button = document.getElementById(id);
    if (!button) throw outcomeError("failed", "Control #" + id + " is missing.");
    if (button.disabled) throw outcomeError("noop", "That action is not available right now.");
    button.click();
    return result();
  }

  function outcomeError(outcome, message) { var e = new Error(message); e.outcome = outcome; return e; }

  function focusCards(cards) {
    var boxes = cards.map(function (c) { return V.getLiveCardPosition(c); }).filter(Boolean);
    if (!boxes.length) return;
    var x0 = Math.min.apply(null, boxes.map(function (b) { return b.x; }));
    var y0 = Math.min.apply(null, boxes.map(function (b) { return b.y; }));
    var x1 = Math.max.apply(null, boxes.map(function (b) { return b.x + 63; }));
    var y1 = Math.max.apply(null, boxes.map(function (b) { return b.y + 88; }));
    V.focusWorldRect({ x: x0, y: y0, width: x1 - x0, height: y1 - y0 }, { fit: true });
    if (cards.length === 1) V.liftCardForPreview(cards[0]);
  }

  function rerender() { V.layoutCards(); V.updateStatsAndBuckets(); V.updateHistoryButtons(); }

  function newestStack(which) {
    tagHistory();
    var pairs = which === "undo"
      ? [[S.undoStack, V.undoMove], [S.bucketUndoStack, V.undoBuckets]]
      : [[S.redoStack, V.redoMove], [S.bucketRedoStack, V.redoBuckets]];
    var best = null;
    pairs.forEach(function (p) {
      var top = p[0][p[0].length - 1];
      if (!top) return;
      var s = top.__wbSeq || 0;
      if (!best || s > best.s) best = { s: s, run: p[1] };
    });
    return best;
  }

  var ops = {
    flush: function () { return { flushed: true }; },
    save_deck: async function (a) {
      if (!window.wbHost) return { error: "Saving needs the app window." };
      var name = String(a.name || "").trim() || String(doc.title || "deck");
      if (!/\.json$/i.test(name)) name += ".mtg-deck-studio.json";
      var bundle = await V.createSaveBundle({ embedImages: !!a.embed_images, quiet: true });
      var saved = await window.wbHost.saveFile(name, JSON.stringify(bundle, null, 2), "exports");
      if (saved && saved.cancelled) return { cancelled: true };
      return { outcome: saved && saved.ok ? "applied" : "failed", saved_as: saved && saved.path, images: !!a.embed_images };
    },
    select_cards: function (a) {
      var cards = requireCards(a);
      V.selectCards(cards, "Selected from chat");
      if (a.focus !== false) focusCards(cards);
      return { selected: cards.map(function (c) { return { id: c.id, name: c.name }; }) };
    },
    focus_cards: function (a) { var cards = requireCards(a); focusCards(cards); return { focused: cards.length }; },
    classify_cards: function (a) {
      var out = {};
      (a.cards || []).forEach(function (card) {
        var result = window.AutoBucketClassifierV2.classifyCard({
          name: card.name || "",
          typeLine: card.type_line || card.typeLine || "",
          oracleText: card.oracle_text || card.oracleText || "",
          manaCost: card.mana_cost || card.manaCost || "",
          faces: card.faces
        });
        out[card.id || card.scryfall_id || card.name] = {
          autoBuckets: result.autoBuckets,
          autoBucketFacts: result.autoBucketFacts,
          ruleVersion: result.ruleVersion
        };
      });
      return { cards: out, rule_version: window.AutoBucketClassifierV2.RULE_VERSION };
    },
    show_links: function (a) { return showLinksOp(a); },
    search_cards: function (a) {
      var matches = rankCards(a.query);
      var picked = null;
      var places = [];
      if (a.select !== false && matches.length) {
        pickCard(matches[0], a.occurrence);
        picked = matches[0].name;
        places = hits.list.map(function (entry) { return { effect: entry.bucketId, label: entry.label }; });
      }
      return {
        query: String(a.query || ""),
        matches: matches.map(function (card) {
          return { id: card.id, name: card.name, printed_name: card.printedName || null };
        }),
        selected: picked,
        places: places,
        place: places.length ? places[Math.min(Number(a.occurrence) || 0, places.length - 1)] : null,
        view: S.viewMode
      };
    },
    clear_selection: function () { V.clearSelection(); return { selected: [] }; },
    set_view_mode: function (a) { V.setViewMode(a.mode); return { view: S.viewMode }; },
    fit_view: function () { V.fitLoadedCards(); return {}; },
    center_view: function () { V.centerTable(); return {}; },
    set_deck_title: function (a) {
      V.rememberTableSnapshot(V.snapshotTableState());
      V.setDeckTitle(String(a.title));
      return { title: S.deckTitle };
    },
    set_strategy_notes: function (a) {
      V.setStrategyNotes(String(a.notes));
      return { characters: (S.strategyNotes || "").length };
    },
    add_cards: async function (a) {
      var added = await V.addCardsByName(String(a.name), a.quantity || 1, true);
      if (!added || !added.length) throw outcomeError("failed", "Scryfall did not return a card for '" + a.name + "'.");
      return { added: added.map(function (c) { return { id: c.id, name: c.name, unresolved: c.error || undefined }; }) };
    },
    move_to_sideboard: function (a) {
      var cards = requireCards({ ids: a.ids, names: a.names });
      if (!cards.length) throw outcomeError("invalid", "No card on the table matches what should move.");
      var ids = cards.map(function (card) { return card.id; });
      var refs = cards.map(sideboardRef);
      V.clearCardPreview();
      V.rememberTableSnapshot(V.snapshotTableState());
      S.cards = S.cards.filter(function (card) { return ids.indexOf(card.id) < 0; });
      cards.forEach(function (card) { S.selectedCardIds.delete(card.id); if (card.element) card.element.remove(); });
      V.syncDecklistFromCards();
      rerender();
      // The sideboard refuses a name that is still in the deck, so the request waits for the save
      // that takes these cards off the table (see flushPendingSideboard).
      pendingSideboard = pendingSideboard.concat(refs);
      V.setStatus("Move to sideboard", cards.length + " card(s) leave the table and join the sideboard once the deck is saved.");
      return { moved: cards.map(function (card) { return { id: card.id, name: card.name }; }) };
    },
    remove_cards: function (a) {
      var cards = requireCards(a);
      var ids = cards.map(function (c) { return c.id; });
      V.clearCardPreview();
      V.rememberTableSnapshot(V.snapshotTableState());
      S.cards = S.cards.filter(function (c) { return ids.indexOf(c.id) < 0; });
      cards.forEach(function (c) { S.selectedCardIds.delete(c.id); if (c.element) c.element.remove(); });
      V.syncDecklistFromCards();
      rerender();
      V.setStatus("Removed", cards.length + " card(s) left the table. Undo can bring them back.");
      return { removed: cards.map(function (c) { return { id: c.id, name: c.name }; }) };
    },
    replace_card: async function (a) {
      var original = requireCards({ ids: a.id ? [a.id] : undefined, names: a.id ? undefined : [a.name] })[0];
      var before = V.snapshotTableState();
      V.clearCardPreview();
      var added = await V.addCardsByName(String(a.scryfall_id), 1, false);
      var card = added && added[0];
      if (!card || card.error) {
        if (card) { S.cards = S.cards.filter(function (c) { return c !== card; }); if (card.element) card.element.remove(); }
        throw outcomeError("failed", "Scryfall did not return the replacement card.");
      }
      ["manualPosition", "tableCategory", "order", "initialOrder", "bucketOrder"].forEach(function (key) { card[key] = original[key]; });
      S.cards = S.cards.filter(function (c) { return c !== original; });
      S.selectedCardIds.delete(original.id);
      if (original.element) original.element.remove();
      V.rememberTableSnapshot(before);
      V.syncDecklistFromCards();
      rerender();
      V.selectCards([card], "Replaced from chat");
      V.setStatus("Replaced", original.name + " was replaced by " + card.name + ". Undo restores it.");
      return { removed: { id: original.id, name: original.name }, added: { id: card.id, name: card.name } };
    },
    reset_positions: function () { V.resetPositions(); return {}; },
    set_card_properties: function (a) {
      var cards = requireCards(a);
      var section = a.table_section === undefined ? undefined : resolveSection(a.table_section);
      var statsIds = function (list) { return (list || []).map(resolveStatsCategory); };
      var add = statsIds(a.stats_add), remove = statsIds(a.stats_remove);
      V.clearCardPreview();
      V.rememberTableSnapshot(V.snapshotTableState());
      cards.forEach(function (card) {
        if (a.is_commander !== undefined) { card.isCommander = !!a.is_commander; if (card.isCommander) card.isAlternateCommander = false; }
        if (a.is_alternate_commander !== undefined) { card.isAlternateCommander = !!a.is_alternate_commander; if (card.isAlternateCommander) card.isCommander = false; }
        if (card.isCommander || card.isAlternateCommander || V.hasType(card, "land")) card.tableCategory = "";
        else if (section !== undefined) card.tableCategory = section;
        if (add.length || remove.length) {
          var stats = new Set(V.effectiveStatsCategoryIds(card));
          add.forEach(function (id) { stats.add(id); });
          remove.forEach(function (id) { stats.delete(id); });
          card.statsCategories = V.normalizeStatsCategoryList(Array.from(stats));
        }
        delete card.manualPosition;
      });
      rerender();
      V.setStatus("Card edits saved", cards.length + " card(s) updated from chat.");
      return { updated: cards.map(cardSummary) };
    },
    set_mana_value: function (a) {
      var cards = requireCards(a);
      var value = a.value === null ? null : V.normalizeManualManaValue(a.value);
      if (a.value !== null && value === null) throw outcomeError("invalid", "Mana value must be a non-negative number, or null to clear.");
      V.rememberTableSnapshot(V.snapshotTableState());
      cards.forEach(function (card) { card.manualManaValue = value; delete card.manualPosition; });
      rerender();
      return { updated: cards.map(cardSummary) };
    },
    set_card_roles: function (a) {
      var cards = requireCards(a);
      var add = (a.add || []).map(resolveRole), remove = (a.remove || []).map(resolveRole);
      var exact = a.exact ? a.exact.map(resolveRole) : null;
      var before = V.snapshotBucketState();
      cards.forEach(function (card) {
        var roles = new Set(exact || V.effectiveBucketIds(card));
        add.forEach(function (id) { roles.add(id); });
        remove.forEach(function (id) { roles.delete(id); });
        V.syncBucketOverridesFromEffective(card, Array.from(roles));
      });
      V.pruneBucketSubcategorySelections();
      if (JSON.stringify(before) === JSON.stringify(V.snapshotBucketState())) throw outcomeError("noop", "Those cards already have those roles.");
      V.rememberBucketSnapshot(before);
      V.updateStatsAndBuckets();
      if (S.viewMode !== "table") V.layoutCards();
      V.updateHistoryButtons();
      return { updated: cards.map(function (c) { return { id: c.id, name: c.name, roles: V.effectiveBucketIds(c).map(V.bucketLabel) }; }) };
    },
    create_role: function (a) {
      var group = resolveGroup(a.group);
      var before = V.snapshotBucketState();
      var role = V.createCustomBucket(String(a.label), group);
      if (!role) throw outcomeError("invalid", "That role name is empty or too vague.");
      if (role.existing) throw outcomeError("noop", "A role named '" + role.label + "' already exists (" + role.id + ").");
      V.rememberBucketSnapshot(before);
      V.updateStatsAndBuckets();
      V.updateHistoryButtons();
      return { role: { id: role.id, label: role.label, group: group } };
    },
    add_table_section: function (a) {
      var before = V.snapshotTableState();
      var section = V.createCustomTableSection(String(a.label));
      if (!section) throw outcomeError("invalid", "Section name is empty.");
      if (section.existing) throw outcomeError("noop", "Section '" + section.label + "' already exists (" + section.id + ").");
      V.rememberTableSnapshot(before);
      rerender();
      return { section: { id: section.id, label: section.label } };
    },
    add_stats_category: function (a) {
      var before = V.snapshotTableState();
      var category = V.createCustomStatsCategory(String(a.label));
      if (!category) throw outcomeError("invalid", "Category name is empty.");
      if (category.existing) throw outcomeError("noop", "Category '" + category.label + "' already exists (" + category.id + ").");
      V.rememberTableSnapshot(before);
      rerender();
      return { category: { id: category.id, label: category.label } };
    },
    flip_cards: function (a) {
      var flipped = requireCards(a).filter(function (card) { return V.toggleCardFace(card) !== false; });
      if (!flipped.length) throw outcomeError("noop", "None of those cards has a second face.");
      return { flipped: flipped.map(cardSummary) };
    },
    toggle_subtypes: function () { return clickControl("toggle-subtypes", function () { return { show_subtypes: !!S.showSubtypes }; }); },
    toggle_granular_view: function () { return clickControl("toggle-bucket-subcategories", function () { return { granular: !!S.showBucketSubcategories }; }); },
    reclassify: async function (a) {
      // An agent has nobody to answer a confirm dialog, so this path is always silent.
      if (a.reset) await V.resetBucketsToAuto(); else await V.autoBucketWithHistory({ force: false, silent: true });
      return { roles_reset: !!a.reset };
    },
    clear_table: function () { return clickControl("clear-table", function () { return { cards: S.cards.length }; }); },
    stop_loading: function () { return clickControl("stop-loading", function () { return {}; }); },
    zoom: function (a) { return clickControl(a.direction === "out" ? "zoom-out" : "zoom-in", function () { return { scale: S.camera && S.camera.scale }; }); },
    toggle_panel: function () { return clickControl("toggle-panel", function () { return {}; }); },
    focus_stats_section: function (a) {
      if (S.viewMode !== "stats") V.setViewMode("stats");
      var available = Array.prototype.map.call(document.querySelectorAll("[data-stats-focus]"), function (el) { return el.getAttribute("data-stats-focus"); });
      if (available.indexOf(String(a.section)) < 0) throw outcomeError("invalid", "Unknown stats section '" + a.section + "'. Available: " + available.join(", "));
      V.focusStatsSection(String(a.section));
      return { view: S.viewMode, section: a.section };
    },
    show_role: function (a) {
      var id = resolveRole(a.role);
      if (S.viewMode !== "buckets") V.setViewMode("buckets");
      V.selectBucketView(id);
      return { role: { id: id, label: V.bucketLabel(id) } };
    },
    show_roles_overview: function () {
      if (S.viewMode !== "buckets") V.setViewMode("buckets");
      V.selectBucketOverview();
      return {};
    },
    delete_role: function (a) {
      var id = resolveRole(a.role);
      if (!S.customBuckets.some(function (b) { return b.id === id; })) throw outcomeError("invalid", "Only custom roles can be deleted.");
      var before = V.snapshotBucketState();
      S.cards.forEach(function (card) { V.syncBucketOverridesFromEffective(card, V.effectiveBucketIds(card).filter(function (b) { return b !== id; })); });
      S.customBuckets = S.customBuckets.filter(function (b) { return b.id !== id; });
      clearBucketCaches();
      V.pruneBucketSubcategorySelections();
      V.rememberBucketSnapshot(before);
      V.updateStatsAndBuckets();
      if (S.viewMode !== "table") V.layoutCards();
      V.updateHistoryButtons();
      return { deleted: id };
    },
    set_role_group: function (a) {
      var id = resolveRole(a.role), group = resolveGroup(a.group);
      if (!S.customBuckets.some(function (b) { return b.id === id; })) throw outcomeError("invalid", "Only custom roles can change group.");
      var before = V.snapshotBucketState();
      S.customBuckets = S.customBuckets.map(function (b) { return b.id === id ? Object.assign({}, b, { groupId: group }) : b; });
      clearBucketCaches();
      V.rememberBucketSnapshot(before);
      V.updateStatsAndBuckets();
      if (S.viewMode !== "table") V.layoutCards();
      return { role: id, group: group };
    },
    set_subtype_sections: function (a) {
      // Accept plain names ("Insect") as well as section ids ("subtype-creatures-insect").
      var wanted = (a.subtypes || []).map(function (value) { return String(value == null ? "" : value).trim(); }).filter(Boolean);
      var groups = V.presentSubtypeGroups ? V.presentSubtypeGroups() : new Map();
      var byLabel = new Map();
      var known = [];
      groups.forEach(function (byId, parent) {
        byId.forEach(function (entry) {
          var label = String(entry.label || "");
          var key = label.toLowerCase();
          if (!byLabel.has(key)) byLabel.set(key, []);
          byLabel.get(key).push(entry.id);
          known.push(label + " " + entry.count);
        });
      });
      var ids = [];
      var unknown = [];
      wanted.forEach(function (name) {
        if (/^subtype-/.test(name)) { ids.push(name); return; }
        var found = byLabel.get(name.toLowerCase());
        if (found) ids = ids.concat(found);
        else unknown.push(name);
      });
      if (unknown.length) {
        known.sort();
        throw outcomeError("invalid", "Unknown subtype" + (unknown.length > 1 ? "s" : "") + " " + unknown.map(function (n) { return "'" + n + "'"; }).join(", ")
          + ". Subtypes in this deck: " + known.slice(0, 14).join(", ") + ".");
      }
      S.activeSubtypeSections = V.normalizeSubtypeSectionList(ids);
      if (S.activeSubtypeSections.length && !S.showSubtypes) S.showSubtypes = true;
      if (S.viewMode !== "table") { S.viewMode = "table"; if (V.setViewMode) V.setViewMode("table"); }
      V.layoutCards();
      V.updateHistoryButtons();
      var counts = {};
      wanted.forEach(function (name) {
        var list = byLabel.get(name.toLowerCase());
        if (!list) return;
        var total = 0;
        groups.forEach(function (byId) { list.forEach(function (id) { var e = byId.get(id); if (e) total = Math.max(total, e.count); }); });
        counts[name] = total;
      });
      if (a.fit !== false && V.fitLoadedCards) { try { V.fitLoadedCards(); } catch (err) {} }
      return { subtype_sections: S.activeSubtypeSections.slice(), show_subtypes: !!S.showSubtypes, counts: counts, requested: wanted };
    },
    set_granular_sections: function (a) {
      if (a.mode === "default") V.restoreDefaultSubBuckets(); else V.clearDeclaredSubBuckets();
      return { mode: a.mode };
    },
    set_settings: function (a) {
      [["zoom-sensitivity", a.zoom_sensitivity], ["pan-sensitivity", a.pan_sensitivity], ["placement-delay", a.placement_delay]].forEach(function (pair) {
        if (pair[1] === undefined) return;
        var input = document.getElementById(pair[0]);
        input.value = String(pair[1]);
        input.dispatchEvent(new Event("input", { bubbles: true }));
      });
      return { zoom_sensitivity: S.zoomSensitivity, pan_sensitivity: S.panSensitivity, placement_delay: S.placementDelay };
    },
    move_cards: function (a) {
      var cards = requireCards(a);
      var x = Number(a.x_mm), y = Number(a.y_mm);
      if (!isFinite(x) || !isFinite(y)) throw outcomeError("invalid", "x_mm and y_mm are required numbers.");
      var step = (V.constants && V.constants.CARD_MM ? V.constants.CARD_MM.width : 63) + 2;
      V.rememberTableSnapshot(V.snapshotTableState());
      cards.forEach(function (card, index) {
        card.manualPosition = { x: Math.round((x + index * step) * 100) / 100, y: Math.round(y * 100) / 100 };
      });
      rerender();
      V.setStatus("Moved", cards.length + " card(s) placed by hand. Reset Positions puts them back in the automatic layout.");
      return { moved: cards.map(cardSummary) };
    },
    toggle_specific_mechanics: function () {
      var board = document.getElementById("bucket-specific-more");
      if (!board || board.hidden) throw outcomeError("noop", "No group is showing specific mechanics right now.");
      clickControl("bucket-specific-more-label", function () {});
      return { open: !board.open };
    },
    toggle_card_index: function () {
      var details = document.getElementById("bucket-card-index-details");
      if (!details) throw outcomeError("failed", "The card index is missing.");
      details.open = !details.open;
      return { open: details.open };
    },
    toggle_review_list: function () {
      var details = document.getElementById("bucket-review-details");
      if (!details) throw outcomeError("failed", "The review list is missing.");
      details.open = !details.open;
      return { open: details.open };
    },
    list_roles: function () {
      var counts = {};
      S.cards.forEach(function (c) { V.effectiveBucketIds(c).forEach(function (id) { counts[id] = (counts[id] || 0) + 1; }); });
      return {
        roles: V.allBucketDefinitions().filter(function (b) { return counts[b.id] || b.custom; })
          .map(function (b) { return { id: b.id, label: b.label, cards: counts[b.id] || 0, custom: !!b.custom }; }),
        groups: V.BUCKET_OVERVIEW_GROUPS.map(function (g) { return { id: g.id, label: g.label }; }),
        table_sections: V.tableSectionDefinitions().map(function (s) { return { id: s.id, label: s.label }; }),
        stats_categories: V.allStatsCategoryDefinitions().map(function (s) { return { id: s.id, label: s.label }; })
      };
    },
    explain_card: function (a) {
      return { cards: requireCards(a).slice(0, 6).map(function (card) {
        return { id: card.id, name: card.name, roles: V.effectiveBucketIds(card).map(V.bucketLabel),
          facts: (card.autoBucketFacts || []).map(function (f) { return { role: f.bucket && f.bucket.label, rule: f.evidence && f.evidence.ruleId, clause: f.evidence && f.evidence.clause }; }),
          gaps: (card.autoBucketGaps || []).map(function (g) { return g.clause; }) };
      }) };
    },
    undo: function () {
      var target = newestStack("undo");
      if (!target) throw outcomeError("noop", "Nothing to undo.");
      target.run();
      return { undone: true };
    },
    redo: function () {
      var target = newestStack("redo");
      if (!target) throw outcomeError("noop", "Nothing to redo.");
      target.run();
      return { redone: true };
    },
    import_mythictools_csv: function (a) {
      V.importMythicToolsCsvText(String(a.text), String(a.name || "MythicTools export"));
      return { started: true, note: "Cards are fetched from Scryfall; progress shows on the table." };
    },
    load_decklist: function (a) {
      document.getElementById("decklist").value = String(a.text);
      V.loadDeck();
      return { started: true, note: "Cards are fetched from Scryfall about one per second; progress shows on the table." };
    }
  };

  async function commitIfChanged() {
    var bundle = await snapshot();
    var fp = fingerprintOf(bundle);
    if (doc.fingerprint === null) { doc.fingerprint = fp; return null; }
    if (fp === doc.fingerprint) return null;
    doc.fingerprint = fp;
    doc.rev += 1;
    return bundle;
  }

  function settled() {
    return new Promise(function (resolve) {
      var started = Date.now();
      (function check() {
        if (!doc.inflight || Date.now() - started > 5000) resolve();
        else setTimeout(check, 50);
      })();
    });
  }

  // A deck card is a candidate the moment it leaves the table: the exact printing travels with it.
  var pendingSideboard = [];

  function sideboardRef(card) {
    if (card.scryfallId) return String(card.scryfallId);
    var set = card.setCode ? String(card.setCode).toUpperCase() : "";
    return set && card.collectorNumber ? card.name + " (" + set + ") " + card.collectorNumber : String(card.name || "");
  }

  function flushPendingSideboard() {
    if (!pendingSideboard.length || !doc.deckId) return;
    var refs = pendingSideboard.splice(0);
    parent.postMessage({ type: "wb.sideboardAdd", deck_id: doc.deckId, refs: refs,
                         reason: "Moved from the deck to the sideboard" }, "*");
  }

  async function run(message) {
    var reply = { type: "wb.ack", command_id: message.command_id, op: message.op, deck_id: doc.deckId };
    var meta = message.meta || {};
    try {
      if (!doc.deckId) throw outcomeError("pane_closed", "No deck is loaded in the studio.");
      if (message.deck_id && message.deck_id !== doc.deckId) throw outcomeError("deck_mismatch", "The studio shows a different deck.");
      var fn = ops[message.op];
      if (!fn) throw outcomeError("invalid", "The viewer does not implement operation '" + message.op + "'.");
      doc.busy = true;
      await settled();
      var before = await commitIfChanged();
      if (meta.kind === "document" && message.expected_rev != null && message.expected_rev !== doc.rev) {
        throw outcomeError("stale_revision", "The deck is at revision " + doc.rev + ", not " + message.expected_rev + ".");
      }
      var result = await fn(message.args || {});
      var after = await commitIfChanged();
      reply.outcome = after || meta.kind !== "document" ? "applied" : "noop";
      if (result && result.started) reply.outcome = "started";
      reply.result = result;
      if (after || before || doc.rev > doc.persistedRev || message.op === "flush") {
        reply.snapshot = await snapshot();
        reply.base_rev = doc.persistedRev;
        doc.inflight = true;
      }
      reply.rev = doc.rev;
    } catch (err) {
      reply.outcome = err.outcome || "failed";
      reply.error = String(err && err.message || err);
      reply.rev = doc.rev;
    } finally {
      doc.busy = false;
    }
    host(reply);
    reportContext(true);
  }

  async function persistIfDirty() {
    if (doc.inflight || doc.rev <= doc.persistedRev) return;
    doc.inflight = true;
    host({ type: "wb.changed", deck_id: doc.deckId, base_rev: doc.persistedRev, rev: doc.rev, snapshot: await snapshot() });
  }

  function reportContext(force) {
    if (!doc.deckId) return;
    var ids = Array.from(S.selectedCardIds);
    var selected = ids.map(function (id) { var c = S.cards.find(function (x) { return x.id === id; }); return c ? { id: id, name: c.name } : null; }).filter(Boolean);
    var record = { type: "wb.context", deck_id: doc.deckId, rev: doc.rev, title: S.deckTitle, card_count: S.cards.length,
      commanders: S.cards.filter(function (c) { return c.isCommander; }).map(function (c) { return c.name; }),
      selected: selected, view: S.viewMode, loading: !!S.loading,
      camera: { x: Math.round(S.camera.x), y: Math.round(S.camera.y), scale: Number((S.camera.scale || 0).toFixed(3)) },
      inner: [window.innerWidth, window.innerHeight] };
    var key = JSON.stringify(record);
    if (!force && key === lastContext) return;
    lastContext = key;
    host(record);
  }

  async function load(message) {
    V.restoreSaveBundle(message.document);
    graph = message.links || { cards: {}, stats: {} };
    if (linksOpen) renderLinks();
    guide.data = null;
    doc.deckId = message.deck_id;
    doc.rev = message.rev;
    doc.persistedRev = message.rev;
    doc.inflight = false;
    doc.fingerprint = null;
    doc.fingerprint = fingerprintOf(await snapshot());
    host({ type: "wb.loaded", deck_id: doc.deckId, rev: doc.rev });
    reportContext(true);
  }


  var guide = { el: null, tabs: null, data: null, tab: "notes" };

  // Full-size card view: the panel thumbnails are too small to read rules text.
  function zoomImage(card) {
    var src = card.image || "";
    if (src.indexOf("/normal/") >= 0) src = src.replace("/normal/", "/large/");
    return src;
  }

  function renderZoom() {
    var card = sideboard.cards[zoom.index];
    if (!card || !zoom.el) return;
    zoom.el.innerHTML = '<div class="wb-zoom-body">' +
      (card.image ? '<img alt="" src="' + zoomImage(card) + '">' : "") +
      '<div class="wb-zoom-caption"><strong>' + escapeHtml(card.name) + "</strong>" +
      (card.mana_cost ? " " + escapeHtml(card.mana_cost) : "") +
      (card.type_line ? " · " + escapeHtml(card.type_line) : "") + "</div>" +
      '<div class="wb-zoom-hint">' + (zoom.index + 1) + " / " + sideboard.cards.length +
      " · click anywhere or press Esc to close · arrow keys move</div></div>";
  }

  function openZoom(index) {
    if (!sideboard.cards[index]) return;
    zoom.index = index;
    if (zoom.el) zoom.el.hidden = false;
    document.body.classList.add("wb-zooming");
    renderZoom();
  }

  function closeZoom() {
    zoom.index = -1;
    if (zoom.el) zoom.el.hidden = true;
    document.body.classList.remove("wb-zooming");
  }

  function stepZoom(delta) {
    if (zoom.index < 0) return;
    var next = zoom.index + delta;
    if (next < 0 || next >= sideboard.cards.length) return;
    zoom.index = next;
    renderZoom();
  }

  function mountZoom() {
    var el = document.createElement("div");
    el.className = "wb-zoom";
    el.hidden = true;
    ["pointerdown", "click"].forEach(function (type) {
      el.addEventListener(type, function (event) { event.stopPropagation(); });
    });
    el.addEventListener("click", closeZoom);
    document.body.appendChild(el);
    zoom.el = el;
    document.addEventListener("keydown", function (event) {
      if (zoom.index < 0) return;
      if (event.key === "Escape") closeZoom();
      else if (event.key === "ArrowRight") stepZoom(1);
      else if (event.key === "ArrowLeft") stepZoom(-1);
    });
  }

  function escapeHtml(text) {
    return String(text).replace(/[&<>"']/g, function (ch) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]; });
  }

  function linkCards(html) {
    var names = S.cards.map(function (c) { return c.name; }).filter(function (n, i, all) { return n && all.indexOf(n) === i; });
    names.sort(function (a, b) { return b.length - a.length; });
    if (!names.length) return html;
    var pattern = new RegExp("(" + names.map(function (n) { return escapeHtml(n).replace(/[.*+?^${}()|[\]\\]/g, "\\$&"); }).join("|") + ")", "g");
    return html.split(/(<[^>]+>)/).map(function (part) {
      return part.charAt(0) === "<" ? part : part.replace(pattern, '<a href="#" class="wb-card-link" data-card="$1">$1</a>');
    }).join("");
  }

  function inline(text) {
    return escapeHtml(text).replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>").replace(/\*([^*]+)\*/g, "<em>$1</em>").replace(/`([^`]+)`/g, "<code>$1</code>");
  }

  function renderMarkdown(markdown) {
    var out = [], list = null;
    var close = function () { if (list) { out.push("</" + list + ">"); list = null; } };
    markdown.split(/\r?\n/).forEach(function (line) {
      var heading = /^(#{1,4})\s+(.*)$/.exec(line);
      var bullet = /^\s*[-*]\s+(.*)$/.exec(line);
      var numbered = /^\s*\d+[.)]\s+(.*)$/.exec(line);
      if (heading) { close(); out.push("<h" + heading[1].length + ">" + inline(heading[2]) + "</h" + heading[1].length + ">"); }
      else if (bullet || numbered) {
        var kind = bullet ? "ul" : "ol";
        if (list !== kind) { close(); out.push("<" + kind + ">"); list = kind; }
        out.push("<li>" + inline((bullet || numbered)[1]) + "</li>");
      } else if (!line.trim()) close();
      else { close(); out.push("<p>" + inline(line) + "</p>"); }
    });
    close();
    return linkCards(out.join(""));
  }

  function showGuideTab(tab) {
    guide.tab = tab;
    var notes = document.getElementById("strategy-notes");
    notes.hidden = tab !== "notes";
    guide.el.hidden = tab !== "guide";
    Array.prototype.forEach.call(guide.tabs.querySelectorAll("button"), function (b) { b.setAttribute("aria-pressed", String(b.dataset.tab === tab)); });
  }

  function renderGuide() {
    var data = guide.data;
    if (!data) {
      guide.el.innerHTML = '<p class="wb-guide-empty">No generated guide yet. Ask in the chat: "Write a strategy guide for this deck."</p>';
      return;
    }
    var banner = data.stale ? '<p class="wb-guide-stale">This guide was written for an earlier card list (revision ' + data.rev + '). Ask the chat to refresh it.</p>' : "";
    guide.el.innerHTML = banner + renderMarkdown(data.markdown);
  }

  function mountGuide() {
    var shell = document.querySelector(".strategy-notes-shell");
    var notes = document.getElementById("strategy-notes");
    if (!shell || !notes) return;
    var style = document.createElement("style");
    style.textContent = ".strategy-notes-shell{grid-template-rows:auto auto minmax(0,1fr)!important}" +
      ".wb-guide-tabs{display:flex;gap:8px;justify-self:center}.wb-guide-tabs button{min-height:32px;padding:4px 14px}" +
      ".wb-guide-tabs button[aria-pressed=true]{background:rgba(102,217,194,.22);border-color:rgba(102,217,194,.6)}" +
      ".wb-guide{overflow:auto;padding:18px 26px;border:1px solid rgba(143,182,255,.32);border-radius:8px;background:rgba(13,19,29,.86);color:#e8f1ff;line-height:1.5;font-size:14px}" +
      ".wb-guide h1{font-size:22px}.wb-guide h2{font-size:17px;margin-top:20px;color:#9fe8d8}.wb-guide h3{font-size:15px}" +
      ".wb-card-link{color:#8fb6ff;text-decoration:underline dotted;cursor:pointer}.wb-guide-stale{padding:8px 12px;border-radius:6px;background:rgba(255,190,90,.16);color:#ffd9a0}" +
      ".wb-guide-empty{opacity:.7}";
    document.head.appendChild(style);
    guide.tabs = document.createElement("div");
    guide.tabs.className = "wb-guide-tabs";
    guide.tabs.innerHTML = '<button type="button" data-tab="guide">Guide</button><button type="button" data-tab="notes">My notes</button>';
    guide.tabs.addEventListener("click", function (event) { var b = event.target.closest("button"); if (b) showGuideTab(b.dataset.tab); });
    guide.el = document.createElement("article");
    guide.el.className = "wb-guide";
    guide.el.addEventListener("click", function (event) {
      var link = event.target.closest(".wb-card-link");
      if (!link) return;
      event.preventDefault();
      run({ command_id: "local-guide-" + Date.now(), deck_id: doc.deckId, op: "select_cards", args: { names: [link.dataset.card] }, meta: { kind: "view" } });
      V.setViewMode("table");
    });
    shell.insertBefore(guide.tabs, notes);
    shell.insertBefore(guide.el, notes);
    renderGuide();
    showGuideTab("notes");
  }

  function setGuide(message) {
    if (message.deck_id !== doc.deckId) return;
    var first = !guide.data && message.guide;
    guide.data = message.guide || null;
    renderGuide();
    if (first || message.reveal) showGuideTab("guide");
    if (message.reveal) V.setViewMode("strategy");
  }

  mountGuide();

  // ---- Sideboard: candidates found for this deck, not part of it yet -------------
  var sideboard = { cards: [], open: false, el: null, tab: null };
  var zoom = { index: -1, el: null };
  var lastSelection = [];

  function sideboardAction(action, card, extra) {
    parent.postMessage(Object.assign({ type: "wb.sideboardAction", deck_id: doc.deckId,
      action: action, scryfall_id: card.scryfall_id }, extra || {}), "*");
  }

  function deckNames(query) {
    var needle = String(query || "").trim().toLowerCase();
    var cards = (V.state.cards || []).slice().sort(function (a, b) {
      return String(a.name || "").localeCompare(String(b.name || ""));
    });
    if (needle) {
      cards = cards.filter(function (card) {
        return String(card.name || "").toLowerCase().indexOf(needle) >= 0;
      });
    }
    return cards.slice(0, 8);
  }

  function swapOptionsHtml(query) {
    var rows = deckNames(query).map(function (card) {
      return '<li><button type="button" class="wb-swap-option" data-id="' + String(card.id).replace(/"/g, "&quot;") + '">' +
        escapeHtml(card.name) + '<span>' + escapeHtml(String(card.manaValue || "")) + "</span></button></li>";
    }).join("");
    return rows || '<li class="wb-swap-none">No deck card matches.</li>';
  }

  function readSelection() {
    var ids = (V.state || {}).selectedCardIds;
    if (!ids) return [];
    if (typeof ids.forEach === "function" && typeof ids.add === "function") {
      var out = [];
      ids.forEach(function (id) { out.push(id); });
      return out.filter(Boolean);
    }
    return Array.isArray(ids) ? ids.filter(Boolean) : [];
  }

  // The viewer clears the table selection on any pointerdown outside a selected card,
  // in the capture phase, so snapshot it before that happens: clicking the sideboard
  // must not forget the cards the swap is about to use.
  function selectionIds() {
    var live = readSelection();
    return live.length ? live : lastSelection.slice();
  }

  window.addEventListener("pointerdown", function () {
    var live = readSelection();
    if (live.length) lastSelection = live;
  }, true);

  function openSwap(article, card) {
    var panel = article.querySelector(".wb-swap-panel");
    if (!panel) return;
    if (!panel.hidden) { panel.hidden = true; return; }
    Array.prototype.forEach.call(sideboard.el.querySelectorAll(".wb-swap-panel"), function (other) { other.hidden = true; });
    var picks = selectionIds();
    panel.innerHTML = '<input type="search" class="wb-swap-search" placeholder="Search deck cards…" autocomplete="off">' +
      '<ul class="wb-swap-list">' + swapOptionsHtml("") + "</ul>" +
      '<div class="wb-swap-footer">' +
      '<button type="button" data-act="swap-selection"' + (picks.length ? "" : " disabled") + ">Use table selection" +
      (picks.length ? " (" + picks.length + ")" : "") + "</button>" +
      '<button type="button" data-act="swap-cancel">Cancel</button></div>';
    panel.hidden = false;
    panel.dataset.scryfall = card.scryfall_id;
    panel.dataset.selection = picks.join(",");
    var input = panel.querySelector(".wb-swap-search");
    input.addEventListener("input", function () {
      panel.querySelector(".wb-swap-list").innerHTML = swapOptionsHtml(input.value);
    });
    input.addEventListener("keydown", function (event) {
      if (event.key === "Escape") { panel.hidden = true; }
      if (event.key === "Enter") {
        var first = panel.querySelector(".wb-swap-option");
        if (first) first.click();
      }
    });
    input.focus();
  }

  function renderSideboard() {
    if (!sideboard.el) return;
    if (sideboard.tab) {
      var count = sideboard.cards.length;
      sideboard.tab.innerHTML = "<span>Sideboard</span>" + (count ? '<span class="wb-sideboard-count">' + count + "</span>" : "");
    }
    if (!sideboard.cards.length) {
      var examples = [
        "Check set Star Trek for cards that fit this deck's plan.",
        "Find tapped-down payoffs under three mana for this deck.",
        "Show graveyard hate that my colour identity can cast.",
        "Which artifacts in the newest set could replace my weakest ramp?",
        "Find a creature that returns lands from my graveyard.",
        "What in this set supports my +1/+1 counter theme?",
        "Look for a card that sacrifices artifacts for value.",
        "Suggest a board wipe that my commander survives."
      ];
      sideboard.el.innerHTML = sideboardAddRow() + '<div class="wb-sideboard-empty"><p>No candidates yet. Add one above by name or with the camera, or ask the chat, for example:</p><ul>' +
        examples.map(function (line) { return "<li>&ldquo;" + line + "&rdquo;</li>"; }).join("") +
        "</ul><p>Whatever it finds lands here, with a reason, ready to add, swap or drop.</p></div>";
      return;
    }
    sideboard.el.innerHTML = sideboardAddRow() + '<div class="wb-sideboard-head"><strong>' + sideboard.cards.length +
      " candidate" + (sideboard.cards.length === 1 ? "" : "s") +
      '</strong><span>Not in the deck. Add, swap in, or remove.</span></div>' +
      '<div class="wb-sideboard-grid">' + sideboard.cards.map(function (card, index) {
        return '<article class="wb-sideboard-card" data-index="' + index + '">' +
          (card.image
            ? '<img alt="" src="' + card.image + '" loading="lazy" data-zoom="1" title="Click to enlarge">'
            : '<div class="wb-sideboard-noimg"></div>') +
          '<div class="wb-sideboard-body"><div class="wb-sideboard-name">' + escapeHtml(card.name) + "</div>" +
          '<div class="wb-sideboard-meta">' + escapeHtml(card.mana_cost || "") +
          (card.mv !== undefined && card.mv !== null ? " · MV " + card.mv : "") +
          '</div><div class="wb-sideboard-type">' + escapeHtml(card.type_line || "") + "</div>" +
          (card.reason ? '<p class="wb-sideboard-reason">' + escapeHtml(card.reason) + "</p>" : "") +
          '<div class="wb-sideboard-actions">' +
          '<button type="button" data-act="promote">Add to deck</button>' +
          '<button type="button" data-act="remove">Remove</button>' +
          '<button type="button" data-act="links">Links</button>' +
          '<button type="button" data-act="swap">Swap in&hellip;</button></div>' +
          '<div class="wb-swap-panel" hidden></div>' +
          "</div></article>";
      }).join("") + "</div>";
  }

  // Full-size card view: the panel thumbnails are too small to read rules text.
  function zoomImage(card) {
    var src = card.image || "";
    if (src.indexOf("/normal/") >= 0) src = src.replace("/normal/", "/large/");
    return src;
  }

  function renderZoom() {
    var card = sideboard.cards[zoom.index];
    if (!card || !zoom.el) return;
    zoom.el.innerHTML = '<div class="wb-zoom-body">' +
      (card.image ? '<img alt="" src="' + zoomImage(card) + '">' : "") +
      '<div class="wb-zoom-caption"><strong>' + escapeHtml(card.name) + "</strong>" +
      (card.mana_cost ? " " + escapeHtml(card.mana_cost) : "") +
      (card.type_line ? " · " + escapeHtml(card.type_line) : "") + "</div>" +
      '<div class="wb-zoom-hint">' + (zoom.index + 1) + " / " + sideboard.cards.length +
      " · click anywhere or press Esc to close · arrow keys move</div></div>";
  }

  function openZoom(index) {
    if (!sideboard.cards[index]) return;
    zoom.index = index;
    if (zoom.el) zoom.el.hidden = false;
    document.body.classList.add("wb-zooming");
    renderZoom();
  }

  function closeZoom() {
    zoom.index = -1;
    if (zoom.el) zoom.el.hidden = true;
    document.body.classList.remove("wb-zooming");
  }

  function stepZoom(delta) {
    if (zoom.index < 0) return;
    var next = zoom.index + delta;
    if (next < 0 || next >= sideboard.cards.length) return;
    zoom.index = next;
    renderZoom();
  }

  function mountZoom() {
    var el = document.createElement("div");
    el.className = "wb-zoom";
    el.hidden = true;
    ["pointerdown", "click"].forEach(function (type) {
      el.addEventListener(type, function (event) { event.stopPropagation(); });
    });
    el.addEventListener("click", closeZoom);
    document.body.appendChild(el);
    zoom.el = el;
    document.addEventListener("keydown", function (event) {
      if (zoom.index < 0) return;
      if (event.key === "Escape") closeZoom();
      else if (event.key === "ArrowRight") stepZoom(1);
      else if (event.key === "ArrowLeft") stepZoom(-1);
    });
  }

  function escapeHtml(text) {
    return String(text === undefined || text === null ? "" : text)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  function openSideboard(open) {
    sideboard.open = open;
    if (sideboard.el) sideboard.el.hidden = !open;
    document.body.classList.toggle("sideboard-open", open);
    if (sideboard.tab) sideboard.tab.classList.toggle("is-active", open);
    if (open) renderSideboard();
  }

  // The viewer is a sandboxed frame: it cannot open the camera itself, so it asks the pane
  // host to take the photo. The host saves it and hands the chat a path to read.
  function wireScanButton() {
    var row = document.querySelector("#add-card-modal-backdrop .button-row");
    if (!row || row.querySelector("#wb-scan-card")) return;
    var button = document.createElement("button");
    button.id = "wb-scan-card";
    button.type = "button";
    button.className = "wb-scan-card";
    button.textContent = "Scan a card";
    button.title = "Photograph a card with the camera and let the chat read its printing";
    button.addEventListener("click", function () {
      parent.postMessage({ type: "wb.camera.open", target: "deck" }, "*");
    });
    row.insertBefore(button, row.firstChild);
  }

  function sideboardAddRow() {
    return '<div class="wb-sideboard-add">' +
      '<input id="wb-sideboard-add-name" type="text" autocomplete="off" placeholder="Add a card: Sol Ring (CMM) 410 or a Scryfall id">' +
      '<button type="button" data-act="sideboard-add">Add</button>' +
      '<button type="button" data-act="sideboard-scan" title="Photograph a card and let the chat add it">Scan</button>' +
      "</div>";
  }

  function mountSideboard() {
    var tabs = document.getElementById("global-view-tabs");
    if (!tabs || sideboard.tab) return;
    var tab = document.createElement("button");
    tab.type = "button";
    tab.className = "view-tab wb-sideboard-tab";
    tab.title = "Cards found for this deck that are not in it yet";
    tab.setAttribute("aria-pressed", "false");
    tab.addEventListener("click", function () { openSideboard(!sideboard.open); });
    tabs.appendChild(tab);
    sideboard.tab = tab;

    var panel = document.createElement("section");
    panel.className = "wb-sideboard";
    panel.hidden = true;
    // The viewer clears the table selection on any pointerdown outside a selected card;
    // clicking the sideboard must not drop the selection it is about to use.
    ["pointerdown", "click"].forEach(function (type) {
      panel.addEventListener(type, function (event) { event.stopPropagation(); });
    });
    panel.addEventListener("click", function (event) {
      var thumb = event.target.closest("[data-zoom]");
      if (thumb) {
        var zoomArticle = thumb.closest(".wb-sideboard-card");
        openZoom(Number(zoomArticle.dataset.index));
        return;
      }
      var option = event.target.closest(".wb-swap-option");
      if (option && option.dataset.id) {
        var optionArticle = option.closest(".wb-sideboard-card");
        var optionCard = sideboard.cards[Number(optionArticle.dataset.index)];
        if (optionCard) {
          sideboardAction("replace", optionCard, { replace_id: option.dataset.id });
          optionArticle.querySelector(".wb-swap-panel").hidden = true;
        }
        return;
      }
      var button = event.target.closest("button[data-act]");
      if (!button) return;
      if (button.dataset.act === "sideboard-scan") {
        parent.postMessage({ type: "wb.camera.open", target: "sideboard" }, "*");
        return;
      }
      if (button.dataset.act === "sideboard-add") {
        var input = document.getElementById("wb-sideboard-add-name");
        var ref = input ? String(input.value || "").trim() : "";
        if (!ref) {
          V.setStatus("Sideboard", "Type a card name, a printing such as Sol Ring (CMM) 410, or press Scan.");
          return;
        }
        button.disabled = true;
        parent.postMessage({ type: "wb.sideboardAdd", deck_id: doc.deckId, refs: [ref], reason: "Added by hand" }, "*");
        return;
      }
      var article = button.closest(".wb-sideboard-card");
      var card = sideboard.cards[Number(article.dataset.index)];
      if (!card) return;
      if (button.dataset.act === "links") {
        openSideboard(false);
        openLinks(card.scryfall_id, true);
        return;
      }
      if (button.dataset.act === "remove") {
        sideboardAction("remove", card);
        sideboard.cards = sideboard.cards.filter(function (entry) { return entry.scryfall_id !== card.scryfall_id; });
        renderSideboard();
        return;
      }
      if (button.dataset.act === "promote") {
        sideboardAction("promote", card);
        return;
      }
      if (button.dataset.act === "swap") {
        openSwap(article, card);
        return;
      }
      if (button.dataset.act === "swap-cancel") {
        article.querySelector(".wb-swap-panel").hidden = true;
        return;
      }
      if (button.dataset.act === "swap-selection") {
        var panel = article.querySelector(".wb-swap-panel");
        var picks = (panel.dataset.selection || "").split(",").filter(Boolean);
        if (!picks.length) return;
        sideboardAction("replace", card, { replace_id: picks[0] });
        panel.hidden = true;
        return;
      }
      var option = event.target.closest(".wb-swap-option");
      if (option && option.dataset.id) {
        sideboardAction("replace", card, { replace_id: option.dataset.id });
        article.querySelector(".wb-swap-panel").hidden = true;
        return;
      }
    });
    document.body.appendChild(panel);
    sideboard.el = panel;
    mountZoom();
    renderSideboard();
  }

  function setSideboard(message) {
    if (message.deck_id && doc.deckId && message.deck_id !== doc.deckId) return;
    sideboard.cards = message.cards || [];
    renderSideboard();
    classifyCandidates();
  }

  // A candidate the player saved but never classified cannot be judged; the classifier lives
  // here, so classify it now and let the host keep the facts (and send the fresh graph back).
  function classifyCandidates() {
    if (!window.AutoBucketClassifierV2 || !sideboard.cards.length) return;
    var out = {};
    var counted = 0;
    var ruleVersion = (window.AutoBucketClassifierV2 && window.AutoBucketClassifierV2.RULE_VERSION) || "";
    sideboard.cards.forEach(function (card) {
      var fresh = (card.autoBucketRuleVersion || "") === ruleVersion;
      if ((card.autoBucketFacts || []).length && fresh) return;
      if (!card.scryfall_id) return;
      var result = window.AutoBucketClassifierV2.classifyCard({
        name: card.name || "",
        typeLine: card.type_line || "",
        oracleText: card.oracle_text || "",
        manaCost: card.mana_cost || ""
      });
      card.autoBucketFacts = result.autoBucketFacts || [];
      card.autoBuckets = result.autoBuckets || [];
      card.autoBucketRuleVersion = ruleVersion;
      out[card.scryfall_id] = { autoBucketFacts: card.autoBucketFacts, autoBuckets: card.autoBuckets,
                                ruleVersion: ruleVersion };
      counted += 1;
    });
    if (!counted) return;
    host({ type: "wb.candidateFacts", deck_id: sideboard.deck_id || doc.deckId, cards: out });
  }

  mountSideboard();
  wireScanButton();

  setInterval(function () { reportContext(true); }, 5000);

  setInterval(async function () {
    if (!doc.deckId || doc.busy) return;
    tagHistory();
    await commitIfChanged();
    await persistIfDirty();
    reportContext(false);
  }, 700);

  window.addEventListener("message", function (event) {
    if (event.source !== parent) return;
    var m = event.data || {};
    if (m.type === "wb.cmd") run(m);
    else if (m.type === "wb.load") load(m);
    else if (m.type === "wb.guide") setGuide(m);
    else if (m.type === "wb.sideboard") setSideboard(m);
    else if (m.type === "wb.sideboardNotice") {
      V.setStatus(m.ok ? "Sideboard" : "Sideboard", m.message || (m.ok ? "Added." : "That card could not be added."));
      if (m.ok) { var addInput = document.getElementById("wb-sideboard-add-name"); if (addInput) addInput.value = ""; }
      renderSideboard();
    }
    else if (m.type === "wb.graph") {
      if (m.links) graph = m.links;
      if (linksOpen) renderLinks();
    }
    else if (m.type === "wb.save") { var saveButton = document.getElementById("save-layout"); if (saveButton && !saveButton.disabled) saveButton.click(); }
    else if (m.type === "wb.saved") {
      doc.inflight = false;
      if (m.status === "saved") flushPendingSideboard();
      if (m.status === "saved" || m.status === "noop") doc.persistedRev = Math.max(doc.persistedRev, m.rev);
      // The host round-trip can rebuild the board and reframe the camera; if a search is
      // still pointing at a card, put the view back on it.
      if (hits.card && hits.list.length) {
        var place = hits.list[hits.index];
        requestAnimationFrame(function () { settleFocus(place, 0); });
      }
    }
  });

  // ---- card search -------------------------------------------------------
  var searchEl = null;
  var searchInput = null;
  var searchResults = null;
  var searchMatches = [];
  var searchIndex = 0;
  var hits = { card: null, list: [], index: 0 };
  var focusWatch = 0;
  var spotEl = null;
  var spotNode = null;

  // A ring drawn in screen space, so the found card is obvious even when the board is
  // zoomed far out and its own outline is a fraction of a pixel.
  function drawSpot() {
    if (!spotNode || !spotNode.isConnected) {
      if (spotEl) spotEl.hidden = true;
      spotNode = null;
      return;
    }
    if (!spotEl) {
      spotEl = document.createElement("div");
      spotEl.id = "wb-spot";
      document.body.appendChild(spotEl);
    }
    var rect = spotNode.getBoundingClientRect();
    spotEl.hidden = false;
    spotEl.style.left = Math.round(rect.left - 5) + "px";
    spotEl.style.top = Math.round(rect.top - 5) + "px";
    spotEl.style.width = Math.round(rect.width + 10) + "px";
    spotEl.style.height = Math.round(rect.height + 10) + "px";
  }

  function watchSpot(node) {
    spotNode = node;
    drawSpot();
    var until = Date.now() + 1500;
    (function frame() {
      drawSpot();
      if (spotNode && Date.now() < until) requestAnimationFrame(frame);
    })();
  }

  function clearSpot() {
    spotNode = null;
    if (spotEl) spotEl.hidden = true;
  }

  function clearMarks() {
    clearSpot();
    Array.prototype.forEach.call(document.querySelectorAll(".is-search-hit"), function (node) {
      node.classList.remove("is-search-hit");
      if (node.dataset.wbLift !== undefined) {
        node.style.zIndex = node.dataset.wbLift;
        delete node.dataset.wbLift;
      }
    });
    hits = { card: null, list: [], index: 0 };
    closePeek();
    renderHits();
  }

  // The close look at a found card: the same overlay the sideboard uses, opened from the
  // search, with the effect it sits in and the arrows to walk them.
  var peekEl = null;

  function peekImage(card) {
    var src = card.imageUri || card.imageData || card.sourceImageUri || "";
    if (src.indexOf("/normal/") >= 0) src = src.replace("/normal/", "/large/");
    return src;
  }

  function ensurePeek() {
    if (peekEl) return peekEl;
    peekEl = document.createElement("div");
    peekEl.className = "wb-zoom";
    peekEl.id = "wb-peek";
    peekEl.hidden = true;
    ["pointerdown", "click"].forEach(function (type) {
      peekEl.addEventListener(type, function (event) { event.stopPropagation(); });
    });
    peekEl.addEventListener("click", function (event) {
      var step = event.target.closest("button[data-peek]");
      if (step) {
        event.preventDefault();
        event.stopPropagation();
        stepHit(step.dataset.peek === "next" ? 1 : -1);
        return;
      }
      if (event.target.closest("button")) return;
      closePeek();
    });
    document.body.appendChild(peekEl);
    return peekEl;
  }

  function renderPeek() {
    if (!peekEl || !hits.card) return;
    var card = hits.card;
    var place = hits.list[hits.index] || {};
    var name = card.printedName || card.name;
    var image = peekImage(card);
    peekEl.innerHTML = '<div class="wb-zoom-body">' +
      (image ? '<img alt="" src="' + image + '">' : "") +
      '<div class="wb-zoom-caption"><strong>' + escapeHtml(name) + "</strong>" +
      (card.manaCost ? " " + escapeHtml(card.manaCost) : "") +
      (card.typeLine ? " · " + escapeHtml(card.typeLine) : "") + "</div>" +
      '<div class="wb-zoom-hint">' +
      (hits.list.length > 1
        ? '<span class="wb-peek-place">' + (hits.index + 1) + " / " + hits.list.length + " · " + escapeHtml(place.label || "") + "</span>" +
          '<span class="wb-peek-nav"><button type="button" data-peek="prev" aria-label="Previous effect">‹</button>' +
          '<button type="button" data-peek="next" aria-label="Next effect">›</button></span>'
        : "") +
      "Esc closes · ← → walk the effects</div></div>";
  }

  function openPeek() {
    ensurePeek();
    renderPeek();
    peekEl.hidden = false;
    document.body.classList.add("wb-zooming");
  }

  function closePeek() {
    if (peekEl) peekEl.hidden = true;
    document.body.classList.remove("wb-zooming");
  }

  function needsPan(node) {
    var rect = node.getBoundingClientRect();
    var safe = V.focusViewportRect();
    return rect.left < safe.left + 24 || rect.right > safe.left + safe.width - 24 ||
      rect.top < safe.top + 24 || rect.bottom > safe.top + safe.height - 24;
  }

  // On the table, nudge the board by the smallest amount that brings the card into view
  // instead of centring it, so the player keeps the layout around it.
  function panIntoView(node) {
    var rect = node.getBoundingClientRect();
    var safe = V.focusViewportRect();
    var margin = 60;
    var dx = 0;
    var dy = 0;
    if (rect.left < safe.left + margin) dx = safe.left + margin - rect.left;
    else if (rect.right > safe.left + safe.width - margin) dx = safe.left + safe.width - margin - rect.right;
    if (rect.top < safe.top + margin) dy = safe.top + margin - rect.top;
    else if (rect.bottom > safe.top + safe.height - margin) dy = safe.top + safe.height - margin - rect.bottom;
    if (!dx && !dy) return;
    S.camera.x += Math.round(dx);
    S.camera.y += Math.round(dy);
    V.applyCamera();
    rememberCamera();
  }

  function offCentre(node) {
    var box = node.getBoundingClientRect();
    var area = V.focusViewportRect();
    return Math.abs(box.left + box.width / 2 - (area.left + area.width / 2)) > 3 ||
      Math.abs(box.top + box.height / 2 - (area.top + area.height / 2)) > 3;
  }

  function searchable(view) {
    var mode = view || S.viewMode;
    return mode === "table" || mode === "buckets";
  }

  function rankCards(query) {
    var q = foldName(query);
    if (!q) return [];
    var scored = [];
    (S.cards || []).forEach(function (card) {
      if (!card || !card.name) return;
      var name = foldName(card.name);
      var printed = foldName(card.printedName);
      var type = foldName(card.typeLine);
      var scoreText = function (text) {
        if (!text) return -1;
        var at = text.indexOf(q);
        if (at === 0) return 100;
        if (at > 0) return /[\s\-:,]/.test(text.charAt(at - 1)) ? 80 : 60;
        return -1;
      };
      var score = Math.max(scoreText(name), scoreText(printed));
      if (score < 0 && type.indexOf(q) >= 0) score = 30;
      if (score < 0) return;
      scored.push({ card: card, score: score - name.length * 0.01 });
    });
    scored.sort(function (a, b) { return b.score - a.score || String(a.card.name).localeCompare(String(b.card.name)); });
    return scored.slice(0, 8).map(function (row) { return row.card; });
  }

  // The world is laid out in millimetres and only moved by the camera translate, so a
  // rendered card can be read back into world coordinates. Its own box is the truth; the
  // entries' positions are per row and do not follow the overview layout.
  function cardNodes(card) {
    var id = String((card && card.id) || "");
    if (!id) return [];
    return Array.prototype.slice.call(document.querySelectorAll('.bucket-card[data-card-id="' + CSS.escape(id) + '"]'))
      .filter(function (node) { return !node.hidden && node.getClientRects().length; });
  }

  // Where a card can be seen right now. In Effects a card is listed once per effect it
  // belongs to, so a single card can have several places to look at.
  function occurrences(card) {
    if (S.viewMode === "buckets") {
      var places = cardNodes(card).map(function (node) {
        var bucketId = node.dataset.bucketId || null;
        return { bucketId: bucketId, label: V.bucketLabel(bucketId) || "", node: node };
      });
      if (places.length) return places;
    }
    var position = V.getLiveCardPosition(card);
    return position ? [{ bucketId: null, label: "Table", x: position.x, y: position.y, node: card.element || null }] : [];
  }

  // Put a rendered card in the middle of the clear area: zoom first, then pan by the
  // screen distance (the camera is a plain translate, so this needs no world maths).
  // framing.js keeps a camera per view and restores it on the next layout pass, so any
  // framing the studio performs must be written back or it is undone a moment later.
  function rememberCamera() {
    try {
      if (window.mtgFrame && window.mtgFrame.saved) {
        var c = S.camera;
        window.mtgFrame.saved[S.viewMode] = { scale: c.scale, x: c.x, y: c.y };
      }
    } catch (error) { /* framing is optional */ }
  }

  function centerOnNode(node, zoom) {
    if (!node) return;
    if (zoom && Math.abs((S.camera.scale || 1) - zoom) > 0.01) {
      S.camera.scale = zoom;
      V.applyCamera();
    }
    var rect = node.getBoundingClientRect();
    var safe = V.focusViewportRect();
    S.camera.x += Math.round(safe.left + safe.width / 2 - (rect.left + rect.width / 2));
    S.camera.y += Math.round(safe.top + safe.height / 2 - (rect.top + rect.height / 2));
    V.applyCamera();
    rememberCamera();
  }

  // Searching points at a card, it does not take the view away: the zoom the player chose
  // stays exactly as it is in both views, and only the camera moves if it has to.
  // Search never zooms the board: the close look comes from the card overlay, so the map
  // keeps its zoom and only pans when the card is off screen.
  function focusZoom() {
    return S.camera.scale;
  }

  // The board is rebuilt when the selection changes, so the node we took can be replaced a
  // frame later; look the place up again by effect instead of holding a stale element.
  function nodeForPlace(place) {
    if (!place || !hits.card) return null;
    if (place.node && place.node.isConnected) return place.node;
    if (!place.bucketId) return null;
    place.node = document.querySelector('.bucket-card[data-card-id="' + CSS.escape(hits.card.id) +
      '"][data-bucket-id="' + CSS.escape(place.bucketId) + '"]');
    return place.node;
  }

  // A found card is often buried under the rest of its pile, so lift it while it is marked
  // and put its stacking order back when the next search takes over.
  function highlightHit(place, node) {
    Array.prototype.forEach.call(document.querySelectorAll(".is-search-hit"), function (other) {
      other.classList.remove("is-search-hit");
      if (other.dataset.wbLift !== undefined) {
        other.style.zIndex = other.dataset.wbLift;
        delete other.dataset.wbLift;
      }
    });
    if (!node) return;
    node.classList.add("is-search-hit");
    if (node.dataset.wbLift === undefined) {
      node.dataset.wbLift = node.style.zIndex || "";
      node.style.zIndex = "5000";
    }
  }

  // Centre on the card, then keep correcting for a few frames: the viewer re-renders the
  // board (and reframes the camera) once the selection lands, which would otherwise undo it.
  function settleFocus(place, attempt) {
    attempt = attempt || 0;
    var node = nodeForPlace(place);
    if (!node) return;
    if (needsPan(node)) panIntoView(node);
    highlightHit(place, node);
    watchSpot(node);
    // The host round-trip after a selection can reframe the board a few hundred ms later,
    // so keep watching for a moment and stop as soon as the card stays put.
    var token = ++focusWatch;
    requestAnimationFrame(function () {
      if (token !== focusWatch) return;
      var live = nodeForPlace(place);
      if (!live || attempt >= 24 || (place.stable || 0) >= 8) return;
      place.stable = needsPan(live) ? 0 : (place.stable || 0) + 1;
      settleFocus(place, attempt + 1);
    });
  }

  function focusHit() {
    var place = hits.list[hits.index];
    if (!place) return;
    if (place.node || place.bucketId) {
      settleFocus(place, 0);
    } else if (place.x != null) {
      V.focusWorldRect({ x: place.x, y: place.y, width: 63, height: 88 }, { fit: true, minScale: 0.2, maxScale: 1.05 });
    }
    renderHits();
  }

  function renderHits() {
    var row = searchEl && searchEl.querySelector("#wb-search-hits");
    if (!row) return;
    if (!hits.list.length) {
      row.hidden = true;
      return;
    }
    row.hidden = false;
    var label = searchEl.querySelector("#wb-search-hits-label");
    var where = hits.list[hits.index] || hits.list[0];
    label.textContent = hits.list.length > 1
      ? (hits.index + 1) + " of " + hits.list.length + " · " + (where.label || "effect")
      : (where.label || "table");
    searchEl.querySelector("#wb-search-next").hidden = hits.list.length < 2;
    searchEl.querySelector("#wb-search-prev").hidden = hits.list.length < 2;
  }

  function stepHit(delta) {
    if (hits.list.length < 2) return;
    hits.index = (hits.index + delta + hits.list.length) % hits.list.length;
    focusHit();
    renderPeek();
  }

  function pickCard(card, occurrenceIndex) {
    if (!card) return;
    if (!searchable()) V.setViewMode("table");
    V.selectCards([card], "Selected from search");
    hits.card = card;
    hits.list = occurrences(card);
    hits.index = Math.max(0, Math.min(Number(occurrenceIndex) || 0, Math.max(0, hits.list.length - 1)));
    focusHit();
    openPeek();
    if (hits.list.length <= 1 && S.viewMode !== "buckets") closeSearch();
  }

  function renderResults() {
    if (!searchResults) return;
    hits = { card: null, list: [], index: 0 };
    clearSpot();
    renderHits();
    var query = searchInput.value;
    searchMatches = rankCards(query);
    searchIndex = 0;
    searchResults.innerHTML = "";
    if (!query.trim()) {
      searchResults.hidden = true;
      return;
    }
    if (!searchMatches.length) {
      searchResults.hidden = false;
      var empty = document.createElement("li");
      empty.className = "wb-search-empty";
      empty.textContent = "No card matches \u201c" + query.trim() + "\u201d. The English name and the name printed on your card are both searched.";
      searchResults.appendChild(empty);
      return;
    }
    searchResults.hidden = false;
    searchMatches.forEach(function (card, index) {
      var item = document.createElement("li");
      var button = document.createElement("button");
      button.type = "button";
      button.className = index === 0 ? "is-active" : "";
      button.textContent = card.printedName || card.name;
      var meta = document.createElement("span");
      meta.textContent = [card.printedName ? card.name : "", card.typeLine, card.manaCost].filter(Boolean).join("  ·  ");
      button.appendChild(meta);
      button.addEventListener("mousedown", function (event) { event.preventDefault(); });
      button.addEventListener("click", function (event) {
        event.preventDefault();
        event.stopPropagation();
        pickCard(card);
      });
      item.appendChild(button);
      searchResults.appendChild(item);
    });
  }

  function moveSearch(delta) {
    if (!searchMatches.length) return;
    searchIndex = (searchIndex + delta + searchMatches.length) % searchMatches.length;
    Array.prototype.forEach.call(searchResults.querySelectorAll("button"), function (button, index) {
      button.classList.toggle("is-active", index === searchIndex);
    });
  }

  function openSearch() {
    if (!searchable()) return;
    searchEl.classList.add("open");
    searchEl.querySelector("#wb-search-panel").hidden = false;
    searchInput.value = "";
    renderResults();
    searchInput.focus();
  }

  function closeSearch() {
    if (!searchEl) return;
    searchEl.classList.remove("open");
    var panel = searchEl.querySelector("#wb-search-panel");
    if (panel) panel.hidden = true;
    if (searchResults) searchResults.hidden = true;
  }

  function syncSearchVisibility() {
    if (!searchEl) return;
    searchEl.hidden = !searchable();
    if (!searchable()) closeSearch();
  }

  function mountSearch() {
    if (searchEl) return;
    searchEl = document.createElement("div");
    searchEl.id = "wb-search";
    searchEl.innerHTML =
      '<button type="button" id="wb-search-toggle" title="Find a card (press /)" aria-label="Find a card">' +
      '<svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="7" cy="7" r="4.4" fill="none" stroke="currentColor" stroke-width="1.7"></circle>' +
      '<path d="M10.4 10.4 14 14" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"></path></svg></button>' +
      '<div id="wb-search-panel" hidden><input id="wb-search-input" type="search" placeholder="Find a card (any language)…" autocomplete="off" spellcheck="false">' +
      '<ul id="wb-search-results" hidden></ul>' +
      '<div id="wb-search-hits" hidden><span id="wb-search-hits-label"></span>' +
      '<span class="wb-search-hits-actions"><button type="button" id="wb-search-prev" title="Previous place (Shift+Enter)" aria-label="Previous place">‹</button>' +
      '<button type="button" id="wb-search-next" title="Next place (Enter)" aria-label="Next place">›</button></span></div></div>';
    document.body.appendChild(searchEl);
    // The viewer listens on the document and clears the selection (and reframes the board)
    // when a pointer lands outside a card; the panel must not look like such a click.
    ["pointerdown", "pointerup", "mousedown", "mouseup", "click"].forEach(function (type) {
      searchEl.addEventListener(type, function (event) { event.stopPropagation(); });
    });
    document.addEventListener("keydown", function (event) {
      if (!peekEl || peekEl.hidden) return;
      if (event.key === "Escape") { closePeek(); event.preventDefault(); }
      else if (event.key === "ArrowRight") { stepHit(1); event.preventDefault(); }
      else if (event.key === "ArrowLeft") { stepHit(-1); event.preventDefault(); }
    });
    ["pointerdown", "wheel"].forEach(function (type) {
      document.addEventListener(type, function (event) {
        focusWatch += 1;
        // A click on the board ends the search's hold on the view, ring included.
        var inPanel = (searchEl && searchEl.contains(event.target)) || (peekEl && peekEl.contains(event.target));
        if (!inPanel) clearMarks();
      }, { capture: true, passive: true });
    });
    searchInput = searchEl.querySelector("#wb-search-input");
    searchResults = searchEl.querySelector("#wb-search-results");
    searchEl.querySelector("#wb-search-toggle").addEventListener("click", function () {
      if (searchEl.classList.contains("open")) closeSearch();
      else openSearch();
    });
    searchEl.querySelector("#wb-search-next").addEventListener("click", function (event) { event.preventDefault(); stepHit(1); });
    searchEl.querySelector("#wb-search-prev").addEventListener("click", function (event) { event.preventDefault(); stepHit(-1); });
    searchInput.addEventListener("input", renderResults);
    searchInput.addEventListener("keydown", function (event) {
      if (event.key === "Escape") { closeSearch(); event.preventDefault(); }
      else if (event.key === "Enter" && event.shiftKey) { event.preventDefault(); stepHit(-1); }
      else if (event.key === "ArrowDown") { moveSearch(1); event.preventDefault(); }
      else if (event.key === "ArrowUp") { moveSearch(-1); event.preventDefault(); }
      else if (event.key === "Enter") {
        event.preventDefault();
        if (hits.card && hits.list.length > 1) stepHit(1);
        else pickCard(searchMatches[searchIndex]);
      }
    });
    document.addEventListener("keydown", function (event) {
      if (event.key !== "/" || event.metaKey || event.ctrlKey || event.altKey) return;
      var tag = (event.target && event.target.tagName) || "";
      if (/^(input|textarea|select)$/i.test(tag)) return;
      if (!searchable()) return;
      event.preventDefault();
      openSearch();
    });
    var nativeSetViewMode = V.setViewMode;
    V.setViewMode = function () {
      var out = nativeSetViewMode.apply(V, arguments);
      syncSearchVisibility();
      return out;
    };
    syncSearchVisibility();
  }

  mountSearch();


  // ---- card links --------------------------------------------------------
  // A link view of its own, like the sideboard: one card in the middle, what feeds it on the
  // left, what it feeds on the right, tiles you can walk through and a zoom for the eyes.
  var graph = { cards: {}, stats: {} };
  var linksPanel = null;
  var linksToggle = null;
  var linksOpen = false;
  var linkCurrent = null;
  var linkHistory = [];
  var linksReturnsToSideboard = false;
  var linkZoom = 1;
  var linkFilter = "";
  // Which link groups the player has opened, keyed by column + label. Absent means collapsed: the
  // panel opens showing every kind of link with its count, and the cards appear when asked for.
  var linkExpandedGroups = {};
  var LINK_ZOOM_STEPS = [96, 124, 156];

  function cardById(id) {
    return (S.cards || []).find(function (card) { return card.id === id; });
  }

  // A candidate from the sideboard is not in the deck but is classified, so it has links too.
  function linkView(id) {
    var card = cardById(id);
    if (card) {
      return {
        id: card.id, name: card.name, printed: card.printedName || "",
        image: card.imageUri || card.imageData || card.sourceImageUri || "",
        typeLine: card.typeLine || "", manaCost: card.manaCost || "",
        buckets: card.autoBuckets || [], inDeck: true
      };
    }
    var meta = (graph.meta || {})[id];
    if (!meta) return null;
    return {
      id: id, name: meta.name, printed: meta.printed_name || "",
      image: meta.image || "", typeLine: meta.type_line || "", manaCost: meta.mana_cost || "",
      buckets: meta.autoBuckets || [], inDeck: false
    };
  }

  function linkCardName(card) {
    if (!card) return "";
    return card.printed || card.printedName || card.name || "";
  }

  function effectChips(card, limit) {
    if (!card) return "";
    var ids = ((card.buckets || card.autoBuckets) || []).slice(0, limit || 6);
    if (!ids.length) return "";
    return '<div class="wb-link-effects">' + ids.map(function (id) {
      return '<span class="wb-link-effect">' + escapeHtml(V.bucketLabel(id) || id) + "</span>";
    }).join("") + "</div>";
  }

  function linkTile(partner, clause, reasonLabel) {
    var card = linkView(partner);
    if (!card) return "";
    var image = card.image || "";
    var width = LINK_ZOOM_STEPS[linkZoom];
    var reason = clause && clause.cause ? clause.cause : (clause && clause.reaction ? clause.reaction : "");
    return '<article class="wb-link-tile' + (card.inDeck ? "" : " is-candidate") + '" data-card="' + escapeHtml(card.id) + '" style="width:' + width + 'px">' +
      '<button type="button" class="wb-link-open" data-open="' + escapeHtml(card.id) + '" title="Open ' +
      escapeHtml(linkCardName(card)) + '">' +
      (image ? '<img alt="" src="' + image + '">' : '<div class="wb-link-noimg">No image</div>') + "</button>" +
      (card.inDeck ? "" : '<span class="wb-link-badge">Sideboard</span>') +
      '<div class="wb-link-name">' + escapeHtml(linkCardName(card)) + "</div>" +
      '<div class="wb-link-meta">' + escapeHtml(card.manaCost || "") +
      (card.inDeck ? "" : " · not in deck") + "</div>" +
      (reasonLabel ? '<div class="wb-link-tag">' + escapeHtml(reasonLabel) + "</div>" : "") +
      effectChips(card, 3) +
      (reason ? '<p class="wb-link-reason" title="' + escapeHtml(reason) + '">↳ ' + escapeHtml(reason) + "</p>" : "") +
      "</article>";
  }

  function isCandidate(id) {
    return !!(graph.meta && graph.meta[id]);
  }

  // Deck cards and sideboard candidates never share a list: a candidate is something the player is
  // considering, and reading it as part of the deck is the mistake this split exists to prevent.
  function linkGroups(entry, side, scope) {
    var groups = (entry && entry[side]) || [];
    var needle = linkFilter.trim().toLowerCase();
    var keyBase = scope || side;
    var sections = [];
    var candidates = [];
    var deckCount = 0;
    var skippedGeneric = 0;
    groups.forEach(function (group, groupIndex) {
      // A group whose cause is "this card merely exists / enters / can be cast" is true of any deck
      // card, so it is not a link the player can act on: it stays in the graph and in the scores and
      // never reaches the panel. Reading it as conditional only moved the noise onto the panel.
      if (group.generic) { skippedGeneric += 1; return; }
      var clauses = {};
      (group.clauses || []).forEach(function (clause) { clauses[clause.card] = clause; });
      var deckIds = [];
      group.cards.forEach(function (partner) {
        var card = linkView(partner);
        var name = card ? String(linkCardName(card)).toLowerCase() : "";
        if (needle && name.indexOf(needle) < 0) return;
        if (isCandidate(partner)) {
          candidates.push({ id: partner, clause: clauses[partner] || null, label: group.label });
        } else {
          deckIds.push(partner);
        }
      });
      deckCount += deckIds.length;
      if (!deckIds.length) return;
      // One group = one disclosure: the header keeps the label and the count, and the tiles are drawn
      // only when the row is open, so a card with six kinds of link reads as six numbered lines.
      // A filter is a request for cards, so it opens every group that still has a match.
      var groupKey = keyBase + "|" + groupIndex + "|" + group.label;
      var open = !!needle || !!linkExpandedGroups[groupKey];
      sections.push('<section class="wb-link-group' + (open ? "" : " is-collapsed") + '">' +
        "<h4><button type=\"button\" class=\"wb-link-group-head\" data-group-toggle=\"" +
        escapeHtml(groupKey) + "\" aria-expanded=\"" + (open ? "true" : "false") + '">' +
        '<span class="wb-link-group-arrow" aria-hidden="true"><svg viewBox="0 0 16 16">' +
        '<path d="m6 3.5 5 4.5-5 4.5" fill="none" stroke="currentColor" stroke-width="1.8" ' +
        'stroke-linecap="round" stroke-linejoin="round"/></svg></span>' +
        '<span class="wb-link-group-label">' + escapeHtml(group.label) + "</span>" +
        '<span class="wb-link-group-count">' + deckIds.length + "</span></button></h4>" +
        (open ? '<div class="wb-link-group-body">' + deckIds.map(function (partner) {
          return linkTile(partner, clauses[partner] || null);
        }).join("") + "</div>" : "") + "</section>");
    });

    var html = sections.join("");
    if (!html) {
      html = (groups.length && !skippedGeneric)
        ? (needle ? "Nothing in the deck matches “" + escapeHtml(linkFilter) + "”." : "No deck card on this side.")
        : "Nothing on this side.";
      html = '<p class="wb-link-empty">' + html + "</p>";
    }
    if (candidates.length) {
      html += '<section class="wb-link-candidates"><h5>In the sideboard, not in the deck <span>' +
        candidates.length + '</span></h5><div class="wb-link-candidate-row">' +
        candidates.map(function (row) {
          return linkTile(row.id, row.clause, row.label);
        }).join("") + "</div></section>";
    }
    return { html: html, deck: deckCount, candidates: candidates.length };
  }

  // "Linked cards" in the card dialog (right-click on the table): close the dialog, open the view.
  document.addEventListener("click", function (event) {
    var button = event.target.closest && event.target.closest("#wb-edit-links");
    if (!button) return;
    event.preventDefault();
    var pending = (window.mtgViewer && window.mtgViewer.state && window.mtgViewer.state.pendingEditCards) || [];
    var card = pending.find(function (item) { return item && item.id; });
    var cancel = document.getElementById("cancel-remove-card");
    if (cancel) cancel.click();
    if (card) openLinks(card.id);
  }, true);

  // "Move to sideboard": the card stops being part of the deck and becomes a candidate, keeping
  // the printing it was imported with. Undo on the table brings it back.
  document.addEventListener("click", function (event) {
    var button = event.target.closest && event.target.closest("#wb-move-sideboard");
    if (!button) return;
    event.preventDefault();
    var state = (window.mtgViewer && window.mtgViewer.state) || {};
    var ids = (state.pendingEditCards || []).map(function (item) { return item && item.id; }).filter(Boolean);
    var cancel = document.getElementById("cancel-remove-card");
    if (cancel) cancel.click();
    if (!ids.length) return;
    run({ type: "wb.cmd", command_id: "local-sideboard-" + Date.now(), op: "move_to_sideboard", args: { ids: ids }, meta: { kind: "document" } });
  }, true);

  // A static boost is not a trigger, so it never appears under "feeds"; it gets its own column
  // ("the cards it improves") instead of being folded into the audited event links.
  // Always rendered, even when empty: left must keep meaning "what reaches this card" and right
  // "what it reaches", so a column may not slide across when its side has nothing to show.
  function staticColumn(groups, title, hint, modifier) {
    groups = groups || [];
    var side = linkGroups({ static: groups }, "static", "static-" + modifier);
    var unique = {};
    groups.forEach(function (group) { (group.cards || []).forEach(function (id) { unique[id] = true; }); });
    return '<div class="wb-links-column is-static ' + modifier + '">' +
      '<header class="wb-links-side">' +
      '<span class="wb-links-side-text"><strong>' + escapeHtml(title) + "</strong><em>" + escapeHtml(hint) + "</em></span>" +
      '<span class="wb-links-side-count">' + Object.keys(unique).length + "</span>" +
      (side.candidates ? '<span class="wb-links-side-hint">+' + side.candidates + "</span>" : "") +
      "</header>" +
      (side.html || '<div class="wb-link-empty">Nothing on this side.</div>') + "</div>";
  }

  function renderLinks() {
    if (!linksPanel) return;
    if (!linkCurrent) {
      var selected = Array.from(S.selectedCardIds || []);
      if (selected.length === 1) linkCurrent = selected[0];
    }
    var card = linkCurrent ? linkView(linkCurrent) : null;
    syncLinksToggle();
    if (!card) {
      linksPanel.innerHTML = '<div class="wb-links-shell"><div class="wb-links-head"><strong>Card links</strong>' +
        '<span class="wb-links-hint">Select a card on the table, or search for one.</span>' +
        '<button type="button" class="wb-links-close" data-close aria-label="Close">\u00d7</button></div>' +
        '<div class="wb-link-empty">No card selected.</div></div>';
      return;
    }
    var entry = graph.cards[card.id] || { fed_by: [], feeds: [] };
    var fedSide = linkGroups(entry, "fed_by");
    var feedsSide = linkGroups(entry, "feeds");
    linksPanel.innerHTML = '<div class="wb-links-shell">' +
      '<div class="wb-links-head">' +
      (linkHistory.length ? '<button type="button" class="wb-links-back" data-back title="Back">\u2039</button>' : "") +
      '<button type="button" class="wb-links-hero" data-open="' + escapeHtml(card.id) + '" title="Open ' +
      escapeHtml(linkCardName(card)) + '">' +
      (card.image ? '<img alt="" src="' + card.image + '">' : "") + "</button>" +
      '<div class="wb-links-title">' +
      (linkHistory.length
        ? '<span class="wb-links-trail">' + linkHistory.map(function (id) {
            var seen = linkView(id);
            return escapeHtml(seen ? linkCardName(seen) : "");
          }).join(" \u203a ") + " \u203a</span>"
        : "") +
      "<strong>" + escapeHtml(linkCardName(card)) + "</strong>" +
      '<span class="wb-links-meta">' + escapeHtml(card.manaCost || "") +
      (card.inDeck ? "" : " · candidate, not in the deck") +
      (card.typeLine ? " \u00b7 " + escapeHtml(card.typeLine) : "") + "</span>" + effectChips(card, 6) + "</div>" +
      '<div class="wb-links-zoom"><button type="button" data-zoom-out aria-label="Smaller cards">\u2212</button>' +
      "<span>" + (linkZoom + 1) + "/3</span>" +
      '<button type="button" data-zoom-in aria-label="Bigger cards">+</button></div>' +
      '<button type="button" class="wb-links-close" data-close aria-label="Close">\u00d7</button>' +
      "</div>" +
      '<div class="wb-links-tools">' +
      '<input type="search" class="wb-links-filter" data-filter placeholder="Filter these cards…" value="' +
      escapeHtml(linkFilter) + '" autocomplete="off">' +
      "</div>" +
      '<div class="wb-links-body">' +
      '<div class="wb-links-column is-in">' +
      '<header class="wb-links-side">' +
      '<svg class="wb-links-side-icon" viewBox="0 0 16 16" aria-hidden="true"><path d="M13 8H4M7.5 4 3.5 8l4 4" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>' +
      '<span class="wb-links-side-text"><strong>Triggers</strong><em>cards that trigger this one</em></span>' +
      '<span class="wb-links-side-count">' + fedSide.deck + "</span>" +
      (fedSide.candidates ? '<span class="wb-links-side-hint">+' + fedSide.candidates + "</span>" : "") +
      "</header>" + fedSide.html + "</div>" +
      '<div class="wb-links-column is-out">' +
      '<header class="wb-links-side">' +
      '<svg class="wb-links-side-icon" viewBox="0 0 16 16" aria-hidden="true"><path d="M3 8h9M8.5 4l4 4-4 4" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>' +
      '<span class="wb-links-side-text"><strong>Triggers</strong><em>cards this one triggers</em></span>' +
      '<span class="wb-links-side-count">' + feedsSide.deck + "</span>" +
      (feedsSide.candidates ? '<span class="wb-links-side-hint">+' + feedsSide.candidates + "</span>" : "") +
      "</header>" + feedsSide.html + "</div>" +
      staticColumn(entry.improved_by, "Effects", "cards that affect this one", "is-up") +
      staticColumn(entry.improves, "Effects", "cards this one affects", "is-down") +
"</div></div>";
  }

  function ensureLinksPanel() {
    if (linksPanel) return linksPanel;
    linksPanel = document.createElement("section");
    linksPanel.id = "wb-links-panel";
    linksPanel.hidden = true;
    ["pointerdown", "pointerup", "mousedown", "mouseup", "click"].forEach(function (type) {
      linksPanel.addEventListener(type, function (event) { event.stopPropagation(); });
    });
    linksPanel.addEventListener("input", function (event) {
      var input = event.target.closest("[data-filter]");
      if (!input) return;
      linkFilter = input.value;
      renderLinks();
      var again = linksPanel.querySelector("[data-filter]");
      if (again) { again.focus(); again.setSelectionRange(again.value.length, again.value.length); }
    });
    linksPanel.addEventListener("click", function (event) {
      if (event.target.closest("[data-close]")) { closeLinks(); return; }
      if (event.target.closest("[data-back]")) {
        if (linkHistory.length) { linkCurrent = linkHistory.pop(); renderLinks(); }
        return;
      }
      var groupHead = event.target.closest("[data-group-toggle]");
      if (groupHead) {
        var groupKey = groupHead.dataset.groupToggle;
        if (linkExpandedGroups[groupKey]) delete linkExpandedGroups[groupKey];
        else linkExpandedGroups[groupKey] = true;
        var scrollTop = linksPanel.scrollTop;
        renderLinks();
        linksPanel.scrollTop = scrollTop;
        return;
      }
      if (event.target.closest("[data-zoom-in]")) { linkZoom = Math.min(2, linkZoom + 1); renderLinks(); return; }
      if (event.target.closest("[data-zoom-out]")) { linkZoom = Math.max(0, linkZoom - 1); renderLinks(); return; }
      var opener = event.target.closest("[data-open]");
      if (opener) { openCardZoom(opener.dataset.open); return; }
      var tile = event.target.closest("[data-card]");
      if (tile) {
        if (linkCurrent) linkHistory.push(linkCurrent);
        linkCurrent = tile.dataset.card;
        renderLinks();
        linksPanel.scrollTop = 0;
      }
    });
    document.body.appendChild(linksPanel);

    linksToggle = document.createElement("button");
    linksToggle.type = "button";
    linksToggle.id = "wb-links-toggle";
    linksToggle.title = "Links of the selected card";
    linksToggle.setAttribute("aria-label", "Card links");
    linksToggle.innerHTML = '<svg viewBox="0 0 16 16" aria-hidden="true">' +
      '<path d="M6.5 9.5 3.8 12.2a2.3 2.3 0 0 1-3.2-3.2l2.7-2.7a2.3 2.3 0 0 1 3.2 0" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/>' +
      '<path d="m9.5 6.5 2.7-2.7a2.3 2.3 0 0 1 3.2 3.2l-2.7 2.7a2.3 2.3 0 0 1-3.2 0" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/>' +
      '<path d="m6 10 4-4" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>';
    ["pointerdown", "pointerup", "mousedown", "mouseup", "click"].forEach(function (type) {
      linksToggle.addEventListener(type, function (event) { event.stopPropagation(); });
    });
    linksToggle.addEventListener("click", function (event) {
      event.preventDefault();
      if (linksOpen) closeLinks(); else openLinks();
    });
    document.body.appendChild(linksToggle);
    return linksPanel;
  }

  // fromSideboard: the sideboard is hidden while the link view is up, so closing it must bring
  // the player back to the list they were browsing, not to the table.
  function openLinks(cardRef, fromSideboard) {
    ensureLinksPanel();
    linksReturnsToSideboard = !!fromSideboard;
    if (cardRef) {
      var card = typeof cardRef === "string" ? linkView(cardRef) : cardRef;
      if (card) { linkCurrent = card.id; linkHistory = []; }
    } else {
      // The icon opens the links of the card selected *now*. It used to keep the card it showed
      // last, so after looking at one card the icon reopened that card instead of the new one.
      var selected = Array.from(S.selectedCardIds || []);
      linkCurrent = selected.length === 1 ? selected[0] : null;
      linkHistory = [];
    }
    linksOpen = true;
    linksPanel.hidden = false;
    document.body.classList.add("wb-links-open");
    renderLinks();
  }

  function closeLinks() {
    linksOpen = false;
    linkHistory = [];
    // Forget the card: a later open without a selection must not resurrect the last one.
    linkCurrent = null;
    if (linksPanel) linksPanel.hidden = true;
    syncLinksToggle();
    document.body.classList.remove("wb-links-open");
    if (linksReturnsToSideboard) {
      linksReturnsToSideboard = false;
      openSideboard(true);
    }
  }

  // One place decides how the trigger looks: it is the only way back into the view, so it
  // must never be left hidden once the panel is closed.
  function syncLinksToggle() {
    if (!linksToggle) return;
    linksToggle.classList.toggle("is-on", linksOpen);
    linksToggle.classList.toggle("is-hidden", linksOpen);
    linksToggle.setAttribute("aria-expanded", linksOpen ? "true" : "false");
  }

  // The big card, the same overlay the sideboard uses.
  function openCardZoom(cardId) {
    var card = linkView(cardId);
    if (!card) return;
    var image = (card.image || "").replace("/normal/", "/large/");
    var el = document.getElementById("wb-peek");
    if (!el) {
      el = document.createElement("div");
      el.className = "wb-zoom";
      el.id = "wb-peek";
      ["pointerdown", "click"].forEach(function (type) {
        el.addEventListener(type, function (event) { event.stopPropagation(); });
      });
      document.body.appendChild(el);
    }
    el.innerHTML = '<div class="wb-zoom-body">' + (image ? '<img alt="" src="' + image + '">' : "") +
      '<div class="wb-zoom-caption"><strong>' + escapeHtml(linkCardName(card)) + "</strong>" +
      (card.manaCost ? " " + escapeHtml(card.manaCost) : "") +
      (card.typeLine ? " \u00b7 " + escapeHtml(card.typeLine) : "") + "</div>" +
      '<div class="wb-zoom-hint">Click anywhere or press Esc to close</div></div>';
    el.hidden = false;
    document.body.classList.add("wb-zooming");
    el.onclick = function (event) {
      if (event.target.closest("img")) return;
      el.hidden = true;
      el.onclick = null;
      document.body.classList.remove("wb-zooming");
    };
  }

  function showLinksOp(a) {
    if (a.card) {
      var cards = cardsByRef({ names: [a.card] });
      if (!cards.length) throw outcomeError("invalid", "No card in this deck matches " + JSON.stringify(a.card));
      V.selectCards(cards, "Selected from the link view");
      openLinks(cards[0].id);
    } else if (a.on === false) {
      closeLinks();
    } else {
      openLinks();
    }
    var id = linkCurrent || Array.from(S.selectedCardIds || [])[0];
    var entry = id ? graph.cards[id] : null;
    return { open: linksOpen, card: entry ? entry.name : null,
      fed_by: entry ? entry.fed_by : [], feeds: entry ? entry.feeds : [], stats: graph.stats || {} };
  }

  ensureLinksPanel();
  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape" && linksOpen && !document.body.classList.contains("wb-zooming")) closeLinks();
  });

  window.mtgDeckStudio = { ops: Object.keys(ops), state: function () { return Object.assign({}, doc); } };
  host({ type: "wb.ready", ops: Object.keys(ops) });
})();
