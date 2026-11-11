"""Agent tool handlers. Arguments are parsed here; outcomes are always JSON."""
import json

from . import bridge, exports, links, operations, scryfall, sideboard, store as store_mod, strategy, summary
from .errors import StudioError

EMPTY_BUNDLE = {"app": "mtg-table-viewer", "version": 4, "deckTitle": "", "strategyNotes": "", "decklist": "",
                "customBuckets": [], "customTableSections": [], "customStatsCategories": [], "layout": {}, "cards": []}


def _bound(store, session_id):
    deck_id = bridge.claim(session_id, store)
    if not deck_id:
        raise StudioError(
            "not_bound",
            "This chat is not bound to a deck. Use mtg_deck_studio_decks to list decks and open one, "
            "or ask the player to open a deck in the MTG Deck Studio pane.",
        )
    return deck_id


def _flush(session_id, deck_id):
    if not bridge.is_live(session_id, deck_id):
        return False
    ack = bridge.send(session_id, deck_id, "flush", {}, kind="query", timeout=4.0)
    return ack.get("outcome") == "applied"


def state(args, session_id, store):
    deck_id = _bound(store, session_id)
    live = _flush(session_id, deck_id)
    doc = store.load(deck_id)
    result = {"source": "live" if live else "stored", **summary.deck_summary(doc)}
    ctx = bridge.context(session_id)
    if ctx and ctx.get("deck_id") == deck_id:
        cards_by_id = {c.get("id"): c for c in doc.get("cards") or []}
        result["selected"] = [
            {**entry, "printed_name": (cards_by_id.get(entry.get("id")) or {}).get("printedName")}
            for entry in (ctx.get("selected") or [])
        ]
        result["view"] = ctx.get("view")
    if args.get("group_by"):
        try:
            result["grouping"] = summary.group_cards(doc, args["group_by"], limit=int(args.get("group_limit") or 14))
        except ValueError as exc:
            raise StudioError("invalid", str(exc))
    query = args.get("cards")
    if query is not None:
        result["cards"] = summary.find_cards(
            doc,
            names=query.get("names"),
            ids=query.get("ids") or ([s["id"] for s in result.get("selected", [])] if query.get("selected") else None),
            section_name=query.get("section"),
            role=query.get("role"),
            text=query.get("text"),
            limit=min(int(query.get("limit") or 60), 120),
        )
    return result


def apply(args, session_id, store):
    name = args.get("op")
    op_args = args.get("args") or {}
    op = operations.validate(name, op_args)
    deck_id = _bound(store, session_id)
    ack = bridge.send(session_id, deck_id, name, op_args, expected_rev=args.get("expected_rev"), kind=op["kind"])
    ack.setdefault("op", name)
    return ack


