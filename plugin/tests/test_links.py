"""Rules of the card-link graph: the join, the precision rules, and coverage over real decks."""
from __future__ import annotations

import json
import os
import unittest
from pathlib import Path

import tempfile

from test_core import core, sample_bundle

FIXTURES = Path(__file__).resolve().parent / "fixtures"
def _profile_home() -> Path:
    """The Hermes home for this machine, resolved like the host does — never a hardcoded path."""
    env = os.environ.get("HERMES_HOME")
    if env:
        return Path(env)
    local_appdata = os.environ.get("LOCALAPPDATA")
    if local_appdata and (Path(local_appdata) / "hermes").is_dir():
        return Path(local_appdata) / "hermes"
    return Path.home() / ".hermes"


DATA = _profile_home() / "plugin-data" / "mtg-deck-studio-dev" / "decks"


def fact(role, action, types, clause, nontoken=False, nonland=False, keyword=None):
    entry = {
        "role": role,
        "action": action,
        "object": {"types": list(types)},
        "evidence": {"clause": clause},
        "qualifiers": {},
    }
    if keyword:
        entry["object"]["name"] = keyword
        entry["object"]["kind"] = "keyword"
    if nontoken:
        entry["qualifiers"]["nontoken"] = True
    if nonland:
        entry["qualifiers"]["nonland"] = True
    return entry


def card(card_id, name, type_line, facts):
    return {"id": card_id, "name": name, "typeLine": type_line, "autoBucketFacts": facts}


def deck(*cards):
    return {"deckTitle": "test", "cards": list(cards)}


LAND = card("land-1", "Swamp", "Basic Land — Swamp", [])
LAND2 = card("land-2", "Forest", "Basic Land — Forest", [])
PAYOFF = card("payoff-1", "Glacier Godmaw", "Creature — Leviathan",
              [fact("trigger", "react-to-enter", ["land"], "Whenever a land you control enters")])
TOKEN_MAKER = card("maker-1", "Secluded Starforge", "Land",
                   [fact("effect", "create", ["artifact", "robot", "token"], "Create a 2/2 colorless Robot artifact creature token.")])
NONTOKEN_PAYOFF = card("payoff-2", "Weapons Manufacturing", "Enchantment",
                       [fact("trigger", "react-to-enter", ["artifact"], "Whenever a nontoken artifact you control enters",
                             nontoken=True)])
BLOOD = card("payoff-3", "Bloodbriar", "Creature — Plant Elemental",
             [fact("trigger", "react-to-sacrifice", ["permanent"], "Whenever you sacrifice another permanent")])
OUTLET = card("outlet-1", "Hearthhull, the Worldseed", "Legendary Artifact — Spacecraft",
              [fact("cost", "sacrifice", ["land"], "{1}, {T}, Sacrifice a land")])
SOL = card("artifact-1", "Sol Ring", "Artifact", [])


def scale(per, amount, clause):
    return {"role": "effect", "action": "scale",
            "object": {"types": ["permanent", per], "name": per.capitalize()},
            "evidence": {"clause": clause}, "qualifiers": {"per": per, "amount": amount}}


AKIRI = card("akiri-1", "Akiri, Line-Slinger", "Legendary Creature — Kor Soldier Ally",
             [scale("artifact", "+1/0", "Akiri gets +1/+0 for each artifact you control.")])
EQUIPMENT = card("equip-1", "Diamond Pick-Axe", "Artifact — Equipment", [])
TREASURE_MAKER = card("treasure-1", "Pitiless Plunderer", "Creature — Human Pirate",
                      [fact("effect", "create", ["artifact", "token", "treasure"], "Create a Treasure token.")])


class CountedBoostTests(unittest.TestCase):
    """\"Gets +1/+0 for each artifact you control\" names no event, so it is a static relation: what
    affects the card is whatever supplies the count — artifacts and the cards that make them."""

    def test_artifacts_and_artifact_makers_affect_a_counted_boost(self):
        graph = core.links.build(deck(AKIRI, SOL, EQUIPMENT, TREASURE_MAKER, LAND))
        entry = graph["cards"]["akiri-1"]
        self.assertEqual(entry["fed_by"], [], "nothing triggers this, so it is not an event link")
        self.assertEqual(entry["feeds"], [])
        groups = {group["label"]: set(group["cards"]) for group in entry["improved_by"]}
        self.assertEqual(groups.get("artifacts you control (+1/0 each)"), {"artifact-1", "equip-1"})
        self.assertEqual(groups.get("artifacts you create (+1/0 each)"), {"treasure-1"})
        self.assertNotIn("land-1", set().union(*groups.values()))

    def test_the_supplier_names_the_payoff_it_grows(self):
        graph = core.links.build(deck(AKIRI, SOL, EQUIPMENT, TREASURE_MAKER, LAND))
        improves = {group["label"]: set(group["cards"]) for group in graph["cards"]["equip-1"]["improves"]}
        self.assertEqual(improves, {"+1/0 each for Akiri, Line-Slinger": {"akiri-1"}})

    def test_a_counted_boost_adds_no_event_pairs(self):
        graph = core.links.build(deck(AKIRI, SOL, EQUIPMENT, TREASURE_MAKER, LAND))
        self.assertEqual(graph["stats"]["pairs"], 0, "a static boost is never an audited event link")
        self.assertEqual(graph["stats"]["pairs_specific"], 0)

    def test_a_counted_cost_is_named_for_what_it_pays_for(self):
        """"Affinity for artifacts" is the same relation as a boost: the artifacts make the card
        cheaper. The group says so instead of printing a rate that does not exist."""
        affinity = card("cost-1", "Steelfin Whale", "Creature — Whale",
                        [{"role": "effect", "action": "reduce-cost",
                          "object": {"types": ["artifact"], "name": "Affinity for artifacts"},
                          "evidence": {"clause": "Affinity for artifacts"}, "qualifiers": {}}])
        graph = core.links.build(deck(affinity, SOL))
        entry = graph["cards"]["cost-1"]
        self.assertEqual([(g["label"], g["count"]) for g in entry["improved_by"]],
                         [("artifacts you control (1 less each)", 1)])
        self.assertEqual([g["label"] for g in graph["cards"]["artifact-1"]["improves"]],
                         ["makes Steelfin Whale cheaper"])


