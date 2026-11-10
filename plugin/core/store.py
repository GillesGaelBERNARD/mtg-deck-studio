"""Durable deck storage. The only module that reads or writes deck files."""
import copy
import hashlib
import json
import os
import threading
import time
import uuid
from pathlib import Path

from .errors import StudioError

from . import PLUGIN_ID as PLUGIN
_LOCK_KEY = "mtg_deck_studio_store_lock"


def _lock() -> threading.Lock:
    import sys

    lock = sys.modules.get(_LOCK_KEY)
    if lock is None:
        lock = sys.modules.setdefault(_LOCK_KEY, threading.RLock())
    return lock


def data_root() -> Path:
    from plugins.plugin_storage import plugin_data_dir

    return Path(plugin_data_dir(PLUGIN))


def _write_json(path: Path, data, *, attempts: int = 8) -> None:
    """Write through a temp file. Windows refuses to replace a file another handle holds open —
    the pane polls sideboard.json while the agent writes it — so the swap is retried briefly
    instead of surfacing a 500."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    for attempt in range(attempts):
        tmp.write_text(payload, encoding="utf-8")
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == attempts - 1:
                tmp.unlink(missing_ok=True)
                raise
            time.sleep(0.05 * (attempt + 1))


def strip_images(bundle: dict) -> dict:
    doc = copy.deepcopy(bundle)
    for card in doc.get("cards") or []:
        card["imageData"] = ""
        for face in card.get("faces") or []:
            face["imageData"] = ""
    doc["offlineImages"] = False
    return doc


PRINTED_FIELDS = ("printedName", "printedLanguage")
_name_cache: dict = {}


def read_name_index(root: Path) -> dict:
    """scryfallId -> {"printed": localised name, "lang": language}, built by scripts/build_name_index.py."""
    path = Path(root) / "name-index.json"
    try:
        mtime = path.stat().st_mtime_ns
    except OSError:
        return {}
    key = str(path)
    cached = _name_cache.get(key)
    if cached and cached[0] == mtime:
        return cached[1]
    try:
        index = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        index = {}
    _name_cache[key] = (mtime, index)
    return index


def annotate_names(doc: dict, index: dict | None = None) -> dict:
    """Give each card the name printed on its own printing, so search and chat can match it."""
    if index is None:
        index = read_name_index(data_root())
    if not index:
        return doc
    for card in doc.get("cards") or []:
        entry = index.get(card.get("scryfallId") or "")
        if not entry:
            continue
        printed = (entry.get("printed") or "").strip()
        if printed and printed != (card.get("name") or "").strip():
            card["printedName"] = printed
            if entry.get("lang"):
                card["printedLanguage"] = entry["lang"]
    return doc


def content_hash(doc: dict) -> str:
    body = {k: v for k, v in doc.items() if k not in ("savedAt", "studio")}
    if isinstance(body.get("cards"), list):
        body["cards"] = [{k: v for k, v in card.items() if k not in PRINTED_FIELDS} for card in body["cards"]]
    layout = dict(body.get("layout") or {})
    layout.pop("camera", None)
    body["layout"] = layout
    return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def validate_bundle(bundle) -> dict:
    if not isinstance(bundle, dict) or bundle.get("app") != "mtg-table-viewer" or not isinstance(bundle.get("cards"), list):
        raise StudioError("invalid", "Not an MTG viewer save (expected app 'mtg-table-viewer' with a cards list).")
    return bundle


class Store:
    def __init__(self, root: Path):
        self.root = Path(root)

    def deck_dir(self, deck_id: str) -> Path:
        if not (isinstance(deck_id, str) and deck_id.startswith("deck_") and deck_id[5:].isalnum()):
            raise StudioError("invalid", f"Malformed deck id '{deck_id}'.")
        return self.root / "decks" / deck_id

    def _index(self) -> dict:
        path = self.root / "index.json"
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        return {"decks": {}, "bindings": {}}

    def _save_index(self, index: dict) -> None:
        _write_json(self.root / "index.json", index)

    def rebuild_index(self) -> dict:
        with _lock():
            index = self._index()
            index["decks"] = {}
            for doc_path in sorted((self.root / "decks").glob("deck_*/document.json")):
                doc = json.loads(doc_path.read_text(encoding="utf-8"))
                index["decks"][doc_path.parent.name] = self._meta_of(doc)
            index["bindings"] = {s: d for s, d in index.get("bindings", {}).items() if d in index["decks"]}
            self._save_index(index)
            return index

    @staticmethod
    def _meta_of(doc: dict) -> dict:
        cards = doc.get("cards") or []
        return {
            "title": doc.get("deckTitle") or "Untitled deck",
            "rev": (doc.get("studio") or {}).get("rev", 1),
            "card_count": len(cards),
            "commanders": [c.get("name") for c in cards if c.get("isCommander")],
            "updated_at": (doc.get("studio") or {}).get("updated_at"),
        }

    def create(self, bundle: dict, title: str | None = None) -> dict:
        doc = strip_images(validate_bundle(bundle))
        if title:
            doc["deckTitle"] = title[:90]
        deck_id = f"deck_{uuid.uuid4().hex[:16]}"
        doc["studio"] = {"deck_id": deck_id, "rev": 1, "updated_at": time.time(), "hash": content_hash(doc)}
        with _lock():
            _write_json(self.deck_dir(deck_id) / "document.json", doc)
            index = self._index()
            index["decks"][deck_id] = self._meta_of(doc)
            self._save_index(index)
        return {"deck_id": deck_id, **self._meta_of(doc)}

    def load(self, deck_id: str) -> dict:
        path = self.deck_dir(deck_id) / "document.json"
        if not path.exists():
            raise StudioError("not_found", f"No stored deck '{deck_id}'.")
        return annotate_names(json.loads(path.read_text(encoding="utf-8")), read_name_index(self.root))

    def sideboard(self, deck_id: str) -> list:
        path = self.deck_dir(deck_id) / "sideboard.json"
        if not path.exists():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        cards = data.get("cards") if isinstance(data, dict) else data
        return [card for card in (cards or []) if isinstance(card, dict) and card.get("scryfall_id")]

    def save_sideboard(self, deck_id: str, cards: list) -> None:
        path = self.deck_dir(deck_id) / "sideboard.json"
        _write_json(path, {"version": 1, "updatedAt": int(time.time()), "cards": list(cards)})

    def rev(self, deck_id: str) -> int:
        return self.load(deck_id)["studio"]["rev"]

    def persist(self, deck_id: str, base_rev: int, rev: int, snapshot: dict) -> dict:
        doc = strip_images(validate_bundle(snapshot))
        digest = content_hash(doc)
        with _lock():
            current = self.load(deck_id)
            stored = current["studio"]
            if rev == stored["rev"] and digest == stored.get("hash"):
                return {"status": "noop", "rev": rev}
            if base_rev != stored["rev"] or rev <= stored["rev"]:
                return {"status": "conflict", "rev": stored["rev"]}
            doc["studio"] = {"deck_id": deck_id, "rev": rev, "updated_at": time.time(), "hash": digest}
            _write_json(self.deck_dir(deck_id) / "document.json", doc)
            index = self._index()
            index["decks"][deck_id] = self._meta_of(doc)
            self._save_index(index)
        return {"status": "saved", "rev": rev}

    def list_decks(self) -> list:
        index = self._index()
        return [{"deck_id": k, **v} for k, v in sorted(index["decks"].items(), key=lambda kv: kv[1]["title"].lower())]

    def bind(self, session_id: str, deck_id: str) -> None:
        if not session_id:
            raise StudioError("invalid", "A session id is required to bind a deck.")
        self.load(deck_id)
        with _lock():
            index = self._index()
            index["bindings"][session_id] = deck_id
            self._save_index(index)

    def close(self, session_id: str) -> None:
        """Forget the deck bound to this session: the studio shows no deck until one is opened."""
        if not session_id:
            return
        with _lock():
            index = self._index()
            index["bindings"].pop(session_id, None)
            self._save_index(index)

    def binding(self, session_id: str | None) -> str | None:
        if not session_id:
            return None
        deck_id = self._index()["bindings"].get(session_id)
        if deck_id and (self.root / "decks" / deck_id / "document.json").exists():
            return deck_id
        return None


def default() -> Store:
    return Store(data_root())
