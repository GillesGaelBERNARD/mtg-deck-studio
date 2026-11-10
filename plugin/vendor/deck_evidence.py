#!/usr/bin/env python3
"""Create bounded, deck-scoped evidence for MTG strategy-note agents."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import uuid
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable


APP_ID = "mtg-table-viewer"
CURRENT_SAVE_VERSION = 4
SUPPORTED_SAVE_VERSIONS = frozenset({3, CURRENT_SAVE_VERSION})
EXCLUDED_DIRECTORY_NAMES = {"other", "others"}
VISIBLE_AGGREGATE_SCHEMA = "mtg-deck-strategy-evidence/1"
SESSION_SCHEMA = "mtg-deck-strategy-session/1"
DEFAULT_MAX_INITIAL_CHARS = 40_000
MAX_MANUAL_NOTE_CHARS = 6_000
MAX_INTERACTION_CANDIDATES = 30
MAX_KEY_CANDIDATES = 12
MAX_BUCKETS_PER_QUERY = 6
MAX_INTERSECTIONS_PER_QUERY = 4
MAX_QUERY_RESPONSE_CHARS = 40_000
MIN_STRATEGY_WORDS = 600
MAX_STRATEGY_WORDS = 1_500
COLOR_ORDER = ("W", "U", "B", "R", "G")

UTILITY_BUCKET_LABELS = {
    "ramp": "Ramp",
    "card-draw": "Card Draw",
    "removal": "Removal",
    "board-wipe": "Board Wipe",
    "counterspell": "Counterspell",
    "protection": "Protection",
    "token-maker": "Token Maker",
    "recursion": "Recursion",
    "tutor": "Tutor",
    "plus-one-counters": "+1/+1 Counter",
    "mana-fixing": "Mana Fixing",
    "sacrifice": "Sacrifice",
    "graveyard": "Graveyard",
    "lifegain": "Lifegain",
}


def overview(
    bucket_id: str,
    cluster: str,
    label: str,
    include: tuple[str, ...],
    exclude: tuple[str, ...] = (),
) -> dict[str, Any]:
    return {
        "id": bucket_id,
        "cluster": cluster,
        "label": label,
        "include": tuple(re.compile(pattern) for pattern in include),
        "exclude": tuple(re.compile(pattern) for pattern in exclude),
    }


OVERVIEW_GROUPS = (
    overview("overview/draw", "Cards & resources", "Draw cards", (r"^cards/draw/", r"\bcard draw\b")),
    overview("overview/filter", "Cards & resources", "Filter & select cards", (r"^cards/filter/", r"\bcard filtering\b")),
    overview("overview/search", "Cards & resources", "Search the library", (r"^cards/search/", r"^tutor\b")),
    overview("overview/acceleration", "Cards & resources", "Mana acceleration", (r"^mana/(accelerate|reduce-cost|cast-without-paying)/", r"^ramp\b")),
    overview("overview/fixing", "Cards & resources", "Mana fixing", (r"^mana/fix-colors/", r"^mana-fixing\b", r"^mana fixing\b")),
    overview("overview/recursion", "Cards & resources", "Recursion", (r"^graveyard/(return|reanimate|cast-from)/", r"^recursion\b")),
    overview("overview/graveyard-setup", "Cards & resources", "Graveyard setup", (r"^graveyard/fill/", r"^graveyard\b")),
    overview("overview/token-create", "Build the board", "Create tokens", (r"^tokens/(create|copy)/", r"^token-maker\b", r"^token maker\b", r"\bcreate .+ tokens?\b")),
    overview("overview/token-use", "Build the board", "Use tokens", (r"^tokens/animate/", r"^sacrifice/pay/(clue|food|treasure|blood|map|token)", r"\buse .+ tokens?\b", r"\binteract with .+ tokens?\b")),
    overview("overview/counters", "Build the board", "Counters", (r"^counters/", r"^plus-one-counters\b", r"\b\+1/\+1 counter\b", r"\binteract with .+counter")),
    overview("overview/boost", "Build the board", "Creature boosts", (r"^boost/", r"^boost\b")),
    overview("overview/evasion", "Build the board", "Evasion", (r"^evasion/", r"^evasion\b")),
    overview("overview/sacrifice", "Build the board", "Sacrifice costs", (r"^sacrifice/pay/", r"^sacrifice\b")),
    overview("overview/lifegain", "Build the board", "Gain life", (r"^life/gain/", r"^lifegain\b")),
    overview("overview/draw-triggers", "Engines & triggers", "Draw-triggered effects", (r"^cards/react-to/draw-", r"\breaction to card draw\b")),
    overview("overview/life-triggers", "Engines & triggers", "Life-gain triggers", (r"^life/react-to/gain",)),
    overview("overview/counter-triggers", "Engines & triggers", "Counter-triggered effects", (r"^counters/react-to/",)),
    overview("overview/token-triggers", "Engines & triggers", "Token-triggered effects", (r"^tokens/react-to/", r"^(death|sacrifice)/react-to/(token|clue|food|treasure|blood|map)")),
    overview("overview/entry-triggers", "Engines & triggers", "Permanent-entry triggers", (r"^(creatures|artifacts|enchantments|tokens|permanents|battles)/react-to/enter",)),
    overview("overview/land-triggers", "Engines & triggers", "Land-entry triggers", (r"^lands/react-to/enter", r"^landfall\b")),
    overview("overview/combat-triggers", "Engines & triggers", "Combat-triggered effects", (r"^combat/react-to/",)),
    overview("overview/damage-triggers", "Engines & triggers", "Damage-triggered effects", (r"^damage/react-to/",)),
    overview("overview/spell-triggers", "Engines & triggers", "Spellcasting triggers", (r"^spells/react-to/",)),
    overview("overview/death-triggers", "Engines & triggers", "Death & sacrifice triggers", (r"^(death|sacrifice)/react-to/",)),
    overview("overview/tap-triggers", "Engines & triggers", "Tap-triggered effects", (r"^control/react-to/tap",)),
    overview("overview/graveyard-triggers", "Engines & triggers", "Graveyard-change triggers", (r"^graveyard/react-to/",)),
    overview("overview/discard-triggers", "Engines & triggers", "Discard-triggered effects", (r"^cards/react-to/discard",)),
    overview("overview/crime-triggers", "Engines & triggers", "Crime-triggered effects", (r"^actions/react-to/crime",)),
    overview("overview/timed-triggers", "Engines & triggers", "Upkeep & turn-step effects", (r"^timing/react-at/",)),
    overview("overview/removal", "Answers", "Targeted removal", (r"^removal/", r"^damage/deal/(any-target|creature)", r"^removal\b"), (r"^removal/(destroy|exile|return-to-hand)/all-", r"^board-wipe\b")),
    overview("overview/board-wipe", "Answers", "Board wipes", (r"^removal/(destroy|exile|return-to-hand)/all-", r"^board-wipe\b")),
    overview("overview/countermagic", "Answers", "Countermagic", (r"^countermagic/", r"^counterspell\b")),
    overview("overview/protection", "Answers", "Protection", (r"^protection/", r"^protection\b")),
    overview("overview/blink", "Answers", "Blink", (r"^blink/", r"^blink\b")),
    overview("overview/control", "Answers", "Tap, stun & restrict", (r"^control/(tap|stun|prevent-)",)),
    overview("overview/theft", "Answers", "Take control", (r"^control/steal/",)),
    overview("overview/opponent-life", "Pressure", "Direct opponent life loss", (r"^life/lose/opponent", r"^damage/deal/(opponent|player)")),
    overview("overview/combat-pressure", "Pressure", "Combat pressure & efficiency", (r"^combat/(have|grant)/",)),
)

CRITICAL_SINGLETON_PATTERN = re.compile(
    r"(^|/)(tutor|search|protection|countermagic|removal|board-wipe|reanimate|recursion|cast-without-paying|extra-turn|win)(/|$)",
    re.IGNORECASE,
)


class EvidenceError(RuntimeError):
    pass


def load_current_save(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8-sig") as source:
            payload = json.load(source)
    except (OSError, json.JSONDecodeError) as error:
        raise EvidenceError(f"Cannot read deck save: {path}") from error
    if (
        not isinstance(payload, dict)
        or payload.get("app") != APP_ID
        or payload.get("version") not in SUPPORTED_SAVE_VERSIONS
    ):
        raise EvidenceError(f"Not a current deck save: {path}")
    if not isinstance(payload.get("cards"), list):
        raise EvidenceError(f"Current deck save has no cards array: {path}")
    return payload


def source_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def text_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(str(item).strip() for item in value if str(item).strip()))


def card_name(card: dict[str, Any]) -> str:
    return str(card.get("name") or card.get("requestedName") or "Unnamed card").strip()


def type_parts(card: dict[str, Any]) -> tuple[str, str]:
    type_line = str(card.get("typeLine") or card.get("type_line") or "")
    front = re.split(r"\s+//\s+", type_line, maxsplit=1)[0]
    parts = re.split(r"\s+[—–-]\s+", front, maxsplit=1)
    return parts[0] if parts else "", parts[1] if len(parts) > 1 else ""


def type_tokens(text: str) -> list[str]:
    return re.findall(r"[A-Za-z0-9][A-Za-z0-9'.-]*", text)


def main_types(card: dict[str, Any]) -> set[str]:
    return {token.casefold() for token in type_tokens(type_parts(card)[0])}


def subtypes(card: dict[str, Any]) -> list[str]:
    found: dict[str, str] = {}
    for token in type_tokens(type_parts(card)[1]):
        found.setdefault(token.casefold(), token[0].upper() + token[1:] if token else token)
    return list(found.values())


def main_category(card: dict[str, Any]) -> str:
    types = main_types(card)
    if "land" in types:
        return "Lands"
    if "creature" in types:
        return "Creatures"
    if "planeswalker" in types:
        return "Planeswalkers"
    if "enchantment" in types:
        return "Enchantments"
    if "artifact" in types or "equipment" in {value.casefold() for value in subtypes(card)}:
        return "Artifacts"
    if "instant" in types:
        return "Instants"
    if "sorcery" in types:
        return "Sorcery"
    return "Others"


def mana_value(card: dict[str, Any]) -> int:
    manual = card.get("manualManaValue")
    if manual not in (None, ""):
        try:
            return max(0, int(float(manual)))
        except (TypeError, ValueError):
            pass
    try:
        return max(0, int(float(card.get("manaValue") or 0)))
    except (TypeError, ValueError):
        return 0


def app_stats(save: dict[str, Any]) -> dict[str, Any]:
    cards = [card for card in save["cards"] if isinstance(card, dict)]
    categories = Counter(main_category(card) for card in cards)
    lands = categories["Lands"]
    nonlands = [card for card in cards if main_category(card) != "Lands"]
    library = [card for card in cards if not card.get("isCommander")]
    library_lands = sum(main_category(card) == "Lands" for card in library)
    opening_size = min(7, len(library))
    opening_lands = f"{(opening_size * library_lands / len(library)) if library else 0:.2f}"
    average_mana = f"{(sum(mana_value(card) for card in nonlands) / len(nonlands)) if nonlands else 0:.2f}"
    maximum_mana = max((mana_value(card) for card in nonlands), default=0)
    mana_curve = {str(value): sum(mana_value(card) == value for card in nonlands) for value in range(maximum_mana + 1)}

    subtype_counts: Counter[tuple[str, str]] = Counter()
    for card in cards:
        parent = main_category(card)
        if parent == "Lands":
            continue
        for subtype in subtypes(card):
            subtype_counts[(parent, subtype)] += 1

    demand = Counter({color: 0 for color in COLOR_ORDER})
    sources = Counter({color: 0 for color in COLOR_ORDER})
    for card in cards:
        # LOCAL PATCH (MTG Deck Studio): a symbol can name more than one colour — hybrid {B/R},
        # monohybrid {2/W}, phyrexian {B/P}, hybrid phyrexian {W/U/P} — and each colour named is a
        # colour the deck must pay, so each counts. The pinned copy under source/ keeps the old
        # single-colour regex; this vendored copy is the Studio's.
        for symbol in re.findall(r"\{([^}]+)\}", str(card.get("manaCost") or "")):
            for part in symbol.upper().split("/"):
                if part in COLOR_ORDER:
                    demand[part] += 1
        for symbol in set(text_list(card.get("producedMana"))):
            if symbol in COLOR_ORDER:
                sources[symbol] += 1

    custom_labels = {
        str(item.get("id")): str(item.get("label") or item.get("name") or item.get("id"))
        for item in save.get("customStatsCategories") or []
        if isinstance(item, dict) and item.get("id")
    }
    custom_counts = Counter(
        category_id
        for card in cards
        for category_id in text_list(card.get("statsCategories"))
        if category_id in custom_labels
    )
    return {
        "cards": len(cards),
        "lands": lands,
        "nonlands": len(nonlands),
        "libraryCards": len(library),
        "libraryLands": library_lands,
        "averageLandsInOpeningHand": opening_lands,
        "averageManaValue": average_mana,
        "manaCurve": mana_curve,
        "types": [
            {"label": label, "count": categories[label]}
            for label in ("Creatures", "Planeswalkers", "Enchantments", "Artifacts", "Instants", "Sorcery", "Others", "Lands")
            if categories[label]
        ],
        "subtypes": [
            {"parent": parent, "label": label, "count": count}
            for (parent, label), count in sorted(
                subtype_counts.items(), key=lambda entry: (-entry[1], entry[0][0], entry[0][1].casefold())
            )
        ],
        "coloredPipDemand": {color: demand[color] for color in COLOR_ORDER},
        "coloredManaSources": {color: sources[color] for color in COLOR_ORDER},
        "customCategories": [
            {"id": category_id, "label": custom_labels[category_id], "count": custom_counts[category_id]}
            for category_id in custom_labels
            if custom_counts[category_id]
        ],
    }


def compact_card(card: dict[str, Any], *, include_oracle: bool) -> dict[str, Any]:
    result = {
        "name": card_name(card),
        "typeLine": str(card.get("typeLine") or ""),
        "manaValue": mana_value(card),
        "manaCost": str(card.get("manaCost") or ""),
        "effectiveBuckets": text_list(card.get("utilityBuckets")),
    }
    if include_oracle:
        result["oracleText"] = str(card.get("oracleText") or "")
    return result


def bucket_definitions(save: dict[str, Any]) -> dict[str, dict[str, Any]]:
    definitions: dict[str, dict[str, Any]] = {
        bucket_id: {"id": bucket_id, "label": label, "parents": [], "views": [], "coarse": True}
        for bucket_id, label in UTILITY_BUCKET_LABELS.items()
    }
    for item in save.get("customBuckets") or []:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        bucket_id = str(item["id"])
        definitions[bucket_id] = {
            "id": bucket_id,
            "label": str(item.get("label") or item.get("name") or bucket_id),
            "parents": [],
            "views": [],
            "groupId": str(item.get("groupId") or item.get("overviewGroupId") or ""),
            "custom": True,
        }
    for card in save["cards"]:
        if not isinstance(card, dict):
            continue
        for fact in card.get("autoBucketFacts") or []:
            bucket = fact.get("bucket") if isinstance(fact, dict) else None
            if not isinstance(bucket, dict) or not bucket.get("id"):
                continue
            bucket_id = str(bucket["id"])
            definitions[bucket_id] = {
                "id": bucket_id,
                "label": str(bucket.get("label") or bucket_id),
                "parents": text_list(bucket.get("parents")),
                "views": text_list(bucket.get("views")),
            }
    return definitions


def overview_ids(definition: dict[str, Any]) -> list[str]:
    assigned = str(definition.get("groupId") or "")
    if assigned and any(group["id"] == assigned for group in OVERVIEW_GROUPS):
        return [assigned]
    values = (str(definition.get("id") or "").casefold(), str(definition.get("label") or "").casefold())
    matches = []
    for group in OVERVIEW_GROUPS:
        included = any(pattern.search(value) for pattern in group["include"] for value in values)
        excluded = any(pattern.search(value) for pattern in group["exclude"] for value in values)
        if included and not excluded:
            matches.append(group["id"])
    return matches


def semantic_by_bucket(save: dict[str, Any]) -> dict[str, dict[str, set[str]]]:
    semantics: dict[str, dict[str, set[str]]] = defaultdict(
        lambda: {"actions": set(), "roles": set(), "objects": set()}
    )
    for card in save["cards"]:
        if not isinstance(card, dict):
            continue
        for fact in card.get("autoBucketFacts") or []:
            if not isinstance(fact, dict):
                continue
            bucket = fact.get("bucket") or {}
            bucket_id = str(bucket.get("id") or "")
            if not bucket_id:
                continue
            semantics[bucket_id]["actions"].add(str(fact.get("action") or ""))
            semantics[bucket_id]["roles"].add(str(fact.get("role") or ""))
            object_data = fact.get("object") or {}
            semantics[bucket_id]["objects"].update(
                value.casefold()
                for value in [object_data.get("kind"), object_data.get("name"), *(object_data.get("types") or [])]
                if isinstance(value, str) and value
            )
    return semantics


def relation(source: dict[str, set[str]], target: dict[str, set[str]]) -> str:
    source_actions = source["actions"]
    target_actions = target["actions"]
    target_reactions = {action for action in target_actions if action.startswith("react-to")}
    shared_objects = source["objects"] & target["objects"]
    if target_reactions and shared_objects:
        if "react-to-enter" in target_reactions and source_actions & {"create", "produce", "return", "reanimate"}:
            return "creates or moves an object that another bucket reacts to entering"
        if "react-to-die" in target_reactions and source_actions & {"sacrifice", "destroy", "force-sacrifice"}:
            return "causes an object to die that another bucket reacts to"
        if "react-to-sacrifice" in target_reactions and source_actions & {"sacrifice", "pay-with", "force-sacrifice"}:
            return "supplies a sacrifice event that another bucket reacts to"
        if "react-to-gain-life" in target_reactions and "gain-life" in source_actions:
            return "gains life that another bucket reacts to"
        if "react-to-add" in target_reactions and source_actions & {"add", "proliferate", "move", "double"}:
            return "adds counters that another bucket reacts to"
        if "react-to-discard" in target_reactions and "discard" in source_actions:
            return "causes a discard event that another bucket reacts to"
        if "react-to-tap" in target_reactions and source_actions & {"tap", "tap-as-cost"}:
            return "taps an object that another bucket reacts to"
        if target_reactions & {"react-to-damage", "react-to-combat-damage"} and "deal-damage" in source_actions:
            return "deals damage that another bucket reacts to"
        if "react-to" in target_reactions:
            return "creates an event that another bucket may react to"
    if source_actions & {"mill", "fill-graveyard", "discard"} and target_actions & {"return", "reanimate", "cast-from"}:
        return "loads the graveyard for a recursion effect"
    if source_actions & {"create", "produce"} and target_actions & {"sacrifice", "pay-with"} and shared_objects:
        return "creates a resource that another bucket spends"
    return ""


def build_bucket_evidence(save: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    cards = [card for card in save["cards"] if isinstance(card, dict)]
    definitions = bucket_definitions(save)
    members: dict[str, set[str]] = defaultdict(set)
    card_by_name = {card_name(card): card for card in cards}
    for card in cards:
        for bucket_id in text_list(card.get("utilityBuckets")):
            members[bucket_id].add(card_name(card))

    cards_by_overview: dict[str, set[str]] = defaultdict(set)
    for bucket_id, names in members.items():
        definition = definitions.get(bucket_id, {"id": bucket_id, "label": bucket_id})
        for group_id in overview_ids(definition):
            cards_by_overview[group_id].update(names)
    overview_rows = [
        {
            "id": group["id"],
            "cluster": group["cluster"],
            "label": group["label"],
            "count": len(cards_by_overview[group["id"]]),
        }
        for group in OVERVIEW_GROUPS
        if cards_by_overview[group["id"]]
    ]
    overview_rows.sort(key=lambda row: (-row["count"], row["label"].casefold()))

    granular_ids = {
        bucket_id
        for bucket_id, definition in definitions.items()
        if not definition.get("coarse") and members.get(bucket_id)
    }
    semantics = semantic_by_bucket(save)
    interactions: list[dict[str, Any]] = []
    for source_id in sorted(granular_ids):
        for target_id in sorted(granular_ids):
            if source_id == target_id:
                continue
            relationship = relation(semantics[source_id], semantics[target_id])
            if not relationship:
                continue
            interactions.append(
                {
                    "fromBucket": source_id,
                    "toBucket": target_id,
                    "relationship": relationship,
                    "fromCards": sorted(members[source_id], key=str.casefold)[:8],
                    "toCards": sorted(members[target_id], key=str.casefold)[:8],
                    "score": 4 + min(4, len(members[source_id]) + len(members[target_id])),
                }
            )
    interactions.sort(
        key=lambda edge: (-edge["score"], edge["fromBucket"].casefold(), edge["toBucket"].casefold())
    )
    interactions = interactions[:MAX_INTERACTION_CANDIDATES]
    connected = {edge[key] for edge in interactions for key in ("fromBucket", "toBucket")}
    commander_names = {
        card_name(card) for card in cards if card.get("isCommander") or card.get("isAlternateCommander")
    }

    detailed = []
    singleton_index = []
    for bucket_id in sorted(granular_ids, key=lambda value: (definitions[value]["label"].casefold(), value)):
        definition = definitions[bucket_id]
        names = sorted(members[bucket_id], key=str.casefold)
        row = {
            "id": bucket_id,
            "label": definition["label"],
            "parents": definition.get("parents", []),
            "views": definition.get("views", []),
            "count": len(names),
        }
        critical = (
            len(names) >= 2
            or bucket_id in connected
            or bool(commander_names & set(names))
            or bool(CRITICAL_SINGLETON_PATTERN.search(bucket_id))
        )
        if critical:
            detailed.append({**row, "cards": names})
        else:
            singleton_index.append(row)

    coarse = [
        {"id": bucket_id, "label": definitions[bucket_id]["label"], "count": len(names)}
        for bucket_id, names in members.items()
        if definitions.get(bucket_id, {}).get("coarse")
    ]
    coarse.sort(key=lambda row: (-row["count"], row["label"].casefold()))

    centrality = Counter()
    reasons: dict[str, set[str]] = defaultdict(set)
    for card in cards:
        name = card_name(card)
        assigned = [bucket_id for bucket_id in text_list(card.get("utilityBuckets")) if bucket_id in granular_ids]
        if len(assigned) > 1:
            centrality[name] += len(assigned) * 2
            reasons[name].add(f"connects {len(assigned)} granular buckets")
        if card.get("isCommander"):
            centrality[name] += 20
            reasons[name].add("active commander")
        if card.get("isAlternateCommander"):
            centrality[name] += 12
            reasons[name].add("alternate commander")
    for edge in interactions:
        for name in edge["fromCards"] + edge["toCards"]:
            centrality[name] += edge["score"]
            reasons[name].add(edge["relationship"])
    key_candidates = [
        {"name": name, "score": score, "reasons": sorted(reasons[name])[:3]}
        for name, score in centrality.most_common(MAX_KEY_CANDIDATES)
        if name in card_by_name
    ]

    return (
        {"overview": overview_rows, "coarse": coarse, "detailed": detailed, "singletonIndex": singleton_index},
        interactions,
        key_candidates,
    )


def safe_output_name(title: str, source: Path) -> str:
    base = title.strip() or source.stem
    base = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", base)
    base = re.sub(r"\s+", " ", base).strip(" .")[:120] or "deck"
    return f"{base}_strategy_note.md"


REQUIRED_NOTE_HEADINGS = (
    r"^## Theme & Story\s*$",
    r"^## Deck Snapshot\s*$",
    r"^## Mechanical Identity\s*$",
    r"^## Game Plan\s*$",
    r"^## Key Cards\s*$",
    r"^## Synergies & Engines\s*$",
    r"^## Combos & Loops\s*$",
    r"^## How to Pilot\s*$",
    r"^### Opening Hand & Mulligans\s*$",
    r"^### Early Game\s*$",
    r"^### Midgame\s*$",
    r"^### Late Game\s*$",
    r"^## What to Protect\s*$",
    r"^## Weaknesses & Recovery\s*$",
    r"^## Alternate Commanders\s*$",
)


def validate_strategy_draft(text: str) -> None:
    if not re.search(r"^#\s+\S", text, re.MULTILINE):
        raise EvidenceError("Strategy draft must start with a deck-title heading")
    missing = [pattern for pattern in REQUIRED_NOTE_HEADINGS if not re.search(pattern, text, re.MULTILINE)]
    if missing:
        raise EvidenceError("Strategy draft is missing required headings")
    word_count = len(re.findall(r"\b[\w'+/-]+\b", text, re.UNICODE))
    if word_count < MIN_STRATEGY_WORDS or word_count > MAX_STRATEGY_WORDS:
        raise EvidenceError(
            f"Strategy draft must contain {MIN_STRATEGY_WORDS}-{MAX_STRATEGY_WORDS} words; found {word_count}"
        )


def generated_metadata(path: Path) -> dict[str, Any] | None:
    try:
        with path.open("r", encoding="utf-8-sig") as generated:
            first_line = generated.readline().strip()
    except OSError:
        return None
    match = re.fullmatch(r"<!-- mtg-deck-strategy-notes (\{.*\}) -->", first_line)
    if not match:
        return None
    try:
        metadata = json.loads(match.group(1))
    except json.JSONDecodeError:
        return None
    return metadata if isinstance(metadata, dict) else None


def output_for_session(intended: Path, source: Path) -> Path:
    if not intended.exists():
        return intended
    metadata = generated_metadata(intended)
    if metadata and metadata.get("sourceFile") == source.name:
        return intended

    suffix = "_strategy_note.md"
    title_base = intended.name[: -len(suffix)] if intended.name.endswith(suffix) else intended.stem
    source_component = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", source.stem).strip(" .") or "source"
    candidate = intended.with_name(f"{title_base}_{source_component}{suffix}")
    index = 2
    while candidate.exists():
        metadata = generated_metadata(candidate)
        if metadata and metadata.get("sourceFile") == source.name:
            return candidate
        candidate = intended.with_name(f"{title_base}_{source_component}_{index}{suffix}")
        index += 1
    return candidate


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def initial_aggregate(save: dict[str, Any], source: Path, job_id: str, digest: str) -> dict[str, Any]:
    cards = [card for card in save["cards"] if isinstance(card, dict)]
    bucket_data, interactions, key_candidates = build_bucket_evidence(save)
    note = str(save.get("strategyNotes") or "")
    truncated = len(note) > MAX_MANUAL_NOTE_CHARS
    title = str(save.get("deckTitle") or "").strip()
    return {
        "schema": VISIBLE_AGGREGATE_SCHEMA,
        "jobId": job_id,
        "sourceHash": digest,
        "deck": {"title": title or source.stem, "cardCount": len(cards)},
        "manualStrategyNote": note[:MAX_MANUAL_NOTE_CHARS],
        "manualStrategyNoteTruncated": truncated,
        "commanders": [compact_card(card, include_oracle=True) for card in cards if card.get("isCommander")],
        "alternateCommanders": [
            compact_card(card, include_oracle=True) for card in cards if card.get("isAlternateCommander")
        ],
        "stats": app_stats(save),
        "bucketOverview": bucket_data["overview"],
        "coarseBuckets": bucket_data["coarse"],
        "granularBuckets": {
            "detailThreshold": 2,
            "detailed": bucket_data["detailed"],
            "singletonIndex": bucket_data["singletonIndex"],
        },
        "interactionCandidates": interactions,
        "keyCardCandidates": key_candidates,
        "queryLimits": {"rounds": 2, "cardsPerRound": 12},
    }


def walk_json_files(directory: Path, recursive: bool) -> Iterable[Path]:
    if recursive:
        for root, directory_names, file_names in os.walk(directory):
            directory_names[:] = [
                name for name in directory_names if name.casefold() not in EXCLUDED_DIRECTORY_NAMES
            ]
            for name in file_names:
                if name.casefold().endswith(".json"):
                    yield Path(root) / name
        return
    for candidate in directory.iterdir():
        if candidate.is_file() and candidate.suffix.casefold() == ".json":
            yield candidate


def discover_sources(inputs: list[Path], recursive: bool) -> list[Path]:
    candidates: list[Path] = []
    for supplied in inputs:
        resolved = supplied.expanduser().resolve()
        if resolved.is_file():
            candidates.append(resolved)
        elif resolved.is_dir() and resolved.name.casefold() not in EXCLUDED_DIRECTORY_NAMES:
            candidates.extend(walk_json_files(resolved, recursive))
        else:
            raise EvidenceError(f"Input does not exist or is excluded: {supplied}")

    valid: dict[str, Path] = {}
    for candidate in candidates:
        if any(part.casefold() in EXCLUDED_DIRECTORY_NAMES for part in candidate.parts):
            continue
        try:
            load_current_save(candidate)
        except EvidenceError:
            continue
        valid[str(candidate.resolve()).casefold()] = candidate.resolve()
    return sorted(valid.values(), key=lambda path: str(path).casefold())


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def read_json_object(path: Path, description: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as error:
        raise EvidenceError(f"Cannot read {description}: {path}") from error
    if not isinstance(payload, dict):
        raise EvidenceError(f"{description.capitalize()} must be a JSON object: {path}")
    return payload


def sanitized_fact(fact: dict[str, Any]) -> dict[str, Any]:
    bucket = fact.get("bucket") or {}
    object_data = fact.get("object") or {}
    evidence = fact.get("evidence") or {}
    return {
        "role": str(fact.get("role") or ""),
        "action": str(fact.get("action") or ""),
        "actor": str(fact.get("actor") or ""),
        "object": {
            "kind": str(object_data.get("kind") or ""),
            "name": object_data.get("name"),
            "types": text_list(object_data.get("types")),
        },
        "scope": str(fact.get("scope") or ""),
        "qualifiers": fact.get("qualifiers") if isinstance(fact.get("qualifiers"), dict) else {},
        "bucket": {
            "id": str(bucket.get("id") or ""),
            "label": str(bucket.get("label") or bucket.get("id") or ""),
            "parents": text_list(bucket.get("parents")),
            "views": text_list(bucket.get("views")),
        },
        "evidenceClause": str(evidence.get("clause") or ""),
    }


def query_card(card: dict[str, Any]) -> dict[str, Any]:
    effective = set(text_list(card.get("utilityBuckets")))
    result = compact_card(card, include_oracle=True)
    result["facts"] = [
        sanitized_fact(fact)
        for fact in card.get("autoBucketFacts") or []
        if isinstance(fact, dict) and str((fact.get("bucket") or {}).get("id") or "") in effective
    ]
    return result


def command_discover(args: argparse.Namespace) -> dict[str, Any]:
    sources = discover_sources([Path(value) for value in args.inputs], args.recursive)
    manifest = Path(args.manifest).expanduser().resolve()
    write_json(manifest, {"jobs": [{"source": str(source)} for source in sources]})
    return {"status": "ok", "jobCount": len(sources), "manifest": str(manifest)}


def command_prepare(args: argparse.Namespace) -> dict[str, Any]:
    source = Path(args.source).expanduser().resolve()
    save = load_current_save(source)
    digest = source_hash(source)
    job_id = uuid.uuid4().hex
    session_directory = Path(args.session_root).expanduser().resolve() / job_id
    aggregate_path = session_directory / "aggregate.json"
    session_path = session_directory / ".session.json"
    title = str(save.get("deckTitle") or "").strip()
    output_path = source.parent / safe_output_name(title, source)
    aggregate = initial_aggregate(save, source, job_id, digest)
    rendered = json.dumps(aggregate, ensure_ascii=False, separators=(",", ":")) + "\n"
    if len(rendered) > args.max_initial_chars:
        raise EvidenceError(
            f"Compact aggregate exceeds {args.max_initial_chars} characters; refine deterministic thresholds"
        )
    write_text(aggregate_path, rendered)
    write_json(
        session_path,
        {
            "schema": SESSION_SCHEMA,
            "jobId": job_id,
            "source": str(source),
            "sourceHash": digest,
            "output": str(output_path),
            "queryRoundsUsed": 0,
            "maxQueryRounds": 2,
            "maxCardsPerRound": 12,
        },
    )
    return {
        "status": "ok",
        "jobId": job_id,
        "aggregate": str(aggregate_path),
        "output": str(output_path),
        "aggregateChars": len(rendered),
    }


def command_query(args: argparse.Namespace) -> dict[str, Any]:
    session_directory = Path(args.session).expanduser().resolve()
    session_path = session_directory / ".session.json"
    session = read_json_object(session_path, "deck evidence session")
    if session.get("schema") != SESSION_SCHEMA:
        raise EvidenceError(f"Unsupported deck evidence session: {session_directory}")
    used = int(session.get("queryRoundsUsed") or 0)
    maximum = int(session.get("maxQueryRounds") or 0)
    if used >= maximum:
        raise EvidenceError(f"Deck evidence query-round budget exhausted for job {session.get('jobId')}")

    request = read_json_object(Path(args.request).expanduser().resolve(), "query request")
    requested_buckets = text_list(request.get("buckets"))
    requested_cards = text_list(request.get("cards"))
    requested_intersections = request.get("intersections") or []
    if len(requested_buckets) > MAX_BUCKETS_PER_QUERY:
        raise EvidenceError(f"A query round may inspect at most {MAX_BUCKETS_PER_QUERY} buckets")
    if len(requested_cards) > int(session.get("maxCardsPerRound") or 0):
        raise EvidenceError(f"A query round may inspect at most {session.get('maxCardsPerRound')} cards")
    if not isinstance(requested_intersections, list) or len(requested_intersections) > MAX_INTERSECTIONS_PER_QUERY:
        raise EvidenceError(f"A query round may inspect at most {MAX_INTERSECTIONS_PER_QUERY} intersections")
    intersection_pairs: list[tuple[str, str]] = []
    for value in requested_intersections:
        if not isinstance(value, list) or len(value) != 2 or not all(isinstance(item, str) for item in value):
            raise EvidenceError("Each intersection must contain exactly two bucket IDs")
        intersection_pairs.append((value[0], value[1]))

    source = Path(str(session.get("source") or ""))
    if not source.is_file() or source_hash(source) != session.get("sourceHash"):
        raise EvidenceError("The deck source changed after evidence preparation")
    save = load_current_save(source)
    cards = [card for card in save["cards"] if isinstance(card, dict)]
    definitions = bucket_definitions(save)
    members: dict[str, set[str]] = defaultdict(set)
    for card in cards:
        for bucket_id in text_list(card.get("utilityBuckets")):
            members[bucket_id].add(card_name(card))
    cards_by_folded_name = {card_name(card).casefold(): card for card in cards}

    bucket_results = []
    for bucket_id in requested_buckets:
        definition = definitions.get(bucket_id, {"id": bucket_id, "label": bucket_id})
        names = sorted(members.get(bucket_id, set()), key=str.casefold)
        bucket_results.append(
            {"id": bucket_id, "label": str(definition.get("label") or bucket_id), "count": len(names), "cards": names}
        )
    intersection_results = []
    for left, right in intersection_pairs:
        names = sorted(members.get(left, set()) & members.get(right, set()), key=str.casefold)
        intersection_results.append({"left": left, "right": right, "count": len(names), "cards": names})

    found_cards = []
    missing_cards = []
    seen_names: set[str] = set()
    for requested_name in requested_cards:
        folded = requested_name.casefold()
        if folded in seen_names:
            continue
        seen_names.add(folded)
        found = cards_by_folded_name.get(folded)
        if found is None:
            missing_cards.append(requested_name)
        else:
            found_cards.append(query_card(found))

    response = {
        "schema": "mtg-deck-strategy-query/1",
        "jobId": session["jobId"],
        "round": used + 1,
        "buckets": bucket_results,
        "intersections": intersection_results,
        "cards": found_cards,
        "missingCards": missing_cards,
    }
    rendered = json.dumps(response, ensure_ascii=False, indent=2) + "\n"
    if len(rendered) > MAX_QUERY_RESPONSE_CHARS:
        raise EvidenceError(f"Sanitized query response exceeds {MAX_QUERY_RESPONSE_CHARS} characters")
    response_path = Path(args.response).expanduser().resolve()
    write_json(response_path, response)
    session["queryRoundsUsed"] = used + 1
    write_json(session_path, session)
    return {
        "status": "ok",
        "jobId": session["jobId"],
        "round": used + 1,
        "response": str(response_path),
        "responseChars": len(rendered),
    }


def command_write_note(args: argparse.Namespace) -> dict[str, Any]:
    session_directory = Path(args.session).expanduser().resolve()
    session = read_json_object(session_directory / ".session.json", "deck evidence session")
    if session.get("schema") != SESSION_SCHEMA:
        raise EvidenceError(f"Unsupported deck evidence session: {session_directory}")
    source = Path(str(session.get("source") or ""))
    if not source.is_file() or source_hash(source) != session.get("sourceHash"):
        raise EvidenceError("The deck source changed after evidence preparation")
    draft_path = Path(args.draft).expanduser().resolve()
    try:
        draft = draft_path.read_text(encoding="utf-8-sig").strip() + "\n"
    except OSError as error:
        raise EvidenceError(f"Cannot read strategy draft: {draft_path}") from error
    validate_strategy_draft(draft)
    intended = Path(str(session.get("output") or ""))
    output = output_for_session(intended, source)
    marker = {
        "sourceFile": source.name,
        "sourceHash": session["sourceHash"],
        "evidenceSchema": VISIBLE_AGGREGATE_SCHEMA,
    }
    content = f"<!-- mtg-deck-strategy-notes {json.dumps(marker, ensure_ascii=False, separators=(',', ':'))} -->\n\n{draft}"
    write_text(output, content)
    return {
        "status": "ok",
        "jobId": session["jobId"],
        "output": str(output),
        "wordCount": len(re.findall(r"\b[\w'+/-]+\b", draft, re.UNICODE)),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    discover = subparsers.add_parser("discover", help="Resolve current deck saves")
    discover.add_argument("inputs", nargs="+")
    discover.add_argument("--recursive", action="store_true")
    discover.add_argument("--manifest", required=True)
    discover.set_defaults(handler=command_discover)
    prepare = subparsers.add_parser("prepare", help="Create one bounded deck evidence session")
    prepare.add_argument("source")
    prepare.add_argument("--session-root", required=True)
    prepare.add_argument("--max-initial-chars", type=int, default=DEFAULT_MAX_INITIAL_CHARS)
    prepare.set_defaults(handler=command_prepare)
    query = subparsers.add_parser("query", help="Run one bounded query against one evidence session")
    query.add_argument("session")
    query.add_argument("--request", required=True)
    query.add_argument("--response", required=True)
    query.set_defaults(handler=command_query)
    write_note = subparsers.add_parser("write-note", help="Validate and atomically publish one strategy note")
    write_note.add_argument("session")
    write_note.add_argument("--draft", required=True)
    write_note.set_defaults(handler=command_write_note)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        result = args.handler(args)
    except EvidenceError as error:
        print(json.dumps({"status": "error", "error": str(error)}), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