class JoinTests(unittest.TestCase):
    def test_land_entering_feeds_a_landfall_payoff(self):
        graph = core.links.build(deck(LAND, PAYOFF))
        entry = graph["cards"]["payoff-1"]
        self.assertEqual(len(entry["fed_by"]), 1)
        self.assertEqual(entry["fed_by"][0]["event"], "enter")
        self.assertEqual(entry["fed_by"][0]["cards"], ["land-1"])
        self.assertTrue(entry["fed_by"][0]["generic"])

    def test_a_card_never_links_to_itself(self):
        alone = card("x", "Omnath, Locus of Rage", "Legendary Creature — Elemental", [
            fact("trigger", "react-to-enter", ["land"], "Whenever a land you control enters"),
            fact("effect", "create", ["creature", "elemental", "token"], "Create a 5/5 Elemental token."),
        ])
        graph = core.links.build(deck(alone))
        entry = graph["cards"]["x"]
        self.assertEqual(entry["fed_by"], [])
        self.assertEqual(entry["feeds"], [])

    def test_self_triggers_do_not_count_as_reactions(self):
        # "When Phylath enters" is its own ETB, not a reaction to another card entering.
        self_etb = card("self", "Phylath, World Sculptor", "Creature — Elemental",
                        [fact("trigger", "react-to-enter", ["permanent", "this-permanent"], "When Phylath enters")])
        graph = core.links.build(deck(LAND, self_etb))
        self.assertEqual(graph["cards"]["self"]["fed_by"], [])

    def test_cast_this_spell_triggers_are_not_reactions(self):
        # "When you cast this spell" is the card's own cast trigger, never a reaction to
        # another card being cast.
        kraken = card("kraken", "Elder Deep-Fiend", "Creature — Eldrazi",
                      [fact("trigger", "react-to-cast", ["spell"], "When you cast this spell, tap up to four target permanents.")])
        spell = card("spell", "Time Stop", "Creature — Leviathan", [])
        graph = core.links.build(deck(spell, kraken))
        self.assertEqual(graph["cards"]["kraken"]["fed_by"], [])

    def test_token_cannot_feed_a_nontoken_payoff(self):
        graph = core.links.build(deck(TOKEN_MAKER, NONTOKEN_PAYOFF))
        self.assertEqual(graph["cards"]["payoff-2"]["fed_by"], [])

    def test_nontoken_payoff_is_fed_by_a_nontoken_artifact(self):
        graph = core.links.build(deck(SOL, NONTOKEN_PAYOFF))
        self.assertEqual(graph["cards"]["payoff-2"]["fed_by"][0]["cards"], ["artifact-1"])

    def test_a_plain_payoff_accepts_token_artifacts(self):
        watcher = card("watch", "Weftstalker Ardent", "Creature — Artificer",
                       [fact("trigger", "react-to-enter", ["artifact"], "Whenever another creature or artifact you control enters")])
        graph = core.links.build(deck(TOKEN_MAKER, watcher))
        ids = graph["cards"]["watch"]["fed_by"][0]["cards"]
        self.assertIn("maker-1", ids)

    def test_sacrificing_a_land_feeds_a_sacrifice_payoff(self):
        graph = core.links.build(deck(OUTLET, BLOOD))
        entry = graph["cards"]["payoff-3"]["fed_by"][0]
        self.assertEqual(entry["event"], "sacrifice")
        self.assertEqual(entry["cards"], ["outlet-1"])
        self.assertIn("Sacrifice a land", entry["clauses"][0]["cause"])

    def test_destroy_kills_but_damage_does_not(self):
        killer = card("killer", "Doom Blade", "Instant", [fact("effect", "destroy", ["creature"], "Destroy target creature.")])
        burner = card("burner", "Shock", "Instant", [fact("effect", "deal-damage", ["creature"], "Deal 2 damage to target creature.")])
        mourner = card("mourner", "Blood Artist", "Creature — Vampire",
                       [fact("trigger", "react-to-die", ["creature"], "Whenever a creature dies")])
        graph = core.links.build(deck(killer, burner, mourner))
        ids = graph["cards"]["mourner"]["fed_by"][0]["cards"]
        self.assertIn("killer", ids)
        self.assertNotIn("burner", ids)

    def test_casting_is_a_link_only_when_the_reaction_asks_for_it(self):
        watcher = card("caster", "Boreal Outrider", "Creature — Elf",
                       [fact("trigger", "react-to-cast", ["creature", "spell"], "Whenever you cast a creature spell")])
        spell = card("spell", "Refute", "Instant", [])
        graph = core.links.build(deck(spell, watcher))
        self.assertEqual(graph["cards"]["caster"]["fed_by"], [])

        creature = card("creature", "Llanowar Elves", "Creature — Elf", [])
        graph = core.links.build(deck(creature, watcher))
        self.assertEqual(graph["cards"]["caster"]["fed_by"][0]["cards"], ["creature"])

    def test_wrong_type_does_not_link(self):
        artifact = card("art", "Sol Ring", "Artifact", [])
        graph = core.links.build(deck(artifact, PAYOFF))
        self.assertEqual(graph["cards"]["payoff-1"]["fed_by"], [])

    def test_a_group_collapses_many_cards(self):
        graph = core.links.build(deck(LAND, LAND2, PAYOFF))
        groups = graph["cards"]["payoff-1"]["fed_by"]
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["count"], 2)
        self.assertEqual(len(groups[0]["cards"]), 2)

    def test_symmetry_holds_both_ways(self):
        graph = core.links.build(deck(LAND, TOKEN_MAKER, PAYOFF, SOL, BLOOD, OUTLET))
        for card_id, entry in graph["cards"].items():
            for group in entry["fed_by"]:
                for partner in group["cards"]:
                    back = graph["cards"][partner]["feeds"]
                    self.assertTrue(any(card_id in other["cards"] for other in back),
                                    f"{partner} feeds {card_id} but not the other way round")

    def test_every_group_carries_evidence(self):
        graph = core.links.build(deck(LAND, TOKEN_MAKER, PAYOFF))
        for entry in graph["cards"].values():
            for group in entry["fed_by"] + entry["feeds"]:
                self.assertTrue(group["clauses"])
                for clause in group["clauses"]:
                    self.assertTrue(clause["cause"] or clause["reaction"])

    def test_groups_and_ids_are_capped(self):
        payoffs = [card(f"p{i}", f"Payoff {i}", "Creature — Elemental",
                        [fact("trigger", "react-to-enter", ["land"], "Whenever a land you control enters")])
                   for i in range(8)]
        lands = [card(f"l{i}", f"Land {i}", "Land", []) for i in range(50)]
        graph = core.links.build(deck(*lands, *payoffs), max_groups=3, max_ids=10)
        entry = graph["cards"]["p0"]
        self.assertLessEqual(len(entry["fed_by"]), 3)
        self.assertLessEqual(len(entry["fed_by"][0]["cards"]), 10)


    def test_artifacts_leaving_the_battlefield_feed_a_sacrifice_outlet(self):
        """A payoff that reacts to an artifact leaving must see the outlet that sacrifices one."""
        anvil = card("anvil", "Oni-Cult Anvil", "Artifact", [
            fact("trigger", "react-to-leave", ["artifact"],
                 "Whenever one or more artifacts you control leave the battlefield during your turn"),
            fact("cost", "sacrifice", ["artifact"], "{T}, Sacrifice an artifact"),
        ])
        tea = card("tea", "Tea, Earl Grey, Hot", "Artifact — Food", [
            fact("cost", "sacrifice", ["artifact"], "{2}, {T}, Sacrifice this artifact"),
        ])
        view = core.links.for_card(deck(anvil, tea), "Oni-Cult Anvil")
        partners = [c["id"] for group in view["fed_by"] for c in group["cards"]]
        self.assertIn("tea", partners, view["fed_by"])
        self.assertIn("artifacts leaving the battlefield", view["fed_by"][0]["label"])


