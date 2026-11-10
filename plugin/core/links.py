"""Card-to-card links: who feeds a card's triggers, and what feeds off its effects.

The classifier already records clause-level facts per card:

    {"role": "trigger", "action": "react-to-enter", "object": {"types": ["land"]},
     "evidence": {"clause": "Whenever a land you control enters"}}
    {"role": "cost", "action": "sacrifice", "object": {"types": ["land"]},
     "evidence": {"clause": "{1}, {T}, Sacrifice a land"}}

A link is a join: card A *causes* an event for which card B has a *reaction*. Direction is
kept because the two questions a player asks are different:

    fed_by  - these cards do something this card reacts to
    feeds   - these cards react to something this card does

Links are derived, never stored in the deck document, and every one carries the clauses that
justified it so the agent can explain the relation instead of asserting it.
"""
from __future__ import annotations

import re
from collections import defaultdict

LINKS_RULE_VERSION = "links-11"

# reaction action -> event name
EVENT_OF_TRIGGER = {
    "react-to-enter": "enter",
    "react-to-die": "die",
    "react-to-sacrifice": "sacrifice",
    "react-to-cast": "cast",
    "react-to-attack": "attack",
    "react-to-block": "block",
    "react-to-combat-damage": "combat-damage",
    "react-to-damage": "damage",
    "react-to-tap": "tap",
    "react-to-add": "counter",
    "react-to-gain-life": "life-gain",
    "react-to-discard": "discard",
    "react-to-graveyard-enter": "graveyard-enter",
    "react-to-graveyard-leave": "graveyard-leave",
    "react-to-crime": "crime",
    "react-to-leave": "leave",
    "react-to-warp": "warp",
    "react-to-create-token": "token-create",
    "react-to-draw": "draw",
    "react-to-untap": "untap",
    "react-to-life-loss": "life-loss",
}

# "Whenever ..." facts whose action stayed generic still name their event in the clause.
GENERIC_TRIGGER_EVENTS = (
    (r"\bwhenever you draw\b|\bwhenever (?:a|an|one or more) (?:cards?|card) .{0,20}draw\b"
     r"|\bwhenever an opponent draws?\b", "draw"),
    (r"\bwhenever .{0,40}becomes? untapped\b", "untap"),
    (r"\bwhenever .{0,40}becomes? tapped\b", "tap"),
    (r"\bwhenever .{0,40}is put into (?:a|your|their) graveyard\b", "graveyard-enter"),
    (r"\bwhenever .{0,40}(?:dies|die)\b", "die"),
    (r"\bwhenever .{0,40}\benters\b", "enter"),
    (r"\bwhenever .{0,40}attacks\b", "attack"),
    (r"\bwhenever you gain life\b", "life-gain"),
    (r"\bwhenever .{0,40}loses? life\b", "life-loss"),
    (r"\bwhenever .{0,40}cast\b", "cast"),
)

# effect/cost action -> events it causes. Only what the fact actually says: "destroy" kills,
# "deal-damage" does not (it may or may not be lethal), so damage never implies death here.
CAUSE_EVENTS = {
    "create": ("enter", "token-create"),
    "copy": ("enter",),
    "enter-with": ("enter",),
    "return": ("enter", "graveyard-leave", "leave"),
    "sacrifice": ("sacrifice", "leave"),
    "destroy": ("die", "leave"),
    "exile": ("die", "leave"),
    "deal-damage": ("damage",),
    "debuff": ("damage",),
    "cast-from": ("cast", "graveyard-leave"),
    "draw": ("draw",),
    "gain-life": ("life-gain",),
    "tap": ("tap",),
    "add": ("counter",),
    "proliferate": ("counter",),
    "discard": ("discard", "graveyard-enter"),
    "warp": ("warp",),
    # Returning a permanent to a hand takes it off the battlefield without killing it.
    "return-to-hand": ("leave",),
    # Reanimation is an entrance (and a graveyard exit).
    "reanimate": ("enter", "graveyard-leave"),
    "mill": ("graveyard-enter",),
    "fill-graveyard": ("graveyard-enter",),
    "cast-without-paying": ("cast",),
    "untap": ("untap",),
    "lose-life": ("life-loss",),
    "gain-control": ("gain-control",),
    # the same effect under its older name, for any deck that has not been reclassified yet
    "steal": ("gain-control",),
    "grant": ("grant",),
}

# Keywords that let a creature connect: a saboteur trigger ("deals combat damage to a player") is
# fed by whoever grants one of these, which is exactly how a theft deck turns its stolen creature
# into a draw engine.
COMBAT_HELPERS = ("haste", "flying", "menace", "fear", "intimidate", "shadow", "skulk", "trample",
                  "unblockable", "can't be blocked", "first strike", "double strike", "deathtouch")

# "cast a blue spell", "cast an instant or sorcery": a type or colour the cast must have.
SPell_RESTRICTION = re.compile(
    r"\b(?:cast|casts?)\s+(?:a|an|one or more)\s+(?:(?:blue|white|black|red|green|colorless|noncreature|"
    r"historic|outlaw|snow|multicolored|artifact|creature|enchantment|instant|sorcery|planeswalker|"
    r"legendary|nonland|permanent)\b\s*(?:or\s+[a-z]+\s*)?)+spell",
    re.I)

PERMANENT_TYPES = ("creature", "artifact", "enchantment", "land", "planeswalker", "battle", "token")
# Everything a "nonland permanent" can be. A bare "permanent" cause is not proof of one.
NONLAND_PERMANENT_TYPES = ("creature", "artifact", "enchantment", "planeswalker", "battle", "token")

# reactions whose subject is the card itself never link to another card
SELF_TRIGGER = re.compile(
    # "this creature or another Pirate you control enters" watches the tribe, not only itself.
    r"^when(?:ever)?\s+(?!this\s+creature\s+or\s+another)(this|~|it\b|enchanted|equipped)"
    r"|^when\s+you\s+cast\s+this\s+spell"
    r"|^when(?:ever)?\s+this\s+spell\s+is\s+cast",
    re.I,
)

EVENT_LABELS = {
    "enter": "entering the battlefield",
    "die": "dying",
    "sacrifice": "being sacrificed",
    "cast": "cast",
    "attack": "attacking",
    "block": "blocking",
    "combat-damage": "dealing combat damage",
    "damage": "being dealt damage",
    "tap": "becoming tapped",
    "counter": "getting counters",
    "life-gain": "gaining you life",
    "discard": "being discarded",
    "graveyard-enter": "reaching the graveyard",
    "graveyard-leave": "leaving the graveyard",
    "crime": "committing a crime",
    "leave": "leaving the battlefield",
    "warp": "being warped",
    "untap": "becoming untapped",
    "life-loss": "losing life",
    "token-create": "being created",
    "draw": "drawn",
    "grant": "granted",
    "gain-control": "taken from an opponent",
}


def _types(fact: dict) -> set:
    return {str(t) for t in ((fact.get("object") or {}).get("types") or []) if isinstance(t, str)}


def _clause(fact: dict) -> str:
    return str((fact.get("evidence") or {}).get("clause") or "").strip()


def _own_names(card: dict) -> list[str]:
    """The short names a clause can use for the card itself (a meld card carries two)."""
    return [part.split(",")[0].strip() for part in str(card.get("name") or "").split("//") if part.strip()]


def _counter_recipient(card: dict, clause: str) -> str:
    """Who gets the counters: 'self' (this card only), 'other' (a target or a group), or ''."""
    text = " ".join(clause.split())
    if re.fullmatch(r"evolve\.?", text, re.I):
        # Evolve counters the creature itself; seeing it doesn't move counters onto somebody else.
        return "self"
    for name in _own_names(card):
        if name and re.search(r"\bon " + re.escape(name) + r"\b", text, re.I):
            return "self"
    if re.search(r"\bon (?:this|~)\b|\bonto this\b", text, re.I):
        return "self"
    if re.search(r"\b(?:another|target|each|all|every|up to \w+) [^.]{0,40}?"
                 r"\b(?:creatures?|permanents?|artifacts?|enchantments?|lands?|planeswalkers?)\b", text, re.I) \
            or re.search(r"\bon (?:a|an) (?:creature|permanent|artifact|enchantment|land)\b", text, re.I):
        return "other"
    return ""