def decks(args, session_id, store):
    action = args.get("action") or "list"
    if action == "list":
        # Deck records plus the JSON files in the studio folder, so the agent never has to
        # open the Decks menu to see what a save produced.
        from . import rpc as rpc_mod

        files = rpc_mod.list_exports({})["files"]
        return {"decks": store.list_decks(), "files": files, "bound": store.binding(session_id)}
    if action == "close":
        # Leave the studio empty: no deck is shown until the player or the agent opens one.
        from . import rpc as rpc_mod

        store.close(session_id)
        ack = bridge.send(session_id, "", "close_deck", {}, kind="session", timeout=6.0)
        return {"outcome": "closed", "deck_id": None,
                "shown_in_studio": ack.get("outcome") in ("applied", "closed"),
                "note": None if ack.get("outcome") in ("applied", "closed")
                        else "Closed. The pane shows the empty state when this chat is focused with the studio open."}
    if action == "open":
        deck_id = args.get("deck_id")
        store.bind(session_id, deck_id)
        ack = bridge.send(session_id, deck_id, "open_deck", {}, kind="session", timeout=6.0)
        shown = ack.get("outcome") == "applied"
        return {"outcome": "bound", "deck_id": deck_id, "shown_in_studio": shown,
                "note": None if shown else "Bound. The pane shows it when this chat is focused with the studio open."}
    if action == "import":
        # Import from a path the player gives us: deck bundles (one file, or every bundle in a
        # folder), or a scanner export — a MythicTools CSV or a plain decklist — which the pane
        # resolves card by card the way its own file picker does.
        import json as json_mod
        from pathlib import Path as PathMod

        from . import rpc as rpc_mod

        raw = str(args.get("path") or "").strip()
        if not raw:
            raise StudioError("invalid", "Give a path to a deck JSON, a folder of them, or a MythicTools CSV.")
        target = PathMod(raw)
        if not target.is_absolute():
            raise StudioError("invalid", "The path must be absolute.")
        if target.is_file() and target.suffix.lower() in (".csv", ".txt"):
            return _import_list_file(args, session_id, store, target, rpc_mod)
        if target.is_dir():
            sources = [p for p in sorted(target.rglob("*.json")) if p.is_file()]
        elif target.is_file():
            sources = [target]
        else:
            raise StudioError("invalid", f"Nothing at {raw}.")
        known = {str((deck.get("title") or "")).strip().casefold() for deck in store.list_decks()}
        imported, skipped, failed = [], [], []
        for source in sources:
            try:
                bundle = json_mod.loads(source.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                failed.append({"path": str(source.name), "error": f"not a deck JSON ({exc.__class__.__name__})"})
                continue
            if not isinstance(bundle, dict) or not isinstance(bundle.get("cards"), list):
                failed.append({"path": str(source.name), "error": "no cards array"})
                continue
            title = str(bundle.get("deckTitle") or source.name).strip()
            if title.casefold() in known:
                skipped.append({"title": title, "path": str(source.name), "reason": "already in the library"})
                continue
            record = rpc_mod.import_file({"path": str(source), "session_id": session_id})
            known.add(title.casefold())
            imported.append({"deck_id": record.get("deck_id"), "title": record.get("title") or title,
                             "card_count": record.get("card_count"), "from": str(source.name)})
        return {"imported": imported, "skipped": skipped, "failed": failed, "bound": store.binding(session_id)}
    if action == "new":
        record = store.create(dict(EMPTY_BUNDLE), title=(args.get("title") or "New deck"))
        store.bind(session_id, record["deck_id"])
        ack = bridge.send(session_id, record["deck_id"], "open_deck", {}, kind="session", timeout=6.0)
        return {"outcome": "created", **record, "shown_in_studio": ack.get("outcome") == "applied",
                "next": "Fill it with mtg_deck_studio_apply op=load_decklist or op=add_cards."}
    if action == "export":
        deck_id = _bound(store, session_id)
        _flush(session_id, deck_id)
        return exports.export(store, deck_id, args.get("format") or "moxfield")
    raise StudioError("invalid", f"Unknown action '{action}'. Use list, open, new or export.")


def _csv_card_total(text: str):
    """The CSV's own card total, so the answer can say how many cards were handed to the pane."""
    import csv
    import io

    try:
        rows = list(csv.DictReader(io.StringIO(text.lstrip("\ufeff"))))
        return sum(int(float(row.get("Quantity") or 0)) for row in rows if str(row.get("Card Name") or "").strip())
    except (TypeError, ValueError, csv.Error):
        return None


def _import_list_file(args, session_id, store, path, rpc_mod):
    """Load a MythicTools CSV or a decklist file into a deck, through the pane's own importer.

    The pane resolves the printing of every card from Scryfall, so this starts a load and
    returns; the card count then shows up in mtg_deck_studio_state.
    """
    # A window that answers anything — even "different deck" — is a pane to load in. Probing
    # with the deck instead of the context table survives a reload, which drops the contexts.
    probe = bridge.send(session_id, str(args.get("deck_id") or ""), "flush", {}, kind="query", timeout=4.0)
    if probe.get("outcome") == "pane_unavailable":
        raise StudioError(
            "pane_unavailable",
            "Open the MTG Deck Studio pane beside this chat first: a CSV load runs inside the pane.",
        )
    read = rpc_mod.import_file({"path": str(path)})
    text, kind = read["text"], read["kind"]
    titled = {deck["deck_id"]: deck.get("title") or "" for deck in store.list_decks()}
    deck_id = args.get("deck_id")
    if deck_id:
        if deck_id not in titled:
            raise StudioError("invalid", f"No deck {deck_id} in the library.")
        title = titled[deck_id]
    else:
        title = str(args.get("title") or read.get("name") or path.stem).strip() or path.stem
        deck_id = store.create(dict(EMPTY_BUNDLE), title=title)["deck_id"]
    store.bind(session_id, deck_id)
    opened = bridge.send(session_id, deck_id, "open_deck", {}, kind="session", timeout=6.0)
    op = "import_mythictools_csv" if kind == "csv" else "load_decklist"
    op_args = {"text": text, "name": read.get("name")} if kind == "csv" else {"text": text}
    operations.validate(op, op_args)  # size limits come back as `invalid`, not from the pane
    ack = bridge.send(session_id, deck_id, op, op_args, kind="document", timeout=15.0)
    return {
        "outcome": ack.get("outcome"),
        "deck_id": deck_id,
        "title": title,
        "format": "mythictools-csv" if kind == "csv" else "decklist",
        "source": str(path),
        "shown_in_studio": opened.get("outcome") == "applied",
        "csv_cards": _csv_card_total(text) if kind == "csv" else None,
        "note": ("The pane fetches the cards from Scryfall about one per second and keeps each "
                 "card's set, collector number, language, finish and condition; read "
                 "mtg_deck_studio_state to follow the count."
                 if kind == "csv" else "The pane is loading the decklist; read mtg_deck_studio_state to follow it."),
    }


def strategy_tool(args, session_id, store):
    deck_id = _bound(store, session_id)
    action = args.get("action")
    if action == "get":
        return strategy.guide_state(store, deck_id)
    if action == "prepare":
        _flush(session_id, deck_id)
        return strategy.prepare(store, deck_id)
    if action == "query":
        request = {k: v for k, v in (args.get("request") or {}).items() if k in ("buckets", "intersections", "cards")}
        return strategy.query(store, deck_id, args.get("job_id"), request)
    if action == "publish":
        result = strategy.publish(store, deck_id, args.get("job_id"), args.get("markdown") or "")
        bridge.notify("guide", {"session_id": session_id, "deck_id": deck_id, "reveal": True})
        result.pop("guide", None)
        return result
    raise StudioError("invalid", "action must be prepare, query, publish or get.")


def _original(doc, args, session_id, deck_id):
    ref = args.get("card")
    if ref:
        key = ref.casefold()
        found = [c for c in doc.get("cards") or []
                 if c.get("id") == ref
                 or (c.get("name") or "").casefold() == key
                 or (c.get("printedName") or "").casefold() == key]
        if not found:
            raise StudioError("invalid", f"No card '{ref}' in this deck.")
        return found[0]
    live = bridge.context(session_id)
    selected = (live or {}).get("selected") or [] if (live or {}).get("deck_id") == deck_id else []
    if len(selected) == 1:
        return next((c for c in doc.get("cards") or [] if c.get("id") == selected[0]["id"]), None)
    return None


def search_tool(args, session_id, store):
    deck_id = _bound(store, session_id)
    _flush(session_id, deck_id)
    doc = store.load(deck_id)
    original = _original(doc, args, session_id, deck_id)
    result = scryfall.find_alternatives(doc, original, args.get("constraints") or {})
    if original:
        result["original"] = {"id": original["id"], "name": original["name"],
                              "printed_name": original.get("printedName"), "mana_cost": original.get("manaCost"),
                              "mv": original.get("manaValue"), "type_line": original.get("typeLine"),
                              "oracle_text": original.get("oracleText"), "image": original.get("imageUri")}
    if args.get("save", True) and result["candidates"]:
        saved = sideboard.add(store, deck_id, result["candidates"], reason=args.get("reason"))
        result["sideboard"] = {"added": saved["added"], "count": saved["count"]}
        result["saved_to_sideboard"] = True
        bridge.notify("sideboard", {"deck_id": deck_id})
    else:
        result["saved_to_sideboard"] = False
    if args.get("show") and result["candidates"]:
        bridge.notify("tray", {"session_id": session_id, "deck_id": deck_id, "original": result.get("original"),
                               "candidates": result["candidates"], "query": result["query"]})
        result["shown_in_tray"] = True
    result["next"] = ("Compare the candidates for the player. Replace only when asked, with mtg_deck_studio_apply "
                      "op=replace_card and the candidate's scryfall_id. Candidates are not part of the deck.")
    return result


_STRATEGY = {
    "name": "mtg_deck_studio_strategy",
    "description": (
        "Deck strategy evidence and the generated strategy guide shown in the studio's Strategy view. "
        "prepare: deterministic evidence pack (stats, curve, effects, interaction and key-card candidates) for the "
        "bound deck. query: one bounded round of card-level evidence (<=6 buckets, <=4 intersections, <=12 cards). "
        "publish: validate and store a guide (Markdown, 600-1500 words, required headings; see skill "
        "mtg-deck-studio references/write-strategy-note.md). get: the current guide and whether it is outdated. "
        "Strategy answers must rest on this evidence, not on familiar archetypes."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["prepare", "query", "publish", "get"]},
            "job_id": {"type": "string", "description": "From prepare; required for query and publish."},
            "request": {
                "type": "object",
                "properties": {
                    "buckets": {"type": "array", "items": {"type": "string"}},
                    "intersections": {"type": "array", "items": {"type": "array", "items": {"type": "string"}}},
                    "cards": {"type": "array", "items": {"type": "string"}},
                },
            },
            "markdown": {"type": "string", "description": "Guide text for publish."},
        },
        "required": ["action"],
    },
}