class StolenCardTests(unittest.TestCase):
    """"You control but don't own": the theft is what makes the boost exist."""

    def lord(self):
        clause = ("Each creature you control but don't own gets +2/+2, has menace and deathtouch, "
                  "and is a Pirate in addition to its other types.")
        grants = [
            fact("effect", "grant", ["deathtouch", "keyword"], clause, keyword="Deathtouch"),
            fact("effect", "grant", ["menace", "keyword"], clause, keyword="Menace"),
        ]
        grants[0]["qualifiers"]["statChange"] = "+2/+2"
        return card("lord", "Don Andres, the Renegade", "Legendary Creature — Vampire Pirate", grants)

    def test_the_theft_is_a_trigger_and_names_everything_the_ability_gives(self):
        treason = card("treason", "Act of Treason", "Sorcery", [
            fact("effect", "gain-control", ["creature"], "Gain control of target creature until end of turn.")])
        bear = card("bear", "Grizzly Bears", "Creature — Bear", [])
        view = core.links.for_card(deck(self.lord(), treason, bear), "Don Andres, the Renegade")
        self.assertEqual(view["improved_by"], [], "a theft is not a static effect on the lord")
        self.assertEqual(len(view["fed_by"]), 1, view["fed_by"])
        group = view["fed_by"][0]
        self.assertEqual([c["id"] for c in group["cards"]], ["treason"])
        # The label names the condition only: what the ability then does is the ability, not the trigger.
        self.assertEqual(group["label"], "taking control of an opponent's creature(s)")

    def test_the_theft_names_the_lord_on_the_other_side(self):
        treason = {"id": "treason", "name": "Act of Treason", "typeLine": "Sorcery",
                   "oracleText": "Gain control of target creature until end of turn.",
                   "autoBucketFacts": [fact("effect", "gain-control", ["creature"],
                                            "Gain control of target creature until end of turn.")]}
        view = core.links.for_card(deck(self.lord(), treason), "Act of Treason")
        self.assertEqual([c["id"] for group in view["feeds"] for c in group["cards"]], ["lord"])
        self.assertEqual(view["improves"], [])

    def test_a_boost_that_only_covers_stolen_creatures_triggers_nothing_in_the_deck(self):
        piracy = card("p", "Coastal Piracy", "Enchantment", [
            fact("trigger", "react-to-combat-damage", ["combat-damage", "player"],
                 "Whenever a creature you control deals combat damage to a player, you may draw a card.")])
        view = core.links.for_card(deck(self.lord(), piracy), "Don Andres, the Renegade")
        labels = {group["label"] for group in view["feeds"] + view["feeds_any_card"]}
        self.assertFalse([label for label in labels if "deathtouch" in label or "menace" in label],
                         "his deathtouch never lands on a deck creature, so it triggers nothing")

    def test_a_cast_trigger_on_cards_you_dont_own_is_fed_by_the_permission(self):
        """Casting their card, not putting it onto the battlefield: Zara goes to the static ability only."""
        lord = card("lord", "Don Andres, the Renegade", "Legendary Creature — Vampire Pirate", [
            fact("trigger", "react-to-cast", ["noncreature", "spell"],
                 "Whenever you cast a noncreature spell you don't own, create two tapped Treasure tokens.")])
        goods = {"id": "goods", "name": "Stolen Goods", "typeLine": "Sorcery",
                 "oracleText": ("Target opponent exiles cards from the top of their library until they exile "
                                "a nonland card. Until end of turn, you may cast that card without paying its "
                                "mana cost."),
                 "autoBucketFacts": []}
        key = {"id": "key", "name": "The Key to the Vault", "typeLine": "Artifact — Equipment",
               "oracleText": ("Look at that many cards from the top of your library. You may cast the exiled "
                              "card without paying its mana cost."),
               "autoBucketFacts": []}
        zara = {"id": "zara", "name": "Zara", "typeLine": "Legendary Creature — Human Pirate",
                "oracleText": ("Whenever Zara attacks, look at defending player's hand. You may put a creature "
                               "card from it onto the battlefield tapped and attacking."),
                "autoBucketFacts": []}
        view = core.links.for_card(deck(lord, goods, key, zara), "Don Andres, the Renegade")
        groups = {group["label"]: group for group in view["fed_by_any_card"] + view["fed_by"]}
        self.assertIn("lets you cast a card you don't own", groups)
        cast = [c["id"] for c in groups["lets you cast a card you don't own"]["cards"]]
        self.assertEqual(cast, ["goods"], "their library yes, your own library no, a put onto the battlefield no")
        self.assertNotIn("zara", cast, "putting a creature onto the battlefield is not casting it")


