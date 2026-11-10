"""Deck exports written to the plugin's exports folder: viewer JSON and Moxfield text."""
import json
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from .errors import StudioError

MOXFIELD = Path(__file__).resolve().parent.parent / "vendor" / "mtg_viewer_to_moxfield.py"


def _bundle(document: dict) -> dict:
    return {k: v for k, v in document.items() if k != "studio"}


def moxfield_text(document: dict) -> str:
    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp) / "deck.json"
        target = Path(tmp) / "deck.txt"
        source.write_text(json.dumps(_bundle(document), ensure_ascii=False), encoding="utf-8")
        proc = subprocess.run([sys.executable, str(MOXFIELD), str(source), "--output", str(target), "--offline"],
                              capture_output=True, text=True, encoding="utf-8", timeout=60)
        if proc.returncode != 0:
            raise StudioError("failed", (proc.stderr or proc.stdout).strip()[-400:])
        return target.read_text(encoding="utf-8")


def export(store, deck_id: str, fmt: str) -> dict:
    document = store.load(deck_id)
    stem = re.sub(r"[^\w\- ]+", "_", document.get("deckTitle") or deck_id).strip() or deck_id
    folder = store.root / "exports"
    folder.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    if fmt == "moxfield":
        text = moxfield_text(document)
        path = folder / f"{stem}_moxfield_{stamp}.txt"
        path.write_text(text, encoding="utf-8")
        return {"outcome": "exported", "format": fmt, "path": str(path), "rev": document["studio"]["rev"], "text": text}
    if fmt == "json":
        path = folder / f"{stem}_{stamp}.mtg-viewer.json"
        path.write_text(json.dumps(_bundle(document), ensure_ascii=False, indent=2), encoding="utf-8")
        return {"outcome": "exported", "format": fmt, "path": str(path), "rev": document["studio"]["rev"],
                "note": "Card images are linked, not embedded; use the viewer's Export button to embed them."}
    raise StudioError("invalid", "format must be moxfield or json.")
