"""Backend RPC methods called by the Desktop half through POST /rpc."""
from . import bridge, store as store_mod
from .errors import StudioError


def _store():
    return store_mod.default()


def ack(params):
    return bridge.receive_ack(params, _store())


def persist(params):
    return _store().persist(params["deck_id"], params.get("base_rev"), params.get("rev"), params["snapshot"])


def context(params):
    from . import viewer

    bridge.update_context(params)
    return {"ok": True, "viewer_version": viewer.version()}


def session_deck(params):
    return {"deck_id": _store().binding(params.get("session_id"))}


def _graph(deck_id: str) -> dict:
    """The deck's link graph, with the classified sideboard candidates included so the pane
    can show what a card the player is considering would interact with."""
    from . import links as links_mod

    store = _store()
    doc = store.load(deck_id)
    virtual, extras = links_mod.with_candidates(doc, store.sideboard(deck_id))
    graph = links_mod.build(virtual)
    if extras:
        graph["meta"] = extras
    return graph


def load_deck(params):
    doc = _store().load(params["deck_id"])
    return {"deck_id": params["deck_id"], "rev": doc["studio"]["rev"], "document": doc,
            "links": _graph(params["deck_id"])}


def sideboard_classify(params):
    """The pane classified its sideboard cards; keep the facts so their links can be drawn."""
    from . import sideboard as sb

    store = _store()
    deck_id = params["deck_id"]
    updated = []
    for scryfall_id, result in (params.get("cards") or {}).items():
        if not (result or {}).get("autoBucketFacts"):
            continue
        try:
            sb.set_classification(store, deck_id, scryfall_id, {**result, "ruleVersion": (result or {}).get("ruleVersion")})
        except StudioError:
            continue
        updated.append(scryfall_id)
    return {"outcome": "applied", "updated": updated, "links": _graph(deck_id)}


def links_for(params):
    """The card-to-card graph, recomputed on demand after the pane edited the deck."""
    from . import links as links_mod

    doc = _store().load(params["deck_id"])
    if params.get("card"):
        return links_mod.for_card(doc, params["card"])
    return _graph(params["deck_id"])


def list_decks(params):
    return {"decks": _store().list_decks()}


def import_bundle(params):
    store = _store()
    record = store.create(params["bundle"], title=params.get("title"))
    if params.get("session_id"):
        store.bind(params["session_id"], record["deck_id"])
    return record


def import_file(params):
    """Read a file the player picked inside a folder they granted to the Desktop half."""
    from pathlib import Path
    import json

    path = Path(params["path"])
    if not path.is_absolute() or not path.is_file():
        raise StudioError("invalid", "Pick a file inside a granted folder.")
    suffix = path.suffix.lower()
    if suffix == ".json":
        record = import_bundle({"bundle": json.loads(path.read_text(encoding="utf-8")), "session_id": params.get("session_id")})
        return {"kind": "save", **record}
    if suffix in (".csv", ".txt"):
        return {"kind": "csv" if suffix == ".csv" else "text", "name": path.stem, "text": path.read_text(encoding="utf-8-sig")}
    raise StudioError("invalid", "Supported files: viewer save (.json), MythicTools CSV (.csv), decklist (.txt).")


def _exports_dir():
    return _store().root / "exports"


def _safe_name(value) -> str:
    import re
    from pathlib import Path

    name = re.sub(r"[^\w.\- ]+", "_", Path(str(value)).name).strip() or "deck.json"
    if not name.lower().endswith(".json"):
        name += ".mtg-deck-studio.json"
    return name


def export_file(params):
    """Write the deck JSON into the studio folder (overwriting by default), optionally copying it elsewhere."""
    import shutil
    from pathlib import Path

    name = _safe_name(params["name"])
    folder = Path(params["folder"]) if params.get("folder") else _exports_dir()
    if not folder.is_absolute():
        raise StudioError("invalid", "Export folder must be absolute.")
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / name
    if not params.get("overwrite", True):
        stem, suffix, n = target.stem, target.suffix, 1
        while target.exists():
            n += 1
            target = folder / f"{stem} ({n}){suffix}"
    target.write_text(str(params["text"]), encoding="utf-8")
    copied = None
    if params.get("copy_to"):
        dest_dir = Path(str(params["copy_to"]))
        if not dest_dir.is_absolute():
            raise StudioError("invalid", "The copy folder must be absolute.")
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / target.name
        shutil.copyfile(target, dest)
        copied = str(dest)
    return {"ok": True, "path": str(target), "copied_to": copied}


