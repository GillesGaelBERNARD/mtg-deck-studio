import importlib.util
import json
import re
import sys
import tempfile
import threading
import unittest
from pathlib import Path

PLUGIN = Path(__file__).resolve().parent.parent
PROJECT = PLUGIN.parent
# The published repository carries one stripped deck (images dropped, 227 kB) so the suite
# runs anywhere; on the author's machine the real save is used instead.
BUNDLED_DECK = Path(__file__).resolve().parent / "fixtures" / "sample-deck.json"
FIXTURE = PROJECT / "decks" / "latest" / "Last_Shivers" / "Last_Shivers_mtg_viewer.json"
if not FIXTURE.is_file():
    FIXTURE = BUNDLED_DECK


def load_core():
    mod = sys.modules.get("mtg_deck_studio_core")
    if mod is None:
        spec = importlib.util.spec_from_file_location(
            "mtg_deck_studio_core", PLUGIN / "core" / "__init__.py", submodule_search_locations=[str(PLUGIN / "core")]
        )
        mod = importlib.util.module_from_spec(spec)
        sys.modules["mtg_deck_studio_core"] = mod
        spec.loader.exec_module(mod)
    return mod.current()


core = load_core()


def sample_bundle():
    path = next((PROJECT / "decks" / "latest").rglob("*mtg_viewer.json"), None) if (PROJECT / "decks" / "latest").is_dir() else None
    if path is None:
        path = BUNDLED_DECK
    if not path.is_file():
        raise unittest.SkipTest("no deck fixture available")
    return json.loads(path.read_text(encoding="utf-8"))


class FakeWindow:
    """Plays the Desktop pane: answers commands for one session the way the viewer does."""

    def __init__(self, store, session_id, deck_id, respond=True):
        self.store, self.session_id, self.deck_id, self.respond = store, session_id, deck_id, respond
        self.rev = store.rev(deck_id)
        self.seen = []

    def __call__(self, event, payload):
        self.seen.append(payload)
        if not self.respond or payload["session_id"] != self.session_id:
            return
        threading.Thread(target=self._answer, args=(payload,)).start()

    def _answer(self, env):
        doc = self.store.load(self.deck_id)
        ack = {"command_id": env["command_id"], "deck_id": env["deck_id"], "op": env["op"]}
        if env.get("expected_rev") is not None and env["expected_rev"] != self.rev:
            ack.update(outcome="stale_revision", rev=self.rev)
        elif env["op"] == "set_deck_title":
            doc["deckTitle"] = env["args"]["title"]
            ack.update(outcome="applied", base_rev=self.rev, rev=self.rev + 1, snapshot=doc)
            self.rev += 1
        else:
            ack.update(outcome="applied", rev=self.rev)
        core.bridge.receive_ack(ack, self.store)