def _counter_on_self(card: dict, clause: str) -> bool:
    """Does this trigger watch counters landing on the card itself?"""
    text = " ".join(clause.split())
    for name in _own_names(card):
        if name and re.search(r"\bon " + re.escape(name) + r"\b", text, re.I):
            return True
    return bool(re.search(r"\bon (?:this|~)\b", text, re.I))


def card_subtypes(card: dict) -> set:
    """Subtypes on the type line: a Kraken card is a kraken for a "cast a Kraken spell" trigger."""
    line = str(card.get("typeLine") or "")
    rest = line.split("—", 1)[1] if "—" in line else ""
    return {word.lower() for word in re.findall(r"[A-Za-z][\w'-]+", rest)}


def card_types(card: dict) -> set:
    """Types this card is when it is on the battlefield."""
    line = str(card.get("typeLine") or "").lower()
    found = set()
    for word in ("creature", "artifact", "enchantment", "land", "planeswalker", "battle"):
        if word in line:
            found.add(word)
    return found


def _is_self_trigger(card: dict, clause: str) -> bool:
    """Does this reaction fire on the card itself rather than on another card?"""
    text = clause.strip()
    if not text:
        return False
    if SELF_TRIGGER.match(text):
        return True
    name = str(card.get("name") or "")
    lowered = " ".join(text.lower().split())
    # "Runo Stromkirk // Krothuss, Lord of the Deep" is triggered as "When Runo enters" or
    # "When Krothuss enters", and cards are often named by their first word in the clause.
    faces = [face.strip() for face in name.split("//") if face.strip()]
    candidates = set()
    for face in faces:
        short = face.split(",")[0].strip()
        if short:
            candidates.add(short.lower())
            candidates.add(short.split()[0].lower())
    for candidate in sorted(candidates, key=len, reverse=True):
        for opener in ("when ", "whenever "):
            if lowered.startswith(opener + candidate):
                rest = lowered[len(opener) + len(candidate):]
                if rest[:1] in (" ", ",", "\u2014", "-"):
                    return True
    return False


ORDINALS = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5}
NUMBER_WORDS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
                "seven": 7, "eight": 8, "nine": 9, "ten": 10}


def draw_amount(fact: dict) -> int | None:
    """How many cards one effect draws, or None when it is X / 'that many'."""
    qualifiers = fact.get("qualifiers") or {}
    amount = str(qualifiers.get("amount") or "").strip().lower()
    if amount.isdigit():
        return int(amount)
    if amount in NUMBER_WORDS:
        return NUMBER_WORDS[amount]
    clause = _clause(fact)
    match = re.search(r"\bdraws?\s+(a|an|one|two|three|four|five|six|seven|eight|nine|ten|\d+)\s+cards?\b", clause, re.I)
    if match:
        word = match.group(1).lower()
        return int(word) if word.isdigit() else NUMBER_WORDS.get(word)
    return None


def reacts(card: dict) -> list[dict]:
    """What this card listens for."""
    found = []
    stolen = [grant for grant in grants(card)
              if grant["group"] == "creatures you control but don't own"]
    if stolen:
        # "Each creature you control but don't own gets +2/+2, has menace and deathtouch" has nothing
        # to apply to until something is taken, so the theft is what turns the ability on: it belongs
        # with the triggers. The label names the condition only — what the ability then does is the
        # ability, not the trigger.
        found.append({"event": "gain-control", "types": {"creature"}, "nontoken": False, "nonland": False,
                      "controller": "you", "subtype": None, "excluded_subtype": None, "subtypes": [],
                      "clause": stolen[0]["clause"], "not_owned": False,
                      "event_label": "taking control of an opponent's creature(s)"})
    for fact in card.get("autoBucketFacts") or []:
        if fact.get("role") != "trigger":
            continue
        action = str(fact.get("action") or "")
        clause_text = _clause(fact)
        word = str((fact.get("qualifiers") or {}).get("threshold") or "").lower()
        if not word:
            named = re.search(
                r"\b(?:your|an opponent's|their|his or her) (first|second|third|fourth|fifth) "
                r"(?:card|spell|creature)\b",
                clause_text, re.I)
            word = named.group(1).lower() if named else ""
        threshold = ORDINALS.get(word)
        event = EVENT_OF_TRIGGER.get(action)
        if not event and action in ("react-to", "react-at", "react-to-trigger"):
            for pattern, name in GENERIC_TRIGGER_EVENTS:
                if re.search(pattern, _clause(fact), re.I):
                    event = name
                    break
        if not event:
            continue
        clause = _clause(fact)
        if _is_self_trigger(card, clause):
            continue
        # The qualifier is not always set, but the clause says it: "Whenever a nontoken
        # artifact you control enters" must not be fed by a token maker.
        nontoken = bool((fact.get("qualifiers") or {}).get("nontoken")) or bool(re.search(r"nontoken", clause, re.I))
        # "if a nonland permanent left the battlefield": a sacrificed land never satisfies it.
        nonland = bool((fact.get("qualifiers") or {}).get("nonland")) or bool(re.search(r"nonland", clause, re.I))
        controller = None
        if re.search(r"\byou control\b|\byou own\b", clause, re.I):
            controller = "you"
        elif re.search(r"\bopponents?\b", clause, re.I):
            controller = "opponent"
        # "Whenever another Insect you control dies" narrows the reaction to a subtype.
        subtype = None
        excluded = None
        negated = re.search(r"\bnon-([A-Z][a-z]+)\b", clause)
        if negated:
            excluded = negated.group(1).lower()
        match = re.search(
            r"\b(?:another|a|an|one or more)\s+(?:nontoken\s+)?([A-Za-z][a-z]+)\b[^.]{0,24}(?:you control|you own)",
            clause)
        if match:
            word = match.group(1).lower()
            generic = {"creature", "permanent", "artifact", "enchantment", "land", "token", "spell", "card"}
            # "one or more artifacts you control leave …" names a type, not a subtype; the plural
            # form used to become subtype "artifacts" and silently killed the link.
            base = word[:-1] if word.endswith("s") and word[:-1] in generic else word
            if base not in generic and base not in ("one", "more", "other", "tapped", "untapped",
                                                   "attacking", "nonland", "nontoken"):
                subtype = base
        # "cast a Kraken, Leviathan, Octopus, or Serpent spell": the fact carries the list; a fact
        # written before the rule existed only has it in the clause, so read both.
        declared = (fact.get("qualifiers") or {}).get("subtypes")
        subtypes = [str(item).lower() for item in (declared if isinstance(declared, list) else [declared])] if declared else []
        if not subtypes:
            listed = re.search(
                r"\bcast(?:s|ing)?\s+(?:a|an|one or more|another)\s+"
                r"([A-Z][\w\u2019'-]+(?:\s*,\s*(?:or\s+|and\s+)?[A-Z][\w\u2019'-]+|\s+or\s+[A-Z][\w\u2019'-]+)+)\s+spells?\b",
                clause, re.I)
            if listed:
                types = {"creature", "instant", "sorcery", "artifact", "enchantment", "land", "planeswalker",
                         "battle", "permanent", "spell", "noncreature", "historic", "colorless", "multicolored",
                         "snow", "legendary", "token"}
                subtypes = [word.strip().lower() for word in
                            re.split(r"\s*,\s*(?:or\s+|and\s+)?|\s+or\s+|\s+and\s+", listed.group(1))
                            if word.strip() and word.strip().lower() not in types]
        if threshold:
            if event == "draw":
                # Counting draws can be grounded: a card that draws two reaches the count alone, and
                # your own draw step already provided the first one.
                found.append({"event": event, "types": _types(fact), "nontoken": nontoken,
                              "nonland": nonland, "controller": controller, "subtype": subtype,
                              "excluded_subtype": excluded, "subtypes": subtypes, "clause": clause,
                              "threshold": threshold,
                              "each_turn": bool(re.search(r"\beach turn\b", clause, re.I))})
                continue
            if event == "cast" and threshold >= 2:
                # "your second spell each turn": no spell reaches that alone, but every spell counts,
                # so the pair is real and stays in the any-card bucket.
                found.append({"event": event, "types": _types(fact), "nontoken": nontoken,
                              "nonland": nonland, "controller": controller, "subtype": subtype,
                              "excluded_subtype": excluded, "subtypes": subtypes, "clause": clause,
                              "threshold_cast": threshold, "opponent_turn": False})
                continue
            if event == "cast" and re.search(r"during (?:each|an even|an) opponent", clause, re.I):
                # "your first spell during each opponent's turn": only what you can cast on their
                # turn can ever feed it, which means an instant.
                found.append({"event": event, "types": _types(fact), "nontoken": nontoken,
                              "nonland": nonland, "controller": controller, "subtype": subtype,
                              "excluded_subtype": excluded, "subtypes": subtypes, "clause": clause,
                              "threshold_cast": threshold, "opponent_turn": True})
                continue
            continue
        found.append({"event": event, "types": _types(fact), "nontoken": nontoken,
                      "nonland": nonland, "controller": controller, "subtype": subtype,
                      "excluded_subtype": excluded, "subtypes": subtypes, "clause": clause,
                      # "Whenever +1/+1 counters are put on Lonis": this only fires for counters that
                      # land on Lonis, so a card that counters itself is not what feeds it.
                      "on_self": _counter_on_self(card, clause) if event == "counter" else False,
                      # "sacrifice a Clue" asks for one kind of token, not any artifact.
                      "kind": str((fact.get("object") or {}).get("name") or ""),
                      # "cast a noncreature spell you don't own": somebody else's card is the point.
                      "not_owned": bool(re.search(r"\byou don'?t own\b", clause, re.I))})
    return found