class ThresholdAndBridgeTests(unittest.TestCase):
    """Counting triggers, and payoffs that only a stolen or evasive creature can turn on."""

    def draw_trigger(self, threshold="second"):
        entry = fact("trigger", "react-to", ["card", "draw"],
                     f"Whenever you draw your {threshold} card each turn, put two +1/+1 counters on Morska.")
        entry["qualifiers"]["threshold"] = threshold
        return entry

    def test_a_single_draw_is_enough_on_your_own_turn(self):
        """The draw step already gave you the first card, so one more draw is a real enabler."""
        morska = card("m", "Morska, Undersea Sleuth", "Legendary Creature — Merfolk Detective",
                      [self.draw_trigger()])
        spell = card("s", "Water Whip", "Instant",
                     [fact("effect", "draw", ["card"], "Draw a card.")])
        view = core.links.for_card(deck(morska, spell), "Morska, Undersea Sleuth")
        self.assertEqual(len(view["fed_by"]), 1, view["fed_by"])
        self.assertEqual([c["id"] for c in view["fed_by"][0]["cards"]], ["s"])
        self.assertFalse(view["fed_by"][0]["conditional"])

    def test_a_card_drawing_two_satisfies_both_halves(self):
        morska = card("m", "Morska, Undersea Sleuth", "Legendary Creature — Merfolk Detective",
                      [self.draw_trigger()])
        big = card("b", "Tidings", "Sorcery", [fact("effect", "draw", ["card"], "Draw two cards.")])
        small = card("s", "Water Whip", "Instant", [fact("effect", "draw", ["card"], "Draw a card.")])
        view = core.links.for_card(deck(morska, big, small), "Morska, Undersea Sleuth")
        labels = {group["label"]: [c["id"] for c in group["cards"]] for group in view["fed_by"]}
        self.assertIn("b", labels["draws 2 or more cards at once"])
        self.assertEqual(labels["draws a card (your draw step is the first)"], ["s"])

    def test_a_draw_that_repeats_can_reach_the_count_on_their_turn(self):
        morska = card("m", "Morska, Undersea Sleuth", "Legendary Creature — Merfolk Detective",
                      [self.draw_trigger()])
        engine = card("e", "Mystic Skyfish", "Creature — Fish", [
            fact("effect", "draw", ["card"], "Whenever this creature attacks, draw a card.")])
        view = core.links.for_card(deck(morska, engine), "Morska, Undersea Sleuth")
        engine_group = [group for group in view["fed_by"] if "again on their turn" in group["label"]]
        self.assertTrue(engine_group, view["fed_by"])
        self.assertFalse(engine_group[0]["conditional"], "a repeatable draw is a real enabler")

    def test_a_saboteur_trigger_is_fed_by_what_lets_a_creature_connect(self):
        piracy = card("p", "Coastal Piracy", "Enchantment", [
            fact("trigger", "react-to-combat-damage", ["combat-damage", "player"],
                 "Whenever a creature you control deals combat damage to a player, you may draw a card.")])
        treason = card("t", "Act of Treason", "Sorcery", [
            fact("effect", "gain-control", ["creature"], "Gain control of target creature until end of turn."),
            fact("effect", "grant", ["haste", "keyword"], "It gains haste until end of turn.", keyword="Haste")])
        steam = card("s", "Steam Vents", "Land", [])
        view = core.links.for_card(deck(piracy, treason, steam), "Coastal Piracy")
        labels = {group["label"] for group in view["fed_by"]}
        self.assertIn("creatures you take can attack", labels)
        self.assertIn("creatures that gain haste", labels)

    def test_a_tribe_reaction_is_not_a_self_trigger(self):
        """"Whenever this creature or another Pirate you control enters" watches the whole tribe."""
        raiders = card("r", "Merchant Raiders", "Creature — Human Pirate", [
            fact("trigger", "react-to-enter", ["creature"],
                 "Whenever this creature or another Pirate you control enters, tap up to one target creature.")])
        pirate = card("p", "Kitesail Freebooter", "Creature — Human Pirate", [])
        bear = card("b", "Grizzly Bears", "Creature — Bear", [])
        view = core.links.for_card(deck(raiders, pirate, bear), "Merchant Raiders")
        fed = [c["id"] for group in view["fed_by"] + view["fed_by_any_card"] for c in group["cards"]]
        self.assertIn("p", fed)
        self.assertNotIn("b", fed)


