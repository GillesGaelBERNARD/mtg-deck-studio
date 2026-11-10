"""Scryfall alternatives: query building, constraint checks and a polite client.

build_query and check are pure. Hard constraints are both expressed in the query
and re-verified on every returned card, so a mistranslated query cannot put a card
that violates them in front of the player.
"""
import json
import threading
import time
import urllib.error
import re
import urllib.parse
import urllib.request

from .errors import StudioError

API = "https://api.scryfall.com/cards/search"
NAMED = "https://api.scryfall.com/cards/named"
CARDS = "https://api.scryfall.com/cards"
USER_AGENT = "hermes-mtg-deck-studio/0.1"
MIN_SPACING_S = 0.12
MAX_CANDIDATES = 8
COLOR_ORDER = "WUBRG"
TYPES = ("artifact", "creature", "enchantment", "instant", "land", "planeswalker", "sorcery", "battle")


def deck_identity(document: dict) -> list:
    colors = set()
    for card in document.get("cards") or []:
        if card.get("isCommander"):
            colors.update(card.get("colorIdentity") or [])
    return [c for c in COLOR_ORDER if c in colors]


def _quote(text: str) -> str:
    return '"' + text.replace('"', "") + '"'


def build_query(original: dict | None, constraints: dict, identity: list) -> tuple[str, list]:
    """Return the Scryfall query and the constraints it could not express."""
    parts, unexpressed = [], []
    parts.append(f"id<={''.join(identity).lower() or 'c'}")
    parts += ["f:commander", "game:paper", "-is:digital"]
    if original:
        parts.append(f"-!{_quote(original['name'])}")
    for name in constraints.get("exclude_names") or []:
        parts.append(f"-!{_quote(name)}")
    if constraints.get("max_mv") is not None:
        parts.append(f"mv<={int(constraints['max_mv'])}")
    if constraints.get("min_mv") is not None:
        parts.append(f"mv>={int(constraints['min_mv'])}")
    for type_name in constraints.get("types") or []:
        parts.append(f"t:{type_name.lower()}" if type_name.lower() in TYPES else f"t:{_quote(type_name.lower())}")
    for code in constraints.get("sets") or []:
        code = str(code).strip().lower()
        if code:
            parts.append(f"s:{code}")
    for type_name in constraints.get("not_types") or []:
        parts.append(f"-t:{type_name.lower()}")
    for phrase in constraints.get("oracle") or []:
        parts.append(f"o:{_quote(phrase)}")
    for tag in constraints.get("function_tags") or []:
        parts.append(f"otag:{tag.lower().replace(' ', '-')}")
    if constraints.get("max_usd") is not None:
        parts.append(f"usd<={float(constraints['max_usd']):g}")
    if constraints.get("extra"):
        parts.append(f"({constraints['extra']})")
    for key in constraints.get("soft") or []:
        unexpressed.append(key)
    return " ".join(parts), unexpressed


def _faces(card: dict) -> list:
    return card.get("card_faces") or [card]


def oracle_text(card: dict) -> str:
    return "\n//\n".join(face.get("oracle_text") or "" for face in _faces(card))


def type_line(card: dict) -> str:
    return card.get("type_line") or " // ".join(face.get("type_line") or "" for face in _faces(card))


def check(card: dict, constraints: dict, identity: list) -> dict:
    satisfied, violated = [], []

    def verdict(name, ok):
        (satisfied if ok else violated).append(name)

    verdict("color_identity", set(card.get("color_identity") or []) <= set(identity))
    verdict("commander_legal", (card.get("legalities") or {}).get("commander") == "legal")
    mv = card.get("cmc")
    if constraints.get("max_mv") is not None:
        verdict(f"mv<={constraints['max_mv']}", mv is not None and mv <= constraints["max_mv"])
    if constraints.get("min_mv") is not None:
        verdict(f"mv>={constraints['min_mv']}", mv is not None and mv >= constraints["min_mv"])
    types = type_line(card).lower()
    for type_name in constraints.get("types") or []:
        verdict(f"type:{type_name}", type_name.lower() in types)
    for code in constraints.get("sets") or []:
        code = str(code).strip().lower()
        if code:
            verdict(f"set:{code}", (card.get("set") or "").lower() == code)
    for type_name in constraints.get("not_types") or []:
        verdict(f"not type:{type_name}", type_name.lower() not in types)
    text = oracle_text(card).lower().replace(card.get("name", "").lower(), "~")
    for phrase in constraints.get("oracle") or []:
        verdict(f"text:{phrase}", phrase.lower() in text or phrase.lower() in oracle_text(card).lower())
    if constraints.get("max_usd") is not None:
        price = (card.get("prices") or {}).get("usd")
        verdict(f"usd<={constraints['max_usd']}", price is not None and float(price) <= float(constraints["max_usd"]))
    return {"satisfied": satisfied, "violated": violated}


