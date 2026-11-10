#!/usr/bin/env python3
"""Convert MTG Viewer JSON saves to paste-ready Moxfield decklists."""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


QUANTITY_LINE = re.compile(r"^\s*(\d+)\s+(.+?)\s*$")
PRINTING_LINE = re.compile(
    r"^(.*?)"
    r"(?:\s+\(([A-Za-z0-9]{2,8})\)\s+#?([^\s]+))?"
    r"(?:\s+\*([FfEe])\*)?$"
)
SCRYFALL_COLLECTION_URL = "https://api.scryfall.com/cards/collection"
SCRYFALL_BATCH_SIZE = 75


@dataclass(frozen=True)
class DeckEntry:
    quantity: int
    name: str
    set_code: str = ""
    collector_number: str = ""
    finish: str = ""


@dataclass(frozen=True)
class ConvertedDeck:
    source: Path
    title: str
    commanders: list[DeckEntry]
    deck: list[DeckEntry]
    source_count: int
    used_fallback_decklist: bool

    @property
    def total_quantity(self) -> int:
        return sum(entry.quantity for entry in self.commanders) + sum(
            entry.quantity for entry in self.deck
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert MTG Viewer JSON saves to Moxfield-compatible decklists."
    )
    parser.add_argument(
        "input",
        type=Path,
        help="A .mtg-viewer.json file or a directory containing viewer saves.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Output .txt file when converting one input file.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help=(
            "Directory for generated .txt files. Defaults to "
            "<input-dir>/moxfield_decklists for directory input."
        ),
    )
    parser.add_argument(
        "--pattern",
        default="*mtg-viewer.json",
        help="Glob used for directory input. Default: *mtg-viewer.json",
    )
    parser.add_argument(
        "--recursive",
        action="store_true",
        help="Search directory input recursively.",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Do not resolve missing set/collector metadata from saved Scryfall IDs.",
    )
    return parser.parse_args()


def load_json(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path}: invalid JSON: {exc}") from exc
    except OSError as exc:
        raise ValueError(f"{path}: cannot read file: {exc}") from exc

    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a JSON object")
    return data


def clean_name(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.split()).strip()


def normalize_finish(value: Any) -> str:
    clean = clean_name(value).lower().replace("_", "").replace("-", "").replace(" ", "")
    if clean in {"f", "foil"}:
        return "foil"
    if clean in {"e", "etched", "etchedfoil"}:
        return "etched"
    if clean in {"n", "nonfoil", "regular"}:
        return "nonfoil"
    return ""


def entry_key(entry: DeckEntry) -> tuple[str, str, str, str]:
    return (
        entry.name,
        entry.set_code.lower(),
        entry.collector_number,
        entry.finish,
    )


def add_quantity(
    entries: OrderedDict[tuple[str, str, str, str], DeckEntry],
    entry: DeckEntry,
    quantity: int = 1,
) -> None:
    if quantity <= 0:
        return
    key = entry_key(entry)
    existing = entries.get(key)
    entries[key] = DeckEntry(
        quantity=(existing.quantity if existing else 0) + quantity,
        name=entry.name,
        set_code=entry.set_code,
        collector_number=entry.collector_number,
        finish=entry.finish,
    )


def to_entries(
    entries: OrderedDict[tuple[str, str, str, str], DeckEntry],
) -> list[DeckEntry]:
    return list(entries.values())


def parse_printed_name(value: str) -> DeckEntry:
    clean = clean_name(value.split("|", 1)[0])
    match = PRINTING_LINE.match(clean)
    if not match:
        return DeckEntry(quantity=1, name=clean)
    name = clean_name(match.group(1))
    marker = (match.group(4) or "").lower()
    finish = "foil" if marker == "f" else "etched" if marker == "e" else ""
    return DeckEntry(
        quantity=1,
        name=name,
        set_code=(match.group(2) or "").lower(),
        collector_number=match.group(3) or "",
        finish=finish,
    )


