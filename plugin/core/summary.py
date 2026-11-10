"""Read models over a stored deck document. Pure functions; no I/O."""
from collections import Counter

CARD_FIELDS = ("id", "name", "manaCost", "typeLine", "category", "tableCategory")


def effective_mana_value(card: dict):
    manual = card.get("manualManaValue")
    return manual if isinstance(manual, (int, float)) else card.get("manaValue")


def section(card: dict) -> str:
    if card.get("isCommander"):
        return "commanders"
    if card.get("isAlternateCommander"):
        return "alternate-commanders"
    return card.get("tableCategory") or card.get("category") or "other"


def card_row(card: dict, text_limit: int = 500) -> dict:
    row = {
        "id": card.get("id"),
        "name": card.get("name"),
        "mana_cost": card.get("manaCost") or "",
        "mana_value": effective_mana_value(card),
        "type_line": card.get("typeLine") or "",
        "section": section(card),
        "roles": card.get("utilityBuckets") or [],
        "printing": {
            k: card.get(k)
            for k in ("setCode", "collectorNumber", "language", "finish")
            if card.get(k)
        },
    }
    text = card.get("oracleText") or ""
    if text_limit:
        row["oracle_text"] = text if len(text) <= text_limit else text[: text_limit - 1] + "…"
    if card.get("error"):
        row["unresolved"] = card.get("error")
    return row


def deck_summary(doc: dict) -> dict:
    cards = doc.get("cards") or []
    curve = Counter()
    for c in cards:
        if "Land" in (c.get("typeLine") or ""):
            continue
        mv = effective_mana_value(c)
        curve[str(int(mv)) if isinstance(mv, (int, float)) and mv < 7 else "7+"] += 1
    identity = sorted({ci for c in cards if c.get("isCommander") for ci in (c.get("colorIdentity") or [])})
    meta = doc.get("studio") or {}
    return {
        "deck_id": meta.get("deck_id"),
        "rev": meta.get("rev"),
        "title": doc.get("deckTitle") or "Untitled deck",
        "card_count": len(cards),
        "commanders": [c.get("name") for c in cards if c.get("isCommander")],
        "alternate_commanders": [c.get("name") for c in cards if c.get("isAlternateCommander")],
        "color_identity": identity,
        "sections": dict(Counter(section(c) for c in cards)),
        "mana_curve": dict(sorted(curve.items())),
        "unresolved": [c.get("requestedName") or c.get("name") for c in cards if c.get("error")],
        "has_notes": bool((doc.get("strategyNotes") or "").strip()),
    }


def find_cards(doc: dict, names=None, ids=None, section_name=None, role=None, text=None, limit=60) -> list:
    wanted = {n.casefold().strip() for n in names or []}
    id_set = set(ids or [])
    out = []
    for c in doc.get("cards") or []:
        name = (c.get("name") or "").casefold()
        faces = {f.strip() for f in name.split("//")}
        if wanted and not (name in wanted or faces & wanted or (c.get("requestedName") or "").casefold() in wanted):
            continue
        if id_set and c.get("id") not in id_set:
            continue
        if section_name and section(c) != section_name:
            continue
        if role and role not in (c.get("utilityBuckets") or []):
            continue
        if text and text.casefold() not in (c.get("oracleText") or "").casefold():
            continue
        out.append(card_row(c))
        if len(out) >= limit:
            break
    return out


SUPERTYPES = {"legendary", "basic", "snow", "world", "ongoing", "host", "elite"}


def _subtypes(type_line: str) -> list:
    if "—" not in type_line:
        return []
    tail = type_line.split("—", 1)[1]
    return [part.strip() for part in tail.split() if part.strip()]


def _card_types(type_line: str) -> list:
    head = type_line.split("—", 1)[0]
    return [w.strip() for w in head.split() if w.strip() and w.strip().lower() not in SUPERTYPES]


def group_cards(doc: dict, by: str = "subtype", limit: int = 14, names_per_group: int = 8) -> dict:
    """Count the deck along one axis: creature subtypes, card types, colours, mana values,
    sections or effects. Answers questions like "what are the main creature types?"."""
    axis = (by or "subtype").strip().lower()
    counter, samples = Counter(), {}
    for card in doc.get("cards") or []:
        type_line = card.get("typeLine") or ""
        if axis in ("subtype", "subtypes", "creature-type", "creature-types"):
            keys = _subtypes(type_line) if "Creature" in type_line else []
        elif axis in ("type", "types", "card-type", "card-types"):
            keys = _card_types(type_line)
        elif axis in ("color", "colors", "colour", "colours"):
            keys = [c for c in (card.get("colorIdentity") or [])] or ["colourless"]
        elif axis in ("mv", "mana-value", "curve"):
            mv = effective_mana_value(card)
            keys = [f"MV {mv}" if mv is not None else "MV ?"]
        elif axis in ("section", "sections", "board"):
            keys = [section(card)]
        elif axis in ("effect", "effects", "role", "roles"):
            keys = [eff for eff in (card.get("utilityBuckets") or [])] or ["unclassified"]
        else:
            raise ValueError(f"Unknown grouping '{by}'.")
        for key in keys:
            counter[key] += 1
            samples.setdefault(key, [])
            if len(samples[key]) < names_per_group:
                samples[key].append(card.get("name"))
    rows = [{"label": label, "count": count, "cards": samples[label]} for label, count in counter.most_common(limit)]
    return {"group_by": axis, "total_groups": len(counter), "groups": rows}
