"""Strategy evidence and guide publication around the vendored deck_evidence.py.

Each prepare freezes a copy of the stored document, so layout edits during the
analysis do not invalidate the session. Guide freshness compares the deck's card
list, not file bytes: moving a card does not make a guide outdated, changing the
list does.
"""
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

from .errors import StudioError

SCRIPT = Path(__file__).resolve().parent.parent / "vendor" / "deck_evidence.py"
QUERY_ROUNDS = 6
MAX_SESSIONS = 5


def card_signature(document: dict) -> str:
    rows = sorted(
        f"{card.get('name')}|{bool(card.get('isCommander'))}|{bool(card.get('isAlternateCommander'))}"
        for card in document.get("cards") or []
    )
    return hashlib.sha256("\n".join(rows).encode("utf-8")).hexdigest()


def _run(*args: str) -> dict:
    proc = subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True, encoding="utf-8", timeout=120)
    if proc.returncode != 0:
        try:
            message = json.loads(proc.stderr.strip().splitlines()[-1])["error"]
        except (ValueError, IndexError, KeyError):
            message = (proc.stderr or proc.stdout).strip()[-500:]
        raise StudioError("evidence_error", message)
    return json.loads(proc.stdout)


def _evidence_root(store, deck_id: str) -> Path:
    return store.deck_dir(deck_id) / "evidence"


def _session_dir(store, deck_id: str, job_id: str) -> Path:
    if not job_id or not all(ch.isalnum() or ch == "-" for ch in job_id):
        raise StudioError("invalid", "Unknown evidence job.")
    root = _evidence_root(store, deck_id)
    for session in root.glob(f"*/sessions/{job_id}"):
        return session
    raise StudioError("invalid", "Unknown evidence job for this deck. Run prepare first.")


def prepare(store, deck_id: str) -> dict:
    document = store.load(deck_id)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    work = _evidence_root(store, deck_id) / f"{stamp}-rev{document['studio']['rev']}"
    work.mkdir(parents=True, exist_ok=True)
    source = work / "source.json"
    source.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    result = _run("prepare", str(source), "--session-root", str(work / "sessions"))
    session_file = Path(result["aggregate"]).parent / ".session.json"
    session = json.loads(session_file.read_text(encoding="utf-8"))
    session["maxQueryRounds"] = QUERY_ROUNDS
    meta = {"deck_id": deck_id, "rev": document["studio"]["rev"], "signature": card_signature(document)}
    session_file.write_text(json.dumps(session, ensure_ascii=False, indent=2), encoding="utf-8")
    (work / "studio.json").write_text(json.dumps(meta), encoding="utf-8")
    _prune(store, deck_id)
    aggregate = json.loads(Path(result["aggregate"]).read_text(encoding="utf-8"))
    return {"job_id": result["jobId"], "rev": meta["rev"], "query_rounds": QUERY_ROUNDS, "evidence": aggregate}


def query(store, deck_id: str, job_id: str, request: dict) -> dict:
    session = _session_dir(store, deck_id, job_id)
    request_path = session.parent.parent / f"request-{time.time_ns()}.json"
    response_path = request_path.with_name(request_path.name.replace("request-", "response-"))
    request_path.write_text(json.dumps(request, ensure_ascii=False), encoding="utf-8")
    result = _run("query", str(session), "--request", str(request_path), "--response", str(response_path))
    return {"job_id": job_id, "round": result["round"], "rounds_left": QUERY_ROUNDS - result["round"],
            "evidence": json.loads(response_path.read_text(encoding="utf-8"))}


def publish(store, deck_id: str, job_id: str, markdown: str) -> dict:
    session = _session_dir(store, deck_id, job_id)
    work = session.parent.parent
    draft = work / "draft.md"
    draft.write_text(markdown, encoding="utf-8")
    result = _run("write-note", str(session), "--draft", str(draft))
    meta = json.loads((work / "studio.json").read_text(encoding="utf-8"))
    guide = Path(result["output"]).read_text(encoding="utf-8")
    deck_dir = store.deck_dir(deck_id)
    (deck_dir / "guide.md").write_text(guide, encoding="utf-8")
    record = {"rev": meta["rev"], "signature": meta["signature"], "job_id": job_id, "published_at": time.time(), "words": result["wordCount"]}
    (deck_dir / "guide.json").write_text(json.dumps(record), encoding="utf-8")
    return {"outcome": "published", **record, **guide_state(store, deck_id)}


def guide_state(store, deck_id: str) -> dict:
    deck_dir = store.deck_dir(deck_id)
    if not (deck_dir / "guide.md").is_file():
        return {"guide": None}
    record = json.loads((deck_dir / "guide.json").read_text(encoding="utf-8"))
    text = (deck_dir / "guide.md").read_text(encoding="utf-8")
    if text.startswith("<!--"):
        text = text.split("-->", 1)[1].lstrip()
    current = card_signature(store.load(deck_id))
    return {"guide": {"markdown": text, "rev": record["rev"], "stale": current != record["signature"], "published_at": record["published_at"]}}


def _prune(store, deck_id: str) -> None:
    import shutil

    runs = sorted(p for p in _evidence_root(store, deck_id).iterdir() if p.is_dir())
    for old in runs[:-MAX_SESSIONS]:
        shutil.rmtree(old, ignore_errors=True)