class SubtypeCastTests(unittest.TestCase):
    """A cast trigger that names subtypes or card types must be fed by the cards it names."""

    def cast_trigger(self, types, **qualifiers):
        entry = fact("trigger", "react-to-cast", types,
                     "Whenever you cast a Kraken, Leviathan, Octopus, or Serpent spell from your hand, look at the top X cards.")
        entry["qualifiers"].update(qualifiers)
        return entry

    def test_a_subtype_list_is_fed_by_every_listed_subtype(self):
        kiora = card("kiora", "Kiora, Sovereign of the Deep", "Legendary Creature — Merfolk Noble",
                     [self.cast_trigger(["spell", "kraken", "leviathan", "octopus", "serpent"],
                                        subtypes=["kraken", "leviathan", "octopus", "serpent"])])
        kraken = card("k1", "Nadir Kraken", "Creature — Kraken", [])
        serpent = card("k2", "Harbor Serpent", "Creature — Serpent", [])
        wizard = card("k3", "Merfolk Wizard", "Creature — Merfolk Wizard", [])
        view = core.links.for_card(deck(kiora, kraken, serpent, wizard), "Kiora, Sovereign of the Deep")
        self.assertEqual(len(view["fed_by"]), 1, view["fed_by"])
        partners = sorted(c["id"] for c in view["fed_by"][0]["cards"])
        self.assertEqual(partners, ["k1", "k2"])
        self.assertIn("kraken", view["fed_by"][0]["label"])
        self.assertEqual(view["fed_by_any_card"], [], "no generic group once the subtype matched")

    def test_a_type_list_links_those_spells_without_claiming_a_synergy(self):
        storm = card("storm", "Thousand-Year Storm", "Enchantment",
                     [fact("trigger", "react-to-cast", ["spell", "instant", "sorcery"],
                           "Whenever you cast an instant or sorcery spell, copy it.")])
        burn = card("i1", "Lightning Bolt", "Instant", [])
        bear = card("c1", "Grizzly Bears", "Creature — Bear", [])
        view = core.links.for_card(deck(storm, burn, bear), "Thousand-Year Storm")
        self.assertEqual(view["fed_by"], [], "a bare 'instant or sorcery' is not a specific synergy")
        self.assertTrue(view["fed_by_any_card"], "an instant still feeds it, as an any-card pair")
        self.assertEqual([c["id"] for group in view["fed_by_any_card"] for c in group["cards"]], ["i1"])

    def test_a_mixed_list_accepts_either_half(self):
        whelp = card("whelp", "Firespitter Whelp", "Creature — Dragon",
                     [self.cast_trigger(["spell", "noncreature", "dragon"],
                                        subtypes=["dragon"], spellTypes=["noncreature"])])
        instant = card("i1", "Abrade", "Instant", [])
        dragon = card("d1", "Shivan Dragon", "Creature — Dragon", [])
        bear = card("c1", "Grizzly Bears", "Creature — Bear", [])
        view = core.links.for_card(deck(whelp, instant, dragon, bear), "Firespitter Whelp")
        fed = [c["id"] for group in view["fed_by"] for c in group["cards"]]
        any_card = [c["id"] for group in view["fed_by_any_card"] for c in group["cards"]]
        self.assertIn("d1", fed, "the Dragon half is a real pairing")
        self.assertIn("i1", any_card, "the noncreature half is the wider, any-card pairing")
        self.assertNotIn("c1", fed + any_card, "a plain creature is neither")


class StaticBoostTests(unittest.TestCase):
    """A group boost is not a trigger: no card reacts to it, so it is its own relation."""

    def boost_fact(self, keyword="Menace"):
        entry = fact("effect", "grant", ["keyword", keyword.lower()],
                     "Artifact creatures you control gain menace until end of turn.")
        entry["object"].update({"kind": "keyword", "name": keyword})
        return entry

    def test_a_group_boost_lists_the_cards_it_improves(self):
        source = card("boost-1", "Organic Avulsion Unit", "Artifact Creature — Borg", [self.boost_fact()])
        rovers = card("art-1", "Edge Rover", "Artifact Creature — Robot", [])
        driller = card("art-2", "Slagdrill Scrapper", "Artifact Creature — Robot", [])
        plain = card("creature-1", "Llanowar Elves", "Creature — Elf Druid", [])
        graph = core.links.build(deck(source, rovers, driller, plain))
        entry = graph["cards"]["boost-1"]
        improves = [group for group in entry["improves"]]
        self.assertEqual(len(improves), 1)
        self.assertEqual(sorted(improves[0]["cards"]), ["art-1", "art-2"])
        # An ordinary creature is not an artifact creature.
        self.assertNotIn("creature-1", improves[0]["cards"])
        # The stat block stays about event links: a static boost is not a pair.
        self.assertEqual(graph["stats"]["pairs"], 0)
        self.assertEqual(entry["fed_by"], [])
        self.assertEqual(entry["feeds"], [])
        self.assertEqual(graph["cards"]["art-1"]["improved_by"][0]["cards"], ["boost-1"])

    def test_a_creature_wide_boost_reaches_every_creature(self):
        source = card("boost-2", "Anthem", "Enchantment", [self.boost_fact()])
        source["autoBucketFacts"][0]["evidence"]["clause"] = "All creatures you control get +1/+1."
        first = card("c1", "Bear", "Creature — Bear", [])
        second = card("c2", "Artifact Bear", "Artifact Creature — Bear", [])
        graph = core.links.build(deck(source, first, second))
        self.assertEqual(sorted(graph["cards"]["boost-2"]["improves"][0]["cards"]), ["c1", "c2"])


    def test_a_targeted_boost_is_not_a_group_boost(self):
        """\"Up to one target creature you control gains haste\" improves one card, not the deck."""
        entry = self.boost_fact()
        entry["evidence"]["clause"] = "Up to one target creature you control gets +1/+0 and gains haste until end of turn."
        source = card("boost-3", "Dowsing Device", "Artifact", [entry])
        graph = core.links.build(deck(source, card("c1", "Bear", "Creature — Bear", [])))
        self.assertEqual(graph["cards"]["boost-3"]["improves"], [])


class FixtureDeckTests(unittest.TestCase):
    """Golden expectations on a real 9-card slice of the user's decks."""

    @classmethod
    def setUpClass(cls):
        cls.document = json.loads((FIXTURES / "links-deck.json").read_text(encoding="utf-8"))
        cls.graph = core.links.build(cls.document)
        cls.ids = {card["name"]: card["id"] for card in cls.document["cards"]}

    def group(self, name, side, event=None):
        entry = self.graph["cards"][self.ids[name]]
        for group in entry[side]:
            if event is None or group["event"] == event:
                return group
        return None

    def test_landfall_payoff_is_fed_by_the_lands(self):
        group = self.group("Phylath, World Sculptor", "fed_by", "enter")
        self.assertIsNotNone(group)
        self.assertEqual(group["count"], 3)
        self.assertIn(self.ids["Swamp"], group["cards"])
        self.assertIn(self.ids["Forest"], group["cards"])

    def test_nontoken_payoff_ignores_the_token_maker(self):
        group = self.group("Weapons Manufacturing", "fed_by", "enter")
        self.assertIsNotNone(group)
        self.assertIn(self.ids["Sol Ring"], group["cards"])
        self.assertNotIn(self.ids["Secluded Starforge"], group["cards"])

    def test_plain_payoff_does_see_the_token_maker(self):
        group = self.group("Weftstalker Ardent", "fed_by", "enter")
        self.assertIsNotNone(group)
        self.assertIn(self.ids["Secluded Starforge"], group["cards"])

    def test_sacrifice_outlet_feeds_the_sacrifice_payoff(self):
        rows = [g for g in self.graph["cards"][self.ids["Bloodbriar"]]["fed_by"] if g["event"] == "sacrifice"]
        self.assertTrue(rows)
        # The outlet spends the permanent; the land that makes a Robot artifact token hands over
        # something to spend. Same trigger, two different steps, so two rows with their own wording.
        self.assertIn(self.ids["Hearthhull, the Worldseed"], {cid for g in rows for cid in g["cards"]})
        maker = [g for g in rows if g["label"].endswith("tokens to sacrifice")]
        self.assertTrue(maker, [g["label"] for g in rows])
        self.assertIn(self.ids["Secluded Starforge"], maker[0]["cards"])

    def test_no_link_points_at_the_card_itself(self):
        for card_id, entry in self.graph["cards"].items():
            for group in entry["fed_by"] + entry["feeds"]:
                self.assertNotIn(card_id, group["cards"], entry["name"])

    def test_for_card_returns_names_and_clauses(self):
        view = core.links.for_card(self.document, "phylath")
        self.assertEqual(view["card"]["name"], "Phylath, World Sculptor")
        groups = view["fed_by"] or view["fed_by_any_card"]
        self.assertTrue(groups, "Phylath listens for lands entering")
        self.assertTrue(all(partner["name"] for partner in groups[0]["cards"]))
        self.assertTrue(groups[0]["clauses"][0]["reaction"])
        # the trivial "any land enters" links are reported apart from the real ones
        self.assertTrue(view["fed_by_any_card"])
        self.assertTrue(all(group["generic"] for group in view["fed_by_any_card"]))

    def test_hubs_are_ranked(self):
        hubs = core.links.hubs(self.document, limit=3)
        self.assertEqual(len(hubs), 3)
        self.assertGreaterEqual(hubs[0]["total"], hubs[-1]["total"])


