"""Per-turn chat context: which deck this chat is bound to and what is selected."""
from . import PLUGIN_ID, bridge, store as store_mod

MAX_NAMES = 40


def block(session_id: str | None, store=None) -> str | None:
    store = store or store_mod.default()
    deck_id = bridge.claim(session_id, store)
    if not deck_id:
        return None
    doc = store.load(deck_id)
    meta = doc["studio"]
    live = bridge.context(session_id)
    lines = [
        f"[MTG Deck Studio] This chat is bound to deck \"{doc.get('deckTitle') or 'Untitled deck'}\" "
        f"({deck_id}, revision {meta['rev']}, {len(doc.get('cards') or [])} cards).",
    ]
    commanders = [c.get("name") for c in doc.get("cards") or [] if c.get("isCommander")]
    if commanders:
        lines.append("Commanders: " + ", ".join(commanders) + ".")
    if live and live.get("deck_id") == deck_id:
        names = [s.get("name") for s in live.get("selected") or []]
        shown = names[:MAX_NAMES]
        extra = f" (+{len(names) - len(shown)} more)" if len(names) > len(shown) else ""
        lines.append(f"Selected on the table ({len(names)}): " + (", ".join(shown) + extra if names else "none") + ".")
        lines.append(f"Active view: {live.get('view')}.")
        if (live.get("rev") or 0) < meta["rev"]:
            lines.append("The selection report predates the stored revision and may be outdated.")
    else:
        lines.append("The MTG Deck Studio pane is not showing this deck right now; edits will fail until it is open.")
    lines.append(f"Use the mtg_deck_studio_* tools to read or change it (skill {PLUGIN_ID}:mtg-deck-studio).")
    return "\n".join(lines)


def pre_llm_call(session_id=None):
    try:
        text = block(session_id)
    except Exception:
        return None
    return {"context": text} if text else None
