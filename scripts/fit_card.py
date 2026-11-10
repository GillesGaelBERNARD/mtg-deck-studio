"""Would this card fit? Fetch it, classify it, judge its links against a deck.

The classifier is JavaScript and lives in the viewer, so this script opens the viewer headlessly
and asks it, exactly like the pane does at runtime:

    uv run --no-project --with playwright python scripts/fit_card.py "Ramunap Excavator" \
        --deck deck_148630200cfb4bc7 --save

Without --save the deck and its sideboard are left untouched.
"""
from __future__ import annotations

import argparse
import base64
import json
import sys
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT / "plugin"))

from core import links as links_mod  # noqa: E402
from core import scryfall, sideboard, store as store_mod  # noqa: E402

from _hermes_paths import plugin_data  # noqa: E402
DATA = plugin_data()

CLASSIFY = """cards => cards.map(card => {
  const result = window.AutoBucketClassifierV2.classifyCard({
    name: card.name, typeLine: card.type_line, oracleText: card.oracle_text, manaCost: card.mana_cost
  });
  return { autoBuckets: result.autoBuckets, autoBucketFacts: result.autoBucketFacts };
})"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("card", help="card name (English or fuzzy)")
    parser.add_argument("--deck", required=True, help="deck id")
    parser.add_argument("--save", action="store_true", help="put the card in the sideboard")
    args = parser.parse_args()

    from playwright.sync_api import sync_playwright

    sys.path.insert(0, str(PROJECT / "plugin" / "tests"))
    from test_core import core  # noqa: E402  (sys.path set above)
    from test_viewer_browser import HOST  # noqa: E402

    store = store_mod.Store(DATA)
    document = store.load(args.deck)
    entry = sideboard._entry(scryfall.named_card(args.card))
    print(f"card     : {entry['name']} {entry.get('mana_cost') or ''} — {entry.get('type_line') or ''}")

    html = core.viewer.build()
    payload = "data:text/html;base64," + base64.b64encode(html.encode()).decode()
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 900, "height": 700})
        page.set_content(HOST)
        page.evaluate("u => { document.getElementById('f').src = u }", payload)
        frame = page.frames[1]
        frame.wait_for_function("() => !!window.mtgViewer", timeout=30000)
        facts = frame.evaluate(CLASSIFY, [{
            "name": entry["name"], "type_line": entry.get("type_line"),
            "oracle_text": entry.get("oracle_text"), "mana_cost": entry.get("mana_cost"),
        }])[0]
        browser.close()

    entry["autoBucketFacts"] = facts["autoBucketFacts"]
    entry["autoBuckets"] = facts["autoBuckets"]
    print(f"facts    : {len(facts['autoBucketFacts'])} | effects: {', '.join(facts['autoBuckets'][:6])}")

    view = links_mod.evaluate(document, entry)
    for side, label in (("fed_by", "this card reacts to"), ("feeds", "cards that react to it")):
        groups = view[side]
        total = sum(len(group["cards"]) for group in groups)
        print(f"\n{label} ({total} cards in {len(groups)} groups)")
        for group in groups:
            names = ", ".join(card["name"] for card in group["cards"][:8])
            more = "" if len(group["cards"]) <= 8 else f" … +{len(group['cards']) - 8}"
            print(f"  · {group['label']} ({group['count']}): {names}{more}")

    if args.save:
        result = sideboard.add(store, args.deck, [entry], reason="Checked with scripts/fit_card.py")
        print(f"\nsaved    : {result['added'] or 'already in the sideboard'} ({result['count']} candidates)")
    print("\n" + json.dumps({"fed_by": sum(len(g["cards"]) for g in view["fed_by"]),
                             "feeds": sum(len(g["cards"]) for g in view["feeds"])}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
