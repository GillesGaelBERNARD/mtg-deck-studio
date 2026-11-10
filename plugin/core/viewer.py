"""Builds the viewer served to the Desktop pane.

The baseline viewer is shipped unmodified in viewer/mtg-viewer.html. Every change the
studio needs is one anchored patch below, applied at serve time. A patch whose
anchor is missing or not unique fails loudly instead of serving a half-patched viewer.
"""
from pathlib import Path

_DIR = Path(__file__).resolve().parent.parent / "viewer"
_TAG = "script"

EXPORTS = (
    "selectCards, clearSelection, undoMove, redoMove, undoBuckets, redoBuckets, setViewMode, "
    "focusWorldRect, getLiveCardPosition, liftCardForPreview, clearCardPreview, rememberTableSnapshot, "
    "snapshotTableState, layoutCards, updateStatsAndBuckets, updateHistoryButtons, syncDecklistFromCards, "
    "resetPositions, fitLoadedCards, centerTable, setStatus, applyCamera, focusViewportRect, togglePanelButton,"
    "snapshotBucketState, rememberBucketSnapshot, syncBucketOverridesFromEffective, pruneBucketSubcategorySelections, "
    "allBucketDefinitions, bucketLabel, tableSectionDefinitions, allStatsCategoryDefinitions, createCustomTableSection, "
    "createCustomStatsCategory, effectiveStatsCategoryIds, normalizeStatsCategoryList, normalizeCategoryId, "
    "normalizeManualManaValue, hasType, toggleCardFace, focusStatsSection, selectBucketView, selectBucketOverview, "
    "autoBucketWithHistory, resetBucketsToAuto, BUCKET_OVERVIEW_GROUPS, normalizeSubtypeSectionList, "
    "presentSubtypeGroups, "
    "restoreDefaultSubBuckets, clearDeclaredSubBuckets, selectedCards, "
)

SAVE_HEAD = """      async function createSaveBundle(options = {}) {
        const embedImages = options.embedImages !== false;
        const cards = [];
        for (const [index, card] of state.cards.entries()) {
          setStatus(
            "Saving","""

SAVE_HEAD_PATCHED = """      async function createSaveBundle(options = {}) {
        const embedImages = options.embedImages !== false;
        const report = options.quiet ? () => {} : setStatus;
        const cards = [];
        for (const [index, card] of state.cards.entries()) {
          report(
            "Saving","""


# The frame is sandboxed without allow-modals, so prompt/confirm/alert return at once
# without showing anything. Each call site is rewritten to an in-frame dialog.
DIALOG_PATCHES = [
    ("      function promptDeckTitle() {\n        const title = window.prompt(",
     "      async function promptDeckTitle() {\n        const title = await window.wbDialog.prompt("),
    ("      function promptAddCustomTableSection() {\n        const label = window.prompt(",
     "      async function promptAddCustomTableSection() {\n        const label = await window.wbDialog.prompt("),
    ("      function promptAddCustomStatsCategory() {\n        const label = window.prompt(",
     "      async function promptAddCustomStatsCategory() {\n        const label = await window.wbDialog.prompt("),
    # Reclassify runs synchronously and is undoable; its summary becomes a notice.
    ("if (!window.confirm(`Reclassify Cards", "if (!window.wbDialog.notice(`Reclassify Cards"),
    ("You can undo after applying.`)) {", "Undo restores the previous effects.`)) {"),
    ("          const embedImages = window.confirm(\"Save card images inside the file for offline imports?\\n\\nOK: include images, larger file.\\nCancel: save URLs only, smaller file.\");",
     "          const embedImages = await window.wbDialog.confirm(\"Save card images inside the file for offline imports?\\n\\nWith images the file is larger but works offline.\", { ok: \"Include images\", cancel: \"URLs only\" });"),
    ("            window.alert(`${card.name}", "            window.wbDialog.alert(`${card.name}"),
    ("        if (\"showSaveFilePicker\" in window) {\n          const handle",
     "        if (window.wbHost) {\n          const name = await window.wbDialog.prompt(\"File name\", suggestedName);\n          if (!name) return;\n          const target = await window.wbDialog.choice(\"Save where?\", [\n            { label: \"Cancel\", value: \"\" },\n            { label: \"Studio folder\", value: \"exports\" },\n            { label: \"Studio folder + copy\u2026\", value: \"copy\" }\n          ]);\n          if (!target) return;\n          const saved = await window.wbHost.saveFile(name, await blob.text(), target);\n          if (saved && saved.cancelled) return;\n          window.wbDialog.notice(saved && saved.ok === false ? `Could not save: ${saved.error}` : `Saved to ${saved.path}`);\n          return;\n        }\n        if (\"showSaveFilePicker\" in window) {\n          const handle"),
]


# The row of view tabs drops under the panel in a narrow pane, but the safe area was
# computed as if the tabs were always above the table: every framing helper then
# aimed below the fold.
VIEWPORT_RECT_PATCHES = [
    ('      function focusViewportRect() {\n        const controlsRect = controls.getBoundingClientRect();\n        const tabsRect = globalViewTabs.getBoundingClientRect();\n        const left = Math.min(\n          window.innerWidth - 160,\n          controls.classList.contains("collapsed") ? controlsRect.right + 18 : controlsRect.right + 22\n        );\n        const top = Math.max(22, tabsRect.bottom + 18);\n        return {\n          left,\n          top,\n          width: Math.max(180, window.innerWidth - left - 32),\n          height: Math.max(160, window.innerHeight - top - 34)\n        };\n      }', '      function focusViewportRect() {\n        const controlsRect = controls.getBoundingClientRect();\n        const tabsRect = globalViewTabs.getBoundingClientRect();\n        const collapsed = controls.classList.contains("collapsed");\n        const left = Math.min(window.innerWidth - 160, collapsed ? controlsRect.right + 12 : controlsRect.right + 16);\n        const tabsAtTop = tabsRect.height > 0 && tabsRect.top < window.innerHeight * 0.4;\n        const top = Math.max(12, tabsAtTop ? tabsRect.bottom + 12 : 12);\n        const bottom = tabsAtTop ? window.innerHeight - 12 : Math.max(top + 120, tabsRect.top - 12);\n        return {\n          left,\n          top,\n          width: Math.max(180, window.innerWidth - left - 12),\n          height: Math.max(120, bottom - top)\n        };\n      }', 1),
]

# Display names only. Ids, class names and the saved format keep "bucket", so deck
# files, operations.json and the host protocol are unchanged.
RENAME_PATCHES = [
    ('title="Review and edit utility buckets.">Buckets<', 'title="Review and edit the effects inferred from each card.">Effects<', 1),
    ('>Add Bucket<', '>Add Effect<', 2),
    ('>Reset Buckets<', '>Reset Effects<', 1),
    ('>Fit bucket cards<', '>Fit effect cards<', 1),
    ('Edit Utility Buckets', 'Edit Effects', 3),
    ('placeholder="New custom bucket"', 'placeholder="New custom effect"', 1),
    ('Save Buckets', 'Save Effects', 3),
    ('>Clear Buckets<', '>Clear Effects<', 1),
    ('>Granular Bucket Sections<', '>Granular Effect Sections<', 1),
    ('Auto Buckets classifies each card locally from its own Oracle text. It previews changes, preserves manual overrides, and can be undone.', "Effects are inferred locally from each card's Oracle text. Changes preview first, manual overrides survive, and everything can be undone.", 1),
    ('setStatus("Bucket unchanged"', 'setStatus("Effect unchanged"', 1),
    ('setStatus("Bucket updated"', 'setStatus("Effect updated"', 1),
    ('<title>MTG Deck Table Viewer</title>', '<title>MTG Deck Studio</title>', 1),
    ('<button id="save-layout" class="primary" type="button" disabled>Export mtg-viewer JSON</button>', '<button id="save-layout" class="primary" type="button" disabled>Save deck (JSON)</button>', 1),
    ('<button id="import-layout" class="primary" type="button">Import mtg-viewer JSON</button>', '<button id="import-layout" class="primary" type="button">Import deck (JSON)</button>', 1),
    ('accept=".mtg-viewer.json,application/json"', 'accept=".mtg-deck-studio.json,.mtg-viewer.json,application/json"', 1),
    ('return `${safeName}-mtg-viewer.mtg-viewer.json`;', 'return `${safeName}.mtg-deck-studio.json`;', 1),
    ('description: "MTG Viewer save",', 'description: "MTG Deck Studio save",', 1),
    ('accept: { "application/json": [".mtg-viewer.json", ".json"] }',
     'accept: { "application/json": [".mtg-deck-studio.json", ".mtg-viewer.json", ".json"] }', 1),
    ('"save-layout": "Export this deck as an mtg-viewer compatible JSON save file."', '"save-layout": "Save this deck as a JSON file in the studio folder."', 1),
    ('Strategy notes are saved with this mtg-viewer JSON.', 'Strategy notes are saved with this deck JSON.', 1),
    ('<h1>MTG Deck Table Viewer</h1>', '<h1>MTG Deck Studio</h1>', 1),
    ('Import a card list, then move cards around.',
     'Arrange your deck on a live table, read its stats and effects, research new cards, and edit it by hand or by asking the AI.', 1),
    ('>Utility Buckets<', '>Effects<', 1),
    ('`Utility Buckets · ${data.bucketRows.length}`', '`Effects · ${data.bucketRows.length}`', 1),
    ('"Utility buckets were restored to the automated sorting."', '"Effects were restored to the automated sorting."', 1),
    ('"Manage Custom Buckets"', '"Manage Custom Effects"', 1),
    ('"No buckets yet."', '"No effects yet."', 1),
    ('subbucket breakdown', 'sub-effect breakdown', 1),
    ('"Bucket deletion staged"', '"Effect deletion staged"', 1),
    ('Strategic parent for ', 'Effect group for ', 2),
    ('"No custom buckets yet."', '"No custom effects yet."', 1),
    ('"No bucket added"', '"No effect added"', 3),
    ('"Bucket already exists"', '"Effect already exists"', 2),
    ('setStatus("Buckets undone"', 'setStatus("Effects undone"', 1),
    ('setStatus("Buckets redone"', 'setStatus("Effects redone"', 1),
    ('setStatus("Buckets reset"', 'setStatus("Effects reset"', 1),
    ('"Buckets saved"', '"Effects saved"', 1),
    ('? "Bucket view"', '? "Effects view"', 1),
    ('"Granular Bucket View"', '"Granular Effect View"', 1),
    ('"All bucket categories"', '"All effect categories"', 1),
]