# A permission to use somebody else's *card*. "You may cast this spell / this card face down / this card
# from your hand" is emerge, morph or warp — your own card, so it never counts.
HANDS_OVER = re.compile(r"\bgain control of\b", re.I)
# A permission over a card: "you may cast that card", "you may play those cards", "you may put a
# creature card from it onto the battlefield".
PERMISSION = re.compile(r"\byou may (?:cast|play|put (?:a|one) creature card from it onto the battlefield)\b",
                        re.I)
# …but not over your own spell: "you may cast this spell by sacrificing" (emerge), "this card face
# down" (morph), "this card from your hand for its warp cost" (warp), "this card from your graveyard"
# (flashback). Those are your cards.
SELF_CAST = re.compile(r"\byou may (?:cast|play)\s+(?:this spell|this card|~)\b", re.I)


def _first_clause(card: dict) -> str:
    """The sentence that makes this card hand you somebody else's card.

    The fact's own clause, not the card's first trigger: "Whenever Zara attacks, look at defending
    player's hand" says nothing about taking the card, the sentence after it does.
    """
    clauses = [_clause(fact) for fact in card.get("autoBucketFacts") or [] if _clause(fact)]
    text = " ".join(str(card.get("oracleText") or "").split())
    sentences = [sentence.strip() for sentence in re.split(r"(?<=[.!?])\s+", text) if sentence.strip()]
    for clause in clauses:
        if HANDS_OVER.search(clause) or (PERMISSION.search(clause) and not SELF_CAST.search(clause)):
            return clause
    for index, sentence in enumerate(sentences):
        if HANDS_OVER.search(sentence) or (PERMISSION.search(sentence) and not SELF_CAST.search(sentence)):
            return " ".join(sentences[max(0, index - 1):index + 1])
    for clause in clauses:
        if THEIRS.search(clause):
            return clause
    return sentences[0] if sentences else ""