_SEARCH = {
    "name": "mtg_deck_studio_search",
    "description": (
        "Find real alternatives on Scryfall for a card in the bound deck (default: the single selected card) and "
        "park them in the studio sideboard so the player can review, promote or replace them. Always restricted "
        "to the deck's color identity, Commander-legal paper cards. Hard constraints are re-checked on every "
        "result. Translate the player's request into constraints."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "card": {"type": "string", "description": "Card id or exact name in the deck. Omit to use the selection."},
            "constraints": {
                "type": "object",
                "properties": {
                    "max_mv": {"type": "integer"}, "min_mv": {"type": "integer"},
                    "types": {"type": "array", "items": {"type": "string"}, "description": "Required types or subtypes, e.g. creature, artifact, elf."},
                    "not_types": {"type": "array", "items": {"type": "string"}},
                    "oracle": {"type": "array", "items": {"type": "string"}, "description": "Phrases that must appear in rules text."},
                    "function_tags": {"type": "array", "items": {"type": "string"}, "description": "Scryfall oracle tags, e.g. ramp, mana-rock, removal, card-advantage, sacrifice-outlet."},
                    "max_usd": {"type": "number"},
                    "exclude_names": {"type": "array", "items": {"type": "string"}},
                    "extra": {"type": "string", "description": "Extra Scryfall syntax for anything not covered above."},
                    "order": {"type": "string", "enum": ["edhrec", "cmc", "usd", "name", "released"]},
                    "soft": {"type": "array", "items": {"type": "string"}, "description": "Preferences you will judge yourself; reported back, not queried."},
                },
            },
            "show": {"type": "boolean",
                     "description": "Also open the comparison tray over the table (default false)."},
            "save": {"type": "boolean",
                     "description": "Park the candidates in the sideboard (default true); false only lists them."},
            "reason": {"type": "string",
                       "description": "Why these candidates were kept, shown on the sideboard row."},
        },
    },
}