# The colour rows carried their numbers only in tooltips. The bar keeps the shape and
# the count sits at the end of the track, so the ratios can be read without hovering.
TOKEN_KINDS_PATCHES = [
    # Lander tokens are artifacts with a sacrifice ability, exactly like Clue or Food, so the
    # classifier should name them instead of folding them into generic "artifact tokens".
    ('const KNOWN_ARTIFACT_TOKENS = ["Clue", "Food", "Treasure", "Blood", "Map", "Powerstone", "Gold", "Junk"];',
     'const KNOWN_ARTIFACT_TOKENS = ["Clue", "Food", "Treasure", "Blood", "Map", "Powerstone", "Gold", "Junk", "Lander", "Thopter", "Robot", "Construct", "Servo", "Munitions"];', 1),
    (r'/^sacrifice\/pay\/(clue|food|treasure|blood|map|token)/',
     r'/^sacrifice\/pay\/(clue|food|treasure|blood|map|lander|token)/', 1),
    (r'/^(death|sacrifice)\/react-to\/(token|clue|food|treasure|blood|map)/',
     r'/^(death|sacrifice)\/react-to\/(token|clue|food|treasure|blood|map|lander)/', 1),
    # "a 2/2 colorless Robot artifact creature token" ended in "artifact creature", which the
    # creature-type rule rejected, so every such token collapsed to the generic artifact bucket.
    (r"const creatureType = clean.match(/([A-Z][A-Za-z'’-]*(?:\s+[A-Z][A-Za-z'’-]*)*)\s+creature$/);",
     r"const creatureType = clean.match(/([A-Z][A-Za-z'’-]*(?:\s+[A-Z][A-Za-z'’-]*)*)\s+(?:artifact\s+)?creature$/);", 1),
]

KEYWORD_PATCHES = [
    # Mechanics the deck builder needs that had no fact at all.
    (r"""    const landwalk = leading.match(/\b(island|plains|swamp|mountain|forest)walk\b/i);""",
     r"""    const abilityKeywords = [
      ["station", "keywords/station", "Station", ["Counters"]],
      ["crew", "keywords/crew", "Crew", ["Combat"]],
      ["cycling", "keywords/cycling", "Cycling", ["Card access"]],
      ["prowess", "keywords/prowess", "Prowess", ["Boost"]],
      ["start your engines", "keywords/start-your-engines", "Start your engines!", ["Life"]],
      ["cascade", "keywords/cascade", "Cascade", ["Card access"]],
      ["discover", "keywords/discover", "Discover", ["Card access"]],
      ["warp", "keywords/warp", "Warp", ["Timing"]],
      ["explore", "keywords/explore", "Explore", ["Card access"]],
      ["partner", "keywords/partner", "Partner", ["Card access"]],
      ["void", "keywords/void", "Void", ["Timing"]]
    ];
    const flat = clean.replace(/\s+/g, " ");
    for (const [keyword, bucketId, label, parents] of abilityKeywords) {
      const phrase = keyword.replace(/ /g, "[ ]");
      const present = new RegExp(`(?:^|[^A-Za-z])${phrase}(?:s|es|d)?(?:[^A-Za-z]|$)`, "i").test(flat);
      if (!present) continue;
      matched = addFact(state, ctx, {
        ruleId: `keyword.${slug(keyword)}`,
        role: "keyword",
        action: "have",
        object: object("keyword", label, [slug(keyword)]),
        bucketId,
        label,
        parents
      }) || matched;
      if (keyword === "warp") {
        matched = addFact(state, ctx, {
          ruleId: "keyword.warp.cast",
          role: "effect",
          action: "warp",
          object: object("spell", "A spell", ["spell"]),
          bucketId: "keywords/warp",
          label: "Cast with Warp",
          parents: ["Timing"]
        }) || matched;
      }
    }

    // Exploit is a sacrifice outlet: "you may sacrifice a creature" is what feeds every
    // "whenever a creature dies" payoff, and the card's own exploit trigger.
    if (/(?:^|[^A-Za-z])exploit(?:s|ed)?(?:[^A-Za-z]|$)/i.test(flat)) {
      matched = addFact(state, ctx, {
        ruleId: "keyword.exploit.sacrifice",
        role: "effect",
        action: "sacrifice",
        object: object("creature", "Creature", ["creature"]),
        bucketId: "sacrifice/pay/creature",
        label: "Sacrifice a creature",
        parents: ["Sacrifice"]
      }) || matched;
    }

    // Endure N either puts counters on the creature or creates a Spirit token: the token branch
    // is an entrance.
    const endure = flat.match(/\bendures?\s+(\d+|one|two|three|four|five|six)\b/i);
    if (endure) {
      matched = addFact(state, ctx, {
        ruleId: "keyword.endure.token",
        role: "effect",
        action: "create",
        object: object("creature", "Spirit", ["creature", "spirit", "token"]),
        bucketId: "tokens/create/spirit",
        label: "Create a Spirit token",
        parents: ["Tokens"]
      }) || matched;
    }

    // Void is a condition on two events at once: a permanent left the battlefield, or a spell
    // was warped. Without these the keyword reads as decoration and links to neither.
    if (/(?:^|[^A-Za-z])void\s*[—–-]/i.test(flat)) {
      matched = addFact(state, ctx, {
        ruleId: "void.leaves",
        role: "trigger",
        action: "react-to-leave",
        object: object("permanent", "Permanent", ["permanent"]),
        qualifiers: { nonland: true },
        bucketId: "permanents/react-to/leave-battlefield",
        label: "Nonland permanent leaves the battlefield",
        parents: ["Triggers"]
      }) || matched;
      matched = addFact(state, ctx, {
        ruleId: "void.warped",
        role: "trigger",
        action: "react-to-warp",
        object: object("spell", "Spell", ["spell"]),
        bucketId: "spells/react-to/warp",
        label: "A spell is warped",
        parents: ["Triggers"]
      }) || matched;
    }

    const landwalk = leading.match(/\b(island|plains|swamp|mountain|forest)walk\b/i);""", 1),
]

ACTION_PATCHES = [
    # Action buckets the review found missing: bounce, board wipes, extra turns, colour choice.
    (r"""    matched = classifyKeywords(clean, ctx, state) || matched;""",
     r"""    matched = classifyKeywords(clean, ctx, state) || matched;

    if (/\breturn (?:up to \w+ )?(?:target|all) [^.]{1,60} to (?:its|their) owner's hand/i.test(clean)) {
      matched = addFact(state, ctx, {
        ruleId: "removal.bounce",
        role: "removal",
        action: "return-to-hand",
        object: object("permanent", "Permanents", ["permanent"]),
        bucketId: "removal/return/hand",
        label: "Return permanents to hand",
        parents: ["Removal", "Control"]
      }) || matched;
    }

    if (/\bdeals? \d+ damage to each creature\b|\bdestroy all (?:nonland )?permanents\b|\ball creatures get -\d+\/-\d+/i.test(clean)) {
      matched = addFact(state, ctx, {
        ruleId: "removal.board-wipe",
        role: "removal",
        action: "destroy",
        object: object("permanent", "Creatures", ["creature"]),
        scope: "all",
        bucketId: "removal/destroy/board",
        label: "Board wipe",
        parents: ["Removal"]
      }) || matched;
    }

    if (/\btake an extra turn\b/i.test(clean)) {
      matched = addFact(state, ctx, {
        ruleId: "timing.extra-turn",
        role: "effect",
        action: "extra-turn",
        object: object("turn", "Extra turn", ["turn"]),
        bucketId: "timing/extra/turn",
        label: "Take an extra turn",
        parents: ["Card access"]
      }) || matched;
    }

    if (/\badd (?:one mana|two mana|an amount of mana|\{[^}]+\}) of (?:that|the chosen) color/i.test(clean)) {
      matched = addFact(state, ctx, {
        ruleId: "mana.choose-color",
        role: "effect",
        action: "choose",
        object: object("color", "Chosen color", ["color"]),
        bucketId: "mana/produce/chosen-color",
        label: "Produce the chosen color",
        parents: ["Mana"]
      }) || matched;
    }""", 1),
]

EFFECT_GROUPS_PATCHES = [
    # The new facts have to land in the Effects overview, or they only exist in the details list.
    (r'''{ id: "overview/acceleration", cluster: "Cards & resources", label: "Mana acceleration",''',
     r'''{ id: "overview/keywords", cluster: "Engines & triggers", label: "Keyword mechanics", description: "Station, crew, cycling, prowess, warp, explore and the other named mechanics the deck leans on.", include: [/^keywords\//] },
        { id: "overview/acceleration", cluster: "Cards & resources", label: "Mana acceleration",''', 1),
    (r'''include: [/^mana\/fix-colors\//, /^mana-fixing\b/, /^mana fixing\b/] },''',
     r'''include: [/^mana\/fix-colors\//, /^mana\/produce\/(chosen-color|any-color)/, /^mana-fixing\b/, /^mana fixing\b/] },''', 1),
    (r'''exclude: [/^removal\/(destroy|exile|return-to-hand)\/all-/, /^board-wipe\b/] },''',
     r'''exclude: [/^removal\/(destroy|exile|return-to-hand)\/all-/, /^removal\/destroy\/board/, /^board-wipe\b/] },''', 1),
    (r'''{ id: "overview/board-wipe", cluster: "Answers", label: "Board wipes", description: "Remove all or most objects of a relevant kind.", include: [/^removal\/(destroy|exile|return-to-hand)\/all-/, /^board-wipe\b/] },''',
     r'''{ id: "overview/board-wipe", cluster: "Answers", label: "Board wipes", description: "Remove all or most objects of a relevant kind.", include: [/^removal\/(destroy|exile|return-to-hand)\/all-/, /^removal\/destroy\/board/, /^board-wipe\b/] },''', 1),
    (r'''label: "Upkeep & turn-step effects", description: "Trigger during upkeep, combat, end steps, or other named turn steps.", include: [/^timing\/react-at\//] },''',
     r'''label: "Upkeep & turn-step effects", description: "Trigger during upkeep, combat, end steps, or other named turn steps.", include: [/^timing\/(react-at|extra)\//] },''', 1),
]