def causes(card: dict) -> list[dict]:
    """What this card makes happen, including the bare fact that it can be played."""
    found = []
    own = card_types(card)
    mine = card_subtypes(card)
    # Their card, and you actually cast it (not merely put it onto the battlefield).
    card_text = " ".join(str(card.get("oracleText") or "").split())
    # Casting their card only counts when the card really was theirs: "you may cast the exiled card"
    # also covers exiling your own library (The Key to the Vault), which is not an opponent's card.
    casts_theirs = bool(CAST_THEIRS.search(card_text)
                        and (THEIRS_SOURCE.search(card_text) or EXILE_TARGET.search(card_text)))
    if own:
        found.append({"event": "enter", "types": set(own), "subtypes": mine, "clause": "enters the battlefield", "generic": True})
        found.append({"event": "cast", "types": {"spell"} | own, "subtypes": mine, "clause": "can be cast", "generic": True})
    if "creature" in own:
        found.append({"event": "die", "types": set(own), "subtypes": mine, "clause": "is a creature that can die", "generic": True})
    spell_kinds = {"instant", "sorcery"} & set(re.split(r"[\s\u2014]+", str(card.get("typeLine") or "").lower()))
    if spell_kinds:
        # "whenever you cast an instant or sorcery": the cast is that kind of spell, not any spell.
        found.append({"event": "cast", "types": {"spell"} | spell_kinds, "subtypes": mine,
                      "clause": "can be cast", "generic": True})
    for fact in card.get("autoBucketFacts") or []:
        if fact.get("role") not in ("effect", "cost"):
            continue
        action = str(fact.get("action") or "")
        types = _types(fact)
        qualifiers = fact.get("qualifiers") or {}
        if action == "return":
            # Only a return *to the battlefield* makes something enter; graveyard -> hand never
            # leaves the battlefield, so it cannot feed "a permanent left the battlefield".
            events = ("graveyard-leave",) if "graveyard" in types else ()
            if "battlefield" in str(qualifiers.get("to") or "").lower() or (
                    not qualifiers.get("to") and re.search(r"\bto the battlefield\b", _clause(fact), re.I)):
                events = events + ("enter",)
        elif action == "exile":
            # Exiling a permanent does not kill it: no "dies" trigger, but it did leave.
            events = () if "graveyard" in types else ("leave",)
        else:
            if action == "grant" and STOLEN_GROUP.search(_clause(fact)):
                # "each creature you control but don't own gets …": the only creatures that benefit
                # are ones you have taken, so this grants nothing to a deck card and cannot feed a
                # trigger on your own creatures.
                continue
            events = CAUSE_EVENTS.get(action, ())
        clause = _clause(fact)
        if action not in ("create",) and '"' in clause and re.search(r"\bcreates?\b.*\btoken\b", clause, re.I):
            # "create ... a token with \"Whenever equipped creature attacks, tap target creature\"": the
            # quoted ability belongs to the token, so this card does not cause that event itself.
            continue
        # "target creature gains \"Whenever ... draw a card\"": the draw depends on that creature
        # connecting, so the link is real but conditional.
        granted = bool('"' in clause) and bool(re.search(r"\b(?:gains?|has)\s+\"", clause, re.I))
        if "creature" not in types and re.search(r"\bartifact creature token\b", clause, re.I):
            types = types | {"creature"}
        if re.search(r"\bsnow\b", clause, re.I):
            types = types | {"snow"}
        if action in ("reanimate", "return"):
            # "return target Pirate creature card ...": the returned card is a Pirate, which is
            # exactly what a subtype-restricted trigger needs to see.
            for word in re.findall(
                    r"\b([A-Z][a-z]+)\b(?=\s+(?:creature|permanent|card|artifact|enchantment|land)\b)", clause):
                lowered = word.lower()
                if lowered not in ("target", "another", "each", "that", "this", "your", "their", "owner",
                                   "basic", "legendary", "snow", "tapped", "untapped", "other"):
                    types = types | {lowered}
        actor = fact.get("actor") or "you"
        if re.search(r"\b(?:each |target |an? )?opponents?\b[^.]{0,40}\b(?:discards?|loses? life|sacrifices?|exiles?)\b", clause, re.I) \
                or re.search(r"\bthat player\b[^.]{0,30}\bdiscards?\b", clause, re.I):
            actor = "opponent"
        unconstrained = action == "cast-without-paying"
        loose_only = bool(action not in ("create",) and granted)
        if re.search(r"\b(?:your|their|an opponent's) (?:first|second|third|fourth|fifth|sixth) "
                     r"(?:card|spell|creature)\b", clause, re.I):
            # "Whenever an opponent draws their second card each turn": the draw depends on a count.
            continue
        if re.search(r"\bif (?:there are|it has|you have|you control) [^.]{0,30}\bor more\b", clause, re.I):
            loose_only = True
        if action == "sacrifice" and re.search(
                r"\b(?:artifact|creature|permanent|land|enchantment|token)s?\s+or\s+"
                r"(?:artifact|creature|permanent|land|enchantment|token)s?\b", clause, re.I):
            # "sacrifice an artifact or creature": the partner only causes the event when that
            # branch is the one taken, so the pair is real but conditional.
            loose_only = True
        amount = draw_amount(fact) if "draw" in events else None
        # A permanent that draws "whenever …" can fire twice in one turn; a one-shot spell or an
        # enters-the-battlefield draw cannot.
        repeatable = bool(action == "draw"
                          and re.match(r"^whenever\b", clause, re.I)
                          and not re.search(r"\b(?:instant|sorcery)\b", str(card.get("typeLine") or ""), re.I))
        granted_keyword = str((fact.get("object") or {}).get("name") or "").lower() if action == "grant" else None
        # "put two +1/+1 counters on Morska" is not the same event as "put a counter on another target
        # creature": only the second can feed somebody else's "counters are put on ..." trigger.
        recipient = _counter_recipient(card, clause) if "counter" in types else ""
        created_kind = str((fact.get("object") or {}).get("name") or "") if action == "create" else ""
        for event in events:
            found.append({"event": event, "types": types, "subtypes": mine, "clause": clause, "generic": False,
                          "actor": actor, "scope": fact.get("scope") or "unspecified",
                          "unconstrained_cast": unconstrained, "granted": loose_only,
                          "amount": amount, "keyword": granted_keyword, "repeatable": repeatable,
                          "recipient": recipient, "created": bool(created_kind), "kind": created_kind,
                          "not_owned": bool(casts_theirs and action in ("cast", "cast-without-paying", "cast-from"))})
    # "exile it, then you may cast it" and "put a creature card from their hand onto the battlefield"
    # hand you a permanent you don't own without ever saying "gain control", so they are the same
    # event as a theft. Whether the card turns out to be a creature is not known, hence conditional.
    if casts_theirs and not any(entry["event"] == "cast" and entry.get("not_owned") for entry in found):
        # "You may cast that card for as long as it remains exiled" is often reminder-adjacent text the
        # classifier leaves as a gap, so the cast permission is added from the card's own wording.
        found.append({"event": "cast", "types": {"spell"}, "subtypes": set(), "clause": _first_clause(card),
                      "generic": False, "actor": "you", "scope": "unspecified", "unconstrained_cast": True,
                      "granted": True, "amount": None, "keyword": None, "repeatable": False, "not_owned": True})
    if in_group(card, "creatures you control but don't own") \
            and not any(entry["event"] == "gain-control" for entry in found):
        # "put a creature card from it onto the battlefield" (Zara) hands over a creature, full stop;
        # "you may cast that card" only does when the card turns out to be one, so only that is loose.
        names_creature = bool(re.search(r"\bcreature card\b|\bcreature spell\b", card_text, re.I))
        found.append({"event": "gain-control", "types": {"creature"}, "subtypes": set(), "clause": _first_clause(card),
                      "generic": False, "actor": "you", "scope": "unspecified", "unconstrained_cast": False,
                      "granted": not names_creature, "amount": None, "keyword": None, "repeatable": False,
                      "not_owned": False})
    return found


def score(cause_types: set, react_types: set, token_cause: bool) -> int:
    """How specifically a cause satisfies a reaction; 0 means no link."""
    if not react_types:
        return 0
    # "Whenever you cast a creature spell" needs a creature, not just any spell.
    if "spell" in react_types:
        wanted = react_types - {"spell"}
        if not wanted:
            return 1 if "spell" in cause_types else 0
        # "a noncreature or Dragon spell" asks for either half, so each half is checked on its own.
        if "noncreature" in wanted and "spell" in cause_types and "creature" not in cause_types:
            return 1
        rest = wanted - {"noncreature"}
        return 1 if (rest & cause_types) else 0
    if react_types & cause_types:
        return 3 + len(react_types & cause_types)
    if "permanent" in react_types and set(PERMANENT_TYPES) & cause_types:
        return 2
    if "creature" in react_types and "creature" in cause_types:
        return 2
    # "Destroy target permanent" can hit a creature, so it can satisfy a creature reaction.
    if "creature" in react_types and "permanent" in cause_types:
        return 2
    if "spell" in react_types and "spell" in cause_types:
        return 1
    return 0


def _label(event: str, types: set, nonland: bool = False) -> str:
    noun = ""
    for candidate in ("land", "creature", "artifact", "enchantment", "token", "permanent", "spell", "player"):
        if candidate in types:
            noun = candidate
            break
    subject = (noun + "s") if noun and noun != "player" else ("cards" if not noun else noun)
    if event == "token-create":
        # "creatures being created" reads wrong; tokens are what is created.
        subject = "tokens"
    if nonland and noun == "permanent":
        subject = "nonland permanents"
    return f"{subject} {EVENT_LABELS.get(event, event)}"


def profile(card: dict) -> dict:
    return {"causes": causes(card), "reacts": reacts(card)}


# "creatures you control but don't own": the group only exists once something is stolen, so its
# members are found through what takes a permanent, never through a card's type line.
STOLEN_GROUP = re.compile(r"\byou control but don'?t own\b|\bcreatures? you don'?t own\b", re.I)
# Effects that hand you a card or a permanent that was somebody else's.
# Somebody else's card: "an opponent's graveyard", "their hand", "defending player's library".
THEIRS = re.compile(r"\bopponents?'?s?\b|\bdefending player\b|\btheir (?:library|hand|graveyard)\b"
                    r"|\bthat player's (?:library|hand|graveyard)\b", re.I)
# The cards being cast really came out of *their* zone — not just "an opponent" somewhere on the card.
THEIRS_SOURCE = re.compile(
    r"\bfrom (?:an opponent's|opponents'|their|that player's|defending player's) "
    r"(?:library|hand|graveyard|exile)\b"
    r"|\b(?:an opponent's|their|that player's|defending player's) (?:library|hand|graveyard)\b"
    r"|\b(?:those )?opponents?' (?:libraries|graveyards|hands)\b", re.I)
# "exile another target creature": a battlefield permanent that may well be theirs (Hostage Taker).
EXILE_TARGET = re.compile(r"\bexile (?:another )?target (?:creature|artifact|permanent|nonland permanent)\b", re.I)
# Actually *casting* their card — "cast that card / it / those cards / a spell from among cards exiled
# this way". Putting a permanent onto the battlefield is not a cast, so Zara feeds the static ability
# and not "whenever you cast a noncreature spell you don't own".
CAST_THEIRS = re.compile(
    r"\byou may (?:cast|play) (?:that card|those cards|it|them|the exiled card)\b"
    r"|\byou may cast (?:a|one) spell from among cards exiled this way\b", re.I)

STOLEN_SOURCE = re.compile(
    r"\bgain control of\b|\byou may (?:cast|play)\b[^.]{0,60}\b(?:exiled|exile|their|opponent)|"
    r"\b(?:an opponent's|opponents'|that player's|their) (?:library|hand|graveyard)\b", re.I)