def front_name(card: dict) -> str:
    return _faces(card)[0].get("name") or card.get("name")


def image_uri(card: dict) -> str | None:
    uris = card.get("image_uris") or _faces(card)[0].get("image_uris") or {}
    return uris.get("normal") or uris.get("large")


def compact(card: dict, verdict: dict, in_deck: bool) -> dict:
    return {
        "name": card.get("name"),
        "front_name": front_name(card),
        "scryfall_id": card.get("id"),
        "oracle_id": card.get("oracle_id"),
        "mana_cost": card.get("mana_cost") or " // ".join(f.get("mana_cost") or "" for f in _faces(card)),
        "mv": card.get("cmc"),
        "type_line": type_line(card),
        "oracle_text": oracle_text(card),
        "usd": (card.get("prices") or {}).get("usd"),
        "edhrec_rank": card.get("edhrec_rank"),
        "image": image_uri(card),
        "scryfall_uri": card.get("scryfall_uri"),
        "in_deck": in_deck,
        "constraints": verdict,
    }


class Client:
    def __init__(self, opener=None):
        self._opener = opener or urllib.request.urlopen
        self._lock = threading.Lock()
        self._last = 0.0

    def get(self, url: str) -> dict:
        for attempt in range(2):
            with self._lock:
                wait = self._last + MIN_SPACING_S - time.monotonic()
                if wait > 0:
                    time.sleep(wait)
                self._last = time.monotonic()
            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
            try:
                with self._opener(request, timeout=20) as response:
                    return json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                if exc.code == 404:
                    return {"data": [], "total_cards": 0}
                if exc.code == 429 and attempt == 0:
                    time.sleep(30)
                    continue
                body = exc.read().decode("utf-8", "replace")
                try:
                    detail = json.loads(body).get("details") or body
                except ValueError:
                    detail = body
                raise StudioError("scryfall_error", f"Scryfall {exc.code}: {detail[:300]}")
            except (urllib.error.URLError, TimeoutError) as exc:
                raise StudioError("scryfall_unreachable", f"Scryfall is unreachable: {exc}")
        raise StudioError("scryfall_error", "Scryfall rate limit persisted.")

    def search(self, query: str, order: str = "edhrec") -> dict:
        params = urllib.parse.urlencode({"q": query, "order": order, "unique": "cards"})
        return self.get(f"{API}?{params}")


CLIENT = Client()


PRINTED_LANGUAGES = ("fr", "de", "es", "it", "pt", "ja", "ko", "ru", "zhs", "zht")


def _by_printed_name(text: str, client: Client) -> dict | None:
    """Find a card by the name printed on a non-English card ("Voltigeur chargé")."""
    if not text:
        return None
    for language in PRINTED_LANGUAGES:
        # `lang:fr` belongs inside the query, and `!"name"` is Scryfall's exact-name match.
        query = urllib.parse.quote(f'!"{text}" lang:{language}')
        url = f"{API}?q={query}&unique=prints"
        try:
            payload = client.get(url)
        except StudioError:
            continue
        for card in payload.get("data") or []:
            printed = str(card.get("printed_name") or "")
            if printed and printed.casefold() == text.casefold():
                return card
    return None