def parse_compact_decklist(decklist: Any, source: Path) -> tuple[list[DeckEntry], int]:
    if not isinstance(decklist, str) or not decklist.strip():
        raise ValueError(f"{source}: no cards[] and no compact decklist fallback")

    entries: OrderedDict[tuple[str, str, str, str], DeckEntry] = OrderedDict()
    total = 0
    for line_number, raw_line in enumerate(decklist.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        match = QUANTITY_LINE.match(line)
        if not match:
            raise ValueError(f"{source}: cannot parse decklist line {line_number}: {line!r}")
        quantity = int(match.group(1))
        entry = parse_printed_name(match.group(2))
        if not entry.name:
            raise ValueError(f"{source}: empty card name on decklist line {line_number}")
        add_quantity(entries, entry, quantity)
        total += quantity
    return to_entries(entries), total


def fetch_scryfall_printings(ids: Iterable[str]) -> dict[str, dict[str, str]]:
    unique_ids = list(dict.fromkeys(clean_name(value).lower() for value in ids if clean_name(value)))
    resolved: dict[str, dict[str, str]] = {}
    for index in range(0, len(unique_ids), SCRYFALL_BATCH_SIZE):
        batch = unique_ids[index : index + SCRYFALL_BATCH_SIZE]
        payload = json.dumps({"identifiers": [{"id": value} for value in batch]}).encode("utf-8")
        request = urllib.request.Request(
            SCRYFALL_COLLECTION_URL,
            data=payload,
            method="POST",
            headers={
                "Accept": "application/json;q=0.9,*/*;q=0.8",
                "Content-Type": "application/json",
                "User-Agent": "mtg-viewer-to-moxfield/1.0",
            },
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            body = json.load(response)
        for card in body.get("data", []):
            card_id = clean_name(card.get("id")).lower()
            if card_id:
                resolved[card_id] = {
                    "set_code": clean_name(card.get("set")).lower(),
                    "collector_number": clean_name(card.get("collector_number")),
                }
        if index + SCRYFALL_BATCH_SIZE < len(unique_ids):
            time.sleep(0.11)
    return resolved


def missing_printing_ids(data_by_path: dict[Path, dict[str, Any]]) -> list[str]:
    ids: list[str] = []
    for data in data_by_path.values():
        cards = data.get("cards")
        if not isinstance(cards, list):
            continue
        for card in cards:
            if not isinstance(card, dict):
                continue
            has_printing = clean_name(card.get("setCode") or card.get("set")) and clean_name(
                card.get("collectorNumber") or card.get("collector_number")
            )
            scryfall_id = clean_name(card.get("scryfallId")).lower()
            if not has_printing and scryfall_id:
                ids.append(scryfall_id)
    return list(dict.fromkeys(ids))


def convert_from_cards(
    cards: Iterable[Any],
    source: Path,
    printings_by_id: dict[str, dict[str, str]] | None = None,
) -> tuple[list[DeckEntry], list[DeckEntry], int]:
    commander_entries: OrderedDict[tuple[str, str, str, str], DeckEntry] = OrderedDict()
    deck_entries: OrderedDict[tuple[str, str, str, str], DeckEntry] = OrderedDict()
    printings_by_id = printings_by_id or {}
    total = 0

    for index, card in enumerate(cards, start=1):
        if not isinstance(card, dict):
            raise ValueError(f"{source}: card {index} is not an object")
        name = clean_name(card.get("name")) or clean_name(card.get("requestedName"))
        if not name:
            raise ValueError(f"{source}: card {index} has no usable name")

        scryfall_id = clean_name(card.get("scryfallId")).lower()
        resolved = printings_by_id.get(scryfall_id, {})
        entry = DeckEntry(
            quantity=1,
            name=name,
            set_code=clean_name(card.get("setCode") or card.get("set") or resolved.get("set_code")).lower(),
            collector_number=clean_name(
                card.get("collectorNumber")
                or card.get("collector_number")
                or resolved.get("collector_number")
            ),
            finish=normalize_finish(card.get("finish")),
        )

        is_commander = card.get("isCommander") is True
        if is_commander:
            add_quantity(commander_entries, entry)
        else:
            add_quantity(deck_entries, entry)
        total += 1

    return to_entries(commander_entries), to_entries(deck_entries), total


def convert_data(
    data: dict[str, Any],
    path: Path,
    printings_by_id: dict[str, dict[str, str]] | None = None,
) -> ConvertedDeck:
    title = clean_name(data.get("deckTitle")) or path.stem
    cards = data.get("cards")

    if isinstance(cards, list) and cards:
        commanders, deck, total = convert_from_cards(cards, path, printings_by_id)
        return ConvertedDeck(
            source=path,
            title=title,
            commanders=commanders,
            deck=deck,
            source_count=total,
            used_fallback_decklist=False,
        )

    deck, total = parse_compact_decklist(data.get("decklist"), path)
    return ConvertedDeck(
        source=path,
        title=title,
        commanders=[],
        deck=deck,
        source_count=total,
        used_fallback_decklist=True,
    )


def convert_file(
    path: Path,
    printings_by_id: dict[str, dict[str, str]] | None = None,
) -> ConvertedDeck:
    return convert_data(load_json(path), path, printings_by_id)


def render_entry(entry: DeckEntry) -> str:
    result = f"{entry.quantity} {entry.name}"
    if entry.set_code and entry.collector_number:
        result += f" ({entry.set_code.upper()}) {entry.collector_number}"
    if entry.finish == "foil":
        result += " *F*"
    elif entry.finish == "etched":
        result += " *E*"
    return result


def render_deck(deck: ConvertedDeck) -> str:
    lines: list[str] = []

    if deck.commanders:
        lines.append("Commander")
        lines.extend(render_entry(entry) for entry in deck.commanders)
        lines.append("")

    lines.append("Deck")
    lines.extend(render_entry(entry) for entry in deck.deck)
    return "\n".join(lines) + "\n"


def output_name_for(source: Path) -> str:
    stem = source.name
    for suffix in (".mtg-viewer.json", "-mtg-viewer.json", ".json"):
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", stem).strip("-._")
    return f"{safe or source.stem}-moxfield.txt"


def discover_inputs(path: Path, pattern: str, recursive: bool) -> list[Path]:
    if path.is_file():
        return [path]
    if not path.is_dir():
        raise ValueError(f"{path}: input is not a file or directory")
    iterator = path.rglob(pattern) if recursive else path.glob(pattern)
    files = sorted(candidate for candidate in iterator if candidate.is_file())
    if not files:
        raise ValueError(f"{path}: no files matched {pattern!r}")
    return files


def resolve_outputs(args: argparse.Namespace, input_files: list[Path]) -> dict[Path, Path]:
    if len(input_files) == 1 and args.output:
        return {input_files[0]: args.output}

    if args.output and len(input_files) > 1:
        raise ValueError("--output can only be used with one input file")

    output_dir = args.output_dir
    if output_dir is None:
        if args.input.is_dir():
            output_dir = args.input / "moxfield_decklists"
        else:
            output_dir = args.input.parent

    return {source: output_dir / output_name_for(source) for source in input_files}


def write_index(output_dir: Path, converted: list[tuple[ConvertedDeck, Path]]) -> None:
    if not converted:
        return
    index_path = output_dir / "INDEX.txt"
    lines = ["MTG Viewer to Moxfield decklists", ""]
    for deck, path in converted:
        mode = "fallback decklist" if deck.used_fallback_decklist else "cards"
        lines.append(
            f"{path.name}\t{deck.total_quantity} cards\t"
            f"{len(deck.commanders)} commander lines\tfrom {mode}\t{deck.title}"
        )
    index_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run() -> int:
    args = parse_args()
    try:
        input_files = discover_inputs(args.input, args.pattern, args.recursive)
        outputs = resolve_outputs(args, input_files)
        data_by_path = {source: load_json(source) for source in input_files}
        printings_by_id: dict[str, dict[str, str]] = {}
        if not args.offline:
            ids = missing_printing_ids(data_by_path)
            if ids:
                try:
                    printings_by_id = fetch_scryfall_printings(ids)
                except (OSError, urllib.error.URLError, ValueError, json.JSONDecodeError) as exc:
                    print(
                        f"warning: could not resolve legacy Scryfall IDs; exporting those cards by name: {exc}",
                        file=sys.stderr,
                    )
        converted: list[tuple[ConvertedDeck, Path]] = []

        for source in input_files:
            deck = convert_data(data_by_path[source], source, printings_by_id)
            if deck.total_quantity != deck.source_count:
                raise ValueError(
                    f"{source}: output count {deck.total_quantity} does not match "
                    f"source count {deck.source_count}"
                )
            output_path = outputs[source]
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(render_deck(deck), encoding="utf-8")
            converted.append((deck, output_path))

        output_dirs = {path.parent for _, path in converted}
        if len(output_dirs) == 1:
            write_index(next(iter(output_dirs)), converted)

        for deck, output_path in converted:
            commander_count = sum(entry.quantity for entry in deck.commanders)
            deck_count = sum(entry.quantity for entry in deck.deck)
            fallback = " fallback" if deck.used_fallback_decklist else ""
            print(
                f"Wrote {output_path} "
                f"({commander_count} commander, {deck_count} deck,{fallback} "
                f"{deck.total_quantity} total)"
            )
        return 0
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(run())