# The studio stores English names plus the name printed on the player's own card
# (store.annotate_names). The base viewer rebuilds cards field by field on load, so
# without this the localised name never reaches the search index.
PRINTED_NAME_PATCHES = [
    (
        r"""          requestedName: String(saved.requestedName || name),
""",
        r"""          requestedName: String(saved.requestedName || name),
          printedName: String(saved.printedName || ""),
          printedLanguage: String(saved.printedLanguage || saved.lang || ""),
""",
    ),
]

# A scanner export names each card twice: "Card Name" is the name printed on the player's copy,
# "English Name" is the identity Scryfall knows. A set with no printing in that language (Star
# Trek is English only) answers 404 to the localised lookup, so the row landed as a placeholder
# card. Resolve the English identity instead and keep the printed name for search and display.
MYTHICTOOLS_LANGUAGE_PATCHES = [
    (
        '        const nameIndex = mythicToolsColumnIndex(headers, "Card Name");\n',
        '        const nameIndex = mythicToolsColumnIndex(headers, "Card Name");\n'
        '        const englishNameIndex = mythicToolsColumnIndex(headers, "English Name");\n',
    ),
    (
        '            scryfallId: scryfallIdIndex >= 0 ? String(row[scryfallIdIndex] || "").trim().toLowerCase() : "",\n',
        '            englishName: englishNameIndex >= 0 ? normalizeCardNameInput(String(row[englishNameIndex] || "")) : "",\n'
        '            scryfallId: scryfallIdIndex >= 0 ? String(row[scryfallIdIndex] || "").trim().toLowerCase() : "",\n',
    ),
    (
        '      async function importMythicToolsCsvText(text, sourceName = "MythicTools CSV") {\n',
        '      async function fetchMythicToolsEntry(entry, signal) {\n'
        '        const printed = String(entry.name || "");\n'
        '        const attempts = [];\n'
        '        const add = (name, language) => {\n'
        '          if (!name || attempts.some(attempt => attempt.name === name && attempt.language === language)) return;\n'
        '          attempts.push({ name, language });\n'
        '        };\n'
        '        // The row\'s own language first, then the same card without it: a set with no\n'
        '        // printing in that language answers 404 to the localised lookup.\n'
        '        add(printed, entry.language || "");\n'
        '        add(printed, "");\n'
        '        add(entry.englishName, "");\n'
        '        add(entry.englishName, entry.language || "");\n'
        '        let failure;\n'
        '        for (const attempt of attempts) {\n'
        '          if (signal.aborted) throw failure || new Error("The import was stopped.");\n'
        '          try {\n'
        '            const card = await fetchCardByDeckEntry(\n'
        '              { ...entry, scryfallId: "", name: attempt.name, language: attempt.language },\n'
        '              signal\n'
        '            );\n'
        '            if (attempt.name !== printed) card.requestedName = printed;\n'
        '            return card;\n'
        '          } catch (error) {\n'
        '            failure = error;\n'
        '          }\n'
        '        }\n'
        '        throw failure || new Error(`Card not found: ${printed}`);\n'
        '      }\n'
        '\n'
        '      async function importMythicToolsCsvText(text, sourceName = "MythicTools CSV") {\n',
    ),
    (
        '            try {\n'
        '              template = await fetchCardByDeckEntry({ ...entry, scryfallId: "" }, signal);\n'
        '              if (entry.setCode && entry.collectorNumber) exactPrintCount += entry.count;\n',
        '            try {\n'
        '              template = await fetchMythicToolsEntry(entry, signal);\n'
        '              if (entry.setCode && entry.collectorNumber) exactPrintCount += entry.count;\n',
    ),
]

# A cost symbol can name more than one colour: {B/R} is payable with either, {2/W} is a
# monohybrid, {B/P} a phyrexian. Each colour named is a colour the deck must be able to pay,
# so each counts once — the old counter only matched single-colour symbols and dropped them.
MANA_PIP_PATCHES = [
    (
        '      function countManaPips(manaCost) {\n'
        '        const counts = Object.fromEntries(COLOR_ORDER.map(color => [color.id, 0]));\n'
        '        const matches = String(manaCost || "").match(/\\{[WUBRG]\\}/g) || [];\n'
        '        for (const match of matches) {\n'
        '          const color = match.replace(/[{}]/g, "");\n'
        '          counts[color] += 1;\n'
        '        }\n'
        '        return counts;\n'
        '      }\n',
        '      // One pip per colour named by a symbol: {B}{R} is one black and one red, {B/R} is one\n'
        '      // of each (either payment is a black cost and a red cost), {2/W} and {W/P} are white,\n'
        '      // and {C}, {S}, {X}, {1} name no colour.\n'
        '      function countManaPips(manaCost) {\n'
        '        const counts = Object.fromEntries(COLOR_ORDER.map(color => [color.id, 0]));\n'
        '        const symbols = String(manaCost || "").match(/\\{[^}]+\\}/g) || [];\n'
        '        for (const symbol of symbols) {\n'
        '          for (const part of symbol.slice(1, -1).toUpperCase().split("/")) {\n'
        '            if (Object.prototype.hasOwnProperty.call(counts, part)) counts[part] += 1;\n'
        '          }\n'
        '        }\n'
        '        return counts;\n'
        '      }\n',
    ),
]

# Evasion is how a board that already exists gets through, not something that builds it:
# flying, menace and unblockable belong next to trample and haste under Pressure, where the
# player asks "how do I actually finish this game?".
# The viewer clears the selection on any document pointerdown that is not on a selected
# card, in the capture phase, so a studio control cannot stop it. A click on our own chrome
# (search panel, card overlay, link toggle) must not deselect what it is about to act on.
UI_CLICK_PATCHES = [
    (
        """        document.addEventListener("pointerdown", event => {
          if (state.selectedCardIds.size && !targetIsSelectedCard(event.target)) {""",
        """        document.addEventListener("pointerdown", event => {
          if (event.target.closest("#wb-search, #wb-peek, #wb-links-toggle, #wb-links-readout, .wb-dialog")) return;
          if (state.selectedCardIds.size && !targetIsSelectedCard(event.target)) {""",
    ),
]

EVASION_CLUSTER_PATCHES = [
    (
        '        { id: "overview/evasion", cluster: "Build the board", label: "Evasion", description: "Flying, menace, unblockable, landwalk, and effects that grant them.", include: [/^evasion\\//, /^evasion\\b/] },\n',
        "",
    ),
    (
        '        { id: "overview/combat-pressure", cluster: "Pressure",',
        '        { id: "overview/evasion", cluster: "Pressure", label: "Evasion", description: "Flying, menace, unblockable, landwalk, and effects that grant them, so attacks get through.", include: [/^evasion\\//, /^evasion\\b/] },\n'
        '        { id: "overview/combat-pressure", cluster: "Pressure",',
    ),
]