class VoidTests(unittest.TestCase):
    """Void is a condition on two events; both must reach the graph."""

    def setUp(self):
        def card(cid, name, type_line, facts, buckets=()):
            return {"id": cid, "name": name, "typeLine": type_line, "autoBucketFacts": facts,
                    "autoBuckets": list(buckets)}

        warp_fact = fact("effect", "warp", ["spell"], "Warp {2}{U}")
        sacrifice_fact = fact("cost", "sacrifice", ["land"], "{1}, Sacrifice a land")
        exile_fact = fact("effect", "exile", ["creature"], "Exile target creature")
        void_leave = fact("trigger", "react-to-leave", ["permanent"],
                          "if a nonland permanent left the battlefield this turn", nonland=True)
        void_warp = fact("trigger", "react-to-warp", ["spell"], "or a spell was warped this turn")
        self.document = {"cards": [
            card("void", "Kavaron Skywarden", "Creature — Kavu Soldier", [void_leave, void_warp]),
            card("warp", "Weftstalker Ardent", "Creature — Human", [warp_fact]),
            card("sac", "Hearthhull, the Worldseed", "Legendary Creature — Dryad", [sacrifice_fact]),
            card("exile", "Pull Through the Weft", "Instant", [exile_fact]),
            card("plain", "Grizzly Bears", "Creature — Bear", []),
        ]}
        self.graph = core.links.build(self.document)

    def test_void_reacts_to_a_warped_spell(self):
        group = self.graph["cards"]["void"]["fed_by"]
        warped = [g for g in group if g["event"] == "warp"]
        self.assertTrue(warped, f"no warp group: {[g['event'] for g in group]}")
        self.assertEqual(list(warped[0]["cards"]), ["warp"])
        self.assertIn("warped", warped[0]["label"])

    def test_void_reacts_to_a_nonland_permanent_leaving(self):
        group = self.graph["cards"]["void"]["fed_by"]
        left = [g for g in group if g["event"] == "leave"]
        self.assertTrue(left, f"no leave group: {[g['event'] for g in group]}")
        ids = {cid for g in left for cid in g["cards"]}
        self.assertIn("exile", ids)
        self.assertIn("leaving the battlefield", left[0]["label"])
        self.assertTrue(left[0]["label"].endswith("leaving the battlefield"))

    def test_a_sacrificed_land_does_not_feed_void(self):
        """Void asks for a *nonland* permanent: Hearthhull's land sacrifice must not count."""
        group = self.graph["cards"]["void"]["fed_by"]
        ids = {cid for g in group for cid in g["cards"]}
        self.assertNotIn("sac", ids, f"a land sacrifice fed Void via {[g['label'] for g in group]}")

    def test_a_warp_card_feeds_and_is_not_itself_a_void_card(self):
        self.assertEqual(self.graph["cards"]["warp"]["fed_by"], [])


class AnyCardLinkTests(unittest.TestCase):
    """A card that merely exists is not a link; only an ability causing an event is."""

    def setUp(self):
        def fact(role, action, types, clause):
            return {"role": role, "action": action, "object": {"types": list(types)},
                    "evidence": {"clause": clause}, "qualifiers": {}}

        self.watcher = {"id": "w", "name": "Aatchik, Emerald Radian", "typeLine": "Creature — Insect",
                        "autoBucketFacts": [fact("trigger", "react-to-die", ["creature"],
                                                 "Whenever another creature dies")]}
        self.plain = {"id": "p", "name": "Grizzly Bears", "typeLine": "Creature — Bear", "autoBucketFacts": []}
        self.killer = {"id": "k", "name": "Doom Blade", "typeLine": "Instant",
                       "autoBucketFacts": [fact("effect", "destroy", ["creature"], "Destroy target creature")]}

    def test_a_creature_is_not_a_link_because_it_can_die(self):
        graph = core.links.build({"cards": [self.watcher, self.plain]})
        entry = graph["cards"]["w"]
        self.assertEqual([g for g in entry["fed_by"] if not g["generic"]], [])
        any_card = [g for g in entry["fed_by"] if g["generic"]]
        self.assertEqual([cid for g in any_card for cid in g["cards"]], ["p"])

    def test_a_removal_spell_is_a_real_link(self):
        graph = core.links.build({"cards": [self.watcher, self.killer]})
        entry = graph["cards"]["w"]
        real = [g for g in entry["fed_by"] if not g["generic"]]
        self.assertEqual([cid for g in real for cid in g["cards"]], ["k"])

    def test_the_two_kinds_never_share_a_group(self):
        graph = core.links.build({"cards": [self.watcher, self.plain, self.killer]})
        for group in graph["cards"]["w"]["fed_by"]:
            generic_ids = {cid for cid in group["cards"] if cid == "p"}
            self.assertEqual(bool(generic_ids), bool(group["generic"]),
                             "an any-card partner must sit in an any-card group")