def sideboard_tool(args, session_id, store):
    """Cards found for the deck but not in it: list, add, drop, promote, replace."""
    deck_id = _bound(store, session_id)
    action = args.get("action") or "list"
    if action == "list":
        return sideboard.list_entries(store, deck_id)
    if action == "add":
        cards = list(args.get("cards") or [])
        # A ref is the identity the Add Cards box takes ("Sol Ring (CMM) 410") or a Scryfall id:
        # what the agent has after reading a card from a photo.
        refs = [str(ref).strip() for ref in (args.get("refs") or []) if str(ref).strip()]
        if refs:
            cards.extend(scryfall.printing(scryfall.resolve_ref(ref)) for ref in refs)
        if not cards:
            raise StudioError("invalid", "Give the candidates to add: cards from mtg_deck_studio_search, or refs such as 'Sol Ring (CMM) 410'.")
        result = sideboard.add(store, deck_id, cards, reason=args.get("reason"))
        bridge.notify("sideboard", {"deck_id": deck_id})
        return result
    if action == "remove":
        result = sideboard.remove(store, deck_id, args.get("scryfall_ids") or [])
        bridge.notify("sideboard", {"deck_id": deck_id})
        return result
    if action == "clear":
        result = sideboard.clear(store, deck_id)
        bridge.notify("sideboard", {"deck_id": deck_id})
        return result
    if action in ("promote", "replace"):
        entry = sideboard.find(store, deck_id, args.get("scryfall_id") or "")
        if action == "promote":
            ack = bridge.send(session_id, deck_id, "add_cards",
                              {"name": entry["name"], "quantity": 1}, kind="document")
        else:
            payload = {"scryfall_id": entry["scryfall_id"]}
            if args.get("replace_id"):
                payload["id"] = args["replace_id"]
            elif args.get("replace_name"):
                payload["name"] = args["replace_name"]
            else:
                raise StudioError("invalid", "Replace needs replace_id or replace_name: the deck card to swap out.")
            ack = bridge.send(session_id, deck_id, "replace_card", payload, kind="document")
        if ack.get("outcome") != "applied":
            return {"outcome": ack.get("outcome"), "error": ack.get("error"), "rev": ack.get("rev"),
                    "note": "The deck did not change; the card stays in the sideboard."}
        sideboard.remove(store, deck_id, [entry["scryfall_id"]])
        bridge.notify("sideboard", {"deck_id": deck_id})
        return {"outcome": "applied", "rev": ack.get("rev"), "card": entry["name"], "action": action,
                "count": len(store.sideboard(deck_id))}
    raise StudioError("invalid", f"Unknown sideboard action '{action}'. Use list, add, remove, clear, promote or replace.")