RULES_V3_PATCHES = [
    # Land sacrifice was missing from the cost parser, which is why "{T}, Sacrifice this land"
    # never produced a sacrifice fact.
    (r"""/sacrifice\s+(?:an?|one|two|three|x|any number of)?\s*(Clues?|artifacts?|creatures?|tokens?|permanents?|this (?:artifact|creature|permanent))/gi""",
     r"""/sacrifice\s+(?:an?|one|two|three|x|any number of)?\s*(Clues?|artifacts?|creatures?|tokens?|lands?|permanents?|this (?:artifact|creature|land|permanent))/gi""", 1),
    # Long-tail clauses the library review surfaced, each with a precise pattern.
    (r"""  function classifyExpression(text, ctx, state, cardContext) {
    let matched = false;
    const clean = String(text || "").trim();
    if (!clean) return false;""",
     r"""  function classifyExpression(text, ctx, state, cardContext) {
    let matched = false;
    const clean = String(text || "").trim();
    if (!clean) return false;

    if (/\bregenerate (?:this creature|it)\b/i.test(clean)) {
      matched = addFact(state, ctx, {
        ruleId: "protection.regenerate",
        role: "protection",
        action: "regenerate",
        object: object("creature", "Creatures", ["creature"]),
        bucketId: "protection/regenerate/creature",
        label: "Regenerate a creature",
        parents: ["Protection"]
      }) || matched;
    }

    if (/\benters with (?:x|a|an|\d+|that many) .{0,20}counters? on it/i.test(clean)) {
      matched = addFact(state, ctx, {
        ruleId: "counters.enter-with",
        role: "effect",
        action: "enter-with",
        object: object("counter", "+1/+1 counters", ["counter"]),
        bucketId: "counters/add/plus-one-plus-one",
        label: "Add +1/+1 counters",
        parents: ["Counters"]
      }) || matched;
    }

    if (/\bplay (?:an|up to \w+) additional lands?\b/i.test(clean)) {
      matched = addFact(state, ctx, {
        ruleId: "lands.extra-drop",
        role: "effect",
        action: "extra-land-drop",
        object: object("land", "Lands", ["land"]),
        bucketId: "lands/play/extra-drop",
        label: "Play extra lands",
        parents: ["Mana", "Card access"]
      }) || matched;
    }

    if (/\bcopy (?:that|target|the) spell\b/i.test(clean)) {
      matched = addFact(state, ctx, {
        ruleId: "spells.copy-spell",
        role: "effect",
        action: "copy",
        object: object("spell", "Spells", ["spell"]),
        bucketId: "spells/copy/spell",
        label: "Copy a spell",
        parents: ["Card access"]
      }) || matched;
    }

    const flatRuleText = clean.replace(/\s+/g, " ");
    const extraKeywords = [
      ["exploit", "keywords/exploit", "Exploit", ["Sacrifice"]],
      ["cumulative upkeep", "keywords/cumulative-upkeep", "Cumulative upkeep", ["Counters"]],
      ["defender", "combat/have/defender", "Defender", ["Combat"]]
    ];
    for (const [word, bucketId, label, parents] of extraKeywords) {
      const phrase = word.replace(/ /g, "[ ]");
      if (!new RegExp(`(?:^|[^A-Za-z])${phrase}(?:s|es|d)?(?:[^A-Za-z]|$)`, "i").test(flatRuleText)) continue;
      matched = addFact(state, ctx, {
        ruleId: `keyword.${slug(word)}`,
        role: "keyword",
        action: "have",
        object: object("keyword", label, [slug(word)]),
        bucketId,
        label,
        parents
      }) || matched;
    }""", 1),
    (r"""    /^as this .+ enters, choose a color\.?$/i""",
     r"""    /^as this .+ enters, choose a color\.?$/i,
    /^as it enters, choose a color\.?$/i,
    /^attach it to target .+\.?$/i""", 1),
]
EFFECT_GROUPS_V2_PATCHES = [
    # Give the buckets the review produced a home in the Effects overview.
    (r"""include: [/^cards\/filter\//, /\bcard filtering\b/] },""",
     r"""include: [/^cards\/(filter|discard|hand-size)\//, /\bcard filtering\b/] },""", 1),
    (r"""include: [/^control\/(tap|stun|prevent-)/] },""",
     r"""include: [/^control\/(tap|untap|stun|prevent-)/] },
        { id: "overview/debuff", cluster: "Answers", label: "Weaken creatures", description: "Reduce power or toughness, permanently or for a turn.", include: [/^debuff\//] },
        { id: "overview/mana-production", cluster: "Cards & resources", label: "Mana production", description: "What the lands and rocks actually produce.", include: [/^mana\/produce\//] },""", 1),
    (r"""label: "Keyword mechanics", description: "Station, crew, cycling, prowess, warp, explore and the other named mechanics the deck leans on.", include: [/^keywords\//] },""",
     r"""label: "Keyword mechanics", description: "Station, crew, cycling, prowess, warp, explore and the other named mechanics the deck leans on.", include: [/^keywords\//, /^timing\/cast\/flash/] },""", 1),
    (r"""label: "Mana acceleration", description: "Produce extra mana, reduce costs, or bypass mana costs.", include: [/^mana\/(accelerate|reduce-cost|cast-without-paying)\//,""",
     r"""label: "Mana acceleration", description: "Produce extra mana, reduce costs, or bypass mana costs.", include: [/^mana\/(accelerate|reduce-cost|cast-without-paying)\//, /^lands\/play\//, /^spells\/copy\//,""", 1),
]
LABEL_PATCHES = [
    # Article and plural mistakes the catalogue exposed: "an Plains", "a artifact", "card cards".
    (r'''label: `Search for ${landType === "basic land" ? "a basic land" : `an ${titleCase(landType)}`} to hand`,''',
     r'''label: `Search for ${landType === "basic land" ? "a basic land" : `${/^[aeiou]/i.test(landType) ? "an" : "a"} ${titleCase(landType)}`} to hand`,''', 1),
    (r'''label: `Search for ${sought.label === "card" ? "a card" : `a ${sought.label}`} to ${destination === "library-top" ? "the library top" : destination}`,''',
     r'''label: `Search for ${sought.label === "card" ? "a card" : `${/^[aeiou]/i.test(sought.label) ? "an" : "a"} ${sought.label}`} to ${destination === "library-top" ? "the library top" : destination}`,''', 1),
    (r'''label: `${destination === "battlefield" ? "Return" : "Return"} ${target.label} cards from graveyard to ${destination}`,''',
     r'''label: `${destination === "battlefield" ? "Return" : "Return"} ${/^cards?$/i.test(target.label) ? "cards" : `${target.label} cards`} from graveyard to ${destination}`,''', 1),
    (r'''label: `Exile ${target.label} cards from a graveyard`,''',
     r'''label: `Exile ${/^cards?$/i.test(target.label) ? "cards" : `${target.label} cards`} from a graveyard`,''', 1),
]
LABEL_PATCHES_2 = [
    # Three different boost buckets shared one identical label, so the catalogue read as duplicates.
    (r'''label: `${negative ? "Reduce" : "Increase"} ${/ you control/i.test(boost[1]) && !/^creatures?/i.test(boost[1]) ? boost[1] : "creature"} power/toughness`,''',
     r'''label: `${negative ? "Reduce" : "Increase"} ${scopeFrom(boost[1]) === "target" ? "a target creature's" : /you control/i.test(boost[1]) ? "your creatures'" : "a creature's"} power/toughness`,''', 1),
    (r'''        bucketId: "debuff/modify-power-toughness/negative",
        label: "Reduce creature power/toughness",''',
     r'''        bucketId: "debuff/modify-power-toughness/negative",
        label: "Reduce a creature's stats by a fixed amount",''', 1),
    # "Graveyard setup" swallowed recursion because the group matched every graveyard bucket.
    (r'''label: "Graveyard setup", description: "Mill or deliberately place cards into a graveyard.", include: [/^graveyard\/fill\//, /^graveyard\b/] },''',
     r'''label: "Graveyard setup", description: "Mill or deliberately place cards into a graveyard.", include: [/^graveyard\/(fill|exile|react-to)\//, /^graveyard\/fill\b/] },''', 1),
]
# The card dialog is where a player lands when they right-click a card; it should also be the
# door to the card's links, instead of forcing them to close it and hunt for the chain button.
EDIT_LINKS_PATCHES = [
    (r"""      <p id="remove-card-modal-detail" class="subtle"></p>""",
     r"""      <p id="remove-card-modal-detail" class="subtle"></p>
      <div class="wb-edit-links-row">
        <button id="wb-edit-links" type="button">
          <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M6.5 9.5 3.8 12.2a2.3 2.3 0 0 1-3.2-3.2l2.7-2.7a2.3 2.3 0 0 1 3.2 0" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/><path d="m9.5 6.5 2.7-2.7a2.3 2.3 0 0 1 3.2 3.2l-2.7 2.7a2.3 2.3 0 0 1-3.2 0" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/><path d="m6 10 4-4" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>
          <span>Linked cards</span>
        </button>
        <button id="wb-move-sideboard" type="button">
          <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M2 8h9M11 8l-3-3M11 8l-3 3" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/><path d="M14 3v10" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>
          <span>Move to sideboard</span>
        </button>
      </div>""", 1),
]

# Fixes from the two-deck card audit (Sci-fi void / Sci-fi toucheterre, 200 cards). Each anchor
# is the exact baseline line; the replacement keeps the rule's shape and narrows or widens it.
AUDIT_RULES_PATCHES = [
    # A token's quoted ability belongs to the token: "create a ... token named X with \"{T}: Add
    # one mana of any color\"" is not the card producing mana, and "crews Vehicles" inside the
    # quote is not the card having Crew. Quoted abilities granted to another permanent by an
    # Equipment/Aura stay the card's (they are not inside a create clause).
    (
        '  function classifyExpression(text, ctx, state, cardContext) {\n'
        '    let matched = false;\n'
        '    const clean = String(text || "").trim();\n',
        '  function classifyExpression(text, ctx, state, cardContext) {\n'
        '    let matched = false;\n'
        '    // Effects inside a created token\'s quoted ability are the token\'s, not the card\'s.\n'
        '    const clean = String(text || "").trim().replace(\n'
        '      /\\b(?:create|creates)\\b[^.]*?\\bwith\\s+"[^"]*"/gi,\n'
        '      chunk => chunk.replace(/"[^"]*"/, " ")\n'
        '    );\n',
    ),
    # "7+ | Flying" and "8+ | Flying, vigilance, haste" separate their keywords with a pipe, so
    # Flying — the first keyword on the line — was the one that never matched.
    (
        '      const pattern = new RegExp(`(?:^|[,;—]\\\\s*)${keyword}(?:$|[,.;])`, "i");\n',
        '      const pattern = new RegExp(`(?:^|[,;—|]\\\\s*)${keyword}(?:$|[,.;])`, "i");\n',
    ),
    # Ward is a cost: "Ward—Discard a card" is not the card making the opponent discard.
    (
        '    if (/\\bdiscard (?:a|one|two|x|that many|\\d+) cards?\\b/i.test(clean) && ctx.role !== "cost") {\n',
        '    if (/\\bdiscard (?:a|one|two|x|that many|\\d+) cards?\\b/i.test(clean) && ctx.role !== "cost"\n'
        '        && !/^ward\\b/i.test(clean)) {\n',
    ),
    # Lands are played, not cast: "You may play lands from your graveyard" is its own effect.
    (
        '    if (/\\b(?:cast|play) .+ from your graveyard\\b/i.test(clean)) {\n',
        '    if (/\\b(?:you may )?play(?:ing)? lands? (?:cards? )?from your graveyard\\b/i.test(clean)) {\n'
        '      matched = addFact(state, ctx, {\n'
        '        ruleId: "graveyard.play-land",\n'
        '        action: "play-land-from-graveyard",\n'
        '        object: object("land", "Land from your graveyard", ["land", "graveyard"]),\n'
        '        bucketId: "graveyard/play/land",\n'
        '        label: "Play lands from your graveyard",\n'
        '        parents: ["Graveyard", "Lands"]\n'
        '      }) || matched;\n'
        '    } else if (/\\b(?:cast|play) (?!lands?) .+ from your graveyard\\b/i.test(clean)) {\n',
    ),
    # "Whenever you cast a noncreature spell" is not a reaction to creature spells.
    (
        '      const spellType = targetDescriptor(text, "spell");\n',
        '      const spellType = /\\bnoncreature spells?\\b/i.test(text)\n'
        '        ? { id: "noncreature", label: "noncreature" }\n'
        '        : targetDescriptor(text, "spell");\n',
    ),
    # A colon inside a quoted ability belongs to the token that has it, not to the card:
    # "create a ... token with \"{T}: Add one mana of any color\"" is not an activated ability.
    (
        '      const colonIndex = clause.indexOf(":");\n'
        '      if (colonIndex > 0) {\n',
        '      const colonIndex = clause.indexOf(":");\n'
        '      const quotesBeforeColon = colonIndex > 0 ? (clause.slice(0, colonIndex).match(/"/g) || []).length : 0;\n'
        '      if (colonIndex > 0 && quotesBeforeColon % 2 === 0) {\n',
    ),
    # Destroying your own permanent is a drawback, not an answer: "Destroy target land you
    # control" must not read as removal.
    (
        '      if (destroy) {\n'
        '        const target = targetDescriptor(destroy[2], "permanent");\n',
        '      if (destroy && !/\\byou control\\b/i.test(destroy[2])) {\n'
        '        const target = targetDescriptor(destroy[2], "permanent");\n',
    ),
    # Evolving Wilds searches for a land *by sacrificing itself*: the land count does not grow.
    (
        '      if (sought.kind === "land" && destination === "battlefield") {\n',
        '      const selfSacrifices = /sacrifice this/i.test(String(ctx.activationCost || ""));\n'
        '      if (sought.kind === "land" && destination === "battlefield" && !selfSacrifices) {\n',
    ),
    # "Play lands from your graveyard" is recursion too (lands are played, not cast).
    (
        '        { id: "overview/recursion", cluster: "Cards & resources", label: "Recursion", description: "Return or cast cards from the graveyard.", include: [/^graveyard\\/(return|reanimate|cast-from)\\//, /^recursion\\b/] },',
        '        { id: "overview/recursion", cluster: "Cards & resources", label: "Recursion", description: "Return or cast cards from the graveyard.", include: [/^graveyard\\/(return|reanimate|cast-from|play)\\//, /^recursion\\b/] },',
    ),
]