class DeckScoreTests(unittest.TestCase):
    """How connected a deck is, and which deck is the most connected."""

    def setUp(self):
        self.document = json.loads((FIXTURES / "links-deck.json").read_text(encoding="utf-8"))

    def test_the_score_adds_the_cards_that_feed_and_the_cards_that_are_fed(self):
        score = core.links.deck_score(self.document)
        self.assertGreater(score["feeding"], 0)
        self.assertGreater(score["fed"], 0)
        self.assertEqual(score["score"], score["feeding"] + score["fed"])
        # a card that both feeds and is fed counts once in linked_cards, twice in score
        self.assertEqual(score["linked_cards"], score["feeding"] + score["fed"] - score["both"])
        self.assertEqual(score["cards"], len(self.document["cards"]))
        self.assertLessEqual(score["specific_score"], score["score"])

    def test_survey_ranks_the_connected_deck_first(self):
        plain = {"cards": [{"id": "a", "name": "Bear", "typeLine": "Creature — Bear", "autoBucketFacts": []}]}
        ranked = core.links.survey({"rich": self.document, "plain": plain})
        self.assertEqual(ranked["most"]["deck_id"], "rich")
        self.assertEqual(ranked["least"]["deck_id"], "plain")
        self.assertGreater(ranked["most"]["score"], ranked["least"]["score"])


class DeckSurveyToolTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = core.store.Store(Path(self.tmp.name))
        self.deck_id = self.store.create(sample_bundle())["deck_id"]

    def tearDown(self):
        self.tmp.cleanup()

    def test_mode_decks_needs_no_binding_and_ranks_every_deck(self):
        spare = json.loads(json.dumps(sample_bundle()))
        spare["cards"] = [{"id": "x1", "name": "Grizzly Bears", "typeLine": "Creature — Bear",
                           "autoBucketFacts": []}]
        spare["deckTitle"] = "Spare Plain Deck"
        second = self.store.create(spare)["deck_id"]
        out = json.loads(core.tools.dispatch("mtg_deck_studio_links", {"mode": "decks"},
                                             session_id="local-unbound", store=self.store))
        self.assertEqual(len(out["decks"]), 2)
        titles = {row["title"] for row in out["decks"]}
        self.assertEqual(len(titles), 2)
        self.assertIn("score", out["most"])
        self.assertGreaterEqual(out["most"]["score"], out["least"]["score"])
        self.assertIn(second, {row["deck_id"] for row in out["decks"]})

    def test_mode_decks_can_focus_one_deck(self):
        out = json.loads(core.tools.dispatch("mtg_deck_studio_links",
                                             {"mode": "decks", "deck_id": self.deck_id},
                                             session_id="local-unbound", store=self.store))
        self.assertEqual([row["deck_id"] for row in out["decks"]], [self.deck_id])


class CandidateTests(unittest.TestCase):
    """A card that is not in the deck yet still gets judged against it."""

    def setUp(self):
        self.document = json.loads((FIXTURES / "links-deck.json").read_text(encoding="utf-8"))

    def candidate(self):
        return {
            "scryfall_id": "11111111-2222-3333-4444-555555555555",
            "name": "Rampaging Baloths",
            "type_line": "Creature — Beast",
            "mana_cost": "{4}{G}{G}",
            "oracle_text": "Landfall — Whenever a land you control enters, create a 4/4 green Beast creature token.",
            "autoBucketFacts": [fact("trigger", "react-to-enter", ["land"], "Whenever a land you control enters")],
            "autoBuckets": ["lands/react-to/enter-land"],
        }

    def test_a_candidate_is_fed_by_the_deck_cards_that_trigger_it(self):
        view = core.links.evaluate(self.document, self.candidate())
        self.assertFalse(view["in_deck"])
        self.assertEqual(view["card"]["name"], "Rampaging Baloths")
        group = view["fed_by"][0]
        self.assertEqual(group["event"], "enter")
        names = {partner["name"] for partner in group["cards"]}
        self.assertIn("Swamp", names)
        self.assertIn("Secluded Starforge", names)

    def test_an_unclassified_candidate_is_rejected(self):
        entry = self.candidate()
        entry["autoBucketFacts"] = []
        with self.assertRaises(ValueError):
            core.links.evaluate(self.document, entry)

    def test_with_candidates_skips_entries_without_facts(self):
        plain = {"scryfall_id": "99999999-0000-0000-0000-000000000000", "name": "Unclassified"}
        virtual, extras = core.links.with_candidates(self.document, [self.candidate(), plain])
        self.assertEqual(len(virtual["cards"]), len(self.document["cards"]) + 1)
        self.assertEqual(list(extras), ["11111111-2222-3333-4444-555555555555"])
        self.assertTrue(extras["11111111-2222-3333-4444-555555555555"]["candidate"])