GROUP_GRANT = re.compile(
    r"\b((?:all |each )?(?:(?:nontoken|token|artifact|legendary) )?creatures you control)"
    r"[^.]{0,24}?\b(?:gains?|have|gets?)\b",
    re.I,
)


def grants(card: dict) -> list[dict]:
    """Static group boosts: what this card gives every qualifying creature you control.

    "Artifact creatures you control gain menace" is not a trigger — nothing reacts to it — but
    the cards it improves are still worth pairing with it, so it is its own relation.
    """
    found = []
    for fact in card.get("autoBucketFacts") or []:
        if fact.get("action") != "grant" or (fact.get("object") or {}).get("kind") != "keyword":
            continue
        clause = _clause(fact)
        if STOLEN_GROUP.search(clause):
            # "Each creature you control but don't own gets +2/+2, has menace and deathtouch":
            # the group is not a card type — it is whatever you have stolen.
            group = "creatures you control but don't own"
        else:
            match = GROUP_GRANT.search(clause)
            if not match:
                continue
            group = " ".join(match.group(1).lower().split())
        keyword = str((fact.get("object") or {}).get("name") or "")
        if keyword and not any(entry["group"] == group and entry["keyword"] == keyword for entry in found):
            found.append({"group": group, "keyword": keyword, "clause": clause})
    return found


def scaling(card: dict) -> list[dict]:
    """Static boosts counted over a kind of permanent: "gets +1/+0 for each artifact you control".

    Nothing triggers, so it is not an event link. What affects such a card is whatever supplies the
    count — the artifacts the deck already runs and everything that makes one — so those cards are
    its `improved_by`, and it is their `improves`.
    """
    found = []
    for fact in card.get("autoBucketFacts") or []:
        if fact.get("action") == "reduce-cost":
            # "Affinity for artifacts (this spell costs {1} less ... for each artifact you control)":
            # the reminder text is stripped before classification, so the kind only survives in the
            # fact's own name. It is the same relation: the count makes this card cheaper.
            name = str((fact.get("object") or {}).get("name") or "")
            match = re.search(r"affinity for ([a-z ]+)", name, re.I)
            if match:
                per = re.sub(r"s$", "", match.group(1).strip().lower())
                if per and not any(entry["per"] == per for entry in found):
                    found.append({"per": per, "amount": "cost", "at_least_power": 0,
                                  "clause": _clause(fact)})
            continue
        if fact.get("action") != "scale":
            continue
        qualifiers = fact.get("qualifiers") or {}
        per = str(qualifiers.get("per") or "").strip().lower()
        if not per:
            continue
        if not any(entry["per"] == per for entry in found):
            found.append({"per": per, "amount": str(qualifiers.get("amount") or ""),
                          "at_least_power": int(qualifiers.get("at_least_power") or 0),
                          "clause": _clause(fact)})
    return found


def _names_kind(text: str, per: str) -> bool:
    """Does this text name every word of the counted kind ("artifact", "snow permanent")?

    "permanent" is true of every card and "other" excludes the card itself, so neither is a type
    word: a snow creature supplies a "snow permanent" for the count.
    """
    words = [word for word in per.split() if word not in ("permanent", "other")]
    if not words:
        return False
    lowered = str(text or "").lower()
    return all(word in lowered for word in words)


def supplies(card: dict, per: str) -> str | None:
    """How this card adds to a count of <per>: "permanent" if it is one, "token" if it makes one."""
    if _names_kind(card.get("typeLine"), per):
        return "permanent"
    for fact in card.get("autoBucketFacts") or []:
        if fact.get("action") not in ("create", "token-create"):
            continue
        obj = fact.get("object") or {}
        text = " ".join([str(obj.get("name") or "")] + [str(kind) for kind in obj.get("types") or []])
        if _names_kind(text, per):
            return "token"
    return None


def in_group(card: dict, group: str) -> bool:
    """Is this card one the group boost applies to?"""
    if group == "creatures you control but don't own":
        # What matters is not what the card *is* but what it *brings*: only a theft, or a card that
        # lets you play somebody else's card, hands you a creature you don't own.
        text = " ".join(str(card.get("oracleText") or "").split())
        if not text:
            text = " ".join(_clause(fact) for fact in card.get("autoBucketFacts") or [])
        if re.search(r"\bgain control of\b", text, re.I):
            return True
        sentences = re.split(r"(?<=[.!?])\s+", text)
        for index, sentence in enumerate(sentences):
            if not PERMISSION.search(sentence) or SELF_CAST.search(sentence):
                continue
            # The permission points at "that card", so where it came from is in the sentence before.
            window = " ".join(sentences[max(0, index - 1):index + 1])
            if THEIRS_SOURCE.search(window) or EXILE_TARGET.search(window):
                return True
        return False
    line = str(card.get("typeLine") or "").lower()
    words = group.split(" creatures")[0].split()
    if "creature" not in line:
        return False
    for qualifier in ("artifact", "enchantment", "legendary", "token"):
        if qualifier in words and qualifier not in line:
            return False
    if "nontoken" in words and "token" in line:
        return False
    return True