# Coverage batch from the two-deck card audit: patterns the base rules never produced a fact
# for. Each pair is (anchor found exactly once, replacement) and build() asserts the count.
COVERAGE_PATCHES = [
    (
        '    matched = classifyKeywords(clean, ctx, state) || matched;\n',
        '    matched = classifyKeywords(clean, ctx, state) || matched;\n    matched = classifyCoverage(clean, ctx, state) || matched;\n',
    ),
    (
        '  function classifyExpression(text, ctx, state, cardContext) {\n',
        '  // Patterns the base rules miss: a draw made by another player, a life loss with a clause in\n  // between, damage whose size is a formula, granted keywords to a group, doubling, counters\n  // "equal to" a value, control change, animation, a card cheated into play, a whole graveyard\n  // exiled, an opponent\'s hand revealed, a creature returning itself.\n  function classifyCoverage(text, ctx, state) {\n    let matched = false;\n    const clean = String(text || "");\n    const add = definition => { matched = addFact(state, ctx, definition) || matched; };\n\n    if (/\\b(?:target (?:player|opponent)|each player|an opponent) draws? (a|one|two|three|four|five|six|seven|x|that many|\\d+) cards?\\b/i.test(clean)) {\n      add({ ruleId: "cards.draw-other-player", action: "draw", actor: "opponent",\n        object: object("card", "Cards", ["card", "draw"]),\n        bucketId: "cards/draw/cards", label: "Draw cards", parents: ["Card access"] });\n    }\n\n    if (/\\byou [^.]{0,40}\\blose (\\d+|x|that much) life\\b/i.test(clean) && !/\\byou lose (\\d+|x|that much) life\\b/i.test(clean)) {\n      add({ ruleId: "life.lose-self-clause", action: "lose-life", actor: "you",\n        object: object("life", "Life", ["life"]), bucketId: "life/lose/you",\n        label: "Lose life yourself", parents: ["Life"] });\n    }\n\n    const plainDamage = /\\bdeals? (?:\\d+|x|that much) damage to (?:each opponent|target opponent|an opponent|target player|any target|target creature|target planeswalker|you)\\b/i.test(clean);\n    if (!plainDamage && /\\bdeals? [^.]{0,90}damage to (?:up to [a-z ]+ )?(?:target|any|each)/i.test(clean)) {\n      const creature = /target creature/i.test(clean);\n      add({ ruleId: "damage.deal-formula", action: "deal-damage", actor: creature ? "you" : "opponent",\n        object: creature ? object("creature", "Creature", ["creature", "damage"]) : object("opponent", "Opponent", ["opponent", "damage"]),\n        bucketId: creature ? "damage/deal/creature" : "damage/deal/opponent",\n        label: creature ? "Deal damage to creature" : "Deal damage to opponents", parents: ["Life"] });\n    }\n\n    const debuff = clean.match(/\\b(all creatures|each creature|target creature|creatures you don\'t control)[^.]{0,60}? gets? -[x\\d]+\\/-[x\\d]+/i);\n    if (debuff) {\n      const all = !/^target creature$/i.test(debuff[1].trim());\n      add({ ruleId: "debuff.weaken", action: "debuff", scope: all ? "all" : "target",\n        object: object("creature", "Creatures", ["creature"]),\n        bucketId: all ? "debuff/modify/all-creatures" : "debuff/modify/target-creature",\n        label: all ? "Weaken all creatures" : "Weaken a creature", parents: ["Removal"] });\n    }\n\n    if (/\\bdestroy (?:those creatures|each creature|all creatures)\\b/i.test(clean)) {\n      add({ ruleId: "removal.destroy-mass", action: "destroy", scope: "all",\n        object: object("creature", "Creatures", ["creature"]),\n        bucketId: "removal/destroy/all-creature", label: "Destroy all creatures", parents: ["Removal"] });\n    } else if (/\\bif [^.]{0,60}(?:power|toughness)[^.]{0,40}\\bdestroy it\\b/i.test(clean)) {\n      add({ ruleId: "removal.destroy-conditional", action: "destroy",\n        object: object("creature", "Creature", ["creature"]),\n        bucketId: "removal/destroy/target-creature", label: "Destroy target creature", parents: ["Removal"] });\n    }\n\n    if (/\\b(?:gains?|has|have)\\b/i.test(clean) && /\\b(?:creature|creatures|it|equipped|attacking|token)\\b/i.test(clean)) {\n      const grants = [\n        ["double strike", "combat/grant/double-strike", "Double strike", ["Combat"]],\n        ["trample", "combat/grant/trample", "Trample", ["Combat"]],\n        ["reach", "combat/grant/reach", "Reach", ["Combat"]],\n        ["haste", "combat/grant/haste", "Haste", ["Combat"]],\n        ["deathtouch", "combat/grant/deathtouch", "Deathtouch", ["Combat"]],\n        ["lifelink", "combat/grant/lifelink", "Lifelink", ["Life"]],\n        ["indestructible", "protection/grant/indestructible", "Indestructible", ["Protection"]]\n      ];\n      for (const [keyword, bucket, label, parents] of grants) {\n        const pattern = new RegExp("\\\\b(?:gains?|has|have)\\\\b[^.]{0,60}\\\\b" + keyword + "\\\\b", "i");\n        if (!pattern.test(clean)) continue;\n        add({ ruleId: "grant." + slug(keyword), action: "grant",\n          object: object("keyword", label, [slug(keyword)]),\n          bucketId: bucket, label: "Grant " + label, parents });\n      }\n    }\n\n    // "Akiri gets +1/+0 for each artifact you control": a static boost counted over a kind of\n    // permanent you control. The base rules only read boosts whose subject opens as a creature, so\n    // the clause lands in the gaps and the card reads as isolated on both sides of the link view.\n    const pwScale = clean.match(/\\bfor each (?:other )?([a-z][a-z -]{2,24}?) you control with power (\\d+) or greater\\b/i); const kindScale = pwScale || clean.match(/\\bfor each (?:other )?([a-z][a-z -]{2,24}?) you control\\b/i) || clean.match(/\\bequal to the number of (?:other )?([a-z][a-z -]{2,24}?) you control\\b/i); if (kindScale) { const counted = String(kindScale[1]).trim().toLowerCase().replace(/\\s+/g, \" \").replace(/s$/, \"\"); const rate = clean.match(/\\b(?:gets?|gains?) ([+-]\\d+)\\/([+-]?\\d+)/i); let measure = \"each\"; if (rate) measure = rate[1] + \"/\" + rate[2]; else if (/power and toughness are each equal to|power is equal to|toughness is equal to/i.test(clean)) measure = \"power\"; else if (/damage equal to|deals? [^.]{0,80}?damage/i.test(clean)) measure = \"damage\"; else if (/life equal to|gain (?:[\\d]+|x) life/i.test(clean)) measure = \"life\"; else if (/draws? (?:a |one |[\\d]+ |x )?cards?/i.test(clean)) measure = \"cards\"; else if (/create .{0,40}tokens?/i.test(clean)) measure = \"tokens\"; else if (/affinity|costs? \\{?[0-9{}]*}? less/i.test(clean)) measure = \"cost\"; else if (/add \\{[^}]+}/i.test(clean)) measure = \"mana\"; else if (/pays? \\{?[\\d]+}?/i.test(clean)) measure = \"tax\"; const noun = counted.charAt(0).toUpperCase() + counted.slice(1); add({ ruleId: \"count.per-each\", action: \"scale\", object: object(\"permanent\", noun, [\"permanent\", counted]), qualifiers: { per: counted, amount: measure, at_least_power: pwScale ? Number(pwScale[2]) : 0 }, bucketId: \"scale/\" + measure + \"/\" + slug(counted), label: \"Scales with \" + counted + \"s\", parents: [\"Boost\"] }); } if (/\\bdouble the (?:power|toughness) of\\b/i.test(clean)) {\n      add({ ruleId: "boost.double", action: "boost",\n        object: object("creature", "Creature", ["creature"]),\n        bucketId: "boost/modify-power-toughness/target",\n        label: "Double a creature\'s power or toughness", parents: ["Boost"] });\n    }\n    if (/\\bdouble the number of [^.]{0,24}counters?\\b/i.test(clean)\n        && !/\\bdouble the number of (?:each kind of )?counter/i.test(clean)) {\n      add({ ruleId: "counter.double-typed", action: "double",\n        object: object("counter", "Any counter type", ["counter"]),\n        bucketId: "counters/double/any", label: "Double counters on permanents", parents: ["Counters"] });\n    }\n    const equalCounters = clean.match(/\\bput (?:a number of|that many|[^.]{0,14}?)\\s*([+−-]\\d+\\/[+−-]\\d+|[A-Za-z][A-Za-z\'’-]*) counters? equal to/i);\n    if (equalCounters) {\n      const counterName = equalCounters[1];\n      add({ ruleId: "counter.add-equal", action: "add",\n        object: object("counter", counterName, ["counter", slug(counterName)]),\n        bucketId: "counters/add/" + slug(counterName), label: "Add " + counterName + " counters", parents: ["Counters"] });\n    }\n\n    if (/\\bgain control of (?:target|that|up to)/i.test(clean)) {\n      add({ ruleId: "control.steal", action: "steal",\n        object: object("permanent", "Permanent", ["permanent"]),\n        bucketId: "control/steal/target", label: "Gain control of a permanent", parents: ["Control"] });\n    }\n    if (/\\btarget (?:Vehicle|Mount)[^.]{0,60}becomes? an? (?:artifact )?creature\\b/i.test(clean)) {\n      add({ ruleId: "animate.vehicle", action: "animate",\n        object: object("permanent", "Vehicle", ["vehicle", "artifact"]),\n        bucketId: "animate/vehicle/creature", label: "Turn a Vehicle into a creature", parents: ["Boost"] });\n    }\n    if (/\\bput (?:a|one|up to [^ ]+) (?:creature|permanent|land|artifact)[^.]{0,30} from your hand onto the battlefield/i.test(clean)) {\n      add({ ruleId: "cheat.into-play", action: "put-into-play",\n        object: object("permanent", "Card from hand", ["permanent"]),\n        bucketId: "cheat/into-play", label: "Put a card from hand onto the battlefield",\n        parents: ["Mana"], views: ["Mana"] });\n    }\n    if (/\\bexile (?:each opponent\'s|target player\'s|all) graveyards?\\b/i.test(clean)) {\n      add({ ruleId: "graveyard.exile-all", action: "exile",\n        object: object("card", "Graveyard", ["graveyard"]),\n        bucketId: "graveyard/exile/all-cards", label: "Exile a graveyard", parents: ["Graveyard", "Zones"] });\n    }\n    if (/\\breveals? (?:their|its) hand\\b/i.test(clean)) {\n      add({ ruleId: "cards.reveal-hand", action: "reveal",\n        object: object("hand", "Hand", ["hand"]),\n        bucketId: "cards/filter/reveal-hand", label: "Look at an opponent\'s hand", parents: ["Card access"] });\n    }\n    if (/\\breturn (?:it|this creature) to the battlefield\\b/i.test(clean)) {\n      add({ ruleId: "graveyard.reanimate-self", action: "reanimate",\n        object: object("creature", "Creature", ["creature"]),\n        bucketId: "graveyard/reanimate/self", label: "Return itself to the battlefield", parents: ["Graveyard", "Zones"] });\n    }\n    return matched;\n  }\n\n  function classifyExpression(text, ctx, state, cardContext) {\n',
    ),
    (
        'for (const tokenMatch of clean.matchAll(/\\bcreate\\s+(.{1,140}?)\\s+tokens?\\b/gi)) {',
        'for (const tokenMatch of clean.matchAll(/\\bcreate[sd]?\\s+(.{1,140}?)\\s+tokens?\\b/gi)) {',
    ),
    (
        '\\bput\\s+(?:(?:a|an|one|two|three|four|five|x|that many|\\d+)\\s+)?',
        '\\bput\\s+(?:(?:a|an|one|two|three|four|five|six|seven|eight|nine|ten|x|that many|\\d+)\\s+)?',
    ),
    (
        '        { id: "overview/token-use", cluster: "Build the board", label: "Use tokens",',
        '        { id: "overview/cheat", cluster: "Build the board", label: "Put cards into play", description: "Put a permanent onto the battlefield from your hand, or turn an artifact into a creature.", include: [/^cheat\\//, /^animate\\//] },\n        { id: "overview/token-use", cluster: "Build the board", label: "Use tokens",',
    ),
]

