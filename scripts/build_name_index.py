"""Cache the printed (localised) name of every card in the library.

Scryfall keeps one card per printing, and a printing in French or Japanese carries
`printed_name`. The studio stores English names, so searching "mandeur" would never find
"Perigee Beckoner" without this index.

    python scripts/build_name_index.py [--force]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "plugin"))

from _hermes_paths import plugin_data  # noqa: E402
DATA = plugin_data()
INDEX = DATA / "name-index.json"
COLLECTION = "https://api.scryfall.com/cards/collection"
BATCH = 75
HEADERS = {"User-Agent": "MTGDeckStudio/0.1", "Accept": "application/json", "Content-Type": "application/json"}


def card_ids() -> dict[str, str]:
    """scryfallId -> stored English name, across every deck."""
    found: dict[str, str] = {}
    for path in (DATA / "decks").glob("deck_*/document.json"):
        document = json.loads(path.read_text(encoding="utf-8"))
        for card in document.get("cards") or []:
            scryfall_id = card.get("scryfallId")
            if scryfall_id:
                found.setdefault(scryfall_id, card.get("name") or "")
    return found


def fetch(ids: list[str]) -> list[dict]:
    body = json.dumps({"identifiers": [{"id": value} for value in ids]}).encode("utf-8")
    request = urllib.request.Request(COLLECTION, data=body, headers=HEADERS, method="POST")
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response).get("data") or []


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="ignore the existing index")
    args = parser.parse_args()

    known = {} if args.force else (json.loads(INDEX.read_text(encoding="utf-8")) if INDEX.exists() else {})
    ids = card_ids()
    missing = [value for value in ids if value not in known]
    print(f"cards: {len(ids)} | cached: {len(known)} | to fetch: {len(missing)}")

    for start in range(0, len(missing), BATCH):
        batch = missing[start:start + BATCH]
        try:
            cards = fetch(batch)
        except urllib.error.HTTPError as error:
            print("collection request failed:", error.code)
            break
        for card in cards:
            printed = (card.get("printed_name") or "").strip()
            english = (card.get("name") or "").strip()
            entry = {"name": english, "lang": card.get("lang") or "", "printed": printed}
            known[card["id"]] = entry
        for value in batch:
            known.setdefault(value, {"name": ids.get(value, ""), "lang": "", "printed": ""})
        print(f"  {min(start + BATCH, len(missing))}/{len(missing)} fetched")
        time.sleep(0.12)

    INDEX.write_text(json.dumps(known, ensure_ascii=False, indent=0), encoding="utf-8")
    localised = sum(1 for entry in known.values() if entry.get("printed") and entry["printed"] != entry.get("name"))
    print(f"wrote {INDEX} | localised names: {localised}/{len(known)}")
    sample = [(entry["printed"], entry["name"]) for entry in known.values()
              if entry.get("printed") and entry["printed"] != entry.get("name")][:5]
    for printed, english in sample:
        print(f"  {printed}  →  {english}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