def build(document: dict, *, max_groups: int = 6, max_ids: int = 40) -> dict:
    """Directed graph of the deck, aggregated per (card, event)."""
    cards = [c for c in (document.get("cards") or []) if isinstance(c, dict) and c.get("id")]
    profiles = {c["id"]: profile(c) for c in cards}
    names = {c["id"]: c.get("name") or "" for c in cards}

    fed_by: dict[str, dict[tuple, dict]] = defaultdict(dict)
    feeds: dict[str, dict[tuple, dict]] = defaultdict(dict)
    pairs = 0

    for actor in cards:
        for listener in cards:
            if actor["id"] == listener["id"]:
                continue
            for cause in profiles[actor["id"]]["causes"]:
                for react in profiles[listener["id"]]["reacts"]:
                    # The saboteur bridge pairs two different events on purpose: a creature that gains
                    # haste or is stolen is what lets a "deals combat damage" trigger happen.
                    bridged = (react["event"] in ("combat-damage", "damage")
                               and bool(react["types"] & {"player", "opponent"})
                               and cause["event"] in ("grant", "gain-control"))
                    # A card that makes the resource a trigger needs is a step towards that trigger:
                    # Morska investigates, Lonis pays off "whenever you sacrifice a Clue". The card
                    # that spends the token is a different link, so this one gets its own wording.
                    # The kind has to be the kind the trigger asks for: a Drake or a Food is not a
                    # Clue, even though all three are artifact tokens.
                    created_kind = str(cause.get("kind") or "")
                    wanted_kind = str(react.get("kind") or "").lower()
                    kind_matches = bool(created_kind) and (
                        wanted_kind == created_kind.lower()
                        or (wanted_kind in ("", "artifact", "token", "permanent") and "artifact" in cause["types"]))
                    resource = bool(react["event"] == "sacrifice" and cause.get("created") and kind_matches)
                    if cause["event"] != react["event"] and not bridged and not resource:
                        continue
                    # Counters on the listener itself can only be fed by a card that places counters
                    # somewhere else; a card that counters itself never triggers another card.
                    if react["event"] == "counter" and react.get("on_self") and cause.get("recipient") != "other":
                        continue
                    if react["nontoken"] and "token" in cause["types"]:
                        continue
                    # A land leaving the battlefield is not a nonland permanent leaving it.
                    if react.get("nonland") and not (cause["types"] & set(NONLAND_PERMANENT_TYPES)):
                        continue
                    # A Treasure, a Clue or a Lander is an artifact: nothing enters as a creature.
                    # A destroyed "permanent" may well be a creature, so only definite non-creatures
                    # (artifacts, enchantments, lands, tokens of those) are excluded.
                    denies_creature = bool(cause["types"] & {"artifact", "enchantment", "land"}) \
                        and "creature" not in cause["types"] and "permanent" not in cause["types"]
                    if "creature" in react["types"] and denies_creature:
                        continue
                    if react["event"] in ("graveyard-leave", "graveyard-enter") and "creature" in react["types"] \
                            and "creature" not in cause["types"]:
                        continue
                    if "plus-one-plus-one" in react["types"] and (cause["types"] & {
                            "quest", "supply", "storage", "charge", "ice", "oil", "foreshadow", "vehicle"}
                            or re.search(r"\bVehicle\b|\bartifact you control\b", cause.get("clause") or "")):
                        continue
                    if react.get("excluded_subtype"):
                        parts = set(cause["types"])
                        for entry in list(cause["types"]):
                            parts.update(entry.split("-"))
                        if react["excluded_subtype"] in parts:
                            continue
                    if react.get("subtype"):
                        # The tribe can sit in the cause's types ("pirate") or in its subtypes
                        # (a Pirate creature is only "creature" as a type).
                        parts = set(cause["types"]) | set(cause.get("subtypes") or set())
                        for entry in list(parts):
                            parts.update(entry.split("-"))
                        # "one or more Pirates you control": the clause names the tribe in the plural.
                        forms = {react["subtype"], react["subtype"].rstrip("s")}
                        if not (forms & parts) and not bridged:
                            continue
                    # "whenever you cast a Kraken ... spell": the card must BE one of those subtypes.
                    # Matching a named subtype is a real synergy, not the bare "it can be cast" pair.
                    subtype_hit = False
                    if react.get("subtypes"):
                        if set(react["subtypes"]) & (cause.get("subtypes") or set()):
                            subtype_hit = True
                        elif not (set(react["types"]) - {"spell"} - set(react["subtypes"])):
                            # The subtypes were the whole request, so a card outside them cannot feed it.
                            continue
                    # A named *subtype* is a deck-building choice ("my Krakens"), so those links are
                    # real; a card *type* ("cast a creature spell") is satisfied by most of any deck,
                    # so it stays a generic pair the panel collapses.
                    if react.get("controller") == "opponent" and cause.get("actor") == "you" \
                            and react["event"] in ("discard", "damage", "life-gain", "graveyard-enter"):
                        continue
                    if cause.get("unconstrained_cast") and SPell_RESTRICTION.search(react.get("clause") or "") \
                            and not (react.get("not_owned") and cause.get("not_owned")):
                        # "cast a noncreature spell" is a type request, but when both sides say "you
                        # don't own", the point is the ownership, and the type of the exiled card is
                        # unknown until you look at it.
                        continue
                    # Removal aimed at an opponent can still be aimed at your own permanent: the link
                    # exists, but it is not what the deck does by itself.
                    one_sided = bool(re.search(
                        r"\beach opponent\b|\bopponents control\b|\btarget opponent controls\b",
                        cause.get("clause") or "", re.I))
                    if cause.get("actor") == "opponent" and react.get("controller") == "you" \
                            and cause.get("scope") in ("all", "each") and one_sided:
                        continue
                    loose = bool(cause.get("actor") == "opponent" and cause.get("scope") == "target"
                                 and react.get("controller") == "you") or bool(cause.get("granted"))
                    if react.get("threshold_cast"):
                        if react.get("opponent_turn"):
                            # Only an instant can be cast on somebody else's turn.
                            if "instant" not in cause["types"]:
                                continue
                            loose = False
                        else:
                            # "your second spell each turn": every spell counts as one, so no single
                            # card is the cause — a real pair, and an any-card one. Reading it as
                            # conditional put a tile on the panel claiming this card triggers the
                            # second-spell bonus, which the player rejects on sight.
                            loose = False
                    if react.get("threshold"):
                        # "Whenever you draw your second card each turn": on your own turn the draw
                        # step already gave you the first card, so one more draw is enough. On an
                        # opponent's turn you get nothing for free, so a card has to draw that many
                        # at once — or be able to fire again and again in that same turn.
                        amount = cause.get("amount")
                        free = 1 if react.get("each_turn") else 0
                        if amount is None or amount >= react["threshold"] or cause.get("repeatable"):
                            loose = amount is None
                        elif amount + free >= react["threshold"]:
                            loose = False
                        else:
                            continue
                    # A matched subtype is what the reaction asked for, so it counts as a hit even
                    # though the subtype word is not one of the card's battlefield types.
                    match_types = set(cause["types"])
                    if subtype_hit:
                        match_types |= set(react["subtypes"]) & set(cause.get("subtypes") or set())
                    value = score(match_types, react["types"], "token" in cause["types"])
                    # A saboteur payoff ("whenever a creature you control deals combat damage to a
                    # player") is fed by cards that let a creature connect: haste for a body you just
                    # stole, or an evasive keyword. Without this bridge a theft deck reads as empty.
                    bridge = None
                    # "Whenever you cast a noncreature spell you don't own": the enabler is a card
                    # that lets you play somebody else's card, and the type of that card is unknown
                    # until you look, so the pair is real but conditional.
                    owner_bridge = bool(react.get("not_owned") and cause.get("not_owned"))
                    if owner_bridge:
                        value = max(value, 3)
                        loose = True
                    if bridged and cause["event"] in ("grant", "gain-control"):
                        keyword = cause.get("keyword") or ""
                        if cause["event"] == "gain-control":
                            bridge = "creatures you take can attack"
                        elif any(helper in keyword for helper in COMBAT_HELPERS):
                            bridge = f"creatures that gain {keyword}"
                        if bridge:
                            value = max(value, 4)
                    resource_kind = str(cause.get("kind") or "token") if resource else ""
                    if resource:
                        # "Morska gives Lonis something to sacrifice" — a real two-step link, and the
                        # label says which step so it cannot be read as the sacrifice itself.
                        bridge = f"{resource_kind} tokens to sacrifice"
                        value = max(value, 3)
                        loose = False
                    if not value:
                        continue
                    pairs += 1
                    # An instant cast on an opponent's turn is a real enabler even though the fact
                    # behind it is the generic "this card can be cast".
                    specific_cast = bool(react.get("threshold_cast") and react.get("opponent_turn"))
                    generic = bool(cause.get("generic")) and not subtype_hit and not bridge and not specific_cast
                    group_label = bridge or _label(react["event"], cause["types"], bool(react.get("nonland")))
                    if owner_bridge:
                        group_label = "lets you cast a card you don't own"
                    if react.get("event_label"):
                        group_label = react["event_label"]
                    if react.get("threshold_cast"):
                        wanted = {2: "second", 3: "third", 4: "fourth", 5: "fifth"}.get(
                            react["threshold_cast"], f"{react['threshold_cast']}th")
                        group_label = ("instants you can cast on their turn" if react.get("opponent_turn")
                                       else f"spells cast (each one is one towards your {wanted} spell)")
                    if react.get("threshold"):
                        amount = cause.get("amount")
                        if amount is None:
                            group_label = "draws an unknown number of cards"
                        elif amount >= react["threshold"]:
                            group_label = f"draws {react['threshold']} or more cards at once"
                        elif cause.get("repeatable"):
                            group_label = "draws a card, and can draw again on their turn"
                        else:
                            whose = "their" if react.get("controller") == "opponent" else "your"
                            group_label = f"draws a card ({whose} draw step is the first)"
                    if subtype_hit and react.get("subtypes"):
                        listed = react["subtypes"]
                        group_label = (", ".join(listed[:-1]) + " or " if len(listed) > 1 else "") + listed[-1] + " spells cast"
                    out_label = _label(cause["event"], cause["types"])
                    if resource:
                        out_label = f"makes {resource_kind} tokens to sacrifice"
                    if cause["event"] == "grant" and cause.get("keyword"):
                        out_label = f"creatures that gain {cause['keyword']}"
                    if cause["event"] == "gain-control":
                        out_label = "taking control of an opponent's creature(s)"
                    # Generic causes ("the card merely exists / enters / can be cast") are rules of
                    # the game, not synergies: they get their own group so a real one never carries
                    # a stray "any creature" card.
                    key_in = (react["event"], group_label, generic or loose)
                    group = fed_by[listener["id"]].setdefault(key_in, {
                        "event": react["event"],
                        "generic": generic or loose,
                        "loose": loose,
                        "label": group_label,
                        "ids": [],
                        "score": value,
                        "clauses": [],
                    })
                    if actor["id"] not in group["ids"] and len(group["ids"]) < max_ids:
                        group["ids"].append(actor["id"])
                        group["clauses"].append({
                            "card": names[actor["id"]],
                            "cause": cause["clause"],
                            "reaction": react["clause"],
                        })
                    group["generic"] = group["generic"] and (generic or loose)
                    group["score"] = max(group["score"], value)

                    # A resource bridge is one link, not one per event the token causes: the maker
                    # "enters the battlefield" and "creates a token" at the same moment, and keying
                    # on both printed the same row twice.
                    key_out = (react["event"] if resource else cause["event"], out_label, generic or loose)
                    out = feeds[actor["id"]].setdefault(key_out, {
                        "event": cause["event"],
                        "generic": generic or loose,
                        "loose": loose,
                        "label": out_label,
                        "ids": [],
                        "score": value,
                        "clauses": [],
                    })
                    if listener["id"] not in out["ids"] and len(out["ids"]) < max_ids:
                        out["ids"].append(listener["id"])
                        out["clauses"].append({
                            "card": names[listener["id"]],
                            "cause": cause["clause"],
                            "reaction": react["clause"],
                        })
                    out["generic"] = out["generic"] and (generic or loose)
                    out["score"] = max(out["score"], value)

    def finish(groups: dict) -> list[dict]:
        rows = []
        for group in groups.values():
            rows.append({
                "event": group["event"],
                "label": group["label"],
                "count": len(group["ids"]),
                "cards": list(group["ids"]),
                "generic": group["generic"],
                "conditional": bool(group.get("loose")),
                "clauses": group["clauses"][:6],
            })
        # A conditional group that names an actual synergy ("lets you cast a card you don't own") is
        # worth more than a collapsed "39 lands cast", so it comes before the plain any-card ones;
        # only the cap made the synergy disappear.
        rows.sort(key=lambda row: (row["generic"] and not row["conditional"], -row["count"]))
        return rows[:max_groups]

    # A static boost is not a trigger: nothing reacts to gaining menace. It is still a real pairing,
    # so it lives beside fed_by/feeds and never inside them — the audited event counts stay honest.
    improves: dict[str, dict[tuple, dict]] = defaultdict(dict)
    improved_by: dict[str, dict[tuple, dict]] = defaultdict(dict)
    for source in cards:
        for grant in grants(source):
            recipients = [c for c in cards if c["id"] != source["id"] and in_group(c, grant["group"])]
            if not recipients:
                continue
            if grant["group"] == "creatures you control but don't own":
                # The theft is what turns the ability on, so it is a trigger link (see reacts), not a
                # static one: nothing here builds "improves" for it.
                continue
            row = improves[source["id"]].setdefault((grant["keyword"], grant["group"]), {
                "label": f"{grant['keyword']} for your {grant['group'].replace(' you control', '')}",
                "keyword": grant["keyword"], "group": grant["group"], "ids": [], "clauses": []})
            for recipient in recipients:
                if recipient["id"] not in row["ids"] and len(row["ids"]) < max_ids:
                    row["ids"].append(recipient["id"])
                    row["clauses"].append({"card": names[recipient["id"]], "cause": grant["clause"]})
                back = improved_by[recipient["id"]].setdefault((source["id"], grant["keyword"]), {
                    "label": f"{grant['keyword']} from {names[source['id']]}",
                    "keyword": grant["keyword"], "source": names[source["id"]], "ids": [], "clauses": []})
                if source["id"] not in back["ids"]:
                    back["ids"].append(source["id"])
                    back["clauses"].append({"card": names[source["id"]], "cause": grant["clause"]})

    # A counted payoff names no event either. "Gets +1/+0 for each artifact you control", "power is
    # equal to the number of artifacts you control", "draws a card for each creature you control",
    # "costs {1} less for each artifact": all of them mean the same thing for deck building — the
    # cards that supply the count are what affect this one. Left in the event graph they read as
    # cards with no synergy at all, on both sides.
    MEASURE_LABELS = {
        "power": ("power/toughness = the count", "counts toward {name}'s power and toughness"),
        "cards": ("a card each", "draws for {name}"),
        "life": ("life each", "life for {name}"),
        "damage": ("damage each", "damage for {name}"),
        "tokens": ("a token each", "tokens for {name}"),
        "cost": ("1 less each", "makes {name} cheaper"),
        "mana": ("mana each", "mana for {name}"),
        "tax": ("more to pay each", "taxes with {name}"),
        "each": ("each", "counts for {name}"),
    }
    for source in cards:
        for scale in scaling(source):
            if scale.get("at_least_power"):
                # "for each creature you control with power 4 or greater" is a filtered count and the
                # card model holds no power, so no supplier can be checked: better silent than wrong.
                continue
            supplied: dict[str, list[str]] = {}
            for other in cards:
                if other["id"] == source["id"]:
                    continue
                kind = supplies(other, scale["per"])
                if kind:
                    supplied.setdefault(kind, []).append(other["id"])
            if not supplied:
                continue
            for kind, ids in supplied.items():
                group_key = ("scale", scale["per"], kind)
                word = f"{scale['per']}s"
                head = f"{word} you control" if kind == "permanent" else f"{word} you create"
                amount = scale["amount"]
                # A rate ("+1/+0 each") reads as a rate; anything else names what the count pays for,
                # because "(number each)" is nonsense on the panel.
                suffix, back_template = MEASURE_LABELS.get(amount, (f"{amount} each", f"{amount} each for {{name}}"))
                each = f" ({suffix})"
                row = improved_by[source["id"]].setdefault(group_key, {
                    "label": head + each, "keyword": scale["per"], "source": names[source["id"]],
                    "ids": [], "clauses": []})
                for card_id in ids:
                    if card_id in row["ids"] or len(row["ids"]) >= max_ids:
                        continue
                    row["ids"].append(card_id)
                    row["clauses"].append({"card": names[card_id], "cause": scale["clause"]})
                back_key = (source["id"], scale["per"])
                label = back_template.format(name=names[source["id"]])
                for card_id in ids:
                    back = improves[card_id].setdefault(back_key, {
                        "label": label, "keyword": scale["per"], "group": head,
                        "ids": [], "clauses": []})
                    if source["id"] not in back["ids"]:
                        back["ids"].append(source["id"])
                        back["clauses"].append({"card": names[source["id"]], "cause": scale["clause"]})

    def finish_static(groups: dict) -> list[dict]:
        rows = [{"label": group["label"], "keyword": group["keyword"], "count": len(group["ids"]),
                 "cards": list(group["ids"]), "generic": False, "conditional": False,
                 "clauses": group["clauses"][:6]} for group in groups.values()]
        rows.sort(key=lambda row: -row["count"])
        return rows[:max_groups]

    cards_out = {}
    for card in cards:
        cards_out[card["id"]] = {
            "name": names[card["id"]],
            "fed_by": finish(fed_by.get(card["id"], {})),
            "feeds": finish(feeds.get(card["id"], {})),
            "improves": finish_static(improves.get(card["id"], {})),
            "improved_by": finish_static(improved_by.get(card["id"], {})),
        }
    def real(entry: dict) -> bool:
        return any(not group["generic"] for group in entry["fed_by"] + entry["feeds"])

    linked = sum(1 for entry in cards_out.values() if real(entry))
    any_only = sum(1 for entry in cards_out.values() if (entry["fed_by"] or entry["feeds"]) and not real(entry))
    specific = sum(group["count"] for entry in cards_out.values()
                   for group in entry["fed_by"] if not group["generic"])
    return {
        "rule_version": LINKS_RULE_VERSION,
        "cards": cards_out,
        "stats": {"cards": len(cards), "pairs": pairs, "pairs_specific": specific,
                  "linked_cards": linked, "any_card_only": any_only},
    }