# Second coverage batch: the misses the first batch left (destroy-up-to, a graveyard of another
# player, land recursion, life equal to a count, untap, copy).
COVERAGE2_PATCHES = [
    (
        '    if (!plainDamage && /\\bdeals? [^.]{0,90}damage to (?:up to [a-z ]+ )?(?:target|any|each)/i.test(clean)) {',
        '    if (!plainDamage && /\\bdeals? [^.]{0,90}damage (?:equal to [^.]{0,80}? )?to (?:up to [a-z ]+ )?(?:target|any|each)/i.test(clean)) {',
    ),
    (
        '    if (/\\b(?:gains?|has|have)\\b/i.test(clean) && /\\b(?:creature|creatures|it|equipped|attacking|token)\\b/i.test(clean)) {',
        '    if (/\\b(?:gains?|has|have)\\b/i.test(clean)) {',
    ),
    (
        '    return matched;\n  }\n\n  function classifyExpression(text, ctx, state, cardContext) {\n',
        '    const destroyUpTo = clean.match(/\\bdestroy up to (?:one|two|three) target ([^.;]+)/i);\n    if (destroyUpTo) {\n      const kind = /creature/i.test(destroyUpTo[1]) ? "creature" : /artifact/i.test(destroyUpTo[1]) ? "artifact" : "permanent";\n      add({ ruleId: "removal.destroy-up-to", action: "destroy",\n        object: object(kind, titleCase(kind), [kind]),\n        bucketId: "removal/destroy/target-" + kind, label: "Destroy target " + kind, parents: ["Removal"] });\n    }\n    if (/\\bfrom (?:defending player\'s|each player\'s|an opponent\'s|target opponent\'s) graveyard (?:onto the battlefield|to (?:your|its owner\'s|the) hand)/i.test(clean)) {\n      add({ ruleId: "graveyard.return-other", action: "reanimate",\n        object: object("card", "Card from a graveyard", ["card", "graveyard"]),\n        bucketId: "graveyard/reanimate/other-player", label: "Return a card from another graveyard", parents: ["Graveyard", "Zones"] });\n    }\n    if (/\\breturn [^.]{0,80}land cards? from your graveyard to the battlefield/i.test(clean)) {\n      add({ ruleId: "graveyard.reanimate-land", action: "reanimate",\n        object: object("land", "Land", ["land", "graveyard"]),\n        bucketId: "graveyard/reanimate/land-to-battlefield", label: "Return land cards from graveyard to the battlefield", parents: ["Graveyard", "Lands"] });\n    }\n    if (/\\byou gain life equal to\\b/i.test(clean)) {\n      add({ ruleId: "life.gain-equal", action: "gain-life",\n        object: object("life", "Life", ["life"]), bucketId: "life/gain/direct", label: "Gain life", parents: ["Life"] });\n    }\n    if (/\\buntaps? (?:that|target|it)\\b/i.test(clean)) {\n      add({ ruleId: "control.untap", action: "untap",\n        object: object("permanent", "Permanent", ["permanent"]),\n        bucketId: "control/untap/target", label: "Untap a permanent", parents: ["Control"] });\n    }\n    if (/\\bas a copy of\\b/i.test(clean)) {\n      add({ ruleId: "copy.permanent", action: "copy",\n        object: object("permanent", "Permanent", ["permanent"]),\n        bucketId: "copy/permanent/enters", label: "Enter as a copy", parents: ["Build"] });\n    }\n    if (/\\bcopy (?:it|that (?:spell|card))\\b/i.test(clean)) {\n      add({ ruleId: "copy.spell", action: "copy",\n        object: object("spell", "Spell", ["spell"]),\n        bucketId: "copy/spell", label: "Copy a card or spell", parents: ["Build"] });\n    }\n    return matched;\n  }\n\n  function classifyExpression(text, ctx, state, cardContext) {\n',
    ),
    (
        'include: [/^cheat\\//, /^animate\\//]',
        'include: [/^cheat\\//, /^animate\\//, /^copy\\//]',
    ),
]

# Batch 3: keep the coverage rules from doubling a fact the base rule already produced.
COVERAGE3_PATCHES = [
    (
        '      const kind = /creature/i.test(destroyUpTo[1]) ? "creature" : /artifact/i.test(destroyUpTo[1]) ? "artifact" : "permanent";',
        '      const kind = /artifact/i.test(destroyUpTo[1]) ? "artifact" : /creature/i.test(destroyUpTo[1]) ? "creature" : "permanent";',
    ),
    (
        "    const debuff = clean.match(/\\b(all creatures|each creature|target creature|creatures you don't control)[^.]{0,60}? gets? -[x\\d]+\\/-[x\\d]+/i);\n    if (debuff) {",
        "    const debuff = clean.match(/\\b(all creatures|each creature|target creature|creatures you don't control)[^.]{0,60}? gets? -[x\\d]+\\/-[x\\d]+/i);\n    if (debuff && /-[^-]?x/i.test(debuff[0])) {",
    ),
    (
        '    if (/\\bgain control of (?:target|that|up to)/i.test(clean)) {',
        '    if (/\\bgain control of (?:that|up to)/i.test(clean)) {',
    ),
]

