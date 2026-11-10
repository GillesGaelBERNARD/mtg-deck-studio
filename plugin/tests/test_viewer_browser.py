"""Browser tests of the viewer in the same sandbox posture as the Desktop pane.

The host page embeds the viewer as a data: URL in an iframe sandboxed with
allow-scripts only (opaque origin, not a secure context) and talks to it over
postMessage exactly as desktop/plugin.js does.

Run: uv run --no-project --with playwright python -m unittest tests.test_viewer_browser
"""
import base64
import copy
import json
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_core import core, sample_bundle  # noqa: E402

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    sync_playwright = None

HOST = """<!doctype html><html><body style="margin:0">
<iframe id="f" sandbox="allow-scripts allow-forms" style="width:1400px;height:900px;border:0"></iframe>
<script>
window.msgs = [];
window.addEventListener('message', e => {
  if (e.source !== document.getElementById('f').contentWindow) return;
  window.msgs.push(e.data);
});
window.send = m => document.getElementById('f').contentWindow.postMessage(m, '*');
</script></body></html>"""


PILE_ORDER_JS = """() => {
  const cards = [...document.querySelectorAll('.card')].filter(c => !c.hidden && c.getBoundingClientRect().width > 1);
  const columns = new Map();
  for (const card of cards) {
    const rect = card.getBoundingClientRect();
    const key = Math.round(rect.left);
    if (!columns.has(key)) columns.set(key, []);
    columns.get(key).push({ top: rect.top, z: Number(card.style.zIndex) || 0 });
  }
  let bad = 0;
  for (const column of columns.values()) {
    column.sort((a, b) => a.top - b.top);
    for (let i = 1; i < column.length; i += 1) if (column[i].z <= column[i - 1].z) bad += 1;
  }
  return bad;
}"""

@unittest.skipIf(sync_playwright is None, "playwright is not installed")



class ViewerInSandboxTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch()
        cls.url = "data:text/html;base64," + base64.b64encode(core.viewer.build().encode("utf-8")).decode("ascii")
        bundle = core.store.strip_images(sample_bundle())
        cls.document = bundle

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()

    def setUp(self):
        self.page = self.browser.new_page(viewport={"width": 1400, "height": 900})
        self.page.set_content(HOST)
        self.page.evaluate("url => { document.getElementById('f').src = url }", self.url)
        self.wait_for(lambda m: m.get("type") == "wb.ready", 20)
        self.page.evaluate("doc => window.send({type: 'wb.load', deck_id: 'deck_test0001', rev: 1, document: doc})", copy.deepcopy(self.document))
        self.wait_for(lambda m: m.get("type") == "wb.loaded", 30)

    def tearDown(self):
        self.page.close()

    def messages(self):
        return self.page.evaluate("window.msgs")

    def wait_for(self, predicate, timeout=15.0, start=0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            for m in self.messages()[start:]:
                if predicate(m):
                    return m
            time.sleep(0.1)
        self.fail(f"no matching message within {timeout}s; last: {[m.get('type') for m in self.messages()[-8:]]}")

    def command(self, op, args=None, kind="document", expected_rev=None):
        start = len(self.messages())
        cid = f"c{time.time_ns()}"
        self.page.evaluate("m => window.send(m)", {"type": "wb.cmd", "command_id": cid, "deck_id": "deck_test0001", "op": op,
                                                   "args": args or {}, "expected_rev": expected_rev, "meta": {"kind": kind}})
        ack = self.wait_for(lambda m: m.get("type") == "wb.ack" and m.get("command_id") == cid, 20, start)
        if ack.get("snapshot"):
            self.page.evaluate("r => window.send({type: 'wb.saved', status: 'saved', rev: r})", ack["rev"])
        return ack

    def frame(self):
        return self.page.frames[1]

    def expand_link_groups(self):
        """The link panel opens folded: one row per kind of link, each with its count. A test that
        reads the cards has to open the rows first, the way a player does."""
        frame = self.frame()
        frame.wait_for_selector("#wb-links-panel:not([hidden]) .wb-link-group", timeout=8000)
        frame.evaluate("""() => document.querySelectorAll('#wb-links-panel [data-group-toggle][aria-expanded="false"]')
            .forEach(button => button.click())""")
        frame.wait_for_selector("#wb-links-panel .wb-link-tile", timeout=8000)

    def test_sandbox_lacks_secure_context_and_polyfill_supplies_ids(self):
        info = self.frame().evaluate("({secure: isSecureContext, origin: location.origin, uuid: crypto.randomUUID()})")
        self.assertFalse(info["secure"])
        self.assertEqual(info["origin"], "null")
        self.assertRegex(info["uuid"], r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")

    def test_loaded_document_keeps_card_ids(self):
        ids = self.frame().evaluate("window.mtgViewer.state.cards.map(c => c.id)")
        self.assertEqual(ids, [c["id"] for c in self.document["cards"]])


    def test_pile_stacking_survives_a_visit_to_roles(self):
        # The Roles view paints its columns with its own z-index; the table must
        # rebuild the pile order when it is drawn again, or the card that should be
        # in front ends up hidden behind the ones above it.
        self.assertEqual(self.pile_order_violations(), 0)
        for view in ("buckets", "cards"):
            mode = "table" if view == "cards" else view
            self.frame().evaluate("v => window.mtgViewer.setViewMode(v)", mode)
            self.frame().wait_for_function("v => window.mtgViewer.state.viewMode === v", arg=mode, timeout=5000)
            time.sleep(0.9)
        self.assertEqual(self.pile_order_violations(), 0)

    def pile_order_violations(self):
        return self.frame().evaluate(PILE_ORDER_JS)


    def test_stats_starts_at_the_top_and_scrolls_with_the_wheel(self):
        # Stats reads like a page: pinned to the top of the pane, moved by the wheel,
        # never by dragging.
        self.frame().evaluate("window.mtgViewer.setViewMode('stats')")
        self.frame().wait_for_function("() => !!document.querySelector('.stats-dashboard-board')", timeout=5000)
        time.sleep(1.2)
        start = self.stats_frame_state()
        self.assertLessEqual(abs(start["boardTop"] - (start["safeTop"] + 12)), 4, start)
        self.frame().evaluate("""() => document.getElementById('viewport').dispatchEvent(
            new WheelEvent('wheel', { deltaY: 240, clientX: 600, clientY: 400, bubbles: true, cancelable: true }))""")
        time.sleep(0.4)
        scrolled = self.stats_frame_state()
        self.assertLess(scrolled["y"], start["y"])
        self.assertLess(scrolled["boardTop"], start["boardTop"])
        self.frame().evaluate("""() => {
          const vp = document.getElementById('viewport');
          const at = (x, y) => ({ pointerId: 1, button: 0, clientX: x, clientY: y, bubbles: true, cancelable: true });
          vp.dispatchEvent(new PointerEvent('pointerdown', at(300, 300)));
          vp.dispatchEvent(new PointerEvent('pointermove', at(300, 120)));
          vp.dispatchEvent(new PointerEvent('pointerup', at(300, 120)));
        }""")
        time.sleep(0.4)
        self.assertEqual(self.stats_frame_state()["y"], scrolled["y"])

    def stats_frame_state(self):
        return self.frame().evaluate("""() => {
          const safe = window.mtgFrame.safeRect();
          const board = document.querySelector('.stats-dashboard-board').getBoundingClientRect();
          return { boardTop: Math.round(board.top), boardBottom: Math.round(board.bottom),
                   safeTop: Math.round(safe.top), y: Math.round(window.mtgViewer.state.camera.y) };
        }""")


    def test_collapsing_the_menu_moves_the_table_with_it(self):
        # The panel floats over the table; collapsing it must give the space back
        # instead of leaving the cards parked behind a dead strip.
        self.assertFalse(self.panel_state()["collapsed"])
        expanded = self.panel_state()
        self.frame().evaluate("document.getElementById('toggle-panel').click()")
        time.sleep(1.1)
        collapsed = self.panel_state()
        self.assertTrue(collapsed["collapsed"])
        self.assertLess(collapsed["cardLeft"], expanded["cardLeft"] - 150)
        self.assertLessEqual(abs(collapsed["cardLeft"] - collapsed["safeLeft"]), 40)
        self.frame().evaluate("document.getElementById('toggle-panel').click()")
        time.sleep(1.1)
        reopened = self.panel_state()
        self.assertFalse(reopened["collapsed"])
        self.assertGreater(reopened["cardLeft"], collapsed["cardLeft"] + 150)

    def panel_state(self):
        return self.frame().evaluate("""() => {
          const safe = window.mtgFrame.safeRect();
          const cards = [...document.querySelectorAll('.card')]
            .map(card => card.getBoundingClientRect()).filter(rect => rect.width > 1);
          return {
            collapsed: document.getElementById('controls').classList.contains('collapsed'),
            safeLeft: Math.round(safe.left),
            cardLeft: cards.length ? Math.round(Math.min(...cards.map(rect => rect.left))) : null,
            inset: getComputedStyle(document.documentElement).getPropertyValue('--wb-left-inset').trim()
          };
        }""")


    def test_strategy_starts_at_the_top_and_keeps_the_panel_rail(self):
        self.frame().evaluate("window.mtgViewer.setViewMode('strategy')")
        self.frame().wait_for_function("() => !document.getElementById('strategy-workspace').hidden", timeout=5000)
        time.sleep(0.9)
        state = self.frame().evaluate("""() => {
          const shell = document.querySelector('.strategy-notes-shell').getBoundingClientRect();
          const panel = document.getElementById('controls');
          return { top: Math.round(shell.top), left: Math.round(shell.left),
                   panelDisplay: getComputedStyle(panel).display,
                   panelRight: Math.round(panel.getBoundingClientRect().right) };
        }""")
        self.assertLessEqual(state["top"], 20, state)
        self.assertNotEqual(state["panelDisplay"], "none")
        self.assertGreaterEqual(state["left"], state["panelRight"], state)
        self.assertGreater(state["left"] - state["panelRight"], 8, state)


    def test_roles_header_stays_visible_at_the_top(self):
        # Roles draws a header, description and sub-role chips above the cards; with
        # free space below, the whole block must start at the top instead of drifting
        # up until the writing is cut off.
        self.frame().evaluate("window.mtgViewer.setViewMode('buckets')")
        self.frame().wait_for_function("() => !!document.querySelector('.bucket-category-board')", timeout=5000)
        time.sleep(1.4)
        state = self.frame().evaluate("""() => {
          const safe = window.mtgFrame.safeRect();
          const board = document.querySelector('.bucket-category-board').getBoundingClientRect();
          return { boardTop: Math.round(board.top), safeTop: Math.round(safe.top) };
        }""")
        self.assertGreaterEqual(state["boardTop"], state["safeTop"] - 1, state)
        self.assertLessEqual(state["boardTop"] - state["safeTop"], 40, state)


    def test_closing_the_menu_works_in_stats(self):
        # Framing used to reopen the panel for wide panes, so the close button looked
        # dead in Stats.
        self.frame().evaluate("window.mtgViewer.setViewMode('stats')")
        self.frame().wait_for_function("() => !!document.querySelector('.stats-dashboard-board')", timeout=5000)
        time.sleep(1.2)
        self.frame().evaluate("document.getElementById('toggle-panel').click()")
        time.sleep(1.2)
        closed = self.stats_panel_state()
        self.assertTrue(closed["collapsed"])
        self.assertLessEqual(abs(closed["boardLeft"] - closed["safeLeft"]), 40)
        self.frame().evaluate("document.getElementById('toggle-panel').click()")
        time.sleep(1.2)
        self.assertFalse(self.stats_panel_state()["collapsed"])

    def stats_panel_state(self):
        return self.frame().evaluate("""() => {
          const safe = window.mtgFrame.safeRect();
          const board = document.querySelector('.stats-dashboard-board').getBoundingClientRect();
          return { collapsed: document.getElementById('controls').classList.contains('collapsed'),
                   safeLeft: Math.round(safe.left), boardLeft: Math.round(board.left) };
        }""")


    def test_sideboard_tab_lists_candidates_and_reports_actions(self):
        # The pane posts the deck's sideboard; the tab lists it and each button asks the
        # host to act. Nothing here touches the deck by itself.
        self.frame().wait_for_selector(".wb-sideboard-tab", timeout=8000)
        cards = [
            {"scryfall_id": "11111111-2222-3333-4444-555555555555", "name": "Cryptic Command",
             "mana_cost": "{2}{U}{U}{U}", "mv": 4, "type_line": "Instant", "reason": "fits the tap plan"},
            {"scryfall_id": "66666666-7777-8888-9999-000000000000", "name": "Frost Breath",
             "mana_cost": "{2}{U}", "mv": 3, "type_line": "Instant", "reason": "keeps creatures tapped"},
        ]
        self.page.evaluate("m => window.send(m)", {"type": "wb.sideboard", "deck_id": "deck_test0001", "cards": cards})
        self.frame().wait_for_function("() => document.querySelectorAll('.wb-sideboard-card').length === 2", timeout=5000)
        self.assertIn("2", self.frame().inner_text(".wb-sideboard-tab"))
        self.frame().click(".wb-sideboard-tab")
        self.assertTrue(self.frame().is_visible(".wb-sideboard"))
        text = self.frame().inner_text(".wb-sideboard")
        self.assertIn("Cryptic Command", text)
        self.assertIn("fits the tap plan", text)
        # One action per candidate: "Swap in" opens the chooser, nothing reaches the deck yet.
        actions = self.frame().eval_on_selector_all(
            ".wb-sideboard-card:first-child button", "els => els.map(e => (e.textContent || '').trim())")
        self.assertEqual([a for a in actions if "swap" in a.lower()], ["Swap in…"], actions)
        self.assertFalse([a for a in actions if "replace" in a.lower()], actions)
        start = len(self.messages())
        self.frame().click(".wb-sideboard-card:first-child button[data-act=swap]")
        self.frame().wait_for_selector(".wb-swap-panel:not([hidden])", timeout=5000)
        options = self.frame().eval_on_selector_all(".wb-swap-option", "els => els.length")
        self.assertGreater(options, 2)
        self.assertEqual([m for m in self.messages()[start:] if m.get("type") == "wb.sideboardAction"], [])


    def test_the_add_card_dialog_offers_the_camera(self):
        """The Add Cards box gains a scan path: the sandboxed frame asks the host for the photo."""
        self.frame().evaluate("() => { document.getElementById('add-card-modal-backdrop').hidden = false }")
        self.frame().wait_for_selector("#wb-scan-card", timeout=8000)
        self.assertEqual(self.frame().inner_text("#wb-scan-card").strip(), "Scan a card")
        start = len(self.messages())
        self.frame().click("#wb-scan-card")
        asked = self.wait_for(lambda m: m.get("type") == "wb.camera.open" and m.get("target") == "deck", 10)
        self.assertTrue(asked, self.messages()[start:])

    def test_the_sideboard_adds_a_card_by_hand_or_with_the_camera(self):
        self.frame().wait_for_selector(".wb-sideboard-tab", timeout=8000)
        self.frame().click(".wb-sideboard-tab")
        self.frame().wait_for_selector("#wb-sideboard-add-name", timeout=8000)
        start = len(self.messages())
        self.frame().click(".wb-sideboard-add button[data-act=sideboard-add]")
        self.frame().wait_for_function("() => !document.querySelector('.wb-sideboard-add button[data-act=sideboard-add]').disabled", timeout=5000)
        self.assertEqual([m for m in self.messages()[start:] if m.get("type") == "wb.sideboardAdd"], [],
                         "an empty box must not send a request")
        self.assertIn("Type a card name", self.frame().inner_text("body"))
        self.frame().fill("#wb-sideboard-add-name", "Hylderblade (EOE) 106")
        self.frame().click(".wb-sideboard-add button[data-act=sideboard-add]")
        sent = self.wait_for(lambda m: m.get("type") == "wb.sideboardAdd", 10)
        self.assertTrue(sent, self.messages()[start:])
        self.assertEqual(sent.get("refs"), ["Hylderblade (EOE) 106"])
        self.frame().click(".wb-sideboard-add button[data-act=sideboard-scan]")
        asked = self.wait_for(lambda m: m.get("type") == "wb.camera.open" and m.get("target") == "sideboard", 10)
        self.assertTrue(asked, self.messages()[start:])

    def test_colour_rows_show_their_numbers(self):
        # The Colours section used to hide its counts in tooltips; both numbers must be
        # on screen, inside their bar.
        self.frame().evaluate("window.mtgViewer.setViewMode('stats')")
        self.frame().wait_for_selector(".color-value", timeout=8000)
        time.sleep(0.8)
        rows = self.frame().evaluate("""() => [...document.querySelectorAll('.color-stat')]
          .filter(row => row.querySelector('.color-value'))
          .filter(row => row.querySelector('.color-track').getBoundingClientRect().width > 4)
          .map(row => {
            const tracks = [...row.querySelectorAll('.color-track')];
            const values = [...row.querySelectorAll('.color-value')];
            return {
              dot: row.querySelector('.color-dot').textContent.trim(),
              texts: values.map(v => v.textContent.trim()),
              inside: values.map((v, i) => {
                const vr = v.getBoundingClientRect(), tr = tracks[i].getBoundingClientRect();
                return vr.width > 2 && vr.left >= tr.left - 1 && vr.right <= tr.right + 1;
              })
            };
          })""")
        self.assertEqual(len(rows), 5, rows)
        self.assertEqual([r["dot"] for r in rows], ["W", "U", "B", "R", "G"])
        for row in rows:
            self.assertEqual(len(row["texts"]), 2, row)
            for text in row["texts"]:
                self.assertRegex(text, r"^\d+$")
            self.assertEqual(row["inside"], [True, True], row)


    def test_classifier_names_tokens_and_keyword_mechanics(self):
        # Classification runs on the live engine, so probe it directly with synthetic cards.
        probes = [
            {"name": "Hearthhull, the Worldseed", "typeLine": "Legendary Artifact — Spacecraft",
             "oracleText": "Station (Tap another creature you control: Put charge counters equal to its power on this "
                           "Spacecraft. Station only as a sorcery.)\n{1}, {T}, Sacrifice a land: Draw two cards."},
            {"name": "Glacier Godmaw", "typeLine": "Creature — Leviathan",
             "oracleText": "When this creature enters, create a Lander token."},
            {"name": "Robot Maker", "typeLine": "Sorcery",
             "oracleText": "Create a 2/2 colorless Robot artifact creature token."},
            {"name": "Walker Probe", "typeLine": "Legendary Planeswalker — Probe",
             "oracleText": "+1: Draw a card.\n-3: Destroy target creature."},
        ]
        results = self.frame().evaluate("""cards => cards.map(card => {
            const result = window.AutoBucketClassifierV2.classifyCard(card);
            return { buckets: result.autoBucketFacts.map(fact => fact.bucket.id),
                     gaps: result.unclassifiedClauses.map(gap => gap.clause) };
        })""", probes)
        by_name = dict(zip([probe["name"] for probe in probes], results))
        self.assertIn("keywords/station", by_name["Hearthhull, the Worldseed"]["buckets"])
        self.assertIn("tokens/create/lander", by_name["Glacier Godmaw"]["buckets"])
        self.assertIn("tokens/create/robot", by_name["Robot Maker"]["buckets"])
        self.assertNotIn("+1", by_name["Walker Probe"]["gaps"])

    def test_new_buckets_are_visible_in_the_effects_overview(self):
        # A fact nobody can see in the Effects view is not integrated.
        mapped = self.frame().evaluate("""() => {
            const groups = window.mtgViewer.BUCKET_OVERVIEW_GROUPS || [];
            const groupFor = id => groups.filter(group => {
                const includes = (group.include || []).some(rx => rx.test(id));
                const excluded = (group.exclude || []).some(rx => rx.test(id));
                return includes && !excluded;
            }).map(group => group.id);
            return {
              station: groupFor('keywords/station'),
              warp: groupFor('keywords/warp'),
              lander: groupFor('tokens/create/lander'),
              chosenColor: groupFor('mana/produce/chosen-color'),
              boardWipe: groupFor('removal/destroy/board'),
              plainRemoval: groupFor('removal/destroy/target-creature'),
              sacrificeLand: groupFor('sacrifice/pay/land')
            };
        }""")
        self.assertIn("overview/keywords", mapped["station"])
        self.assertIn("overview/keywords", mapped["warp"])
        self.assertIn("overview/token-create", mapped["lander"])
        self.assertIn("overview/fixing", mapped["chosenColor"])
        self.assertIn("overview/board-wipe", mapped["boardWipe"])
        self.assertNotIn("overview/board-wipe", mapped["plainRemoval"])
        self.assertIn("overview/sacrifice", mapped["sacrificeLand"])

    def test_search_walks_every_effect_the_card_belongs_to(self):
        frame = self.frame()
        # The saved layout may open Effects filtered to a single effect; the whole board is
        # what lists a card once per effect it belongs to.
        frame.evaluate("() => { window.mtgViewer.setViewMode('buckets'); window.mtgViewer.selectBucketOverview(); }")
        frame.wait_for_function("() => (window.mtgViewer.state.bucketEntries || []).length > 4", timeout=15000)
        target = frame.evaluate("""() => {
            const counts = {};
            for (const entry of window.mtgViewer.state.bucketEntries) {
                if (!entry.card || !entry.card.name) continue;
                counts[entry.card.id] = (counts[entry.card.id] || 0) + 1;
            }
            const best = Object.entries(counts).sort((a, b) => b[1] - a[1])[0];
            const card = window.mtgViewer.state.cards.find(c => c.id === best[0]);
            return { name: card.name, places: best[1] };
        }""")
        self.assertGreater(target["places"], 1, "the fixture must list a card under several effects")

        before_zoom = frame.evaluate("() => +window.mtgViewer.state.camera.scale.toFixed(2)")
        frame.wait_for_selector("#wb-search-toggle", state="visible", timeout=5000)
        frame.click("#wb-search-toggle")
        frame.fill("#wb-search-input", target["name"][:6])
        frame.wait_for_selector("#wb-search-results button", timeout=5000)
        frame.click("#wb-search-results button")

        frame.wait_for_selector(".bucket-card.is-search-hit", timeout=5000)
        time.sleep(0.35)
        self.assertRegex(frame.inner_text("#wb-search-hits-label"), r"^1 of \d+")
        state = frame.evaluate("""() => {
            const node = document.querySelector('.bucket-card.is-search-hit');
            const spot = document.getElementById('wb-spot');
            const safe = window.mtgViewer.focusViewportRect();
            const rect = node.getBoundingClientRect();
            return {
                zoom: +window.mtgViewer.state.camera.scale.toFixed(2),
                ring: spot ? !spot.hidden : false,
                inView: rect.left >= safe.left && rect.right <= safe.left + safe.width &&
                        rect.top >= safe.top && rect.bottom <= safe.top + safe.height,
                bucket: node.dataset.bucketId,
                marked: document.querySelectorAll('.is-search-hit').length
            };
        }""")
        # The board keeps its zoom; the close look happens in the overlay.
        self.assertAlmostEqual(state["zoom"], before_zoom, places=2)
        peek = frame.evaluate("""() => {
            const el = document.getElementById('wb-peek');
            return el ? { open: !el.hidden, text: el.innerText } : { open: false, text: '' };
        }""")
        self.assertTrue(peek["open"], peek)
        self.assertRegex(peek["text"], r"1 / \d+")
        self.assertTrue(state["inView"], state)
        self.assertTrue(state["ring"], "the search ring must be visible")
        self.assertEqual(state["marked"], 1, "only the found card is marked")

        frame.click("#wb-peek [data-peek=next]")
        time.sleep(0.35)
        self.assertRegex(frame.inner_text("#wb-search-hits-label"), r"^2 of \d+")
        second = frame.evaluate("() => document.querySelector('.bucket-card.is-search-hit').dataset.bucketId")
        self.assertNotEqual(state["bucket"], second, "the next arrow must show the next effect")
        self.assertRegex(frame.inner_text("#wb-peek"), r"2 / \d+")
        # Enter walks forward, Shift+Enter back.
        frame.press("#wb-search-input", "Shift+Enter")
        time.sleep(0.35)
        self.assertRegex(frame.inner_text("#wb-search-hits-label"), r"^1 of \d+")

    def test_search_matches_the_printed_language_and_ignores_accents(self):
        frame = self.frame()
        target = frame.evaluate("""() => {
            const card = window.mtgViewer.state.cards[0];
            card.printedName = "Mandeur de p\u00e9ril\u00e9e";
            window.mtgViewer.setViewMode("table");
            return card.name;
        }""")
        before_zoom = frame.evaluate("() => +window.mtgViewer.state.camera.scale.toFixed(2)")
        frame.wait_for_selector("#wb-search-toggle", state="visible", timeout=5000)
        frame.click("#wb-search-toggle")
        # Accent-free query, localised name only.
        frame.fill("#wb-search-input", "mandeur de perilee")
        frame.wait_for_selector("#wb-search-results button", timeout=5000)
        label = frame.eval_on_selector("#wb-search-results button", "el => el.textContent")
        self.assertIn("Mandeur de p\u00e9ril\u00e9e", label)
        meta = frame.eval_on_selector("#wb-search-results button span", "el => el.textContent")
        self.assertIn(target, meta)
        frame.click("#wb-search-results button")
        selected = frame.evaluate("""() => {
            const ids = Array.from(window.mtgViewer.state.selectedCardIds || []);
            return window.mtgViewer.state.cards.filter(card => ids.indexOf(card.id) >= 0).map(card => card.name);
        }""")
        self.assertEqual(selected, [target])
        # Searching brings the card up to reading size, marks it, and keeps it in view.
        marked = frame.evaluate("""() => {
            const node = document.querySelector('.card.is-search-hit');
            const spot = document.getElementById('wb-spot');
            const safe = window.mtgViewer.focusViewportRect();
            const rect = node ? node.getBoundingClientRect() : null;
            return {
                marked: !!node,
                zoom: +window.mtgViewer.state.camera.scale.toFixed(2),
                inView: rect ? rect.left >= safe.left && rect.right <= safe.left + safe.width : false,
                ring: spot ? !spot.hidden : false
            };
        }""")
        self.assertTrue(marked["marked"], "the card must be marked on the board")
        self.assertTrue(marked["inView"], marked)
        self.assertTrue(marked["ring"], "the search ring must be visible")
        self.assertAlmostEqual(marked["zoom"], before_zoom, places=2)
        peek = frame.evaluate("""() => {
            const el = document.getElementById('wb-peek');
            return el ? { open: !el.hidden, text: el.innerText } : { open: false, text: '' };
        }""")
        self.assertTrue(peek["open"], "the card opens in the overlay")
        shown = frame.evaluate("""() => {
            const card = window.mtgViewer.state.cards.find(c => (c.printedName || c.name) === c.printedName ? true : false);
            return null;
        }""")
        self.assertIn("Mandeur de périlée", peek["text"])

    def load_with_links(self):
        graph = core.links.build(self.document)
        self.page.evaluate("d => window.send({type: 'wb.load', deck_id: 'deck_test0001', rev: 1, document: d.document, links: d.links})",
                           {"document": copy.deepcopy(self.document), "links": graph})
        self.wait_for(lambda m: m.get("type") == "wb.loaded", 30)
        return graph

    def test_link_panel_opens_as_a_view_for_the_selected_card(self):
        graph = self.load_with_links()
        target = next(cid for cid, entry in graph["cards"].items() if entry["fed_by"])
        self.frame().evaluate("() => window.mtgViewer.setViewMode('table')")
        self.frame().evaluate("id => window.mtgViewer.selectCards([{ id }])", target)
        self.frame().click("#wb-links-toggle")
        self.frame().wait_for_selector("#wb-links-panel:not([hidden])", timeout=8000)
        title = self.frame().inner_text(".wb-links-title strong")
        self.assertEqual(title, graph["cards"][target]["name"])
        self.expand_link_groups()
        self.assertGreater(self.frame().eval_on_selector_all(".wb-link-tile", "els => els.length"), 0)
        headers = self.frame().inner_text(".wb-links-column.is-in .wb-links-side-text")
        # Two families, two directions each: Triggers (events) and Effects (static impacts).
        self.assertIn("Triggers", headers)
        self.assertIn("cards that trigger this one", headers)
        self.assertIn("cards this one triggers", self.frame().inner_text(".wb-links-column.is-out .wb-links-side-text").lower())
        # a view, not an overlay on the table: the board is not clickable behind it
        self.assertFalse(self.frame().is_visible("#table") and self.frame().evaluate(
            "() => getComputedStyle(document.getElementById('table')).pointerEvents !== 'none'"))

    def test_each_kind_of_link_folds_to_a_numbered_row(self):
        """One row per kind of link. The panel opens folded, so a card with several kinds of link
        reads as a short list of numbers; opening one row shows exactly that kind's cards and leaves
        the others folded."""
        graph = self.load_with_links()
        target = next(cid for cid, entry in graph["cards"].items()
                      if len([g for g in entry["fed_by"] if not g["generic"]]) > 1)
        expected = [g for g in graph["cards"][target]["fed_by"] if not g["generic"]]
        frame = self.frame()
        frame.evaluate("id => window.mtgViewer.selectCards([{ id }])", target)
        frame.click("#wb-links-toggle")
        frame.wait_for_selector("#wb-links-panel:not([hidden]) .wb-link-group", timeout=8000)

        rows = frame.eval_on_selector_all(
            ".wb-links-column.is-in .wb-link-group-head",
            """els => els.map(e => ({
                 label: e.querySelector('.wb-link-group-label').textContent,
                 count: e.querySelector('.wb-link-group-count').textContent,
                 open: e.getAttribute('aria-expanded'),
                 width: Math.round(e.getBoundingClientRect().width),
                 top: Math.round(e.getBoundingClientRect().top),
               }))""")
        self.assertEqual([r["label"] for r in rows], [g["label"] for g in expected])
        self.assertEqual([r["count"] for r in rows], [str(g["count"]) for g in expected])
        self.assertTrue(all(r["open"] == "false" for r in rows), "the panel opens folded")
        self.assertEqual(frame.eval_on_selector_all(".wb-links-column.is-in .wb-link-tile", "els => els.length"), 0,
                         "no cards are drawn until a row is opened")
        # The row is the control: it spans its column, and rows stack instead of overlapping.
        column = frame.eval_on_selector(".wb-links-column.is-in", "el => Math.round(el.getBoundingClientRect().width)")
        self.assertGreater(rows[0]["width"], column * 0.8)
        self.assertLess(rows[0]["top"], rows[1]["top"])

        frame.eval_on_selector_all(".wb-links-column.is-in .wb-link-group-head", "els => els[0].click()")
        groups = frame.eval_on_selector_all(
            ".wb-links-column.is-in .wb-link-group",
            """els => els.map(e => ({
                 open: e.querySelector('.wb-link-group-head').getAttribute('aria-expanded'),
                 tiles: e.querySelectorAll('.wb-link-tile').length,
               }))""")
        self.assertEqual(groups[0], {"open": "true", "tiles": len(expected[0]["cards"])})
        self.assertEqual(groups[1], {"open": "false", "tiles": 0}, "the other kind of link stays folded")

        frame.eval_on_selector_all(".wb-links-column.is-in .wb-link-group-head", "els => els[0].click()")
        self.assertEqual(frame.eval_on_selector_all(".wb-links-column.is-in .wb-link-tile", "els => els.length"), 0)

    def test_the_card_dialog_moves_a_card_to_the_sideboard(self):
        """Right-click lands on the card dialog; "Move to sideboard" takes the card off the table
        and asks the host to keep it as a candidate, printing included."""
        graph = self.load_with_links()
        target = next(cid for cid, entry in graph["cards"].items() if entry["fed_by"])
        self.frame().evaluate("""id => {
          const state = window.mtgViewer.state;
          state.pendingEditCards = [state.cards.find(card => card.id === id)];
          document.getElementById('remove-card-modal-backdrop').hidden = false;
        }""", target)
        self.frame().wait_for_selector("#wb-move-sideboard", timeout=8000)
        start = len(self.messages())
        self.frame().click("#wb-move-sideboard")
        # The sideboard refuses a name still in the deck, so the viewer asks only after the deck is
        # saved: the host answers a persist with wb.saved, and the harness plays the host.
        self.page.evaluate("() => window.send({type: 'wb.saved', status: 'saved', rev: 2})")
        sent = self.wait_for(lambda m: m.get("type") == "wb.sideboardAdd", 15)
        self.assertTrue(sent, self.messages()[start:])
        self.assertEqual(len(sent.get("refs") or []), 1, sent)
        self.assertIn("sideboard", str(sent.get("reason", "")).lower())
        self.frame().wait_for_function("id => !window.mtgViewer.state.cards.some(c => c.id === id)",
                                       arg=target, timeout=8000)

    def test_the_link_button_always_opens_the_card_selected_now(self):
        """The panel kept the card it showed last, so pressing the icon after selecting another
        card reopened the previous card's links (also after walking to a partner)."""
        graph = self.load_with_links()
        ids = [cid for cid, entry in graph["cards"].items() if entry["fed_by"]][:2]
        self.assertGreaterEqual(len(ids), 2, "the fixture needs two cards with links")
        first, second = ids
        self.frame().evaluate("() => window.mtgViewer.setViewMode('table')")
        self.frame().evaluate("id => window.mtgViewer.selectCards([{ id }])", first)
        self.frame().click("#wb-links-toggle")
        self.frame().wait_for_selector("#wb-links-panel:not([hidden])", timeout=8000)
        self.assertEqual(self.frame().inner_text(".wb-links-title strong"), graph["cards"][first]["name"])

        self.frame().click("#wb-links-panel [data-close]")
        self.frame().wait_for_selector("#wb-links-panel[hidden]", state="attached", timeout=8000)
        self.frame().evaluate("id => window.mtgViewer.selectCards([{ id }])", second)
        self.frame().click("#wb-links-toggle")
        self.frame().wait_for_selector("#wb-links-panel:not([hidden])", timeout=8000)
        self.assertEqual(self.frame().inner_text(".wb-links-title strong"), graph["cards"][second]["name"],
                         "the icon must read the selection, not the card it showed before")

        self.frame().click("#wb-links-panel [data-close]")
        self.frame().evaluate("() => window.mtgViewer.clearSelection()")
        self.frame().click("#wb-links-toggle")
        self.frame().wait_for_selector("#wb-links-panel:not([hidden])", timeout=8000)
        self.assertEqual(self.frame().inner_text(".wb-link-empty"), "No card selected.")

    def test_the_link_button_sits_beside_the_search_button_at_the_top_right(self):
        """A missing rule once dropped it into the page flow: a 2px stub at the top-left."""
        self.load_with_links()
        self.frame().evaluate("() => window.mtgViewer.setViewMode('table')")
        box = self.frame().evaluate("""() => {
          const link = document.getElementById('wb-links-toggle');
          const search = document.getElementById('wb-search-toggle');
          if (!link || !search) return null;
          const a = link.getBoundingClientRect(), b = search.getBoundingClientRect();
          return {link: [a.x, a.y, a.width, a.height], search: [b.x, b.y, b.width, b.height],
                  inner: [innerWidth, innerHeight]};
        }""")
        self.assertIsNotNone(box, "the link button is missing")
        lx, ly, lw, lh = box["link"]
        sx, sy, sw, sh = box["search"]
        self.assertAlmostEqual(lw, 30, delta=3)
        self.assertAlmostEqual(sw, 30, delta=3)
        self.assertLess(ly, 20)
        self.assertGreater(lx, box["inner"][0] * 0.7)
        self.assertLess(lx + lw, sx + 1, "the link button overlaps the search button")
        self.assertEqual(round(ly), round(sy))

    def test_the_link_button_comes_back_after_the_panel_is_closed(self):
        """It is the only way into the view: hiding it while open once left it gone for good."""
        graph = self.load_with_links()
        target = next(cid for cid, entry in graph["cards"].items() if entry["fed_by"])
        self.frame().evaluate("() => window.mtgViewer.setViewMode('table')")
        self.frame().evaluate("id => window.mtgViewer.selectCards([{ id }])", target)
        self.frame().click("#wb-links-toggle")
        self.frame().wait_for_selector("#wb-links-panel:not([hidden])", timeout=8000)
        self.assertEqual(self.frame().evaluate("() => getComputedStyle(document.getElementById('wb-links-toggle')).display"), "none")
        self.frame().click("#wb-links-panel [data-close]")
        self.frame().wait_for_selector("#wb-links-panel[hidden]", state="attached", timeout=8000)
        self.assertNotEqual(self.frame().evaluate("() => getComputedStyle(document.getElementById('wb-links-toggle')).display"), "none")
        self.frame().click("#wb-links-toggle")
        self.frame().wait_for_selector("#wb-links-panel:not([hidden])", timeout=8000)

    def test_any_card_pairs_never_reach_the_panel(self):
        """A card that merely exists is not a synergy, and listing it was the complaint: "any creature
        can enter or die" is true of every card, so it is not shown, not even folded away."""
        graph = self.load_with_links()
        target = next(cid for cid, entry in graph["cards"].items()
                      if any(g["generic"] for g in entry["fed_by"] + entry["feeds"]))
        self.frame().evaluate("() => window.mtgViewer.setViewMode('table')")
        self.frame().evaluate("id => window.mtgViewer.selectCards([{ id }])", target)
        self.frame().click("#wb-links-toggle")
        self.frame().wait_for_selector("#wb-links-panel:not([hidden])", timeout=8000)
        panel = self.frame().inner_text("#wb-links-panel")
        self.assertNotIn("any-card", panel)
        self.assertEqual(self.frame().eval_on_selector_all(".wb-link-anycard, .wb-links-side-any", "els => els.length"), 0)
        # Every group on the panel stands on a real cause, never on "this card exists".
        entry = graph["cards"][target]
        shown = set(self.frame().eval_on_selector_all(
            ".wb-links-column.is-in .wb-link-group-label, .wb-links-column.is-out .wb-link-group-label",
            "els => els.map(e => e.textContent)"))
        allowed = {g["label"] for g in entry["fed_by"] + entry["feeds"] if not g["generic"] and g["cards"]}
        self.assertTrue(shown <= allowed, f"generic or unknown groups on the panel: {shown - allowed}")

    def test_the_card_dialog_opens_the_link_view(self):
        """Right-click lands on the card dialog; its 'Linked cards' button must lead to the links."""
        graph = self.load_with_links()
        target = next(cid for cid, entry in graph["cards"].items() if entry["fed_by"])
        name = graph["cards"][target]["name"]
        self.frame().evaluate("() => window.mtgViewer.setViewMode('table')")
        self.frame().evaluate("""id => {
          const state = window.mtgViewer.state;
          state.pendingEditCards = [state.cards.find(card => card.id === id)];
          document.getElementById('remove-card-modal-backdrop').hidden = false;
        }""", target)
        self.frame().wait_for_selector("#wb-edit-links", timeout=8000)
        self.frame().click("#wb-edit-links")
        self.frame().wait_for_selector("#wb-links-panel:not([hidden])", timeout=8000)
        self.assertEqual(self.frame().inner_text(".wb-links-title strong"), name)
        self.assertTrue(self.frame().evaluate("() => document.getElementById('remove-card-modal-backdrop').hidden"),
                        "the dialog should close when the links open")
        self.expand_link_groups()
        self.assertGreater(self.frame().eval_on_selector_all(".wb-link-tile", "els => els.length"), 0)

    def test_link_panel_walks_to_a_partner_and_comes_back(self):
        graph = self.load_with_links()
        target = next(cid for cid, entry in graph["cards"].items() if entry["fed_by"])
        self.frame().evaluate("id => window.mtgViewer.selectCards([{ id }])", target)
        self.frame().click("#wb-links-toggle")
        self.expand_link_groups()
        first = self.frame().eval_on_selector(".wb-link-tile", "el => el.dataset.card")
        first_name = self.frame().eval_on_selector(".wb-link-tile .wb-link-name", "el => el.textContent")
        self.frame().click(".wb-link-tile .wb-link-name")
        time.sleep(0.3)
        self.assertEqual(self.frame().inner_text(".wb-links-title strong"), first_name)
        self.frame().click("[data-back]")
        time.sleep(0.3)
        self.assertEqual(self.frame().inner_text(".wb-links-title strong"), graph["cards"][target]["name"])
        self.assertEqual(first, self.frame().eval_on_selector(".wb-link-tile", "el => el.dataset.card"))

    def test_link_panel_zooms_the_tiles(self):
        self.load_with_links()
        target = next(cid for cid, entry in core.links.build(self.document)["cards"].items() if entry["fed_by"])
        self.frame().evaluate("id => window.mtgViewer.selectCards([{ id }])", target)
        self.frame().click("#wb-links-toggle")
        self.expand_link_groups()
        small = self.frame().eval_on_selector(".wb-link-tile", "el => el.getBoundingClientRect().width")
        self.frame().click("[data-zoom-in]")
        time.sleep(0.2)
        bigger = self.frame().eval_on_selector(".wb-link-tile", "el => el.getBoundingClientRect().width")
        self.assertGreater(bigger, small)
        self.frame().click("[data-zoom-out]")
        time.sleep(0.2)
        self.assertAlmostEqual(self.frame().eval_on_selector(".wb-link-tile", "el => el.getBoundingClientRect().width"), small, delta=2)

    def test_the_classifier_reads_exploit_and_endure(self):
        ack = self.command("classify_cards", {"cards": [
            {"id": "exploit", "name": "Fell Stinger", "type_line": "Creature — Zombie Scorpion",
             "oracle_text": "Deathtouch Exploit (When this creature enters, you may sacrifice a creature.)"},
            {"id": "endure", "name": "Sandskitter Outrider", "type_line": "Creature — Goblin Soldier",
             "oracle_text": "Menace When this creature enters, it endures 2."},
        ]}, kind="query")
        cards = ack["result"]["cards"]
        exploit = {(f.get("role"), f.get("action")) for f in cards["exploit"]["autoBucketFacts"]}
        self.assertIn(("effect", "sacrifice"), exploit)
        endure = [f for f in cards["endure"]["autoBucketFacts"]
                  if f.get("action") == "create" and "creature" in (f.get("object") or {}).get("types", [])]
        self.assertTrue(endure, "endure must produce a creature token")

    def test_classify_cards_op_runs_the_classifier_for_a_card_outside_the_deck(self):
        ack = self.command("classify_cards", {"cards": [{
            "id": "11111111-2222-3333-4444-555555555555",
            "name": "Rampaging Baloths",
            "type_line": "Creature — Beast",
            "oracle_text": "Landfall — Whenever a land you control enters, create a 4/4 green Beast creature token.",
        }]}, kind="query")
        self.assertEqual(ack.get("outcome"), "applied")
        result = ack["result"]["cards"]["11111111-2222-3333-4444-555555555555"]
        self.assertTrue(result["autoBucketFacts"])
        actions = {(f.get("role"), f.get("action")) for f in result["autoBucketFacts"]}
        self.assertIn(("trigger", "react-to-enter"), actions)
        self.assertTrue(result["autoBuckets"])

    def test_a_saved_candidate_is_classified_by_the_pane(self):
        entry = {
            "scryfall_id": "33333333-4444-5555-6666-777777777777",
            "name": "Rampaging Baloths",
            "type_line": "Creature — Beast",
            "mana_cost": "{4}{G}{G}",
            "oracle_text": "Landfall — Whenever a land you control enters, create a 4/4 green Beast creature token.",
        }
        self.page.evaluate("c => window.send({type: 'wb.load', deck_id: 'deck_test0001', rev: 1, document: c.document, links: c.links})",
                           {"document": copy.deepcopy(self.document), "links": core.links.build(self.document)})
        self.wait_for(lambda m: m.get("type") == "wb.loaded", 30)
        self.page.evaluate("c => window.send({type: 'wb.sideboard', deck_id: 'deck_test0001', cards: c})", [entry])
        message = self.wait_for(lambda m: m.get("type") == "wb.candidateFacts", 15)
        facts = message["cards"]["33333333-4444-5555-6666-777777777777"]
        self.assertTrue(facts["autoBucketFacts"])
        actions = {(f.get("role"), f.get("action")) for f in facts["autoBucketFacts"]}
        self.assertIn(("trigger", "react-to-enter"), actions)

    def test_sideboard_candidates_are_listed_below_the_deck_cards(self):
        """A candidate must never be read as a deck card: it sits in its own block at the bottom."""
        def fact(role, action, types, clause):
            return {"role": role, "action": action, "object": {"types": list(types)},
                    "evidence": {"clause": clause}, "qualifiers": {}}

        make = fact("effect", "create", ["creature", "token"], "Create a 2/2 green Beast creature token")
        watch = fact("trigger", "react-to-enter", ["creature"], "Whenever a creature enters")
        deck_maker = {"id": "deck-maker", "name": "Deck Maker", "typeLine": "Creature — Beast",
                      "autoBucketFacts": [make], "autoBuckets": []}
        deck_watch = {"id": "deck-watch", "name": "Deck Watcher", "typeLine": "Creature — Elf",
                      "autoBucketFacts": [watch], "autoBuckets": []}
        cand_maker = {"scryfall_id": "cand-maker", "name": "Candidate Maker", "type_line": "Creature — Beast",
                      "mana_cost": "{1}{G}", "autoBucketFacts": [make], "autoBuckets": []}
        cand_watch = {"scryfall_id": "cand-watch", "name": "Candidate Watcher", "type_line": "Creature — Elf",
                      "mana_cost": "{1}{G}", "autoBucketFacts": [watch], "autoBuckets": []}

        document = copy.deepcopy(self.document)
        document["cards"] = [deck_maker, deck_watch]
        virtual, meta = core.links.with_candidates(document, [cand_maker, cand_watch])
        graph = core.links.build(virtual)
        graph["meta"] = meta
        self.page.evaluate("d => window.send({type: 'wb.load', deck_id: 'deck_test0001', rev: 1, document: d.document, links: d.links})",
                           {"document": copy.deepcopy(document), "links": graph})
        self.wait_for(lambda m: m.get("type") == "wb.loaded", 30)
        self.page.evaluate("c => window.send({type: 'wb.sideboard', deck_id: 'deck_test0001', cards: c})",
                           [cand_maker, cand_watch])

        # a deck card: the candidate that feeds it is not among its deck cards
        self.frame().evaluate("() => window.mtgViewer.setViewMode('table')")
        self.frame().evaluate("() => window.mtgViewer.selectCards([{ id: 'deck-watch' }])")
        self.frame().click("#wb-links-toggle")
        self.frame().wait_for_selector("#wb-links-panel:not([hidden])", timeout=8000)
        self.expand_link_groups()
        grouped = self.frame().eval_on_selector_all(
            ".wb-links-column.is-in .wb-link-group .wb-link-name", "els => els.map(e => e.textContent)")
        below = self.frame().eval_on_selector_all(
            ".wb-links-column.is-in .wb-link-candidates .wb-link-name", "els => els.map(e => e.textContent)")
        self.assertEqual(grouped, ["Deck Maker"])
        self.assertIn("Candidate Maker", below)
        self.assertNotIn("Candidate Maker", grouped)
        self.assertIn("In the sideboard, not in the deck", self.frame().inner_text(".wb-links-column.is-in"))
        self.assertIn("SIDEBOARD", self.frame().inner_text(".wb-link-candidates").upper())
        self.frame().click("#wb-links-panel [data-close]")
        self.frame().wait_for_selector("#wb-links-panel[hidden]", state="attached", timeout=8000)

        # a candidate: the deck cards it feeds first, the other candidates below them
        self.frame().click(".wb-sideboard-tab")
        self.frame().evaluate("() => document.querySelector('button[data-act=links]').click()")
        self.frame().wait_for_selector("#wb-links-panel:not([hidden])", timeout=8000)
        self.assertEqual(self.frame().inner_text(".wb-links-title strong"), "Candidate Maker")
        self.expand_link_groups()
        deck_out = self.frame().eval_on_selector_all(
            ".wb-links-column.is-out .wb-link-group .wb-link-name", "els => els.map(e => e.textContent)")
        cand_out = self.frame().eval_on_selector_all(
            ".wb-links-column.is-out .wb-link-candidates .wb-link-name", "els => els.map(e => e.textContent)")
        self.assertEqual(deck_out, ["Deck Watcher"])
        self.assertEqual(cand_out, ["Candidate Watcher"])

    def test_closing_the_link_view_returns_to_the_sideboard(self):
        entry = {
            "scryfall_id": "44444444-5555-6666-7777-888888888888",
            "name": "Rampaging Baloths",
            "type_line": "Creature — Beast",
            "mana_cost": "{4}{G}{G}",
            "autoBucketFacts": [{
                "role": "trigger", "action": "react-to-enter", "object": {"types": ["land"]},
                "qualifiers": {}, "evidence": {"clause": "Whenever a land you control enters"},
            }],
            "autoBuckets": ["lands/react-to/enter-land"],
        }
        virtual, meta = core.links.with_candidates(self.document, [entry])
        graph = core.links.build(virtual)
        graph["meta"] = meta
        self.page.evaluate("d => window.send({type: 'wb.load', deck_id: 'deck_test0001', rev: 1, document: d.document, links: d.links})",
                           {"document": copy.deepcopy(self.document), "links": graph})
        self.wait_for(lambda m: m.get("type") == "wb.loaded", 30)
        self.page.evaluate("c => window.send({type: 'wb.sideboard', deck_id: 'deck_test0001', cards: c})", [entry])

        self.frame().click(".wb-sideboard-tab")
        self.assertFalse(self.frame().evaluate("() => document.querySelector('.wb-sideboard').hidden"))
        self.frame().evaluate("() => document.querySelector('button[data-act=links]').click()")
        self.frame().wait_for_selector("#wb-links-panel:not([hidden])", timeout=8000)
        self.assertTrue(self.frame().evaluate("() => document.querySelector('.wb-sideboard').hidden"))

        self.frame().click("#wb-links-panel [data-close]")
        self.frame().wait_for_selector("#wb-links-panel[hidden]", state="attached", timeout=8000)
        self.assertFalse(self.frame().evaluate("() => document.querySelector('.wb-sideboard').hidden"),
                         "closing the links should leave the sideboard open")

        # opened from the table, closing must not summon the sideboard
        self.frame().click(".wb-sideboard-tab")
        self.frame().evaluate("() => window.mtgViewer.setViewMode('table')")
        self.frame().evaluate("id => window.mtgViewer.selectCards([{ id }])", self.document["cards"][0]["id"])
        self.frame().click("#wb-links-toggle")
        self.frame().wait_for_selector("#wb-links-panel:not([hidden])", timeout=8000)
        self.frame().click("#wb-links-panel [data-close]")
        self.frame().wait_for_selector("#wb-links-panel[hidden]", state="attached", timeout=8000)
        self.assertTrue(self.frame().evaluate("() => document.querySelector('.wb-sideboard').hidden"))

    def test_sideboard_links_button_opens_the_link_view_on_the_candidate(self):
        entry = {
            "scryfall_id": "11111111-2222-3333-4444-555555555555",
            "name": "Rampaging Baloths",
            "type_line": "Creature — Beast",
            "mana_cost": "{4}{G}{G}",
            "autoBucketFacts": [{
                "role": "trigger", "action": "react-to-enter",
                "object": {"types": ["land"]}, "qualifiers": {},
                "evidence": {"clause": "Whenever a land you control enters"},
            }],
            "autoBuckets": ["lands/react-to/enter-land"],
        }
        virtual, meta = core.links.with_candidates(self.document, [entry])
        graph = core.links.build(virtual)
        graph["meta"] = meta
        self.page.evaluate("d => window.send({type: 'wb.load', deck_id: 'deck_test0001', rev: 1, document: d.document, links: d.links})",
                           {"document": copy.deepcopy(self.document), "links": graph})
        self.wait_for(lambda m: m.get("type") == "wb.loaded", 30)
        self.page.evaluate("c => window.send({type: 'wb.sideboard', deck_id: 'deck_test0001', cards: c})", [entry])
        self.frame().wait_for_selector(".wb-sideboard-tab", timeout=8000)
        self.frame().click(".wb-sideboard-tab")
        self.frame().wait_for_selector("button[data-act=links]", timeout=8000)
        self.frame().click("button[data-act=links]")
        self.frame().wait_for_selector("#wb-links-panel:not([hidden])", timeout=8000)
        self.assertIn("Rampaging Baloths", self.frame().inner_text(".wb-links-title strong"))
        self.assertIn("candidate", self.frame().inner_text(".wb-links-meta"))
        # landfall is an any-card pairing ("a land enters" is true of any land), so the panel says
        # there is nothing to read rather than listing the cards that merely exist
        self.assertIn("Nothing on this side", self.frame().inner_text("#wb-links-panel"))

    def test_show_links_op_selects_and_reports_the_card(self):
        graph = self.load_with_links()
        target = next(cid for cid, entry in graph["cards"].items() if entry["fed_by"])
        name = graph["cards"][target]["name"]
        ack = self.command("show_links", {"card": name}, kind="view")
        self.assertEqual(ack.get("outcome"), "applied")
        result = ack["result"]
        self.assertTrue(result["open"])
        self.assertEqual(result["card"], name)
        self.assertTrue(result["fed_by"])
        self.assertLessEqual(len(result["fed_by"]), 6)
        self.assertTrue(result["stats"]["cards"])
        self.assertFalse(self.frame().is_hidden("#wb-links-panel"))

    def test_search_finds_and_focuses_a_card(self):
        frame = self.frame()
        target = frame.evaluate("() => window.mtgViewer.state.cards[0].name")
        frame.evaluate("() => window.mtgViewer.setViewMode('table')")
        before_zoom = frame.evaluate("() => +window.mtgViewer.state.camera.scale.toFixed(2)")
        frame.wait_for_selector("#wb-search-toggle", state="visible", timeout=5000)
        frame.click("#wb-search-toggle")
        frame.fill("#wb-search-input", target.split(",")[0][:6])
        frame.wait_for_selector("#wb-search-results button", timeout=5000)
        frame.click("#wb-search-results button")
        selected = frame.evaluate("""() => {
            const ids = Array.from(window.mtgViewer.state.selectedCardIds || []);
            return window.mtgViewer.state.cards.filter(card => ids.indexOf(card.id) >= 0).map(card => card.name);
        }""")
        self.assertEqual(selected, [target])
        frame.evaluate("() => window.mtgViewer.setViewMode('stats')")
        hidden = frame.evaluate("""() => {
            const el = document.getElementById('wb-search');
            return !el || el.hidden || getComputedStyle(el).display === 'none';
        }""")
        self.assertTrue(hidden, "search should stay hidden in Stats")

    def test_subtype_sections_accept_names_and_report_counts(self):
        # The op used to take internal section ids only, so a plain name silently did nothing.
        subtype = self.frame().evaluate(r"""() => {
            for (const card of window.mtgViewer.state.cards) {
              const parts = String(card.typeLine || '').split('—');
              if (parts.length > 1) {
                const token = parts[1].trim().split(/\s+/)[0];
                if (token) return token;
              }
            }
            return null;
        }""")
        self.assertTrue(subtype, "fixture deck has no subtypes")
        ack = self.command("set_subtype_sections", {"subtypes": [subtype], "fit": False}, kind="view")
        self.assertEqual(ack["outcome"], "applied", ack.get("error"))
        self.assertEqual(len(ack["result"]["subtype_sections"]), 1)
        self.assertTrue(ack["result"]["show_subtypes"])
        self.assertGreaterEqual(ack["result"]["counts"][subtype], 1)
        bad = self.command("set_subtype_sections", {"subtypes": ["Definitely Not A Subtype"], "fit": False}, kind="view")
        self.assertEqual(bad["outcome"], "invalid")
        self.assertIn("Definitely Not A Subtype", bad["error"])

    def test_sideboard_card_opens_at_full_size(self):
        # Thumbnails are too small to read; clicking one must open the large printing.
        card = {"scryfall_id": "88888888-9999-aaaa-bbbb-cccccccccccc", "name": "U.S.S. Farragut, Bold Explorer",
                "mana_cost": "{6}", "mv": 6, "type_line": "Legendary Artifact — Spacecraft",
                "image": "https://cards.scryfall.io/normal/front/b/f/bfbef056-cf8b-46f8-892e-30742fd78707.jpg",
                "reason": "Landfall cheats a creature in."}
        self.frame().wait_for_function("() => !!window.mtgViewer && !!document.querySelector('.wb-sideboard')", timeout=15000)
        self.page.evaluate("c => window.send({type: 'wb.sideboard', deck_id: 'deck_test0001', cards: c})", [card])
        self.frame().click(".wb-sideboard-tab")
        self.frame().wait_for_selector(".wb-sideboard-card img[data-zoom]", timeout=15000)
        self.frame().click(".wb-sideboard-card img[data-zoom]")
        zoom = self.frame().wait_for_selector(".wb-zoom:not([hidden])", timeout=5000)
        self.assertIsNotNone(zoom)
        state = self.frame().evaluate("""() => {
          const z = document.querySelector('.wb-zoom');
          return { src: z.querySelector('img').getAttribute('src'),
                   caption: z.querySelector('.wb-zoom-caption').textContent,
                   hint: z.querySelector('.wb-zoom-hint').textContent };
        }""")
        self.assertIn("/large/", state["src"])
        self.assertIn("U.S.S. Farragut", state["caption"])
        self.assertIn("Esc", state["hint"])
        self.page.keyboard.press("Escape")
        time.sleep(0.3)
        self.assertTrue(self.frame().evaluate("() => document.querySelector('.wb-zoom').hidden"))


    def test_sideboard_swap_chooser_searches_and_uses_the_table_selection(self):
        card = {"scryfall_id": "99999999-8888-7777-6666-555555555555", "name": "Starship Enterprise",
                "mana_cost": "{5}", "mv": 5, "type_line": "Artifact — Spacecraft", "reason": "landfall"}
        self.frame().wait_for_function("() => !!document.querySelector('.wb-sideboard')", timeout=15000)
        self.page.evaluate("c => window.send({type: 'wb.sideboard', deck_id: 'deck_test0001', cards: c})", [card])
        self.frame().click(".wb-sideboard-tab")
        self.frame().wait_for_selector(".wb-sideboard-card button[data-act=swap]", timeout=8000)
        self.frame().click(".wb-sideboard-card button[data-act=swap]")
        self.frame().wait_for_selector(".wb-swap-panel:not([hidden])", timeout=5000)
        self.assertGreater(self.frame().eval_on_selector_all(".wb-swap-option", "els => els.length"), 3)
        target = self.frame().evaluate("() => window.mtgViewer.state.cards[0].name")
        self.frame().fill(".wb-swap-panel .wb-swap-search", target[:5])
        time.sleep(0.3)
        rows = self.frame().eval_on_selector_all(".wb-swap-option", "els => els.map(e => e.textContent.trim())")
        self.assertTrue(any(target in row for row in rows), rows)
        start = len(self.messages())
        self.frame().click(".wb-swap-option")
        action = self.wait_for(lambda m: m.get("type") == "wb.sideboardAction", 10, start)
        self.assertEqual(action["action"], "replace")
        self.assertTrue(action["replace_id"])
        # The viewer clears the table selection on pointerdown outside a card; the chooser
        # must still offer the cards that were selected before it opened.
        self.frame().evaluate("() => window.mtgViewer.selectCards([{ id: window.mtgViewer.state.cards[0].id }])")
        time.sleep(0.2)
        self.frame().click(".wb-sideboard-card button[data-act=swap]")
        time.sleep(0.3)
        state = self.frame().evaluate("""() => {
          const panel = document.querySelector('.wb-swap-panel:not([hidden])');
          const button = panel && panel.querySelector('[data-act=swap-selection]');
          return button ? { disabled: button.disabled, text: button.textContent } : null;
        }""")
        self.assertIsNotNone(state)
        self.assertFalse(state["disabled"])
        self.assertIn("1", state["text"])


    def test_save_dialog_has_a_close_cross_that_cancels(self):
        start = len(self.messages())
        self.frame().click("#save-layout")
        self.frame().click(".wb-dialog-actions button:first-child")
        self.frame().wait_for_selector(".wb-dialog", timeout=5000)
        self.assertEqual(self.frame().eval_on_selector_all(".wb-dialog-close", "els => els.length"), 1)
        self.frame().click(".wb-dialog-close")
        time.sleep(0.5)
        self.assertEqual(self.frame().query_selector_all(".wb-dialog"), [])
        self.assertFalse([m for m in self.messages()[start:] if m.get("type") == "wb.file.save"])

    def test_rename_then_undo_from_chat(self):
        ack = self.command("set_deck_title", {"title": "Renamed by chat"})
        self.assertEqual(ack["outcome"], "applied")
        self.assertEqual(ack["rev"], 2)
        self.assertEqual(ack["snapshot"]["deckTitle"], "Renamed by chat")
        self.assertEqual(self.frame().inner_text("#deck-title-banner"), "Renamed by chat")
        undo = self.command("undo")
        self.assertEqual(undo["outcome"], "applied")
        self.assertEqual(undo["snapshot"]["deckTitle"], self.document["deckTitle"])

    def test_remove_cards_and_undo_restores_them(self):
        name = next(c["name"] for c in self.document["cards"] if not c.get("isCommander"))
        before = len(self.document["cards"])
        ack = self.command("remove_cards", {"names": [name]})
        self.assertEqual(ack["outcome"], "applied")
        self.assertLess(len(ack["snapshot"]["cards"]), before)
        undo = self.command("undo")
        self.assertEqual(len(undo["snapshot"]["cards"]), before)

    def test_stale_revision_is_refused(self):
        ack = self.command("set_deck_title", {"title": "x"}, expected_rev=99)
        self.assertEqual(ack["outcome"], "stale_revision")
        self.assertNotIn("snapshot", ack)

    def test_selection_reaches_context_report(self):
        name = self.document["cards"][0]["name"]
        start = len(self.messages())
        ack = self.command("select_cards", {"names": [name]}, kind="view")
        self.assertEqual(ack["outcome"], "applied")
        ctx = self.wait_for(lambda m: m.get("type") == "wb.context" and m.get("selected"), 5, start)
        self.assertEqual(ctx["selected"][0]["name"], name)

    def test_manual_edit_is_reported_as_a_new_revision(self):
        start = len(self.messages())
        self.frame().evaluate("document.getElementById('reset-positions').click()")
        changed = self.wait_for(lambda m: m.get("type") == "wb.changed", 5, start)
        self.assertEqual(changed["rev"], 2)
        self.assertEqual(changed["base_rev"], 1)

    def test_guide_renders_with_card_links_and_stale_banner(self):
        name = self.document["cards"][0]["name"]
        markdown = f"# Guide\n\n## Key Cards\n\n- **{name}** drives the plan.\n"
        self.page.evaluate("m => window.send(m)", {"type": "wb.guide", "deck_id": "deck_test0001", "reveal": True,
                                                   "guide": {"markdown": markdown, "stale": True, "rev": 1}})
        self.frame().wait_for_selector(".wb-card-link", timeout=5000)
        self.assertEqual(self.frame().get_attribute(".wb-card-link", "data-card"), name)
        self.assertIn("earlier card list", self.frame().inner_text(".wb-guide-stale"))
        self.assertTrue(self.frame().evaluate("document.getElementById('strategy-notes').hidden"))
        start = len(self.messages())
        self.frame().click(".wb-card-link")
        ctx = self.wait_for(lambda m: m.get("type") == "wb.context" and m.get("selected"), 5, start)
        self.assertEqual(ctx["selected"][0]["name"], name)

    def test_replace_card_keeps_place_and_undoes_in_one_step(self):
        original = next(c for c in self.document["cards"] if not c.get("isCommander"))
        payload = json.loads((Path(__file__).resolve().parent / "fixtures" / "scryfall-mana-rocks.json").read_text(encoding="utf-8"))
        signet = payload["data"][0]["id"]
        ack = self.command("replace_card", {"id": original["id"], "scryfall_id": signet})
        self.assertEqual(ack["outcome"], "applied", ack.get("error"))
        names = [c["name"] for c in ack["snapshot"]["cards"]]
        self.assertNotIn(original["id"], [c["id"] for c in ack["snapshot"]["cards"]])
        self.assertIn(payload["data"][0]["name"], names)
        self.assertEqual(len(names), len(self.document["cards"]))
        undo = self.command("undo")
        self.assertIn(original["id"], [c["id"] for c in undo["snapshot"]["cards"]])
        self.assertNotIn(payload["data"][0]["name"], [c["name"] for c in undo["snapshot"]["cards"]])

    def test_manual_rename_uses_in_frame_dialog(self):
        start = len(self.messages())
        self.frame().click("#name-deck")
        self.frame().fill(".wb-dialog input", "Typed by hand")
        self.frame().click(".wb-dialog-actions button:last-child")
        self.assertEqual(self.frame().inner_text("#deck-title-banner"), "Typed by hand")
        changed = self.wait_for(lambda m: m.get("type") == "wb.changed", 5, start)
        self.assertEqual(changed["snapshot"]["deckTitle"], "Typed by hand")

    def test_save_button_exports_through_the_host(self):
        # Three questions on the way out: images or URLs, the file name, then where it goes.
        start = len(self.messages())
        self.frame().click("#save-layout")
        self.frame().click(".wb-dialog-actions button:first-child")
        self.frame().wait_for_selector(".wb-dialog input", timeout=5000)
        suggested = self.frame().input_value(".wb-dialog input")
        self.assertTrue(suggested.endswith(".mtg-deck-studio.json"), suggested)
        self.frame().click(".wb-dialog-actions button:last-child")
        self.frame().wait_for_selector(".wb-dialog", timeout=5000)
        self.frame().click(".wb-dialog-actions button:nth-child(2)")
        request = self.wait_for(lambda m: m.get("type") == "wb.file.save", 10, start)
        self.assertEqual(request["name"], suggested)
        self.assertEqual(request["target"], "exports")
        self.assertEqual(json.loads(request["text"])["app"], "mtg-table-viewer")
        self.page.evaluate("id => window.send({type: 'wb.file.saved', request_id: id, ok: true, path: 'exports/deck.json'})", request["request_id"])
        self.frame().wait_for_selector(".wb-notice", timeout=5000)
        self.assertIn("exports/deck.json", self.frame().inner_text(".wb-notice"))

    def test_save_can_target_a_folder_the_reader_picks(self):
        start = len(self.messages())
        self.frame().click("#save-layout")
        self.frame().click(".wb-dialog-actions button:first-child")
        self.frame().wait_for_selector(".wb-dialog input", timeout=5000)
        self.frame().click(".wb-dialog-actions button:last-child")
        self.frame().wait_for_selector(".wb-dialog", timeout=5000)
        self.frame().click(".wb-dialog-actions button:last-child")
        request = self.wait_for(lambda m: m.get("type") == "wb.file.save", 10, start)
        self.assertEqual(request["target"], "copy")
        # Cancelling the folder picker writes nothing and shows no notice.
        self.page.evaluate("id => window.send({type: 'wb.file.saved', request_id: id, ok: false, cancelled: true})", request["request_id"])
        time.sleep(0.4)
        self.assertEqual(self.frame().query_selector_all(".wb-notice"), [])

    def test_card_properties_mana_value_and_roles(self):
        card = next(c for c in self.document["cards"] if not c.get("isCommander") and "Land" not in (c.get("typeLine") or ""))
        lists = self.command("list_roles", kind="query")
        self.assertEqual(lists["outcome"], "applied")
        role = next(r for r in lists["result"]["roles"] if r["id"] not in card.get("utilityBuckets", []))
        ack = self.command("set_card_properties", {"ids": [card["id"]], "is_alternate_commander": True})
        self.assertEqual(ack["outcome"], "applied", ack.get("error"))
        edited = next(c for c in ack["snapshot"]["cards"] if c["id"] == card["id"])
        self.assertTrue(edited["isAlternateCommander"])
        ack = self.command("set_mana_value", {"ids": [card["id"]], "value": 9})
        self.assertEqual(next(c for c in ack["snapshot"]["cards"] if c["id"] == card["id"])["manualManaValue"], 9)
        ack = self.command("set_card_roles", {"ids": [card["id"]], "add": [role["label"]]})
        self.assertEqual(ack["outcome"], "applied", ack.get("error"))
        self.assertIn(role["id"], next(c for c in ack["snapshot"]["cards"] if c["id"] == card["id"])["utilityBuckets"])
        again = self.command("set_card_roles", {"ids": [card["id"]], "add": [role["id"]]})
        self.assertEqual(again["outcome"], "noop")
        undo = self.command("undo")
        self.assertNotIn(role["id"], next(c for c in undo["snapshot"]["cards"] if c["id"] == card["id"])["utilityBuckets"])
        self.assertEqual(next(c for c in undo["snapshot"]["cards"] if c["id"] == card["id"])["manualManaValue"], 9)

    def test_custom_sections_categories_and_roles(self):
        groups = self.command("list_roles", kind="query")["result"]["groups"]
        ack = self.command("create_role", {"label": "Value engines", "group": groups[0]["id"]})
        self.assertEqual(ack["outcome"], "applied", ack.get("error"))
        self.assertTrue(any(b.get("label") == "Value engines" for b in ack["snapshot"]["customBuckets"]))
        ack = self.command("add_table_section", {"label": "Engines"})
        section = ack["result"]["section"]
        ack = self.command("add_stats_category", {"label": "Win cons"})
        self.assertTrue(any(c.get("label") == "Win cons" for c in ack["snapshot"]["customStatsCategories"]))
        card = next(c for c in self.document["cards"] if not c.get("isCommander") and "Land" not in (c.get("typeLine") or ""))
        ack = self.command("set_card_properties", {"ids": [card["id"]], "table_section": "Engines", "stats_add": ["Win cons"]})
        moved = next(c for c in ack["snapshot"]["cards"] if c["id"] == card["id"])
        self.assertEqual(moved["tableCategory"], section["id"])
        self.assertEqual(self.command("add_table_section", {"label": "engines"})["outcome"], "noop")

    def test_a_mana_value_with_no_cards_draws_no_column(self):
        ack = self.command("set_view_mode", {"mode": "stats"}, kind="view")
        self.assertEqual(ack["outcome"], "applied", ack.get("error"))
        frame = self.frame()
        # The stats board has to be on screen before any bar has a height.
        frame.wait_for_selector("[data-stats-section='mana'] .mana-column-bar:not(.is-empty)", state="visible", timeout=8000)
        rows = frame.evaluate("""() => Array.from(
            document.querySelectorAll("[data-stats-section='mana'] .mana-column")).map(column => {
            const bar = column.querySelector('.mana-column-bar');
            const count = Number(column.querySelector('.mana-column-count').textContent);
            const style = bar ? getComputedStyle(bar) : null;
            const height = bar ? Math.round(bar.getBoundingClientRect().height) : 0;
            return {
                count,
                label: column.querySelector('.mana-column-label').textContent,
                visible: Boolean(bar) && style.display !== 'none' && height > 0,
                height,
                disabled: Boolean(column.disabled),
            };
        })""")
        self.assertTrue(rows)
        empty = [row for row in rows if row["count"] == 0]
        filled = [row for row in rows if row["count"] > 0]
        self.assertTrue(empty, "the sample deck has mana values with no cards")
        self.assertTrue(filled)
        # A tiny nub at an empty mana value reads as "one card": it must not be drawn at all.
        for row in empty:
            self.assertFalse(row["visible"], row)
            self.assertEqual(row["height"], 0, row)
            self.assertTrue(row["disabled"], row)
        for row in filled:
            self.assertTrue(row["visible"], row)
            self.assertGreaterEqual(row["height"], 6, row)

    def test_view_and_query_operations_do_not_create_revisions(self):
        for op, args in (("zoom", {"direction": "in"}), ("show_roles_overview", {}), ("select_cards", {"names": [self.document["cards"][1]["name"]]})):
            ack = self.command(op, args, kind="view")
            self.assertEqual(ack["outcome"], "applied", (op, ack.get("error")))
            self.assertEqual(ack["rev"], 1)
            self.assertNotIn("snapshot", ack)
        name = self.document["cards"][0]["name"]
        explained = self.command("explain_card", {"names": [name]}, kind="query")
        self.assertEqual(explained["result"]["cards"][0]["name"], name)

    def test_reclassify_and_subtypes_are_undoable(self):
        ack = self.command("toggle_subtypes")
        self.assertEqual(ack["outcome"], "applied", ack.get("error"))
        self.assertNotEqual(ack["snapshot"]["layout"].get("showSubtypes"), self.document["layout"].get("showSubtypes"))
        ack = self.command("reclassify", {"reset": True})
        self.assertIn(ack["outcome"], ("applied", "noop"), ack.get("error"))

    def test_remaining_simple_operations(self):
        name = self.document["cards"][2]["name"]
        for op, args, kind in (("set_view_mode", {"mode": "stats"}, "view"), ("set_view_mode", {"mode": "table"}, "view"),
                               ("fit_view", {}, "view"), ("center_view", {}, "view"), ("focus_cards", {"names": [name]}, "view"),
                               ("clear_selection", {}, "view"), ("show_role", {"role": self.command("list_roles", kind="query")["result"]["roles"][0]["id"]}, "view"),
                               ("stop_loading", {}, "view")):
            ack = self.command(op, args, kind=kind)
            self.assertIn(ack["outcome"], ("applied", "noop"), (op, ack.get("error")))
        notes = self.command("set_strategy_notes", {"notes": "Keep Muldrotha safe."})
        self.assertEqual(notes["snapshot"]["strategyNotes"], "Keep Muldrotha safe.")
        moved = self.command("reset_positions")
        self.assertIn(moved["outcome"], ("applied", "noop"))
        renamed = self.command("set_deck_title", {"title": "Before redo"})
        self.command("undo")
        redone = self.command("redo")
        self.assertEqual(redone["snapshot"]["deckTitle"], "Before redo")
        panel = self.command("toggle_panel", kind="view")
        self.assertEqual(panel["outcome"], "applied")
        flipped = self.command("flip_cards", {"names": [name]})
        self.assertIn(flipped["outcome"], ("applied", "noop"))
        granular = self.command("toggle_granular_view")
        self.assertIn(granular["outcome"], ("applied", "noop"))
        cleared = self.command("clear_table")
        self.assertEqual(cleared["snapshot"]["cards"], [])
        self.assertEqual(self.command("undo")["outcome"], "noop", "the viewer's clear wipes history")

    def wait_until_loaded(self, timeout=60):
        self.frame().wait_for_function("!window.mtgViewer.state.loading", timeout=timeout * 1000)

    def test_flush_commits_pending_manual_change_before_reading(self):
        self.frame().evaluate("window.mtgViewer.setDeckTitle('Typed, not yet committed')")
        ack = self.command("flush", kind="query")
        self.assertEqual(ack["outcome"], "applied")
        self.assertEqual(ack["rev"], 2)

    def test_focus_stats_section_validates_the_section(self):
        self.assertEqual(self.command("focus_stats_section", {"section": "nope"}, kind="view")["outcome"], "invalid")
        self.assertEqual(self.command("focus_stats_section", {"section": "mana"}, kind="view")["outcome"], "applied")

    def test_add_cards_and_loads_fetch_from_scryfall(self):
        ack = self.command("add_cards", {"name": "Sol Ring", "quantity": 2})
        self.assertEqual(ack["outcome"], "applied", ack.get("error"))
        self.assertEqual(len(ack["snapshot"]["cards"]), len(self.document["cards"]) + 2)
        ack = self.command("load_decklist", {"text": "1 Sol Ring\n1 Island"})
        self.assertIn(ack["outcome"], ("applied", "started"), ack.get("error"))
        self.wait_until_loaded()
        self.assertEqual(sorted(c["name"] for c in self.frame().evaluate("window.mtgViewer.state.cards")), ["Island", "Sol Ring"])
        csv = next(Path(__file__).resolve().parents[2].glob("decks/latest/*/MythicTools_export_*.csv"), None)
        if csv is None:
            self.skipTest("MythicTools fixtures are not present")
        lines = csv.read_text(encoding="utf-8-sig").splitlines()[:4]
        ack = self.command("import_mythictools_csv", {"text": "\n".join(lines), "name": "Sample"})
        self.assertIn(ack["outcome"], ("applied", "started"), ack.get("error"))
        self.wait_until_loaded()
        self.assertEqual(len(self.frame().evaluate("window.mtgViewer.state.cards")), 3)

    def test_role_library_subtypes_granular_and_settings(self):
        lists = self.command("list_roles", kind="query")["result"]
        created = self.command("create_role", {"label": "Pressure", "group": lists["groups"][0]["id"]})["result"]["role"]
        card = self.document["cards"][5]
        self.command("set_card_roles", {"ids": [card["id"]], "add": [created["id"]]})
        moved = self.command("set_role_group", {"role": created["id"], "group": lists["groups"][1]["id"]})
        self.assertEqual(next(b for b in moved["snapshot"]["customBuckets"] if b["id"] == created["id"])["groupId"], lists["groups"][1]["id"])
        deleted = self.command("delete_role", {"role": created["id"]})
        self.assertFalse(any(b["id"] == created["id"] for b in deleted["snapshot"]["customBuckets"]))
        self.assertNotIn(created["id"], next(c for c in deleted["snapshot"]["cards"] if c["id"] == card["id"]).get("utilityBuckets", []))
        builtin = lists["roles"][0]["id"]
        if not lists["roles"][0]["custom"]:
            self.assertEqual(self.command("delete_role", {"role": builtin})["outcome"], "invalid")
        subtype = next(t.strip() for c in self.document["cards"] if "—" in (c.get("typeLine") or "") for t in c["typeLine"].split("—")[1].split())
        ack = self.command("set_subtype_sections", {"subtypes": [subtype]})
        self.assertIn(ack["outcome"], ("applied", "noop"), ack.get("error"))
        ack = self.command("set_granular_sections", {"mode": "default"})
        self.assertIn(ack["outcome"], ("applied", "noop"), ack.get("error"))
        ack = self.command("set_granular_sections", {"mode": "clear"})
        self.assertIn(ack["outcome"], ("applied", "noop"), ack.get("error"))
        settings = self.command("set_settings", {"zoom_sensitivity": 1.5, "placement_delay": 300}, kind="view")
        self.assertEqual(settings["result"]["zoom_sensitivity"], 1.5)
        self.assertEqual(settings["result"]["placement_delay"], 300)

    def test_move_cards_and_role_view_details(self):
        first, second = self.document["cards"][6], self.document["cards"][7]
        ack = self.command("move_cards", {"ids": [first["id"], second["id"]], "x_mm": 120, "y_mm": 240})
        self.assertEqual(ack["outcome"], "applied", ack.get("error"))
        moved = {c["id"]: c.get("manualPosition") for c in ack["snapshot"]["cards"]}
        self.assertEqual((moved[first["id"]]["x"], moved[first["id"]]["y"]), (120, 240))
        self.assertGreater(moved[second["id"]]["x"], 120, "several cards are laid out side by side")
        self.assertEqual(self.frame().evaluate("window.mtgViewer.state.cards.find(c => c.id === %r).manualPosition.x" % first["id"]), 120)
        self.command("reset_positions")
        self.assertEqual(self.frame().evaluate("window.mtgViewer.state.cards.filter(c => c.manualPosition).length"), 0)
        self.assertEqual(self.command("move_cards", {"ids": [first["id"]]})["outcome"], "invalid")
        for op in ("toggle_card_index", "toggle_review_list"):
            opened = self.command(op, kind="view")
            self.assertEqual(opened["outcome"], "applied", opened.get("error"))
            self.assertIn(opened["result"]["open"], (True, False))
            self.assertEqual(self.command(op, kind="view")["result"]["open"], not opened["result"]["open"])
        self.assertIn(self.command("toggle_specific_mechanics", kind="view")["outcome"], ("applied", "noop"))

    def test_unknown_operation_fails_without_change(self):
        ack = self.command("drop_table")
        self.assertEqual(ack["outcome"], "invalid")


if __name__ == "__main__":
    unittest.main()