class LinksToolTests(unittest.TestCase):
    """The chat tool over a real deck document."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = core.store.Store(Path(self.tmp.name))
        self.deck_id = self.store.create(sample_bundle())["deck_id"]
        self.store.bind("local-a", self.deck_id)
        self.document = self.store.load(self.deck_id)

    def tearDown(self):
        self.tmp.cleanup()

    def call(self, args):
        return json.loads(core.tools.dispatch("mtg_deck_studio_links", args, session_id="local-a", store=self.store))

    def test_card_mode_reports_both_directions_with_clauses(self):
        graph = core.links.build(self.document)
        target = next(entry["name"] for entry in graph["cards"].values() if entry["fed_by"])
        result = self.call({"card": target})
        self.assertIn("fed_by", result)
        self.assertEqual(result["card"]["name"], target)
        self.assertTrue(result["fed_by"])
        group = result["fed_by"][0]
        self.assertGreater(group["count"], 0)
        self.assertTrue(group["cards"][0]["name"])
        self.assertTrue(group["clauses"][0]["reaction"])
        self.assertIn("next", result)

    def test_card_fragment_resolves(self):
        graph = core.links.build(self.document)
        name = next(entry["name"] for entry in graph["cards"].values() if entry["fed_by"] or entry["feeds"])
        result = self.call({"card": name.split(",")[0][:5].lower()})
        self.assertTrue(result["card"]["name"])

    def test_hubs_and_event_modes(self):
        hubs = self.call({"mode": "hubs", "limit": 5})
        self.assertLessEqual(len(hubs["hubs"]), 5)
        self.assertGreaterEqual(hubs["hubs"][0]["total"], hubs["hubs"][-1]["total"])
        event = self.call({"mode": "event", "event": "enter"})
        self.assertEqual(event["event"], "enter")
        self.assertTrue(event["causes"])
        self.assertTrue(event["stats"]["cards"])

    def test_candidate_mode_judges_a_sideboard_card_without_changing_the_deck(self):
        entry = {
            "scryfall_id": "11111111-2222-3333-4444-555555555555",
            "name": "Rampaging Baloths",
            "type_line": "Creature — Beast",
            "mana_cost": "{4}{G}{G}",
            "autoBucketFacts": [fact("trigger", "react-to-enter", ["land"], "Whenever a land you control enters")],
        }
        core.sideboard.add(self.store, self.deck_id, [entry], reason="check links")
        before = len(self.document["cards"])
        result = self.call({"mode": "candidate", "card": "Rampaging Baloths"})
        self.assertFalse(result["in_deck"])
        self.assertTrue(result["fed_by"])
        self.assertFalse(result["saved_to_sideboard"])
        self.assertEqual(len(self.store.load(self.deck_id)["cards"]), before)

    def test_candidate_mode_saves_the_card_to_the_sideboard(self):
        entry = {
            "scryfall_id": "22222222-3333-4444-5555-666666666666",
            "name": "Zendikar's Roil",
            "type_line": "Enchantment",
            "autoBucketFacts": [fact("trigger", "react-to-enter", ["land"], "Whenever a land you control enters")],
        }
        core.sideboard.add(self.store, self.deck_id, [entry], reason="check links")
        result = self.call({"mode": "candidate", "card": "Zendikar's Roil", "save": True})
        self.assertTrue(result["saved_to_sideboard"])
        self.assertTrue(core.sideboard.find_candidate(self.store, self.deck_id, "Zendikar's Roil"))

    def test_candidate_mode_refuses_a_card_already_in_the_deck(self):
        name = self.document["cards"][0]["name"]
        result = self.call({"mode": "candidate", "card": name})
        self.assertIn("error", result)

    def test_unknown_event_and_card_are_invalid(self):
        self.assertIn("error", self.call({"mode": "event", "event": "teleport"}))
        self.assertIn("error", self.call({"card": "Definitely Not A Card"}))


@unittest.skipUnless(DATA.exists(), "stored decks are not on this machine")
class RealDeckTests(unittest.TestCase):
    """Coverage and invariants over every deck in the player's library."""

    @classmethod
    def setUpClass(cls):
        cls.decks = []
        for path in sorted(DATA.glob("deck_*/document.json")):
            cls.decks.append(json.loads(path.read_text(encoding="utf-8")))

    def test_every_deck_builds_with_sane_size_and_links(self):
        self.assertTrue(self.decks)
        for document in self.decks:
            with self.subTest(deck=document.get("deckTitle")):
                graph = core.links.build(document)
                stats = graph["stats"]
                self.assertEqual(stats["cards"], len(document["cards"]))
                self.assertGreater(stats["pairs"], 0)
                # A deck cannot link every card to every card; the precision rules cut deep.
                self.assertLess(stats["pairs"], stats["cards"] * stats["cards"])
                # Real links (an ability causing an event) are a subset of all links: the rest are
                # "any card that enters/dies" pairs, which are true but are not synergies.
                self.assertLessEqual(stats["pairs_specific"], stats["pairs"])
                self.assertLessEqual(stats["linked_cards"], stats["cards"])
                self.assertLessEqual(stats["linked_cards"] + stats["any_card_only"], stats["cards"])

    def test_invariants_hold_on_every_deck(self):
        # Symmetry is a property of the graph, not of the panel. build() keeps the six loudest groups
        # per card and side for display, which legitimately drops a quiet partner from one side of a
        # pair while the other side still lists it — assert on the uncapped graph.
        for document in self.decks:
            graph = core.links.build(document, max_groups=10_000, max_ids=10_000)
            with self.subTest(deck=document.get("deckTitle")):
                for card_id, entry in graph["cards"].items():
                    self.assertNotIn(card_id, [pid for g in entry["fed_by"] for pid in g["cards"]])
                    self.assertNotIn(card_id, [pid for g in entry["feeds"] for pid in g["cards"]])
                    for group in entry["fed_by"]:
                        for partner in group["cards"]:
                            self.assertTrue(
                                any(card_id in other["cards"] for other in graph["cards"][partner]["feeds"]),
                                "asymmetric link")
                    for group in entry["fed_by"] + entry["feeds"]:
                        self.assertTrue(group["event"])
                        self.assertTrue(group["label"])
                        self.assertTrue(1 <= group["count"] <= len(document["cards"]))

    def test_self_referential_triggers_never_produce_links(self):
        # Any card whose only reactions are its own ETB/dies triggers must have no fed_by
        # for those events.
        for document in self.decks:
            graph = core.links.build(document)
            for card in document["cards"]:
                entry = graph["cards"][card["id"]]
                self_events = {
                    core.links.EVENT_OF_TRIGGER.get(f.get("action"))
                    for f in card.get("autoBucketFacts") or []
                    if f.get("role") == "trigger" and core.links._is_self_trigger(card, (f.get("evidence") or {}).get("clause", ""))
                }
                for group in entry["fed_by"]:
                    if group["event"] in self_events:
                        # allowed only when another card also reacts to that event
                        reactions = [
                            f for f in card.get("autoBucketFacts") or []
                            if f.get("role") == "trigger"
                            and core.links.EVENT_OF_TRIGGER.get(f.get("action")) == group["event"]
                            and not core.links._is_self_trigger(card, (f.get("evidence") or {}).get("clause", ""))
                        ]
                        self.assertTrue(reactions, f"{card['name']} links on a self trigger")


if __name__ == "__main__":
    unittest.main()