# Rule 3.3: a token made and then sacrificed is a real event for the Void payoffs, and
# Beam me up carries a cost that takes a creature off the battlefield.
SACRIFICE_PATCHES = [
    (
        '    for (const sacrifice of clean.matchAll(/sacrifice\\s+(?:an?|one|two|three|x|any number of)?\\s*(Clues?|artifacts?|creatures?|tokens?|lands?|permanents?|this (?:artifact|creature|land|permanent))/gi)) {\n',
        '    for (const sacrifice of clean.matchAll(/sacrifice\\s+(?:an?|one|two|three|x|any number of)?(?:another\\s+)?\\s*(Clues?|artifacts?|creatures?|tokens?|lands?|permanents?|this (?:artifact|creature|land|permanent))/gi)) {\n',
    ),
    (
        '      ["void", "keywords/void", "Void", ["Timing"]]\n    ];\n',
        '      ["void", "keywords/void", "Void", ["Timing"]],\n      ["beam me up", "keywords/beam-me-up", "Beam me up", ["Graveyard"]]\n    ];\n',
    ),
    (
        '      if (keyword === "warp") {\n',
        '      if (keyword === "beam me up") {\n        matched = addFact(state, ctx, {\n          ruleId: "keyword.beam-me-up.return",\n          role: "cost",\n          action: "return-to-hand",\n          object: object("creature", "Creature", ["creature"]),\n          bucketId: "protection/return-to-hand/target-creature",\n          label: "Return a creature to hand (Beam me up)",\n          parents: ["Protection", "Graveyard"]\n        }) || matched;\n      }\n      if (keyword === "warp") {\n',
    ),
    (
        '    if (/\\bgain control of (?:that|up to)/i.test(clean)) {\n',
        '    if (/\\bsacrifice (?:it|this token|that token)\\b/i.test(clean)\n        && /\\b(?:at the beginning of (?:the next|your next|your)?\\s*end step|at end of turn)\\b/i.test(clean)) {\n      add({ ruleId: "sacrifice.self-delayed", action: "sacrifice",\n        object: object("creature", "Token", ["creature", "token"]),\n        bucketId: "sacrifice/self/delayed",\n        label: "Sacrifice a token at end of turn", parents: ["Sacrifice"] });\n    }\n    if (/\\bgain control of (?:that|up to)/i.test(clean)) {\n',
    ),
]

# Rule 3.4: a deck that pays off token creation has to listen for it, not only for the entry
# the token happens to make.
TOKEN_TRIGGER_PATCHES = [
    (
        '    if (/\\b(?:you|a player) sacrifice(?:s)?\\b|\\b(?:an artifact|a creature|a token|a clue) is sacrificed\\b/i.test(text)) {\n',
        '    if (/\\bwhenever you create (?:or sacrifice )?(?:a|one or more) tokens?\\b/i.test(text)) {\n      matched = addFact(state, ctx, {\n        ruleId: "trigger.token-created",\n        role: "trigger",\n        action: "react-to-create-token",\n        object: object("token", "Token", ["token"]),\n        bucketId: "tokens/react-to/create",\n        label: "React to creating a token",\n        parents: ["Tokens"]\n      }) || matched;\n      if (/\\bor sacrifice\\b/i.test(text)) {\n        matched = addFact(state, ctx, {\n          ruleId: "trigger.token-sacrificed",\n          role: "trigger",\n          action: "react-to-sacrifice",\n          object: object("token", "Token", ["token", "creature"]),\n          bucketId: "sacrifice/react-to/token",\n          label: "React to sacrificing a token",\n          parents: ["Sacrifice"]\n        }) || matched;\n      }\n    }\n    if (/\\b(?:you|a player) sacrifice(?:s)?\\b|\\b(?:an artifact|a creature|a token|a clue) is sacrificed\\b/i.test(text)) {\n',
    ),
]

# Rule 3.5: a trigger that accepts two kinds ("another creature or artifact you control enters")
# was narrowed to the last one, so a creature token never matched it.
PAIR_ENTER_PATCHES = [
    (
        '    const entering = text.match(/(?:one or more |another |a |an |this )?(nontoken )?(artifact|creature|token|land|enchantment|permanent|aura|equipment|vehicle|spacecraft|battle|siege)s? (?:you control )?(?:enters|enter)/i);\n',
        '    // "another creature or artifact you control enters" names two kinds; both satisfy it.\n    const enteringPair = text.match(/\\b(?:this or another|another|a|an|one or more)\\s+((?:artifact|creature|token|land|enchantment|permanent|aura|equipment|vehicle|spacecraft|battle|siege))\\s+or\\s+(?:an?\\s+|another\\s+)?((?:artifact|creature|token|land|enchantment|permanent|aura|equipment|vehicle|spacecraft|battle|siege))\\s+(?:you control\\s+)?enters?\\b/i);\n    if (enteringPair) {\n      const pairKinds = [enteringPair[1].toLowerCase(), enteringPair[2].toLowerCase()];\n      matched = addFact(state, ctx, {\n        ruleId: "trigger.object-enters-pair",\n        role: "trigger",\n        action: "react-to-enter",\n        object: object(pairKinds[0], titleCase(pairKinds.join(" or ")), uniqueSorted(Array.from(new Set(pairKinds)))),\n        scope: "any",\n        bucketId: "permanents/react-to/enter-" + pairKinds.map(slug).join("-or-"),\n        label: "React to " + pairKinds.join(" or ") + " entering",\n        parents: ["Permanents"]\n      }) || matched;\n    }\n    const entering = text.match(/(?:one or more |another |a |an |this )?(nontoken )?(artifact|creature|token|land|enchantment|permanent|aura|equipment|vehicle|spacecraft|battle|siege)s? (?:you control )?(?:enters|enter)/i);\n',
    ),
    (
        '    if (entering) {\n',
        '    if (entering && !enteringPair) {\n',
    ),
]

FACT_REPAIR_PATCHES = [
    # The link work showed three holes that belong to the classifier itself:
    #   "steal" and "gain-control" are the same effect under two names,
    #   a plural type ("creatures") never matches a request for "creature",
    #   and a fact that says "deals 3 damage" without recording the 3 cannot be compared.
    # They are repaired here, so every deck is read the same way, old data included.
    ("""      function normalizeAutoBucketFacts(value) {
        if (!Array.isArray(value)) return [];
        return value
          .filter(fact => fact && fact.bucket && fact.bucket.id && fact.bucket.label)
          .map(fact => JSON.parse(JSON.stringify(fact)));
      }""",
     """      const TYPE_SINGULAR = {
        creatures: "creature", artifacts: "artifact", enchantments: "enchantment",
        lands: "land", permanents: "permanent", tokens: "token", spells: "spell",
        players: "player"
      };
      const AMOUNT_PATTERNS = {
        draw: /\\bdraws?\\s+(a|an|one|two|three|four|five|six|seven|eight|nine|ten|x|that many|\\d+)\\s+cards?\\b/i,
        "deal-damage": /\\bdeals?\\s+(a|an|one|two|three|four|five|six|seven|eight|nine|ten|x|that much|\\d+)\\s+damage\\b/i,
        "gain-life": /\\bgains?\\s+(a|an|one|two|three|four|five|six|seven|eight|nine|ten|x|that much|\\d+)\\s+life\\b/i,
        "lose-life": /\\bloses?\\s+(a|an|one|two|three|four|five|six|seven|eight|nine|ten|x|that much|\\d+)\\s+life\\b/i
      };

      function repairAutoBucketFact(fact) {
        if (!fact || typeof fact !== "object") return fact;
        if (fact.action === "steal") fact.action = "gain-control";
        const object = fact.object;
        if (object && Array.isArray(object.types)) {
          const repaired = [];
          for (const type of object.types) {
            const singular = typeof type === "string" ? (TYPE_SINGULAR[type] || type) : type;
            if (!repaired.includes(singular)) repaired.push(singular);
          }
          object.types = repaired;
        }
        const pattern = AMOUNT_PATTERNS[fact.action];
        if (pattern) {
          fact.qualifiers = fact.qualifiers || {};
          if (!fact.qualifiers.amount) {
            const match = pattern.exec(String((fact.evidence || {}).clause || ""));
            if (match) fact.qualifiers.amount = String(match[1]).toLowerCase();
          }
        }
        return fact;
      }

      function normalizeAutoBucketFacts(value) {
        if (!Array.isArray(value)) return [];
        return value
          .filter(fact => fact && fact.bucket && fact.bucket.id && fact.bucket.label)
          .map(fact => repairAutoBucketFact(JSON.parse(JSON.stringify(fact))));
      }""", 1),
]

RULE_VERSION_PATCHES = [
    # Bump whenever the rules change so stored decks can be told apart from current ones.
    ('const RULE_VERSION = "2-prototype.2";', 'const RULE_VERSION = "2-prototype.3";', 1),
    ('const RULE_VERSION = "2-prototype.3";', 'const RULE_VERSION = "2-prototype.4";', 1),
    ('const RULE_VERSION = "2-prototype.4";', 'const RULE_VERSION = "2-prototype.5";', 1),
    ('const RULE_VERSION = "2-prototype.5";', 'const RULE_VERSION = "2-prototype.6";', 1),
    ('const RULE_VERSION = "2-prototype.6";', 'const RULE_VERSION = "2-prototype.7";', 1),
    ('const RULE_VERSION = "2-prototype.7";', 'const RULE_VERSION = "2-prototype.8";', 1),
    ('const RULE_VERSION = "2-prototype.8";', 'const RULE_VERSION = "2-prototype.9";', 1),
    ('const RULE_VERSION = "2-prototype.9";', 'const RULE_VERSION = "3.0";', 1),
    ('const RULE_VERSION = "3.0";', 'const RULE_VERSION = "3.1";', 1),
    ('const RULE_VERSION = "3.1";', 'const RULE_VERSION = "3.2";', 1),
    ('const RULE_VERSION = "3.2";', 'const RULE_VERSION = "3.3";', 1),
    ('const RULE_VERSION = "3.3";', 'const RULE_VERSION = "3.4";', 1),
    ('const RULE_VERSION = "3.4";', 'const RULE_VERSION = "3.5";', 1),
    ('const RULE_VERSION = "3.5";', 'const RULE_VERSION = "3.6";', 1),
    ('const RULE_VERSION = "3.6";', 'const RULE_VERSION = "3.7";', 1),
    ('const RULE_VERSION = "3.7";', 'const RULE_VERSION = "3.8";', 1),
    ('const RULE_VERSION = "3.8";', 'const RULE_VERSION = "3.9";', 1),
    ('const RULE_VERSION = "3.9";', 'const RULE_VERSION = "4.0";', 1),
    ('const RULE_VERSION = "4.0";', 'const RULE_VERSION = "4.1";', 1),
    ('const RULE_VERSION = "4.1";', 'const RULE_VERSION = "4.2";', 1),
    ('const RULE_VERSION = "4.2";', 'const RULE_VERSION = "4.3";', 1),
    ('const RULE_VERSION = "4.3";', 'const RULE_VERSION = "4.4";', 1),
    ('const RULE_VERSION = "4.4";', 'const RULE_VERSION = "4.5";', 1),
    ('const RULE_VERSION = "4.5";', 'const RULE_VERSION = "4.6";', 1),
    ('const RULE_VERSION = "4.6";', 'const RULE_VERSION = "4.7";', 1),
    ('const RULE_VERSION = "4.7";', 'const RULE_VERSION = "4.8";', 1),
    ('const RULE_VERSION = "4.8";', 'const RULE_VERSION = "4.9";', 1),
]

