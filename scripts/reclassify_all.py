"""Reclassify every deck with the viewer's current rules, through the viewer itself.

The pane is the only writer of a deck document, so this script plays the pane's role
headlessly: it serves the same build, runs the same `reclassify` operation, and persists the
snapshot the viewer returns. Nothing here re-implements classification.

    python scripts/reclassify_all.py --check      # report drift, write nothing
    python scripts/reclassify_all.py              # reclassify and persist
    python scripts/reclassify_all.py --deck deck_x

Requires playwright (uv run --no-project --with playwright python scripts/reclassify_all.py).
"""
from __future__ import annotations

import argparse
import base64
import json
import sys
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "plugin"))
sys.path.insert(0, str(PROJECT / "plugin" / "tests"))

from core import store as store_mod  # noqa: E402

from _hermes_paths import plugin_data  # noqa: E402
DATA = plugin_data()

LOAD = """d => window.send({type: 'wb.load', deck_id: d.deck_id, rev: d.rev, document: d.document})"""
COUNT = """() => ({
  facts: window.mtgViewer.state.cards.reduce((n, c) => n + (c.autoBucketFacts || []).length, 0),
  gaps: window.mtgViewer.state.cards.reduce((n, c) => n + (c.autoBucketGaps || []).length, 0),
  buckets: new Set(window.mtgViewer.state.cards.flatMap(c => c.autoBuckets || [])).size,
  rule: window.mtgViewer.state.cards[0]?.autoBucketRuleVersion || ''
})"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="report only, persist nothing")
    parser.add_argument("--deck", action="append", default=[], help="limit to deck ids")
    args = parser.parse_args()

    from playwright.sync_api import sync_playwright

    sys.path.insert(0, str(PROJECT / "plugin" / "tests"))
    from test_core import core
    from test_viewer_browser import HOST

    store = store_mod.Store(DATA)
    decks = [d for d in store.list_decks() if not args.deck or d["deck_id"] in args.deck]
    html = core.viewer.build()
    payload = "data:text/html;base64," + base64.b64encode(html.encode()).decode()

    rows, failures = [], []
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        for index, deck in enumerate(decks, 1):
            deck_id = deck["deck_id"]
            document = store.load(deck_id)
            page = browser.new_page(viewport={"width": 900, "height": 700})
            page.set_content(HOST)
            page.evaluate("u => { document.getElementById('f').src = u }", payload)
            frame = page.frames[1]
            frame.wait_for_function("() => !!window.mtgViewer", timeout=30000)
            page.evaluate(LOAD, {"deck_id": deck_id, "rev": document["studio"]["rev"], "document": core.store.strip_images(document)})
            frame.wait_for_function("() => window.mtgViewer.state.cards.length > 0 && !window.mtgViewer.state.loading", timeout=60000)
            before = frame.evaluate(COUNT)

            def send(op: str) -> dict:
                command_id = f"{op}-{deck_id}-{time.time_ns()}"
                page.evaluate(
                    "m => window.send(m)",
                    {"type": "wb.cmd", "command_id": command_id, "deck_id": deck_id,
                     "op": op, "args": {"reset": False}, "meta": {"kind": "document"}},
                )
                deadline = time.time() + 120
                while time.time() < deadline:
                    for message in page.evaluate("() => window.msgs || []"):
                        if message.get("type") == "wb.ack" and message.get("command_id") == command_id:
                            return message
                    time.sleep(0.2)
                return {}

            # Loading already reclassifies cards whose stamped rule version is stale, so the
            # reclassify op can be a no-op; flush then persists whatever the load produced.
            ack = send("reclassify")
            if not ack.get("snapshot"):
                flushed = send("flush")
                if flushed.get("snapshot"):
                    ack = {**ack, "snapshot": flushed["snapshot"], "base_rev": flushed.get("base_rev", ack.get("base_rev")),
                           "rev": flushed.get("rev", ack.get("rev")), "outcome": ack.get("outcome")}
            after = frame.evaluate(COUNT) if ack else before
            row = {"deck": deck["title"], "deck_id": deck_id, "rev": document["studio"]["rev"],
                   "facts_before": before["facts"], "facts_after": after["facts"],
                   "gaps_before": before["gaps"], "gaps_after": after["gaps"],
                   "rule": after["rule"] or before["rule"], "outcome": (ack or {}).get("outcome", "no-ack")}

            row["changed"] = bool(ack and ack.get("snapshot"))
            if ack and ack.get("snapshot") and not args.check:
                base = document["studio"]["rev"]
                saved = store.persist(deck_id, base, base + 1, ack["snapshot"])
                row["persist"] = saved.get("status")
                row["rev"] = saved.get("rev", ack["rev"])
            elif ack and ack.get("snapshot") and args.check:
                same = store_mod.content_hash(core.store.strip_images(ack["snapshot"])) == document["studio"].get("hash")
                row["persist"] = "unchanged" if same else "would-change"
                row["changed"] = not same
            else:
                row["persist"] = "unchanged"
            if row["outcome"] not in ("applied", "noop"):
                failures.append(row)
            rows.append(row)
            print(f"[{index}/{len(decks)}] {row['deck'][:28]:28} rev {row['rev']:>3} facts {row['facts_before']:>4} -> {row['facts_after']:>4} "
                  f"gaps {row['gaps_before']:>3} -> {row['gaps_after']:>3} {row['outcome']}/{row['persist']} {row['rule']}")
            page.close()
        browser.close()

    changed = [r for r in rows if r.get("changed")]
    print(f"\ndecks: {len(rows)} | changed: {len(changed)} | failures: {len(failures)}")
    print(f"facts: {sum(r['facts_before'] for r in rows)} -> {sum(r['facts_after'] for r in rows)}")
    print(f"gaps:  {sum(r['gaps_before'] for r in rows)} -> {sum(r['gaps_after'] for r in rows)}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