_STATE = {
    "name": "mtg_deck_studio_state",
    "description": (
        "Read the MTG deck bound to this chat in the MTG Deck Studio: summary (commanders, sections, curve, revision), "
        "the player's current table selection, and optionally a filtered card list with oracle text. "
        "Reflects manual edits made with the mouse. Never guess deck contents; read them here."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "group_by": {
                "type": "string",
                "enum": ["subtype", "type", "color", "mv", "section", "effect"],
                "description": "Count the deck along one axis, e.g. subtype for creature types. Answers \"main creature types\" style questions.",
            },
            "group_limit": {"type": "integer", "description": "How many groups to return (default 14)."},
            "cards": {
                "type": "object",
                "description": "Include matching cards. Omit for summary only.",
                "properties": {
                    "names": {"type": "array", "items": {"type": "string"}},
                    "ids": {"type": "array", "items": {"type": "string"}},
                    "selected": {"type": "boolean", "description": "Only the cards selected on the table."},
                    "section": {"type": "string", "description": "Table section id, e.g. creatures, lands, commanders."},
                    "role": {"type": "string", "description": "Effect id."},
                    "text": {"type": "string", "description": "Substring of oracle text."},
                    "limit": {"type": "integer"},
                },
            }
        },
    },
}


def _apply_schema():
    reg = operations.registry()
    lines = [f"- {n}: {reg[n]['summary']}" for n in operations.public_names()]
    return {
        "name": "mtg_deck_studio_apply",
        "description": (
            "Run one operation on the deck shown in the MTG Deck Studio beside this chat. The table updates and the "
            "result reports the acknowledged outcome and new revision. 'applied' means it happened; any other outcome "
            "means it did not. Operations:\n" + "\n".join(lines)
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "op": {"type": "string", "enum": operations.public_names()},
                "args": {"type": "object", "description": "Operation arguments, e.g. {\"title\": \"...\"} or {\"names\": [...]}."},
                "expected_rev": {"type": "integer", "description": "Refuse if the deck changed since this revision."},
            },
            "required": ["op"],
        },
    }