GAP_NOISE_PATCHES = [
    # Reminder text and loyalty fragments are not gaps; counting them hid the real ones.
    (r"""    /^(?:\{[^}]+\}[, ]*)+$/""",
     r"""    /^(?:\{[^}]+\}[, ]*)+$/,
    /^[+\u2212-]?\d+$/,
    /^\(.+\)\.?$/,
    /^put the rest (?:on the bottom|into your graveyard).*$/i,
    /^this ability triggers only once each turn\.?$/i,
    /^if you do, it enters tapped\.?$/i,
    /^activate only as a sorcery\.?$/i,
    /^as this .+ enters, choose a color\.?$/i""", 1),
]

SUBTYPE_ORDER_PATCHES = [
    # Sections read biggest-first by the cards actually filed in them, and empty ones are dropped:
    # a card with two active subtypes only lands in the first match, so raw subtype counts lie.
    ("""          const subtypeRows = byParent.has(category.id)
            ? [...byParent.get(category.id).values()].filter(section => active.has(section.id)).sort((a, b) => a.label.localeCompare(b.label))
            : [];""",
     """          const filedCounts = new Map();
          for (const card of cards) {
            const filedId = activeSubtypeCategoryFor(card);
            if (filedId) filedCounts.set(filedId, (filedCounts.get(filedId) || 0) + 1);
          }
          const subtypeRows = byParent.has(category.id)
            ? [...byParent.get(category.id).values()]
                .filter(section => active.has(section.id) && (filedCounts.get(section.id) || 0) > 0)
                .map(section => ({ ...section, count: filedCounts.get(section.id) || 0 }))
                .sort((a, b) => b.count - a.count || a.label.localeCompare(b.label))
            : [];""", 1),
    ('const sections = [...groups.get(category.id).values()].sort((a, b) => a.label.localeCompare(b.label));',
     'const sections = [...groups.get(category.id).values()].sort((a, b) => b.count - a.count || a.label.localeCompare(b.label));', 1),
]

COLOR_NUMBERS_PATCHES = [
    ("""          row.innerHTML = `
            <span class="color-dot" style="background: ${color.color}; color: ${color.text};">${color.short}</span>
            <span class="color-track" title="Required pips"><span class="color-fill" style="width: ${data.maxColor ? (data.demand[color.id] / data.maxColor) * 100 : 0}%; background: ${color.color};"></span></span>
            <span class="color-track" title="Sources"><span class="color-fill" style="width: ${data.maxColor ? (data.sources[color.id] / data.maxColor) * 100 : 0}%; background: ${color.color};"></span></span>
          `;""", """          const danger = data.sources[color.id] < data.demand[color.id];
          row.innerHTML = `
            <span class="color-dot" style="background: ${color.color}; color: ${color.text};">${color.short}</span>
            <span class="color-track" title="Required pips"><span class="color-fill" style="width: ${data.maxColor ? (data.demand[color.id] / data.maxColor) * 100 : 0}%; background: ${color.color};"></span><span class="color-value">${data.demand[color.id]}</span></span>
            <span class="color-track" title="Sources"><span class="color-fill" style="width: ${data.maxColor ? (data.sources[color.id] / data.maxColor) * 100 : 0}%; background: ${color.color};"></span><span class="color-value${danger ? " is-short" : ""}">${data.sources[color.id]}</span></span>
          `;""", 1),
]


# Cards keep the z-index the Roles view gave them: coming back to the table, piles were
# painted bottom-first and the card that should be in front was hidden behind the others.
PILE_STACKING_PATCHES = [
    ("""        for (const card of state.cards) {
          const element = ensureCardElement(card);
          element.dataset.category = currentTableCategory(card);
          element.dataset.manaValue = String(effectiveManaValue(card));
          element.dataset.landManaGroup = hasType(card, "land") ? landManaGroup(card).id : "";
          if (card.manualPosition) {
            setElementPosition(element, card.manualPosition.x, card.manualPosition.y);
          } else {
            const position = layout.positions.get(card.id);
            if (position) setElementPosition(element, position.x, position.y);
          }
        }""", """        const placed = [];
        for (const card of state.cards) {
          const element = ensureCardElement(card);
          element.dataset.category = currentTableCategory(card);
          element.dataset.manaValue = String(effectiveManaValue(card));
          element.dataset.landManaGroup = hasType(card, "land") ? landManaGroup(card).id : "";
          if (card.manualPosition) {
            setElementPosition(element, card.manualPosition.x, card.manualPosition.y);
            placed.push({ element, x: card.manualPosition.x, y: card.manualPosition.y, order: Number(card.order) || 0 });
          } else {
            const position = layout.positions.get(card.id);
            if (position) {
              setElementPosition(element, position.x, position.y);
              placed.push({ element, x: position.x, y: position.y, order: Number(card.order) || 0 });
            }
          }
        }
        // The Roles view paints its columns with its own z-index, so the table has to
        // rebuild the pile order every time it draws: within a pile the card further
        // down is in front, exactly as the pile looks before a view switch.
        placed.sort((a, b) => (a.x - b.x) || (a.y - b.y) || (a.order - b.order));
        placed.forEach((entry, index) => {
          entry.element.style.zIndex = String(31 + index);
        });""", 1),
]


# Stats is read like a page: it scrolls with the wheel and never moves under a drag.
STATS_SCROLL_PATCHES = [
    ("""        if (event.button !== 0 || event.target.closest("#controls, #strategy-workspace") || event.target.closest(".card, .bucket-card, .category-label")) return;""", """        if (state.viewMode === "stats") return;
        if (event.button !== 0 || event.target.closest("#controls, #strategy-workspace") || event.target.closest(".card, .bucket-card, .category-label")) return;""", 1),
]


def patches() -> list:
    prelude = (_DIR / "prelude.js").read_text(encoding="utf-8")
    framing = (_DIR / "framing.js").read_text(encoding="utf-8")
    studio = (_DIR / "studio.js").read_text(encoding="utf-8")
    ui_css = (_DIR / "ui.css").read_text(encoding="utf-8")
    return [
        ("<head>", f"<head>\n<{_TAG}>{prelude}</{_TAG}>"),
        ("</head>", f"<style>\n{ui_css}</style>\n</head>"),
        ("const HISTORY_LIMIT = 5;", "const HISTORY_LIMIT = 30;"),
        (SAVE_HEAD, SAVE_HEAD_PATCHED),
        ('        setStatus("Saving", "Preparing save file.", 1);', '        report("Saving", "Preparing save file.", 1);'),
        ("        window.mtgViewer = {\n", "        window.mtgViewer = {\n          " + EXPORTS + "\n"),
        ("</body>", f"<{_TAG}>{studio}</{_TAG}>\n<{_TAG}>{framing}</{_TAG}>\n</body>"),
    ] + DIALOG_PATCHES + VIEWPORT_RECT_PATCHES + RENAME_PATCHES + PILE_STACKING_PATCHES + STATS_SCROLL_PATCHES + COLOR_NUMBERS_PATCHES + SUBTYPE_ORDER_PATCHES + TOKEN_KINDS_PATCHES + KEYWORD_PATCHES + ACTION_PATCHES + GAP_NOISE_PATCHES + EFFECT_GROUPS_PATCHES + RULES_V3_PATCHES + EFFECT_GROUPS_V2_PATCHES + LABEL_PATCHES + LABEL_PATCHES_2 + RULE_VERSION_PATCHES + FACT_REPAIR_PATCHES + PRINTED_NAME_PATCHES + MYTHICTOOLS_LANGUAGE_PATCHES + MANA_PIP_PATCHES + AUDIT_RULES_PATCHES + COVERAGE_PATCHES + COVERAGE2_PATCHES + COVERAGE3_PATCHES + SACRIFICE_PATCHES + TOKEN_TRIGGER_PATCHES + PAIR_ENTER_PATCHES + EVASION_CLUSTER_PATCHES + UI_CLICK_PATCHES + EDIT_LINKS_PATCHES


def build() -> str:
    html = (_DIR / "mtg-viewer.html").read_text(encoding="utf-8")
    for patch in patches():
        anchor, replacement = patch[0], patch[1]
        expected = patch[2] if len(patch) > 2 else 1
        count = html.count(anchor)
        if count != expected:
            raise RuntimeError(f"Viewer patch anchor found {count} times, expected {expected}: {anchor[:60]!r}")
        html = html.replace(anchor, replacement, expected)
    return html


_SOURCES = ("mtg-viewer.html", "prelude.js", "framing.js", "studio.js", "ui.css")
_version_cache = {}


def version() -> str:
    """Hash of the files the served viewer is built from; changes when any of them changes."""
    import hashlib

    paths = [_DIR / name for name in _SOURCES] + [Path(__file__)]
    key = tuple(p.stat().st_mtime_ns for p in paths)
    if key not in _version_cache:
        digest = hashlib.sha256()
        for path in paths:
            digest.update(path.read_bytes())
        _version_cache.clear()
        _version_cache[key] = digest.hexdigest()[:16]
    return _version_cache[key]


def payload() -> dict:
    html = build()
    dev = (Path(__file__).resolve().parent.parent / ".dev-reload").exists()
    return {"html": html, "bytes": len(html.encode("utf-8")), "version": version(), "dev": dev}
