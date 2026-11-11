"""Relays commands from tool threads to the bound Desktop window and waits for acks.

State is kept under a fixed sys.modules key so it is shared by both plugin halves and
survives development reloads of this module.
"""
import json
import sys
import threading
import time
import types
import uuid
from pathlib import Path

from . import PLUGIN_ID as PLUGIN
from . import store as store_mod

COMMAND_EVENT = "command"
ACK_TIMEOUT_S = 20.0


def _state():
    key = f"mtg_deck_studio_state:{PLUGIN}"
    state = sys.modules.get(key)
    if state is None:
        fresh = types.ModuleType(key)
        fresh.lock = threading.Lock()
        fresh.pending = {}
        fresh.contexts = {}
        fresh.windows = {}
        state = sys.modules.setdefault(key, fresh)
    return state


STATE = _state()


def _broadcast(event: str, payload: dict) -> None:
    # The event bus is Hermes's own module. Inside Hermes it is always importable; in an outside
    # harness (a test runner, a CI job) it is not, and a missing bus must not turn a tool call into
    # an exception — the pane simply never receives the event.
    try:
        from hermes_cli.plugin_events import broadcast_plugin_event
    except ModuleNotFoundError:  # pragma: no cover - only reachable outside Hermes
        return

    broadcast_plugin_event(PLUGIN, event, payload)


broadcast = _broadcast


def send(session_id: str, deck_id: str, op: str, args: dict, expected_rev=None, kind=None, timeout: float | None = None) -> dict:
    timeout = timeout or ACK_TIMEOUT_S
    command_id = uuid.uuid4().hex
    slot = {"event": threading.Event(), "ack": None, "deck_id": deck_id}
    with STATE.lock:
        STATE.pending[command_id] = slot
    envelope = {
        "command_id": command_id,
        "session_id": session_id,
        "deck_id": deck_id,
        "op": op,
        "args": args,
        "expected_rev": expected_rev,
        "meta": {"kind": kind},
        "sent_at": time.time(),
    }
    try:
        trace("send", envelope)
        broadcast(COMMAND_EVENT, envelope)
        received = slot["event"].wait(timeout)
    finally:
        with STATE.lock:
            STATE.pending.pop(command_id, None)
    if not received:
        return {
            "outcome": "pane_unavailable",
            "error": "No Desktop window acknowledged. Open the MTG Deck Studio pane beside this chat with the deck loaded.",
        }
    return slot["ack"]


def receive_ack(ack: dict, store: "store_mod.Store") -> dict:
    """Persist the acknowledged snapshot first, then release the waiting tool."""
    command_id = ack.get("command_id")
    with STATE.lock:
        slot = STATE.pending.get(command_id)
    deck_id = ack.get("deck_id") or (slot or {}).get("deck_id")
    result = {k: v for k, v in ack.items() if k not in ("snapshot", "command_id", "window_id")}
    snapshot = ack.get("snapshot")
    if snapshot is not None and deck_id:
        saved = store.persist(deck_id, ack.get("base_rev"), ack.get("rev"), snapshot)
        result["saved"] = saved["status"]
        if saved["status"] == "conflict":
            result["outcome"] = "conflict"
            result["error"] = f"Stored deck is at revision {saved['rev']}; the window must reload it."
            result["rev"] = saved["rev"]
    if slot is None:
        return {"matched": False, **result}
    slot["ack"] = result
    slot["event"].set()
    return {"matched": True, **result}


def update_context(record: dict) -> None:
    session_id = record.get("session_id")
    if not session_id:
        return
    with STATE.lock:
        STATE.contexts[session_id] = {**record, "received_at": time.time()}


def context(session_id: str | None) -> dict | None:
    if not session_id:
        return None
    with STATE.lock:
        record = STATE.contexts.get(session_id)
    return dict(record) if record else None


LIVE_WINDOW_S = 12.0


def any_live_window() -> bool:
    now = time.time()
    with STATE.lock:
        return any(now - r["received_at"] < LIVE_WINDOW_S for r in STATE.contexts.values())


CLAIM_WAIT_S = 1.2


def claim(session_id: str | None, store) -> str | None:
    """Return the chat's deck, giving an open pane a moment to bind a brand-new chat.

    The pane binds the focused chat to the deck it shows as soon as it sees the chat's
    id, which can be just after the first turn starts.
    """
    deck_id = store.binding(session_id)
    if deck_id or not session_id or not any_live_window():
        return deck_id
    deadline = time.time() + CLAIM_WAIT_S
    while time.time() < deadline:
        time.sleep(0.15)
        deck_id = store.binding(session_id)
        if deck_id:
            return deck_id
    return None


_TRACE_KEYS = ("session_id", "deck_id", "command_id", "op", "outcome", "rev", "base_rev", "window_id", "status", "saved")


def trace(kind: str, record: dict, result=None) -> None:
    """Development trace of bridge traffic, written only when the .dev-reload marker exists."""
    try:
        if not (Path(__file__).resolve().parent.parent / ".dev-reload").exists():
            return
        line = {"t": round(time.time(), 3), "kind": kind, **{k: record.get(k) for k in _TRACE_KEYS if k in record}}
        if isinstance(result, dict):
            line["result"] = {k: result.get(k) for k in _TRACE_KEYS if k in result}
        with open(store_mod.default().root / "trace.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps(line, default=str) + "\n")
    except Exception:
        pass


def notify(event: str, payload: dict) -> None:
    """Fire-and-forget update for windows (guide published, sideboard candidates)."""
    try:
        broadcast(event, payload)
    except Exception:
        pass


def is_live(session_id: str | None, deck_id: str) -> bool:
    record = context(session_id)
    return bool(record and record.get("deck_id") == deck_id and time.time() - record["received_at"] < LIVE_WINDOW_S)