def named_card(name: str, client: Client = None) -> dict:
    """One card by name, English or as printed in another language."""
    client = client or CLIENT
    text = (name or "").strip()
    if not text:
        raise StudioError("invalid", "A card name is required.")
    quoted = urllib.parse.quote(text)
    # The client returns an empty envelope for 404 instead of raising, so check the object type.
    payload = client.get(f"{NAMED}?exact={quoted}")
    if payload.get("object") != "card":
        payload = client.get(f"{NAMED}?fuzzy={quoted}")
    if payload.get("object") != "card":
        found = _by_printed_name(text, client)
        if not found:
            raise StudioError("not_found", f"No card named '{text}' (tried English and printed names).")
        payload = found
    return compact(payload, {"satisfied": [], "violated": []}, False)


_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_PRINTING = re.compile(r"^(?P<name>.+?)\s*\((?P<set>[A-Za-z0-9]{2,6})\)\s*(?P<number>[A-Za-z0-9\u2605-]+)$")


def resolve_ref(ref: str, client: Client = None) -> dict:
    """One printing from "Name (SET) 123", a Scryfall id, or a bare card name.

    The same identity the Add Cards box takes, so a photo the agent identified, a
    tool argument and the desktop's own box all land on the same printing. Returns
    the raw Scryfall card object.
    """
    client = client or CLIENT
    text = (ref or "").strip()
    if not text:
        raise StudioError("invalid", "Give a card name, a printing such as 'Sol Ring (CMM) 410', or a Scryfall id.")
    payload = None
    if _UUID.match(text):
        payload = client.get(f"{CARDS}/{urllib.parse.quote(text)}")
    else:
        printing = _PRINTING.match(text)
        if printing:
            payload = client.get(
                f"{CARDS}/{urllib.parse.quote(printing.group('set').lower())}/{urllib.parse.quote(printing.group('number'))}"
            )
    if not (isinstance(payload, dict) and payload.get("object") == "card"):
        named = named_card(text, client)  # resolves bare and printed-language names, or raises not_found
        payload = client.get(f"{CARDS}/{urllib.parse.quote(named['scryfall_id'])}")
    if not isinstance(payload, dict) or payload.get("object") != "card":
        raise StudioError("not_found", f"No printing matches '{text}'.")
    return payload


def printing(card: dict) -> dict:
    """The fields the sideboard keeps for a card: everything a row shows, plus its identity."""
    return {
        "name": card.get("name"),
        "front_name": front_name(card),
        "printed_name": card.get("printed_name"),
        "lang": card.get("lang"),
        "scryfall_id": card.get("id"),
        "mana_cost": card.get("mana_cost") or " // ".join(face.get("mana_cost") or "" for face in _faces(card)),
        "mv": card.get("cmc"),
        "type_line": type_line(card),
        "oracle_text": oracle_text(card),
        "image": image_uri(card),
        "scryfall_uri": card.get("scryfall_uri"),
        "set": card.get("set"),
        "set_name": card.get("set_name"),
        "collector_number": card.get("collector_number"),
    }


def find_alternatives(document: dict, original: dict | None, constraints: dict, client: Client = None) -> dict:
    client = client or CLIENT
    identity = deck_identity(document)
    query, unexpressed = build_query(original, constraints, identity)
    payload = client.search(query, order=constraints.get("order") or "edhrec")
    deck_names = {(card.get("name") or "").casefold() for card in document.get("cards") or []}
    seen, candidates, already = set(), [], []
    for card in payload.get("data") or []:
        if card.get("oracle_id") in seen:
            continue
        seen.add(card.get("oracle_id"))
        verdict = check(card, constraints, identity)
        if verdict["violated"]:
            continue
        in_deck = card.get("name", "").casefold() in deck_names or front_name(card).casefold() in deck_names
        (already if in_deck else candidates).append(compact(card, verdict, in_deck))
        if len(candidates) >= MAX_CANDIDATES:
            break
    return {
        "query": query,
        "total_matches": payload.get("total_cards", 0),
        "warnings": payload.get("warnings") or [],
        "unexpressed": unexpressed,
        "candidates": candidates,
        "already_in_deck": [c["name"] for c in already],
    }