def list_exports(params):
    """The deck JSON files in the studio folder, newest first."""
    folder = _exports_dir()
    if not folder.exists():
        return {"folder": str(folder), "files": []}
    files = [
        {"name": p.name, "path": str(p), "size": p.stat().st_size, "modified": p.stat().st_mtime}
        for p in folder.glob("*.json") if p.is_file()
    ]
    files.sort(key=lambda item: item["modified"], reverse=True)
    return {"folder": str(folder), "files": files}


def guide(params):
    from . import strategy

    return strategy.guide_state(_store(), params["deck_id"])


def sideboard_list(params):
    from . import sideboard

    return sideboard.list_entries(_store(), params["deck_id"])


def sideboard_remove(params):
    from . import sideboard

    return sideboard.remove(_store(), params["deck_id"], params.get("scryfall_ids") or [])


def sideboard_promote(params):
    """Add a sideboard card to the deck, or swap a deck card for it."""
    from . import bridge, sideboard

    store = _store()
    deck_id = params["deck_id"]
    entry = sideboard.find(store, deck_id, params.get("scryfall_id") or "")
    if params.get("replace_id"):
        ack = bridge.send(params.get("session_id"), deck_id, "replace_card",
                          {"id": params["replace_id"], "scryfall_id": entry["scryfall_id"]}, kind="document")
    else:
        ack = bridge.send(params.get("session_id"), deck_id, "add_cards",
                          {"name": entry["name"], "quantity": 1}, kind="document")
    if ack.get("outcome") == "applied":
        sideboard.remove(store, deck_id, [entry["scryfall_id"]])
    return {"outcome": ack.get("outcome"), "error": ack.get("error"), "rev": ack.get("rev"),
            "entry": entry, "count": len(store.sideboard(deck_id))}


def save_scan(params):
    """Store a camera capture beside the decks and return the file the chat reads.

    The pane cannot hand an image to the chat itself, so the photo becomes a file
    and the chat message carries its path: the agent opens it with its own eyes.
    """
    import base64
    import re
    import time

    raw = str(params.get("data_url") or "")
    match = re.match(r"^data:image/(?:jpeg|png);base64,(?P<data>[A-Za-z0-9+/=\s]+)$", raw, re.I)
    if not match:
        raise StudioError("invalid", "Expected a JPEG or PNG data URL.")
    try:
        blob = base64.b64decode(re.sub(r"\s+", "", match.group("data")), validate=True)
    except Exception as exc:  # noqa: BLE001 - any decode failure is the same answer to the pane
        raise StudioError("invalid", f"The photo could not be decoded ({exc.__class__.__name__}).") from exc
    if len(blob) < 512:
        raise StudioError("invalid", "That photo is empty.")
    if len(blob) > 12 * 1024 * 1024:
        raise StudioError("invalid", "That photo is larger than 12 MB.")
    folder = _store().root / "scans"
    folder.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    path = folder / f"scan-{stamp}.jpg"
    suffix = 1
    while path.exists():
        suffix += 1
        path = folder / f"scan-{stamp}-{suffix}.jpg"
    path.write_bytes(blob)
    return {"path": str(path), "bytes": len(blob)}


def sideboard_add(params):
    """Add exact printings ("Name (SET) 123", a Scryfall id, or a bare name) to the sideboard."""
    from . import scryfall, sideboard

    refs = [str(ref).strip() for ref in (params.get("refs") or []) if str(ref).strip()]
    if not refs:
        raise StudioError("invalid", "Give at least one card to add.")
    deck_id = params["deck_id"]
    cards = [scryfall.printing(scryfall.resolve_ref(ref)) for ref in refs]
    result = sideboard.add(_store(), deck_id, cards, reason=params.get("reason") or "Added by hand")
    bridge.notify("sideboard", {"deck_id": deck_id})
    return result


def bind(params):
    _store().bind(params["session_id"], params["deck_id"])
    return {"ok": True}


def close_deck(params):
    """Close the deck shown in the studio: nothing is loaded until another one is bound."""
    _store().close(params.get("session_id") or "")
    return {"ok": True, "deck_id": None}


METHODS = {f.__name__: f for f in (ack, persist, context, session_deck, load_deck, list_decks, import_bundle,
                                  import_file, export_file, guide, sideboard_list, sideboard_remove,
                                  sideboard_promote, sideboard_classify, list_exports, bind, links_for,
                                  save_scan, sideboard_add, close_deck)}


def call(method, params):
    result = _call(method, params)
    if method != "context":
        bridge.trace(method, params, result)
    return result


def _call(method, params):
    fn = METHODS.get(method)
    if fn is None:
        return {"outcome": "invalid", "error": f"Unknown method '{method}'."}
    try:
        return fn(params)
    except StudioError as exc:
        return exc.to_dict()
    except KeyError as exc:
        return {"outcome": "invalid", "error": f"Missing parameter {exc}."}
