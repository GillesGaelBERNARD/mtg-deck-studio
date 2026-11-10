import io
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path

from test_core import core, sample_bundle

FIXTURES = Path(__file__).resolve().parent / "fixtures"
HEADINGS = [
    "## Theme & Story", "## Deck Snapshot", "## Mechanical Identity", "## Game Plan", "## Key Cards",
    "## Synergies & Engines", "## Combos & Loops", "## How to Pilot", "### Opening Hand & Mulligans",
    "### Early Game", "### Midgame", "### Late Game", "## What to Protect", "## Weaknesses & Recovery",
    "## Alternate Commanders",
]


def recorded(payload):
    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    calls = []

    def opener(request, timeout=None):
        calls.append(request.full_url)
        if isinstance(payload, Exception):
            raise payload
        return Response(json.dumps(payload).encode("utf-8"))

    return opener, calls


class LocalisedNameTests(unittest.TestCase):
    """Cards are bought in French, German, Italian... so names must resolve as printed."""

    def card(self, name="Burdened Aerialist", printed="Voltigeur chargé"):
        return {"object": "card", "id": "abc", "name": name, "printed_name": printed, "lang": "fr",
                "mana_cost": "{2}{U}", "cmc": 3, "type_line": "Creature — Human Pirate",
                "oracle_text": "When this creature enters, create a Treasure token.", "prices": {"usd": "0.10"}}

    def test_a_printed_name_resolves_through_the_search_endpoint(self):
        empty = {"object": "error", "code": "not_found"}
        search = {"object": "list", "total_cards": 1, "data": [self.card()]}
        payloads = [empty, empty, search]
        opener, calls = recorded(payloads[0])
        calls_seen = []

        class Response(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        def opener(request, timeout=None):
            calls_seen.append(request.full_url)
            payload = payloads[min(len(calls_seen) - 1, len(payloads) - 1)]
            return Response(json.dumps(payload).encode("utf-8"))

        card = core.scryfall.named_card("Voltigeur chargé", core.scryfall.Client(opener))
        self.assertEqual(card["name"], "Burdened Aerialist")
        self.assertTrue(card["scryfall_id"])
        self.assertIn("lang%3Afr", " ".join(calls_seen), "the search must ask for the French printing")

    def test_an_unknown_name_is_reported_not_silently_empty(self):
        empty = {"object": "error", "code": "not_found"}

        class Response(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        def opener(request, timeout=None):
            return Response(json.dumps(empty).encode("utf-8"))

        with self.assertRaises(core.errors.StudioError):
            core.scryfall.named_card("Ce nom n'existe pas", core.scryfall.Client(opener))


class ScryfallTests(unittest.TestCase):
    def setUp(self):
        self.doc = core.store.strip_images(sample_bundle())
        self.identity = core.scryfall.deck_identity(self.doc)
        self.original = next(c for c in self.doc["cards"] if not c.get("isCommander"))

    def test_query_always_carries_identity_legality_and_excludes_original(self):
        query, unexpressed = core.scryfall.build_query(self.original, {"max_mv": 2, "function_tags": ["mana rock"], "soft": ["cheap"]}, ["U", "B", "G"])
        for part in ("id<=ubg", "f:commander", "game:paper", "-is:digital", "mv<=2", "otag:mana-rock", f'-!"{self.original["name"]}"'):
            self.assertIn(part, query)
        self.assertEqual(unexpressed, ["cheap"])

    def test_results_violating_hard_constraints_never_reach_the_tray(self):
        payload = json.loads((FIXTURES / "scryfall-mana-rocks.json").read_text(encoding="utf-8"))
        payload["data"].append({"name": "Off Color Rock", "oracle_id": "x", "cmc": 1, "type_line": "Artifact",
                                "color_identity": ["R"], "legalities": {"commander": "legal"}})
        payload["data"].append({"name": "Banned Rock", "oracle_id": "y", "cmc": 1, "type_line": "Artifact",
                                "color_identity": [], "legalities": {"commander": "banned"}})
        opener, calls = recorded(payload)
        result = core.scryfall.find_alternatives(self.doc, self.original, {"max_mv": 1, "types": ["artifact"]}, core.scryfall.Client(opener))
        names = [c["name"] for c in result["candidates"]]
        self.assertNotIn("Off Color Rock", names)
        self.assertNotIn("Banned Rock", names)
        self.assertTrue(all(c["mv"] <= 1 for c in result["candidates"]))
        self.assertLessEqual(len(result["candidates"]), core.scryfall.MAX_CANDIDATES)
        self.assertIn("unique=cards", calls[0])

    def test_cards_already_in_deck_are_listed_separately(self):
        payload = json.loads((FIXTURES / "scryfall-mana-rocks.json").read_text(encoding="utf-8"))
        deck_names = {c["name"] for c in self.doc["cards"]}
        opener, _ = recorded(payload)
        result = core.scryfall.find_alternatives(self.doc, None, {}, core.scryfall.Client(opener))
        self.assertFalse(any(c["name"] in deck_names for c in result["candidates"]))
        self.assertEqual(set(result["already_in_deck"]), {c["name"] for c in payload["data"]} & deck_names)

    def test_not_found_is_zero_results_not_an_error(self):
        opener, _ = recorded(urllib.error.HTTPError("u", 404, "not found", {}, io.BytesIO(b"{}")))
        result = core.scryfall.find_alternatives(self.doc, self.original, {"oracle": ["no card says this"]}, core.scryfall.Client(opener))
        self.assertEqual(result["candidates"], [])

    def test_double_faced_cards_use_face_text_and_front_name(self):
        card = {"name": "Front // Back", "card_faces": [{"name": "Front", "oracle_text": "Draw a card.", "type_line": "Creature", "mana_cost": "{U}", "image_uris": {"normal": "f.jpg"}},
                                                       {"name": "Back", "oracle_text": "Scry 2.", "type_line": "Land", "mana_cost": ""}],
                "cmc": 1, "color_identity": ["U"], "legalities": {"commander": "legal"}}
        self.assertEqual(core.scryfall.front_name(card), "Front")
        self.assertIn("Scry 2.", core.scryfall.oracle_text(card))
        self.assertEqual(core.scryfall.check(card, {"oracle": ["scry 2"], "types": ["land"]}, ["U"])["violated"], [])
        self.assertEqual(core.scryfall.image_uri(card), "f.jpg")


class StrategyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = core.store.Store(Path(self.tmp.name))
        self.deck_id = self.store.create(sample_bundle())["deck_id"]

    def tearDown(self):
        self.tmp.cleanup()

    def draft(self, title):
        filler = " ".join(["The deck wants steady land drops and careful sequencing."] * 7)
        return "\n\n".join([f"# {title}"] + [f"{h}\n\n{filler}" for h in HEADINGS]) + "\n"

    def test_prepare_query_publish_and_freshness(self):
        prepared = core.strategy.prepare(self.store, self.deck_id)
        self.assertEqual(prepared["evidence"]["schema"], "mtg-deck-strategy-evidence/1")
        self.assertEqual(prepared["evidence"]["stats"]["cards"], 100)
        commander = prepared["evidence"]["commanders"][0]["name"]
        answer = core.strategy.query(self.store, self.deck_id, prepared["job_id"], {"cards": [commander]})
        self.assertEqual(answer["round"], 1)
        self.assertEqual(answer["evidence"]["cards"][0]["name"], commander)

        doc = self.store.load(self.deck_id)
        moved = json.loads(json.dumps(doc))
        moved["cards"][3]["manualPosition"] = {"x": 1, "y": 2}
        self.store.persist(self.deck_id, 1, 2, moved)

        published = core.strategy.publish(self.store, self.deck_id, prepared["job_id"], self.draft(doc["deckTitle"]))
        self.assertEqual(published["outcome"], "published")
        guide = core.strategy.guide_state(self.store, self.deck_id)["guide"]
        self.assertFalse(guide["stale"], "a layout-only change must not make the guide outdated")
        self.assertTrue(guide["markdown"].startswith("# "))

        smaller = self.store.load(self.deck_id)
        smaller["cards"] = smaller["cards"][:-1]
        self.store.persist(self.deck_id, 2, 3, smaller)
        self.assertTrue(core.strategy.guide_state(self.store, self.deck_id)["guide"]["stale"])
        self.assertEqual(self.store.load(self.deck_id).get("strategyNotes"), doc.get("strategyNotes"))

    def test_publish_refuses_a_draft_missing_required_sections(self):
        prepared = core.strategy.prepare(self.store, self.deck_id)
        with self.assertRaises(core.errors.StudioError) as raised:
            core.strategy.publish(self.store, self.deck_id, prepared["job_id"], "# Too short\n\nNothing here.")
        self.assertEqual(raised.exception.outcome, "evidence_error")
        self.assertIsNone(core.strategy.guide_state(self.store, self.deck_id)["guide"])

    def test_query_budget_is_bounded(self):
        prepared = core.strategy.prepare(self.store, self.deck_id)
        for _ in range(core.strategy.QUERY_ROUNDS):
            core.strategy.query(self.store, self.deck_id, prepared["job_id"], {"cards": []})
        with self.assertRaises(core.errors.StudioError):
            core.strategy.query(self.store, self.deck_id, prepared["job_id"], {"cards": []})


class ExportTests(unittest.TestCase):
    def test_moxfield_export_and_json_round_trip_the_bundled_deck(self):
        fixture = Path(__file__).resolve().parent / "fixtures" / "sample-deck.json"
        if not fixture.is_file():
            self.skipTest("the bundled deck fixture is missing")
        with tempfile.TemporaryDirectory() as tmp:
            store = core.store.Store(Path(tmp))
            bundle = json.loads(fixture.read_text(encoding="utf-8"))
            deck_id = store.create(bundle)["deck_id"]
            result = core.exports.export(store, deck_id, "moxfield")
            lines = [line for line in result["text"].splitlines() if line.strip()]
            self.assertEqual(lines[0], "Commander")
            self.assertIn("Deck", lines)
            quantities = [int(line.split(" ", 1)[0]) for line in lines if line[0].isdigit()]
            self.assertEqual(sum(quantities), len(bundle["cards"]))
            self.assertIn("Morska, Undersea Sleuth", result["text"])
            self.assertTrue(Path(result["path"]).is_file())
            saved = core.exports.export(store, deck_id, "json")
            reimported = store.create(json.loads(Path(saved["path"]).read_text(encoding="utf-8")))
            self.assertEqual([c["id"] for c in store.load(reimported["deck_id"])["cards"]], [c["id"] for c in store.load(deck_id)["cards"]])


if __name__ == "__main__":
    unittest.main()
