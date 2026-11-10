"""Export every card-to-card link the engine claims, with both cards' text, for an audit.

One record per directed pair: partner -> listener, where the partner is claimed to cause an event
the listener reacts to. "Generic" pairs (a card merely existing/entering) are written to a separate
file: they are true at the table but they are not synergies.

    python scripts/audit_links.py            # writes work/link_audit/*.jsonl
    python scripts/audit_links.py --summary  # counts only
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT / "plugin"))

from core import links, store as store_mod  # noqa: E402

from _hermes_paths import plugin_data  # noqa: E402
DATA = plugin_data()
OUT = PROJECT / "work" / "link_audit"


def slug(text: str) -> str:
    keep = [c.lower() if c.isalnum() else "-" for c in text]
    out = "".join(keep)
    while "--" in out:
        out = out.replace("--", "-")
    return out.strip("-")[:60] or "deck"


def pairs_for(document: dict) -> tuple[list[dict], list[dict]]:
    """(real pairs, any-card pairs) for one deck, one record per directed pair."""
    graph = links.build(document, max_groups=999, max_ids=999)
    cards = {c["id"]: c for c in document.get("cards") or [] if isinstance(c, dict)}
    real, generic = [], []
    for listener_id, entry in graph["cards"].items():
        listener = cards.get(listener_id) or {}
        for group in entry["fed_by"]:
            for index, partner_id in enumerate(group["cards"]):
                partner = cards.get(partner_id) or {}
                clause = None
                for row in group["clauses"]:
                    if row.get("card") == partner.get("name"):
                        clause = row
                        break
                record = {
                    "deck": document.get("deckTitle") or document.get("studio", {}).get("title") or "",
                    "listener": listener.get("name") or entry["name"],
                    "listener_type": listener.get("typeLine") or "",
                    "listener_text": " ".join((listener.get("oracleText") or "").split()),
                    "partner": partner.get("name") or "",
                    "partner_type": partner.get("typeLine") or "",
                    "partner_text": " ".join((partner.get("oracleText") or "").split()),
                    "direction": "partner_causes__listener_reacts",
                    "event": group["event"],
                    "group": group["label"],
                    "cause_clause": (clause or {}).get("cause", ""),
                    "reaction_clause": (clause or {}).get("reaction", ""),
                }
                (generic if group["generic"] else real).append(record)
    return real, generic


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--summary", action="store_true", help="counts only, write nothing")
    args = parser.parse_args()

    store = store_mod.Store(DATA)
    OUT.mkdir(parents=True, exist_ok=True)
    total_real = total_generic = 0
    index = []
    for meta in store.list_decks():
        document = store.load(meta["deck_id"])
        real, generic = pairs_for(document)
        total_real += len(real)
        total_generic += len(generic)
        name = slug(meta.get("title") or meta["deck_id"])
        if not args.summary:
            (OUT / f"{name}.real.jsonl").write_text(
                "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in real), encoding="utf-8")
            (OUT / f"{name}.anycard.jsonl").write_text(
                "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in generic), encoding="utf-8")
        index.append({"deck_id": meta["deck_id"], "title": meta.get("title"), "file": name,
                      "real": len(real), "any_card": len(generic)})
        print(f"{meta.get('title') or meta['deck_id']:28} real {len(real):4} | any-card {len(generic):4}")
    if not args.summary:
        (OUT / "index.json").write_text(json.dumps({"decks": index, "real": total_real,
                                                    "any_card": total_generic}, indent=2), encoding="utf-8")
    print(f"\ntotal real {total_real} | any-card {total_generic}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