def find_card(document: dict, ref: str) -> dict | None:
    """Resolve a card by id, exact name, or a unique fragment (players type "phylath")."""
    raw = str(ref or "").strip()
    if not raw:
        return None
    cards = [c for c in document.get("cards") or [] if isinstance(c, dict)]
    for card in cards:
        if str(card.get("id") or "") == raw:
            return card
    key = raw.casefold()

    def names(card):
        return {str(card.get(field) or "").casefold() for field in ("name", "printedName")}

    exact = [c for c in cards if key in names(c)]
    if exact:
        return exact[0]
    partial = [c for c in cards if any(key in value for value in names(c) if value)]
    return partial[0] if partial else None


def for_card(document: dict, ref: str) -> dict:
    card = find_card(document, ref)
    if not card:
        raise ValueError(f"No card '{ref}' in this deck.")
    graph = build(document)
    entry = graph["cards"].get(card["id"], {"fed_by": [], "feeds": []})
    names = {c["id"]: (c.get("printedName") or c.get("name") or "") for c in document.get("cards") or []}
    view: dict = {"card": {"id": card["id"], "name": card.get("name"), "printed_name": card.get("printedName")}}
    # Static boosts: what this card improves, and what improves it.
    for side in ("improves", "improved_by"):
        view[side] = [
            {**group, "cards": [{"id": cid, "name": names.get(cid, "")} for cid in group["cards"]]}
            for group in entry.get(side, [])
        ]
    for side in ("fed_by", "feeds"):
        converted = [
            {**group, "cards": [{"id": cid, "name": names.get(cid, "")} for cid in group["cards"]]}
            for group in entry[side]
        ]
        view[side] = [group for group in converted if not group["generic"]]
        # "any card that enters/dies" links: true at the table, but not a synergy
        view[f"{side}_any_card"] = [group for group in converted if group["generic"]]
    view["stats"] = graph["stats"]
    return view