def _classify_via_pane(session_id, deck_id, entries):
    """Ask the open pane to classify cards it does not have (the classifier is JavaScript)."""
    if not bridge.is_live(session_id, deck_id):
        return None
    cards = [{
        "id": entry.get("scryfall_id"),
        "name": entry.get("name"),
        "type_line": entry.get("type_line"),
        "oracle_text": entry.get("oracle_text"),
        "mana_cost": entry.get("mana_cost"),
    } for entry in entries]
    try:
        ack = bridge.send(session_id, deck_id, "classify_cards", {"cards": cards}, kind="query", timeout=8.0)
    except StudioError:
        return None
    if ack.get("outcome") != "applied":
        return None
    return ((ack.get("result") or {}).get("cards")) or {}


def _candidate_links(args, session_id, store):
    deck_id = _bound(store, session_id)
    _flush(session_id, deck_id)
    document = store.load(deck_id)
    ref = str(args.get("card") or "").strip()
    if not ref:
        raise StudioError("invalid", "mode=candidate needs a card name or Scryfall id.")
    entry = sideboard.find_candidate(store, deck_id, ref)
    fresh = False
    if entry is None:
        if links.find_card(document, ref):
            raise StudioError("invalid", "That card is already in the deck; ask for it with mode=card.")
        fetched = scryfall.named_card(ref)
        entry = sideboard._entry(fetched)
        fresh = True
    if not (entry.get("autoBucketFacts") or []):
        classified = _classify_via_pane(session_id, deck_id, [entry])
        result = (classified or {}).get(entry["scryfall_id"]) or {}
        if not result.get("autoBucketFacts"):
            raise StudioError(
                "unavailable",
                "This card has not been classified yet. Open the MTG Deck Studio pane (its classifier runs there) "
                "and ask again, or look it up with mtg_deck_studio_search and save it to the sideboard first.",
            )
        entry["autoBucketFacts"] = result["autoBucketFacts"]
        entry["autoBuckets"] = result.get("autoBuckets") or []
        if sideboard.find_candidate(store, deck_id, entry["scryfall_id"]):
            sideboard.set_classification(store, deck_id, entry["scryfall_id"], result)

    saved = False
    if args.get("save") and not sideboard.find_candidate(store, deck_id, entry["scryfall_id"]):
        sideboard.add(store, deck_id, [entry], reason=args.get("reason") or "Candidate checked for links")
        saved = True
        bridge.notify("sideboard", {"deck_id": deck_id})
    elif args.get("save"):
        saved = True

    result = links.evaluate(document, entry)
    result["saved_to_sideboard"] = saved
    result["fresh_lookup"] = fresh
    result["next"] = (
        "Say which deck cards would feed this card and which it would feed, quoting the clauses. "
        "It is not in the deck yet; mention that it is in the sideboard if saved_to_sideboard is true."
    )
    return result