class AnyDeckWindow:
    """Answers for whatever deck the command names — used where the tool creates the deck itself."""

    def __init__(self, store):
        self.store = store
        self.seen = []

    def __call__(self, event, payload):
        self.seen.append(payload)
        outcome = "started" if payload["op"] == "import_mythictools_csv" else "applied"
        ack = {"command_id": payload["command_id"], "deck_id": payload["deck_id"], "op": payload["op"], "outcome": outcome}
        threading.Thread(target=core.bridge.receive_ack, args=(ack, self.store)).start()


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = core.store.Store(Path(self.tmp.name))
        self.bundle = sample_bundle()

    def tearDown(self):
        self.tmp.cleanup()

    def test_import_keeps_identity_and_printing_and_drops_images(self):
        record = self.store.create(self.bundle)
        doc = self.store.load(record["deck_id"])
        self.assertEqual([c["id"] for c in doc["cards"]], [c["id"] for c in self.bundle["cards"]])
        for field in ("setCode", "collectorNumber", "language", "finish", "scryfallId"):
            self.assertEqual([c.get(field) for c in doc["cards"]], [c.get(field) for c in self.bundle["cards"]], field)
        self.assertTrue(all(c["imageData"] == "" for c in doc["cards"]))
        self.assertEqual(doc["studio"]["rev"], 1)
        self.assertLess((Path(self.tmp.name) / "decks" / record["deck_id"] / "document.json").stat().st_size, 1_500_000)

    def test_localised_names_are_annotated_and_hash_neutral(self):
        deck_id = self.store.create(self.bundle)["deck_id"]
        card = self.bundle["cards"][0]
        (Path(self.tmp.name) / "name-index.json").write_text(
            json.dumps({card["scryfallId"]: {"name": card["name"], "lang": "fr", "printed": "Mandeur de périlée"}}),
            encoding="utf-8")
        doc = self.store.load(deck_id)
        annotated = next(c for c in doc["cards"] if c["id"] == card["id"])
        self.assertEqual(annotated["printedName"], "Mandeur de périlée")
        self.assertEqual(annotated["printedLanguage"], "fr")
        bare = dict(doc)
        bare["cards"] = [{k: v for k, v in c.items() if k not in ("printedName", "printedLanguage")} for c in doc["cards"]]
        self.assertEqual(core.store.content_hash(doc), core.store.content_hash(bare))
        self.assertEqual(self.store.persist(deck_id, 1, 2, doc)["status"], "saved")

    def test_persist_requires_current_base_revision(self):
        deck_id = self.store.create(self.bundle)["deck_id"]
        doc = self.store.load(deck_id)
        doc["deckTitle"] = "Renamed"
        self.assertEqual(self.store.persist(deck_id, 1, 2, doc)["status"], "saved")
        self.assertEqual(self.store.persist(deck_id, 1, 2, doc)["status"], "noop")
        doc["deckTitle"] = "Other window"
        self.assertEqual(self.store.persist(deck_id, 1, 2, doc)["status"], "conflict")
        self.assertEqual(self.store.load(deck_id)["deckTitle"], "Renamed")

    def test_bindings_and_index_rebuild(self):
        deck_id = self.store.create(self.bundle)["deck_id"]
        self.store.bind("local-a", deck_id)
        self.assertEqual(self.store.binding("local-a"), deck_id)
        self.assertIsNone(self.store.binding("local-b"))
        (Path(self.tmp.name) / "index.json").unlink()
        self.store.rebuild_index()
        self.assertEqual([d["deck_id"] for d in self.store.list_decks()], [deck_id])

    def test_rejects_non_viewer_documents_and_bad_ids(self):
        with self.assertRaises(core.errors.StudioError):
            self.store.create({"cards": []})
        with self.assertRaises(core.errors.StudioError):
            self.store.load("../etc")


class ToolTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = core.store.Store(Path(self.tmp.name))
        self.deck_id = self.store.create(sample_bundle())["deck_id"]
        self.store.bind("local-a", self.deck_id)
        self._broadcast = core.bridge.broadcast

    def tearDown(self):
        core.bridge.broadcast = self._broadcast
        core.bridge.STATE.contexts.clear()
        self.tmp.cleanup()

    def call(self, name, args, session="local-a"):
        return json.loads(core.tools.dispatch(name, args, session_id=session, store=self.store))

    def test_apply_round_trip_persists_acknowledged_revision(self):
        core.bridge.broadcast = FakeWindow(self.store, "local-a", self.deck_id)
        result = self.call("mtg_deck_studio_apply", {"op": "set_deck_title", "args": {"title": "From chat"}})
        self.assertEqual(result["outcome"], "applied")
        self.assertEqual(result["rev"], 2)
        self.assertEqual(self.store.load(self.deck_id)["deckTitle"], "From chat")

    def test_stale_revision_changes_nothing(self):
        core.bridge.broadcast = FakeWindow(self.store, "local-a", self.deck_id)
        result = self.call("mtg_deck_studio_apply", {"op": "set_deck_title", "args": {"title": "x"}, "expected_rev": 7})
        self.assertEqual(result["outcome"], "stale_revision")
        self.assertNotEqual(self.store.load(self.deck_id)["deckTitle"], "x")

    def test_no_window_is_unavailable_not_success(self):
        core.bridge.broadcast = FakeWindow(self.store, "local-a", self.deck_id, respond=False)
        old = core.bridge.ACK_TIMEOUT_S
        core.bridge.ACK_TIMEOUT_S = 0.3
        try:
            result = self.call("mtg_deck_studio_apply", {"op": "set_deck_title", "args": {"title": "x"}})
        finally:
            core.bridge.ACK_TIMEOUT_S = old
        self.assertEqual(result["outcome"], "pane_unavailable")

    def test_invalid_operation_and_arguments_are_refused_before_sending(self):
        window = FakeWindow(self.store, "local-a", self.deck_id)
        core.bridge.broadcast = window
        self.assertEqual(self.call("mtg_deck_studio_apply", {"op": "rm_rf"})["outcome"], "invalid")
        self.assertEqual(self.call("mtg_deck_studio_apply", {"op": "set_deck_title", "args": {"title": 3}})["outcome"], "invalid")
        self.assertEqual(self.call("mtg_deck_studio_apply", {"op": "set_deck_title", "args": {"title": "a", "x": 1}})["outcome"], "invalid")
        self.assertEqual(window.seen, [])

    def test_unbound_session_is_told_how_to_bind(self):
        result = self.call("mtg_deck_studio_state", {}, session="local-z")
        self.assertEqual(result["outcome"], "not_bound")

    def test_state_reads_stored_deck_and_filters_cards(self):
        result = self.call("mtg_deck_studio_state", {"cards": {"section": "lands", "limit": 5}})
        self.assertEqual(result["source"], "stored")
        self.assertEqual(result["card_count"], 100)
        self.assertTrue(result["commanders"])
        self.assertLessEqual(len(result["cards"]), 5)
        self.assertTrue(all(c["section"] == "lands" for c in result["cards"]))

    def test_context_block_names_deck_and_selection(self):
        self.assertIsNone(core.context.block("local-z", store=self.store))
        doc = self.store.load(self.deck_id)
        card = doc["cards"][0]
        core.bridge.update_context({"session_id": "local-a", "deck_id": self.deck_id, "rev": 1,
                                    "selected": [{"id": card["id"], "name": card["name"]}], "view": "table"})
        text = core.context.block("local-a", store=self.store)
        self.assertIn(self.deck_id, text)
        self.assertIn(card["name"], text)


class ImportFileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = core.store.Store(Path(self.tmp.name) / "data")
        self._default = core.store.default
        core.store.default = lambda: self.store

    def tearDown(self):
        core.store.default = self._default
        self.tmp.cleanup()

    def test_large_save_is_read_by_the_backend_and_bound(self):
        source = next((PROJECT / "decks" / "latest").rglob("*mtg_viewer.json"), None)
        if source is None:
            self.skipTest("deck fixtures are not present")
        self.assertGreater(source.stat().st_size, 4 * 1024 * 1024, "the case ctx.files.readText refuses")
        result = core.rpc.call("import_file", {"path": str(source), "session_id": "local-x"})
        self.assertEqual(result["kind"], "save")
        self.assertEqual(self.store.binding("local-x"), result["deck_id"])

    def test_csv_and_text_are_returned_for_the_viewer_to_resolve(self):
        csv = Path(self.tmp.name) / "export.csv"
        csv.write_text("\ufeffCard Name,Quantity\nSol Ring,1\n", encoding="utf-8")
        result = core.rpc.call("import_file", {"path": str(csv)})
        self.assertEqual((result["kind"], result["name"]), ("csv", "export"))
        self.assertTrue(result["text"].startswith("Card Name"))

    def test_relative_or_missing_paths_are_refused(self):
        self.assertEqual(core.rpc.call("import_file", {"path": "decks/x.json"})["outcome"], "invalid")
        self.assertEqual(core.rpc.call("import_file", {"path": str(Path(self.tmp.name) / "none.json")})["outcome"], "invalid")



