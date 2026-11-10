"""Sideboard: cards found for a deck that are not part of it yet.

Candidates are kept beside the deck document, never inside it, so the viewer's saved
format and revision history stay untouched. Promoting or replacing runs the ordinary
deck operation, then drops the entry once the change is acknowledged.
"""
from . import store as store_mod
from .errors import StudioError

LIMIT = 80
KEEP = (
    "name", "front_name", "printed_name", "printedName", "lang", "scryfall_id", "mana_cost", "mv", "type_line", "oracle_text",
    "image", "scryfall_uri", "set", "set_name", "collector_number", "reason",
    "autoBucketFacts", "autoBuckets", "autoBucketRuleVersion",
)


def _entry(card: dict, reason: str | None = None) -> dict:
    if not isinstance(card, dict):
        raise StudioError("invalid", "Each candidate must be a card object.")
    scryfall_id = (card.get("scryfall_id") or card.get("id") or "").strip()
    name = (card.get("name") or "").strip()
    if not scryfall_id or len(scryfall_id) != 36:
        raise StudioError("invalid", f"Candidate '{name or '?'}' has no Scryfall id.")
    if not name:
        raise StudioError("invalid", "Candidate has no card name.")
    entry = {key: card.get(key) for key in KEEP if card.get(key) not in (None, "")}
    entry["scryfall_id"] = scryfall_id
    entry["name"] = name
    if reason:
        entry["reason"] = reason
    return entry


def list_entries(store, deck_id: str) -> dict:
    entries = store.sideboard(deck_id)
    return {"deck_id": deck_id, "count": len(entries), "cards": entries}


def set_classification(store, deck_id: str, scryfall_id: str, classification: dict) -> dict:
    """Store the classifier's facts on one candidate, so links can be computed for it."""
    entries = store.sideboard(deck_id)
    for entry in entries:
        if entry.get("scryfall_id") == scryfall_id:
            entry["autoBucketFacts"] = classification.get("autoBucketFacts") or []
            entry["autoBuckets"] = classification.get("autoBuckets") or []
            entry["autoBucketRuleVersion"] = classification.get("ruleVersion") or ""
            store.save_sideboard(deck_id, entries)
            return entry
    raise StudioError("not_found", "That candidate is not in the sideboard.")


def find_candidate(store, deck_id: str, ref: str) -> dict | None:
    """Look a candidate up by Scryfall id, exact name, or a unique fragment. None if absent."""
    key = (ref or "").strip().casefold()
    if not key:
        return None
    for entry in store.sideboard(deck_id):
        if entry.get("scryfall_id") == ref or (entry.get("name") or "").casefold() == key:
            return entry
    for entry in store.sideboard(deck_id):
        if key in (entry.get("name") or "").casefold():
            return entry
    return None


def add(store, deck_id: str, cards: list, reason: str | None = None) -> dict:
    store.load(deck_id)
    entries = store.sideboard(deck_id)
    known = {entry["scryfall_id"]: entry for entry in entries}
    deck_names = {(card.get("name") or "").casefold() for card in store.load(deck_id).get("cards") or []}
    added, updated = [], []
    for card in cards or []:
        entry = _entry(card, reason or card.get("reason"))
        if entry["name"].casefold() in deck_names:
            continue
        if entry["scryfall_id"] in known:
            known[entry["scryfall_id"]].update({k: v for k, v in entry.items() if v})
            updated.append(entry["name"])
            continue
        entries.insert(0, entry)
        known[entry["scryfall_id"]] = entry
        added.append(entry["name"])
    entries = entries[:LIMIT]
    store.save_sideboard(deck_id, entries)
    return {"outcome": "applied", "added": added, "updated": updated, "count": len(entries),
            "cards": entries}


def remove(store, deck_id: str, scryfall_ids: list) -> dict:
    store.load(deck_id)
    drop = {value for value in scryfall_ids or [] if value}
    entries = [entry for entry in store.sideboard(deck_id) if entry["scryfall_id"] not in drop]
    store.save_sideboard(deck_id, entries)
    return {"outcome": "applied", "count": len(entries), "cards": entries}


def clear(store, deck_id: str) -> dict:
    store.load(deck_id)
    store.save_sideboard(deck_id, [])
    return {"outcome": "applied", "count": 0, "cards": []}


def find(store, deck_id: str, scryfall_id: str) -> dict:
    for entry in store.sideboard(deck_id):
        if entry["scryfall_id"] == scryfall_id:
            return entry
    raise StudioError("not_found", "That card is not in the sideboard any more.")