_DECKS = {
    "name": "mtg_deck_studio_decks",
    "description": ("List the player's stored MTG Deck Studio decks and every deck JSON in the studio folder, import "
                    "a deck (deck JSON, folder of them, or a scanner export: MythicTools CSV or a decklist file), "
                    "create an empty deck, or export the bound deck as Moxfield text or a deck JSON "
                    "(written to the studio folder; the result gives the path)."),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["list", "open", "close", "import", "new", "export"]},
            "path": {"type": "string", "description": ("For import: an absolute path to a deck JSON, a folder to scan for deck JSON files, "
                                                     "or a MythicTools .csv / decklist .txt export. A CSV or .txt is loaded by the pane "
                                                     "card by card (printing, set and price kept), so the result says `started`: read "
                                                     "mtg_deck_studio_state to follow the count. "
                                                     "After importing new printings, run scripts/build_name_index.py so cards can be "
                                                     "found by the name printed on them.")},
            "format": {"type": "string", "enum": ["moxfield", "json"]},
            "deck_id": {"type": "string", "description": ("For import of a CSV or decklist: the deck to load it into, whose cards it "
                                                          "replaces. Omit to create a new deck titled from `title` or the file name.")},
            "title": {"type": "string", "description": "For new or import: the deck's title."},
        },
        "required": ["action"],
    },
}


def _deck_survey(args, store):
    """Every stored deck scored by how connected it is, without needing a bound deck."""
    only = str(args.get("deck_id") or "").strip()
    documents = {}
    titles = {}
    for meta in store.list_decks():
        if only and meta["deck_id"] != only:
            continue
        try:
            documents[meta["deck_id"]] = store.load(meta["deck_id"])
        except StudioError:
            continue
        titles[meta["deck_id"]] = meta.get("title") or meta["deck_id"]
    if not documents:
        raise StudioError("not_found", "No stored deck matched that request.")
    out = links.survey(documents)
    for row in out["decks"]:
        row["title"] = titles.get(row["deck_id"], row["deck_id"])
    out["next"] = ("Name the most and the least connected deck with their numbers, and say what the "
                   "score counts, so the player can compare fairly.")
    return out


def links_tool(args, session_id, store):
    if str(args.get("mode") or "") == "decks":
        return _deck_survey(args, store)
    deck_id = _bound(store, session_id)
    _flush(session_id, deck_id)
    document = store.load(deck_id)
    mode = str(args.get("mode") or ("card" if args.get("card") else "hubs"))
    if mode == "candidate":
        return _candidate_links(args, session_id, store)
    if mode == "card":
        ref = str(args.get("card") or "").strip()
        if not ref:
            raise StudioError("invalid", "mode=card needs a card name, id or fragment.")
        try:
            result = links.for_card(document, ref)
        except ValueError as exc:
            raise StudioError("invalid", str(exc)) from exc
        result["next"] = ("Explain the relation with the clauses given: say which card does what and which "
                          "reaction it feeds. Do not invent links that are not in this answer.")
        return result
    if mode == "event":
        event = str(args.get("event") or "").strip()
        if event not in set(links.EVENT_OF_TRIGGER.values()):
            raise StudioError("invalid", f"Unknown event '{event}'. Known: {sorted(set(links.EVENT_OF_TRIGGER.values()))}")
        graph = links.build(document)
        names = {c["id"]: c.get("name") for c in document.get("cards") or []}
        reacting = []
        causing = []
        for card_id, entry in graph["cards"].items():
            for group in entry["fed_by"]:
                if group["event"] == event:
                    reacting.append({"id": card_id, "name": names.get(card_id),
                                     "uses": [{"card": names.get(pid), "clause": clause["reaction"]}
                                              for pid, clause in zip(group["cards"], group["clauses"])][:6]})
            for group in entry["feeds"]:
                if group["event"] == event:
                    causing.append({"id": card_id, "name": names.get(card_id), "feeds": group["count"]})
        reacting.sort(key=lambda row: len(row["uses"]), reverse=True)
        causing.sort(key=lambda row: row["feeds"], reverse=True)
        return {"event": event, "reacts": reacting[:12], "causes": causing[:12], "stats": graph["stats"]}
    limit = max(1, min(20, int(args.get("limit") or 8)))
    return {"hubs": links.hubs(document, limit=limit), "stats": links.build(document)["stats"]}