class MythicToolsCsvImportTests(unittest.TestCase):
    """The pane's CSV importer, reachable from the AI: by path, and as a public operation."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = core.store.Store(Path(self.tmp.name) / "data")
        self._default = core.store.default
        core.store.default = lambda: self.store
        self.csv = Path(self.tmp.name) / "scan_2026-11-09.csv"
        self.csv.write_text(
            "\ufeffCard Name,English Name,Set Code,Collector Number,Rarity,Language,Quantity,Price (EUR)\n"
            '"Mandeur de périgée","Perigee Beckoner","eoe","112","common","fr",1,0.11\n'
            '"Montagne","Mountain","snc","268","common","fr",2,0.05\n',
            encoding="utf-8",
        )
        self._broadcast = core.bridge.broadcast
        core.bridge.STATE.contexts.clear()

    def tearDown(self):
        core.bridge.broadcast = self._broadcast
        core.bridge.STATE.contexts.clear()
        core.store.default = self._default
        self.tmp.cleanup()

    def call(self, args, session="local-a"):
        return json.loads(core.tools.dispatch("mtg_deck_studio_decks", args, session_id=session, store=self.store))

    def pane(self):
        window = AnyDeckWindow(self.store)
        core.bridge.broadcast = window
        # A live context is what tells the tool a pane is there to run the load.
        core.bridge.update_context({"session_id": "local-a", "deck_id": "deck_any", "rev": 1, "view": "table"})
        return window

    def test_csv_by_path_creates_a_deck_and_runs_the_pane_importer(self):
        window = self.pane()
        result = self.call({"action": "import", "path": str(self.csv), "title": "Sci-fi void"})
        self.assertEqual(result["outcome"], "started")
        self.assertEqual(result["format"], "mythictools-csv")
        self.assertEqual(result["csv_cards"], 3)
        self.assertEqual(result["title"], "Sci-fi void")
        self.assertEqual(self.store.binding("local-a"), result["deck_id"])
        self.assertEqual([e["op"] for e in window.seen], ["flush", "open_deck", "import_mythictools_csv"])
        sent = window.seen[2]["args"]
        self.assertEqual(sent["name"], "scan_2026-11-09")
        self.assertIn("Mandeur de périgée", sent["text"])
        self.assertEqual([d["title"] for d in self.store.list_decks()], ["Sci-fi void"])

    def test_csv_into_an_existing_deck_replaces_its_cards(self):
        deck_id = self.store.create(core.tools.EMPTY_BUNDLE, title="Sci-fi void")["deck_id"]
        window = self.pane()
        result = self.call({"action": "import", "path": str(self.csv), "deck_id": deck_id})
        self.assertEqual((result["outcome"], result["deck_id"]), ("started", deck_id))
        self.assertEqual(len(self.store.list_decks()), 1)
        self.assertEqual(window.seen[-1]["deck_id"], deck_id)

    def test_a_missing_pane_is_reported_before_a_deck_is_created(self):
        result = self.call({"action": "import", "path": str(self.csv)})
        self.assertEqual(result["outcome"], "pane_unavailable")
        self.assertEqual(self.store.list_decks(), [])

    def test_the_operation_is_public_so_the_apply_tool_can_send_the_text(self):
        self.assertIn("import_mythictools_csv", core.operations.public_names())
        window = self.pane()
        deck_id = self.store.create(core.tools.EMPTY_BUNDLE, title="Sci-fi void")["deck_id"]
        self.store.bind("local-a", deck_id)
        core.bridge.update_context({"session_id": "local-a", "deck_id": deck_id, "rev": 1})
        result = json.loads(core.tools.dispatch(
            "mtg_deck_studio_apply",
            {"op": "import_mythictools_csv", "args": {"text": self.csv.read_text(encoding="utf-8-sig"), "name": "scan"}},
            session_id="local-a", store=self.store))
        self.assertEqual(result["outcome"], "started")
        self.assertEqual(window.seen[-1]["op"], "import_mythictools_csv")


class SideboardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = core.store.Store(Path(self.tmp.name))
        self.deck_id = self.store.create(sample_bundle())["deck_id"]
        self.store.bind("local-a", self.deck_id)
        self._broadcast = core.bridge.broadcast

    def tearDown(self):
        core.bridge.broadcast = self._broadcast
        core.bridge.STATE.contexts.clear()
        self.tmp.cleanup()

    def card(self, name="Cryptic Command", scryfall_id="11111111-2222-3333-4444-555555555555", **extra):
        data = {"name": name, "scryfall_id": scryfall_id, "mv": 4, "type_line": "Instant",
                "oracle_text": "Counter target spell.", "image": "https://img/x.jpg"}
        data.update(extra)
        return data

    def call(self, args, session="local-a"):
        return json.loads(core.tools.dispatch("mtg_deck_studio_sideboard", args, session_id=session, store=self.store))

    def test_add_dedupes_and_skips_cards_already_in_the_deck(self):
        in_deck = self.store.load(self.deck_id)["cards"][0]["name"]
        result = self.call({"action": "add", "reason": "fits the tap plan",
                            "cards": [self.card(), self.card(), self.card(name=in_deck)]})
        self.assertEqual(result["added"], ["Cryptic Command"])
        self.assertEqual(result["count"], 1)
        self.assertEqual(self.store.sideboard(self.deck_id)[0]["reason"], "fits the tap plan")
        # Adding the same card again updates it instead of duplicating.
        again = self.call({"action": "add", "cards": [self.card(reason=None)]})
        self.assertEqual(again["added"], [])
        self.assertEqual(again["updated"], ["Cryptic Command"])
        self.assertEqual(len(self.store.sideboard(self.deck_id)), 1)

    def test_remove_and_clear(self):
        self.call({"action": "add", "cards": [self.card()]})
        removed = self.call({"action": "remove", "scryfall_ids": ["11111111-2222-3333-4444-555555555555"]})
        self.assertEqual(removed["count"], 0)
        self.call({"action": "add", "cards": [self.card()]})
        self.assertEqual(self.call({"action": "clear"})["count"], 0)
        self.assertEqual(self.store.sideboard(self.deck_id), [])

    def test_promote_adds_the_card_then_drops_the_entry(self):
        window = FakeWindow(self.store, "local-a", self.deck_id)
        core.bridge.broadcast = window
        self.call({"action": "add", "cards": [self.card()]})
        result = self.call({"action": "promote", "scryfall_id": "11111111-2222-3333-4444-555555555555"})
        self.assertEqual(result["outcome"], "applied")
        self.assertEqual(result["card"], "Cryptic Command")
        self.assertEqual(result["count"], 0)
        self.assertTrue(any(p.get("op") == "add_cards" for p in window.seen))

    def test_replace_sends_the_deck_card_id(self):
        window = FakeWindow(self.store, "local-a", self.deck_id)
        core.bridge.broadcast = window
        target = self.store.load(self.deck_id)["cards"][3]
        self.call({"action": "add", "cards": [self.card()]})
        result = self.call({"action": "replace", "scryfall_id": "11111111-2222-3333-4444-555555555555",
                            "replace_id": target["id"]})
        self.assertEqual(result["outcome"], "applied")
        sent = [p for p in window.seen if p.get("op") == "replace_card"]
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0]["args"]["id"], target["id"])
        self.assertEqual(sent[0]["args"]["scryfall_id"], "11111111-2222-3333-4444-555555555555")
        self.assertEqual(self.store.sideboard(self.deck_id), [])

    def test_a_refused_deck_change_keeps_the_entry(self):
        core.bridge.broadcast = FakeWindow(self.store, "local-a", self.deck_id, respond=False)
        self.call({"action": "add", "cards": [self.card()]})
        old = core.bridge.ACK_TIMEOUT_S
        try:
            core.bridge.send.__defaults__ = (None, None, 0.3)
            result = self.call({"action": "promote", "scryfall_id": "11111111-2222-3333-4444-555555555555"})
        finally:
            core.bridge.send.__defaults__ = (None, None, old)
        self.assertEqual(result["outcome"], "pane_unavailable")
        self.assertEqual(len(self.store.sideboard(self.deck_id)), 1)

    def test_add_from_a_ref_resolves_the_named_printing(self):
        """A card the player photographed arrives as 'Name (SET) 123', not as a search hit."""
        seen = []

        def fake_resolve(ref, client=None):
            seen.append(ref)
            return {
                "object": "card", "id": "99999999-8888-7777-6666-555555555555", "name": "Hylderblade",
                "set": "eoe", "set_name": "Edge of Eternities", "collector_number": "106", "lang": "en",
                "printed_name": None, "mana_cost": "{B}", "cmc": 1, "type_line": "Artifact — Equipment",
                "oracle_text": "Equipped creature gets +3/+1.", "scryfall_uri": "https://scryfall.com/x",
                "image_uris": {"normal": "https://img/eoe106.jpg"},
            }

        original = core.scryfall.resolve_ref
        core.scryfall.resolve_ref = fake_resolve
        try:
            result = self.call({"action": "add", "refs": ["Hylderblade (EOE) 106"], "reason": "Scanned from a photo"})
        finally:
            core.scryfall.resolve_ref = original
        self.assertEqual(seen, ["Hylderblade (EOE) 106"])
        self.assertEqual(result["added"], ["Hylderblade"])
        entry = self.store.sideboard(self.deck_id)[0]
        self.assertEqual(entry["set"], "eoe")
        self.assertEqual(entry["collector_number"], "106")
        self.assertEqual(entry["reason"], "Scanned from a photo")

    def test_a_locked_file_is_retried_not_failed(self):
        """Windows refuses os.replace while another handle holds the file (the pane polls
        sideboard.json); one lost race must not surface as a 500."""
        import os as os_mod
        from unittest import mock

        import tempfile

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "sideboard.json"
        real = os_mod.replace
        calls = {"n": 0}

        def flaky(src, dst):
            calls["n"] += 1
            if calls["n"] < 3:
                raise PermissionError(5, "Access is denied")
            return real(src, dst)

        with mock.patch.object(core.store.os, "replace", side_effect=flaky):
            core.store._write_json(path, {"cards": []})
        self.assertEqual(calls["n"], 3)
        self.assertIn("cards", path.read_text(encoding="utf-8"))
        self.assertFalse(list(path.parent.glob(".*.tmp")), "the temp file must not be left behind")

    def test_set_constraint_reaches_query_and_verification(self):
        query, _ = core.scryfall.build_query(None, {"sets": ["blb"]}, ["U", "B"])
        self.assertIn("s:blb", query)
        verdict = core.scryfall.check({"set": "dmu", "legalities": {"commander": "legal"}, "color_identity": ["U"]},
                                      {"sets": ["blb"]}, ["U"])
        self.assertIn("set:blb", verdict["violated"])


class ScannedPhotoTests(unittest.TestCase):
    """A camera capture becomes a file the chat can open: the pane cannot attach images."""

    def setUp(self):
        import tempfile

        self.tmp = tempfile.TemporaryDirectory()
        self.store = core.store.Store(Path(self.tmp.name))
        self.original = core.rpc._store
        core.rpc._store = lambda: self.store

    def tearDown(self):
        core.rpc._store = self.original
        self.tmp.cleanup()

    def data_url(self, blob):
        import base64

        return "data:image/jpeg;base64," + base64.b64encode(blob).decode("ascii")

    def test_saves_the_photo_beside_the_decks(self):
        blob = b"\xff\xd8\xff\xe0" + b"card" * 600
        out = core.rpc.save_scan({"data_url": self.data_url(blob)})
        path = Path(out["path"])
        self.assertTrue(path.exists())
        self.assertEqual(path.read_bytes(), blob)
        self.assertEqual(path.parent.name, "scans")
        self.assertEqual(out["bytes"], len(blob))
        # A second capture never overwrites the first.
        again = core.rpc.save_scan({"data_url": self.data_url(blob)})
        self.assertNotEqual(again["path"], out["path"])

    def test_refuses_anything_that_is_not_a_picture(self):
        for bad in ("https://example.com/card.jpg", "data:text/plain;base64,aGVsbG8=", "", "data:image/jpeg;base64,"):
            with self.assertRaises(core.errors.StudioError, msg=bad[:40]):
                core.rpc.save_scan({"data_url": bad})

    def test_sideboard_add_from_a_ref_uses_the_same_resolver(self):
        calls = []

        def fake_resolve(ref, client=None):
            calls.append(ref)
            return {"object": "card", "id": "99999999-8888-7777-6666-555555555555", "name": "Hylderblade",
                    "set": "eoe", "collector_number": "106", "lang": "en", "mana_cost": "{B}", "cmc": 1,
                    "type_line": "Artifact — Equipment", "oracle_text": "x"}

        deck_id = self.store.create(sample_bundle())["deck_id"]
        original = core.scryfall.resolve_ref
        core.scryfall.resolve_ref = fake_resolve
        try:
            result = core.rpc.sideboard_add({"deck_id": deck_id, "refs": ["Hylderblade (EOE) 106"]})
        finally:
            core.scryfall.resolve_ref = original
        self.assertEqual(calls, ["Hylderblade (EOE) 106"])
        self.assertEqual(result["added"], ["Hylderblade"])


class RegistryConsistencyTests(unittest.TestCase):
    def test_operations_json_matches_viewer_and_tool_enum(self):
        registry = core.operations.registry()
        viewer_ops = set(re.findall(r"^    (\w+): (?:async )?function", (PLUGIN / "viewer" / "studio.js").read_text(encoding="utf-8"), re.M))
        guest_ops = {n for n, op in registry.items() if op.get("handler") != "host"}
        self.assertEqual(viewer_ops, guest_ops)
        enum = next(s for s in core.tools.schemas() if s["name"] == "mtg_deck_studio_apply")["parameters"]["properties"]["op"]["enum"]
        self.assertEqual(set(enum), {n for n, op in registry.items() if not op.get("internal")})
        for op in registry.values():
            self.assertIn(op["kind"], {"document", "view", "query", "session"})

    def test_viewer_patches_apply_exactly_once(self):
        html = core.viewer.build()
        self.assertIn("const HISTORY_LIMIT = 30;", html)
        self.assertIn("window.mtgDeckStudio", html)
        self.assertIn("crypto.randomUUID = function", html)
        for blocked in ("window.prompt(", "window.confirm(", "window.alert("):
            self.assertNotIn(blocked, html, "sandboxed frames block modal dialogs")

    def test_evasion_is_grouped_under_pressure(self):
        # Evasion does not build the board; it is how an existing board connects.
        html = core.viewer.build()
        self.assertIn('cluster: "Pressure", label: "Evasion"', html)
        self.assertNotIn('cluster: "Build the board", label: "Evasion"', html)

    def test_manifest_declares_registered_tools(self):
        manifest = (PLUGIN / "plugin.yaml").read_text(encoding="utf-8")
        declared = set(re.findall(r"^  - (mtg_deck_studio_\w+)$", manifest, re.M))
        self.assertEqual(declared, {s["name"] for s in core.tools.schemas()})


if __name__ == "__main__":
    unittest.main()