def hubs(document: dict, limit: int = 8) -> list[dict]:
    """Cards that the deck is built around: the most inbound and outbound links."""
    graph = build(document)
    rows = []
    for card_id, entry in graph["cards"].items():
        inbound = sum(group["count"] for group in entry["fed_by"] if not group["generic"])
        outbound = sum(group["count"] for group in entry["feeds"] if not group["generic"])
        rows.append({"id": card_id, "name": entry["name"], "fed_by": inbound, "feeds": outbound,
                     "total": inbound + outbound})
    rows.sort(key=lambda row: (-row["total"], row["name"]))
    return rows[:limit]


def candidate_card(entry: dict) -> dict:
    """Shape a sideboard candidate like a deck card, so the same join can read it."""
    facts = entry.get("autoBucketFacts") or []
    return {
        "id": str(entry.get("scryfall_id") or entry.get("id") or ""),
        "name": entry.get("name") or "",
        "printedName": entry.get("printed_name") or entry.get("printedName") or "",
        "typeLine": entry.get("type_line") or entry.get("typeLine") or "",
        "manaCost": entry.get("mana_cost") or entry.get("manaCost") or "",
        "autoBucketFacts": facts,
        "autoBuckets": entry.get("autoBuckets") or [],
        "imageUri": entry.get("image") or entry.get("imageUri") or "",
    }


def with_candidates(document: dict, entries: list) -> tuple[dict, dict]:
    """The deck plus classified sideboard candidates, and a lookup for the extras."""
    extras = {}
    cards = list(document.get("cards") or [])
    for entry in entries or []:
        if not (entry.get("autoBucketFacts") or []):
            continue
        card = candidate_card(entry)
        if not card["id"]:
            continue
        cards.append(card)
        extras[card["id"]] = {
            "id": card["id"],
            "name": card["name"],
            "printed_name": card["printedName"] or card["name"],
            "type_line": card["typeLine"],
            "mana_cost": card["manaCost"],
            "image": card["imageUri"],
            "autoBuckets": card["autoBuckets"],
            "candidate": True,
        }
    return {"cards": cards}, extras


def evaluate(document: dict, entry: dict, *, max_groups: int = 6, max_ids: int = 40) -> dict:
    """Links for a card that is not in the deck yet: build the graph with it added."""
    card = candidate_card(entry)
    if not card["autoBucketFacts"]:
        raise ValueError("That card has not been classified yet, so its links cannot be judged.")
    virtual, _ = with_candidates(document, [entry])
    graph = build(virtual, max_groups=max_groups, max_ids=max_ids)
    names = {}
    for item in virtual["cards"]:
        names[item["id"]] = item.get("printedName") or item.get("name") or ""
    view: dict = {"card": {"id": card["id"], "name": card["name"], "printed_name": card["printedName"]}}
    view.update(entry)
    entry_graph = graph["cards"].get(card["id"], {"fed_by": [], "feeds": []})
    for side in ("fed_by", "feeds"):
        view[side] = [
            {**group, "cards": [{"id": cid, "name": names.get(cid, "")} for cid in group["cards"]]}
            for group in entry_graph[side]
        ]
    view["stats"] = graph["stats"]
    view["in_deck"] = False
    return view


def deck_score(document: dict) -> dict:
    """How connected a deck is: how many of its cards feed something, and how many are fed.

    A card that does both is counted in each column, so `score` is the sum the deck builder asked
    for ("cards that feed another one + cards that are fed by another one"), while `linked_cards`
    counts every card that takes part in at least one link exactly once.
    """
    graph = build(document)
    entries = list(graph["cards"].values())
    def feeds_any(entry):
        return any(group["count"] for group in entry["feeds"] if not group["generic"])

    def fed_any(entry):
        return any(group["count"] for group in entry["fed_by"] if not group["generic"])

    feeding = sum(1 for entry in entries if feeds_any(entry))
    fed = sum(1 for entry in entries if fed_any(entry))
    both = sum(1 for entry in entries if feeds_any(entry) and fed_any(entry))
    any_feeding = sum(1 for entry in entries if any(group["count"] for group in entry["feeds"] if group["generic"]))
    any_fed = sum(1 for entry in entries if any(group["count"] for group in entry["fed_by"] if group["generic"]))
    # Links whose cause is the card merely existing ("enters the battlefield", "can be cast") say
    # little about a deck; the specific ones are the real card-to-card interactions.
    specific_feed = sum(1 for entry in entries if any(group["count"] for group in entry["feeds"] if not group.get("generic")))
    specific_fed = sum(1 for entry in entries if any(group["count"] for group in entry["fed_by"] if not group.get("generic")))
    specific_pairs = sum(group["count"] for entry in entries for group in entry["fed_by"] if not group.get("generic"))
    cards = len(document.get("cards") or [])
    return {
        "cards": cards,
        "linked_cards": feeding + fed - both,
        "feeding": feeding,
        "fed": fed,
        "both": both,
        "score": feeding + fed,
        "any_card_feeding": any_feeding,
        "any_card_fed": any_fed,
        "specific_score": specific_feed + specific_fed,
        "specific_pairs": specific_pairs,
        "pairs": int(graph["stats"].get("pairs") or 0),
        "density": round((feeding + fed - both) / cards, 3) if cards else 0.0,
    }


def survey(documents: dict) -> dict:
    """Rank every deck by how connected its cards are."""
    rows = []
    for deck_id, document in documents.items():
        row = {"deck_id": deck_id}
        row.update(deck_score(document))
        rows.append(row)
    rows.sort(key=lambda row: (-row["score"], -row["specific_score"], -row["pairs"]))
    return {"decks": rows, "most": rows[0] if rows else None, "least": rows[-1] if rows else None,
            "metric": ("score = cards that feed something + cards that are fed by something "
                       "(a card that does both counts twice); linked_cards counts distinct cards.")}