_LINKS = {
    "name": "mtg_deck_studio_links",
    "description": (
        "Card-to-card links for the bound deck, computed from the classifier facts. Two directions: "
        "fed_by (cards that do something this card reacts to) and feeds (cards that react to something "
        "this card does). mode=card gives one card with its groups, partner names and the clauses that "
        "justify each link; mode=hubs ranks the cards the deck is built around; mode=event lists every "
        "card touching one event (enter, die, sacrifice, cast, attack, block, combat-damage, damage, tap, "
        "counter, life-gain, discard, graveyard-enter, graveyard-leave, crime). Quote the clauses; do not "
        "claim a synergy that is not returned here. Every link is an ability causing an event: "
        "pairs that only hold because a card exists, enters or can be cast are returned separately as "
        "*_any_card and must never be presented as synergy. "
        "mode=candidate answers \"would this card fit?\": it classifies the card (the open pane runs the classifier), "
        "builds the deck's link graph with it added, and returns the deck cards it would interact with in both "
        "directions. save=true also puts it in the sideboard so the pane can show those links."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "card": {"type": "string", "description": "Card name (English or printed), id, or a unique fragment."},
            "mode": {"type": "string", "enum": ["card", "candidate", "hubs", "event", "decks"],
                     "description": ("card: links of a deck card. candidate: what-if links for a card that is not in the deck "
                                     "yet (sideboard entry, or fetched from Scryfall and classified by the open pane). "
                                     "hubs: the cards the deck is built around. event: everything touching one event. "
                                     "decks: ranks every stored deck by how connected its cards are (no binding needed).")},
            "event": {"type": "string", "description": "For mode=event."},
            "limit": {"type": "integer", "minimum": 1, "maximum": 20, "description": "For mode=hubs."},
            "save": {"type": "boolean", "description": "For mode=candidate: put the card in the sideboard so the pane can show its links."},
            "reason": {"type": "string", "description": "For mode=candidate with save: why this card is worth a look."},
        },
    },
}

HANDLERS = {"mtg_deck_studio_state": state, "mtg_deck_studio_apply": apply, "mtg_deck_studio_decks": decks,
            "mtg_deck_studio_strategy": strategy_tool, "mtg_deck_studio_search": search_tool,
            "mtg_deck_studio_sideboard": sideboard_tool, "mtg_deck_studio_links": links_tool}


_SIDEBOARD = {
    "name": "mtg_deck_studio_sideboard",
    "description": (
        "The deck's sideboard: cards found as candidates but not part of the deck, shown in the pane's "
        "Sideboard tab. list: current entries. add: put cards in it (deduplicated by Scryfall id) — either "
        "search candidates, or refs naming an exact printing such as 'Sol Ring (CMM) 410', which is how a "
        "card the player photographed is added. remove/clear: drop entries. promote: add one to the deck. "
        "replace: swap a deck card for it (replace_id or replace_name). promote/replace only report applied "
        "once the studio acknowledges the deck change; the entry leaves the sideboard at that point."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["list", "add", "remove", "clear", "promote", "replace"]},
            "cards": {"type": "array", "items": {"type": "object"}, "description": "Candidates from mtg_deck_studio_search."},
            "refs": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Exact printings to add: 'Name (SET) 123' or a Scryfall id.",
            },
            "reason": {"type": "string", "description": "Why these cards fit; shown under each card in the pane."},
            "scryfall_ids": {"type": "array", "items": {"type": "string"}},
            "scryfall_id": {"type": "string", "description": "Entry to promote or replace."},
            "replace_id": {"type": "string", "description": "Deck card id to swap out."},
            "replace_name": {"type": "string", "description": "Deck card name to swap out."},
        },
        "required": ["action"],
    },
}


def schemas():
    return [_STATE, _apply_schema(), _DECKS, _STRATEGY, _SEARCH, _SIDEBOARD, _LINKS]


def dispatch(name, args, session_id=None, store=None):
    store = store or store_mod.default()
    try:
        result = HANDLERS[name](args, session_id, store)
    except StudioError as exc:
        result = exc.to_dict()
    return json.dumps(result, ensure_ascii=False, default=str)
