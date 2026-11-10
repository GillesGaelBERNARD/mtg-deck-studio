"""The link rules that came out of the full audit (864 pairs, 16 decks).

Each test is one rule the audit proved necessary: a cause must really produce the event a
reaction listens for, with the right object, controller, zone and kind.
"""
import unittest

import test_core  # noqa: F401  (loads the plugin package)
core = test_core.core


def fact(role, action, types, clause, actor="you", scope="unspecified", **qualifiers):
    entry = {"role": role, "action": action, "object": {"types": list(types)},
             "evidence": {"clause": clause}, "qualifiers": dict(qualifiers),
             "actor": actor, "scope": scope}
    return entry


def card(cid, name, type_line, facts):
    return {"id": cid, "name": name, "typeLine": type_line, "autoBucketFacts": facts}


def graph_of(*cards):
    return core.links.build({"cards": list(cards)})


def real(groups):
    return [g for g in groups if not g["generic"]]


def ids(groups):
    return {cid for group in real(groups) for cid in group["cards"]}


def named(role, action, types, clause, name, actor="you", scope="unspecified", **qualifiers):
    entry = fact(role, action, types, clause, actor, scope, **qualifiers)
    entry["object"]["name"] = name
    return entry


def labels(groups):
    return [group["label"] for group in groups]


class CounterRecipientRule(unittest.TestCase):
    """Counters only trigger what they actually land on."""

    def test_a_card_that_counters_itself_does_not_feed_another_cards_counter_trigger(self):
        morska = card("morska", "Morska, Undersea Sleuth", "Legendary Creature — Fish Detective",
                      [fact("effect", "add", ["counter", "plus-one-plus-one"],
                            "put two +1/+1 counters on Morska.")])
        lonis = card("lonis", "Lonis, Genetics Expert", "Legendary Creature — Snake Elf",
                     [fact("trigger", "react-to-add", ["counter", "plus-one-plus-one"],
                           "Whenever one or more +1/+1 counters are put on Lonis")])
        self.assertEqual(ids(graph_of(morska, lonis)["cards"]["lonis"]["fed_by"]), set())

    def test_evolve_counters_itself_and_does_not_feed_the_other_trigger(self):
        fillet = card("fillet", "Ray Fillet, Wave Warrior", "Legendary Creature — Mutant",
                      [fact("trigger", "react-to-enter", ["creature", "larger-creature"], "Evolve"),
                       fact("effect", "add", ["counter", "plus-one-plus-one"], "Evolve")])
        lonis = card("lonis", "Lonis, Genetics Expert", "Legendary Creature — Snake Elf",
                     [fact("trigger", "react-to-add", ["counter", "plus-one-plus-one"],
                           "Whenever one or more +1/+1 counters are put on Lonis")])
        self.assertEqual(ids(graph_of(fillet, lonis)["cards"]["lonis"]["fed_by"]), set())

    def test_a_counter_for_another_creature_still_feeds_it(self):
        wizard = card("wizard", "Wizard Class", "Enchantment — Class",
                      [fact("effect", "add", ["counter", "plus-one-plus-one"],
                            "put a +1/+1 counter on target creature you control.", scope="target")])
        lonis = card("lonis", "Lonis, Genetics Expert", "Legendary Creature — Snake Elf",
                     [fact("trigger", "react-to-add", ["counter", "plus-one-plus-one"],
                           "Whenever one or more +1/+1 counters are put on Lonis")])
        self.assertEqual(ids(graph_of(wizard, lonis)["cards"]["lonis"]["fed_by"]), {"wizard"})

    def test_counters_on_a_group_you_control_still_feed_it(self):
        sidequest = card("sidequest", "Sidequest", "Enchantment",
                         [fact("effect", "add", ["counter", "plus-one-plus-one"],
                               "Put a +1/+1 counter on each creature you control.")])
        lonis = card("lonis", "Lonis, Genetics Expert", "Legendary Creature — Snake Elf",
                     [fact("trigger", "react-to-add", ["counter", "plus-one-plus-one"],
                           "Whenever one or more +1/+1 counters are put on Lonis")])
        self.assertEqual(ids(graph_of(sidequest, lonis)["cards"]["lonis"]["fed_by"]), {"sidequest"})


class SacrificeResourceRule(unittest.TestCase):
    """A card that makes the token a sacrifice payoff wants is a step towards that payoff."""

    def payoff(self):
        return card("payoff", "Lonis, Genetics Expert", "Legendary Creature — Snake Elf",
                    [named("trigger", "react-to-sacrifice", ["artifact", "clue", "token"],
                           "Whenever you sacrifice a Clue", "Clue")])

    def test_a_clue_maker_feeds_the_clue_payoff(self):
        maker = card("morska", "Morska, Undersea Sleuth", "Legendary Creature — Fish Detective",
                     [named("effect", "create", ["artifact", "clue", "token"], "investigate.", "Clue")])
        groups = graph_of(maker, self.payoff())["cards"]["payoff"]["fed_by"]
        self.assertEqual(ids(groups), {"morska"})
        self.assertIn("Clue tokens to sacrifice", labels(groups))
        self.assertTrue(all(not group["conditional"] for group in real(groups)))

    def test_a_food_maker_does_not_feed_a_clue_payoff(self):
        maker = card("cook", "Cooking Campsite", "Land",
                     [named("effect", "create", ["artifact", "food", "token"], "create a Food token.", "Food")])
        self.assertEqual(ids(graph_of(maker, self.payoff())["cards"]["payoff"]["fed_by"]), set())

    def test_an_artifact_sacrifice_payoff_takes_any_artifact_token(self):
        watcher = card("watch", "Disciple of the Vault", "Creature — Human Cleric",
                       [named("trigger", "react-to-sacrifice", ["artifact"], "Whenever an artifact is put into a graveyard",
                              "Artifact")])
        maker = card("cook", "Cooking Campsite", "Land",
                     [named("effect", "create", ["artifact", "food", "token"], "create a Food token.", "Food")])
        self.assertEqual(ids(graph_of(maker, watcher)["cards"]["watch"]["fed_by"]), {"cook"})

    def test_the_same_row_is_not_printed_twice(self):
        maker = card("morska", "Morska, Undersea Sleuth", "Legendary Creature — Fish Detective",
                     [named("effect", "create", ["artifact", "clue", "token"], "investigate.", "Clue")])
        groups = real(graph_of(maker, self.payoff())["cards"]["payoff"]["fed_by"])
        self.assertEqual(len(labels(groups)), len(set(labels(groups))))


class TokenTypeRule(unittest.TestCase):
    def test_an_artifact_token_does_not_enter_as_a_creature(self):
        maker = card("maker", "Rapacious Dragon", "Creature — Dragon",
                     [fact("effect", "create", ["artifact", "token", "treasure"], "create two Treasure tokens.")])
        watcher = card("watch", "Dragon Tempest", "Enchantment",
                       [fact("trigger", "react-to-enter", ["creature", "dragon"],
                             "Whenever a Dragon you control enters")])
        self.assertEqual(ids(graph_of(maker, watcher)["cards"]["watch"]["fed_by"]), set())

    def test_a_creature_token_does_enter(self):
        maker = card("maker", "Faebloom Trick", "Instant",
                     [fact("effect", "create", ["creature", "faerie", "token"],
                           "Create two 1/1 blue Faerie creature tokens with flying.")])
        watcher = card("watch", "Faerie Seer", "Creature — Faerie",
                       [fact("trigger", "react-to-enter", ["creature", "faerie"],
                             "Whenever another Faerie you control enters")])
        self.assertEqual(ids(graph_of(maker, watcher)["cards"]["watch"]["fed_by"]), {"maker"})

    def test_an_artifact_creature_token_counts_as_a_creature(self):
        maker = card("maker", "Animation Module", "Artifact",
                     [fact("effect", "create", ["artifact", "servo", "token"],
                           "create a 1/1 colorless Servo artifact creature token")])
        watcher = card("watch", "Elusive Krasis", "Creature — Fish Mutant",
                       [fact("trigger", "react-to-enter", ["creature"], "Evolve")])
        self.assertEqual(ids(graph_of(maker, watcher)["cards"]["watch"]["fed_by"]), {"maker"})


class ZoneRule(unittest.TestCase):
    def test_returning_a_card_to_hand_is_not_entering_the_battlefield(self):
        sower = card("sower", "Turntimber Sower", "Creature — Elf Druid",
                     [fact("effect", "return", ["graveyard", "land"],
                           "Return target land card from your graveyard to your hand.", from_="graveyard", to="hand")])
        landfall = card("land", "The Necrobloom", "Creature — Plant",
                        [fact("trigger", "react-to-enter", ["land"], "Whenever a land you control enters")])
        self.assertEqual(ids(graph_of(sower, landfall)["cards"]["land"]["fed_by"]), set())

    def test_returning_a_card_to_the_battlefield_enters(self):
        revive = card("revive", "Breath of Life", "Sorcery",
                      [fact("effect", "return", ["creature", "graveyard"],
                            "Return target creature card from your graveyard to the battlefield.",
                            from_="graveyard", to="battlefield")])
        watcher = card("watch", "Elas il-Kor, Sadistic Pilgrim", "Creature — Phyrexian Kor Cleric",
                       [fact("trigger", "react-to-enter", ["creature"],
                             "Whenever another creature you control enters")])
        self.assertIn("revive", ids(graph_of(revive, watcher)["cards"]["watch"]["fed_by"]))

    def test_exiling_is_not_dying(self):
        exile = card("exile", "Ride's End", "Instant",
                     [fact("effect", "exile", ["creature"], "Exile target creature")])
        watcher = card("watch", "Elas il-Kor", "Creature — Kor",
                       [fact("trigger", "react-to-die", ["creature"], "Whenever another creature you control dies")])
        self.assertEqual(ids(graph_of(exile, watcher)["cards"]["watch"]["fed_by"]), set())


class SelfTriggerRule(unittest.TestCase):
    def test_a_card_entering_does_not_react_to_its_own_name_trigger(self):
        runo = card("runo", "Runo Stromkirk // Krothuss, Lord of the Deep", "Legendary Creature — Vampire",
                    [fact("trigger", "react-to-enter", ["permanent"], "When Runo enters, create a token")])
        maker = card("maker", "Nadir Kraken", "Creature — Kraken",
                     [fact("effect", "create", ["creature", "kraken", "token"], "create a 1/1 blue Tentacle token")])
        self.assertEqual(ids(graph_of(maker, runo)["cards"]["runo"]["fed_by"]), set())


class ScopeRule(unittest.TestCase):
    def test_your_discard_does_not_feed_an_opponent_discard_trigger(self):
        refute = card("refute", "Refute", "Instant",
                      [fact("effect", "discard", ["card"], "Draw a card, then discard a card.", actor="you")])
        wake = card("wake", "Raiders' Wake", "Enchantment",
                    [fact("trigger", "react-to-discard", ["card"], "Whenever an opponent discards a card")])
        self.assertEqual(ids(graph_of(refute, wake)["cards"]["wake"]["fed_by"]), set())

    def test_an_opponents_discard_still_feeds_it(self):
        quandary = card("pq", "Painful Quandary", "Enchantment",
                        [fact("effect", "discard", ["card"], "that player loses 5 life unless they discard a card.")])
        wake = card("wake", "Raiders' Wake", "Enchantment",
                    [fact("trigger", "react-to-discard", ["card"], "Whenever an opponent discards a card")])
        self.assertEqual(ids(graph_of(quandary, wake)["cards"]["wake"]["fed_by"]), {"pq"})

    def test_mass_removal_of_opponents_creatures_is_not_your_creature_dying(self):
        wipe = card("wipe", "Angrath, Minotaur Pirate", "Planeswalker",
                    [fact("effect", "destroy", ["creature"], "Destroy each creature target opponent controls",
                          actor="opponent", scope="all")])
        crew = card("crew", "Rakish Crew", "Enchantment",
                    [fact("trigger", "react-to-die", ["creature"], "Whenever an outlaw you control dies")])
        self.assertEqual(ids(graph_of(wipe, crew)["cards"]["crew"]["fed_by"]), set())

    def test_a_targeted_destroy_can_still_hit_your_own_creature_but_only_loosely(self):
        twister = card("tw", "Desert Twister", "Sorcery",
                       [fact("effect", "destroy", ["permanent"], "Destroy target permanent",
                             actor="opponent", scope="target")])
        elas = card("elas", "Elas il-Kor", "Creature — Kor",
                    [fact("trigger", "react-to-die", ["creature"], "Whenever another creature you control dies")])
        entry = graph_of(twister, elas)["cards"]["elas"]["fed_by"]
        loose = [group for group in entry if group["generic"]]
        self.assertEqual({cid for group in loose for cid in group["cards"]}, {"tw"})
        self.assertTrue(all(group["conditional"] for group in loose))
        self.assertEqual(ids(entry), set(), "not a synergy unless you aim it at your own creature")


class SubtypeRule(unittest.TestCase):
    def test_a_destroy_spell_does_not_satisfy_a_subtype_restricted_death_trigger(self):
        sideswipe = card("sw", "Hellish Sideswipe", "Sorcery",
                         [fact("effect", "destroy", ["creature"], "Destroy target creature or Vehicle",
                               actor="opponent", scope="target")])
        aatchik = card("aat", "Aatchik, Emerald Radian", "Legendary Creature — Insect Druid",
                       [fact("trigger", "react-to-die", ["creature", "permanent"],
                             "Whenever another Insect you control dies")])
        entry = graph_of(sideswipe, aatchik)["cards"]["aat"]["fed_by"]
        self.assertEqual(ids(entry), set(), "a generic destroy cannot promise an Insect dying")

    def test_a_compound_token_type_satisfies_the_subtype(self):
        crew = card("crew", "Skeleton Crew", "Creature — Human Pirate",
                    [fact("effect", "create", ["creature", "skeleton-pirate", "token"],
                          "create a 2/2 black Skeleton Pirate creature token.")])
        forerunner = card("for", "Forerunner of the Coalition", "Creature — Human Pirate",
                          [fact("trigger", "react-to-enter", ["creature", "pirate"],
                                "Whenever another Pirate you control enters")])
        self.assertEqual(ids(graph_of(crew, forerunner)["cards"]["for"]["fed_by"]), {"crew"})

    def test_a_non_hydra_trigger_is_not_fed_by_a_hydra(self):
        hydra = card("hydra", "Phytohydra", "Creature — Plant Hydra", [])
        scourge = card("scourge", "Wildwood Scourge", "Creature — Hydra",
                       [fact("trigger", "react-to-enter", ["creature"],
                             "Whenever another non-Hydra creature you control enters")])
        self.assertEqual(ids(graph_of(hydra, scourge)["cards"]["scourge"]["fed_by"]), set())


class CounterKindRule(unittest.TestCase):
    def test_quest_counters_are_not_plus_one_counters(self):
        quest = card("quest", "Ior Ruin Expedition", "Enchantment",
                     [fact("effect", "add", ["counter", "quest"], "put a quest counter on this enchantment")])
        module = card("module", "Animation Module", "Artifact",
                      [fact("trigger", "react-to-add", ["counter", "plus-one-plus-one"],
                            "Whenever one or more +1/+1 counters are put on a permanent you control")])
        self.assertEqual(ids(graph_of(quest, module)["cards"]["module"]["fed_by"]), set())


class GrantedAbilityRule(unittest.TestCase):
    def test_a_tokens_quoted_ability_is_not_the_card_itself(self):
        icing = card("icing", "Icingdeath, Frost Tyrant", "Creature — Dragon",
                     [fact("effect", "create", ["artifact", "token"],
                           'create a token with "Whenever equipped creature attacks, tap target creature"'),
                      fact("effect", "tap", ["creature"],
                           'create a token with "Whenever equipped creature attacks, tap target creature"')])
        hylda = card("hylda", "Hylda of the Icy Crown", "Legendary Creature — Human",
                     [fact("trigger", "react-to-tap", ["permanent"],
                           "Whenever you tap one or more untapped creatures your opponents control")])
        self.assertEqual(ids(graph_of(icing, hylda)["cards"]["hylda"]["fed_by"]), set())

    def test_a_granted_ability_is_conditional(self):
        katana = card("katana", "Quintessential Katana", "Artifact — Equipment",
                      [fact("effect", "gain-life", ["life"],
                            'Equipped creature has "Whenever this creature deals combat damage, you gain 2 life."')])
        panda = card("panda", "Fiendish Panda", "Creature — Bear",
                     [fact("trigger", "react-to-gain-life", ["life"], "Whenever you gain life")])
        entry = graph_of(katana, panda)["cards"]["panda"]["fed_by"]
        # the ability is real but it depends on the equipped creature connecting
        self.assertEqual(ids(entry), set())
        loose = [group for group in entry if group["generic"]]
        self.assertEqual({cid for group in loose for cid in group["cards"]}, {"katana"})


class CoverageRule(unittest.TestCase):
    """Events that used to be invisible on either side of the join."""

    def test_a_draw_spell_feeds_a_draw_payoff(self):
        draw = card("draw", "Kiora, the Rising Tide", "Legendary Creature — Merfolk",
                    [fact("effect", "draw", ["card", "cards", "draw"], "draw two cards, then discard two cards.")])
        payoff = card("pay", "Nadir Kraken", "Creature — Kraken",
                      [fact("trigger", "react-to", ["card", "draw"], "Whenever you draw a card")])
        self.assertEqual(ids(graph_of(draw, payoff)["cards"]["pay"]["fed_by"]), {"draw"})

    def test_a_second_draw_counts_from_your_draw_step(self):
        """On your own turn the draw step already gave the first card, so one draw spell does reach a
        "second card each turn" payoff — and the group label says why."""
        draw = card("draw", "Ponder", "Sorcery",
                    [fact("effect", "draw", ["card", "draw"], "Draw a card.")])
        payoff = card("pay", "Alandra, Sky Dreamer", "Legendary Creature — Merfolk",
                      [fact("trigger", "react-to", ["card", "draw"], "Whenever you draw your second card each turn",
                            threshold="second")])
        groups = graph_of(draw, payoff)["cards"]["pay"]["fed_by"]
        self.assertEqual([(g["label"], g["generic"]) for g in groups],
                         [("draws a card (your draw step is the first)", False)])

    def test_a_second_spell_trigger_stays_an_any_card_pair(self):
        """"Whenever you cast your second spell each turn": every spell is one towards the count, so
        no card triggers it. Kept as an any-card pair it stays out of the panel; read as conditional
        it printed a tile claiming this creature turns the bonus on."""
        bear = card("bear", "Akiri, Line-Slinger", "Legendary Creature — Kor Soldier Ally", [])
        blastmaker = card("blast", "Breeches, the Blastmaker", "Legendary Creature — Goblin Pirate",
                          [fact("trigger", "react-to-cast", ["spell"],
                                "Whenever you cast your second spell each turn", threshold="second")])
        groups = graph_of(bear, blastmaker)["cards"]["blast"]["fed_by"]
        self.assertEqual([(g["label"], g["generic"], g["conditional"]) for g in groups],
                         [("spells cast (each one is one towards your second spell)", True, False)])

    def test_reanimation_enters_the_battlefield(self):
        brass = card("brass", "Admiral Brass, Unsinkable", "Legendary Creature — Human Pirate",
                     [fact("effect", "reanimate", ["creature", "graveyard"],
                           "return target Pirate creature card from your graveyard to the battlefield",
                           from_="graveyard", to="battlefield")])
        forerunner = card("for", "Forerunner of the Coalition", "Creature — Human Pirate",
                          [fact("trigger", "react-to-enter", ["creature", "pirate"],
                                "Whenever another Pirate you control enters")])
        self.assertEqual(ids(graph_of(brass, forerunner)["cards"]["for"]["fed_by"]), {"brass"})

    def test_returning_to_hand_leaves_the_battlefield_but_does_not_enter(self):
        bounce = card("bounce", "Into the Roil", "Instant",
                      [fact("effect", "return-to-hand", ["creature"], "Return target creature to its owner's hand")])
        landfall = card("land", "The Necrobloom", "Creature — Plant",
                        [fact("trigger", "react-to-enter", ["land"], "Whenever a land you control enters")])
        void = card("void", "Kavaron Skywarden", "Creature — Kavu",
                    [fact("trigger", "react-to-leave", ["permanent"], "if a nonland permanent left the battlefield",
                          nonland=True)])
        graph = graph_of(bounce, landfall, void)
        self.assertEqual(ids(graph["cards"]["land"]["fed_by"]), set())
        self.assertIn("bounce", ids(graph["cards"]["void"]["fed_by"]))

    def test_milling_fills_the_graveyard(self):
        mill = card("mill", "Admiral Brass, Unsinkable", "Creature — Human Pirate",
                    [fact("effect", "mill", ["card", "library"], "mill four cards.")])
        payoff = card("pay", "Necrotic Wound", "Instant",
                      [fact("trigger", "react-to-graveyard-enter", ["card", "graveyard"],
                            "Whenever a card is put into your graveyard")])
        self.assertEqual(ids(graph_of(mill, payoff)["cards"]["pay"]["fed_by"]), {"mill"})

    def test_a_snow_token_fires_a_snow_permanent_trigger(self):
        ring = card("ring", "Replicating Ring", "Snow Artifact",
                    [fact("effect", "create", ["artifact", "token"], "create eight colorless snow artifact tokens")])
        slumber = card("slumber", "Marit Lage's Slumber", "Legendary Snow Enchantment",
                       [fact("trigger", "react-to-enter", ["permanent"],
                             "Whenever another snow permanent you control enters")])
        self.assertEqual(ids(graph_of(ring, slumber)["cards"]["slumber"]["fed_by"]), {"ring"})


class ConditionalRule(unittest.TestCase):
    """Links that are real only behind a count, a board condition or another creature."""

    def test_a_draw_gated_on_an_opponents_count_does_not_feed_your_draw_payoff(self):
        unagi = card("unagi", "The Unagi of Kyoshi Island", "Legendary Creature — Serpent",
                     [fact("effect", "draw", ["card", "draw"],
                           "Whenever an opponent draws their second card each turn, you draw a card")])
        payoff = card("pay", "Nadir Kraken", "Creature — Kraken",
                      [fact("trigger", "react-to", ["card", "draw"], "Whenever you draw a card")])
        self.assertEqual(ids(graph_of(unagi, payoff)["cards"]["pay"]["fed_by"]), set())

    def test_a_conditional_cause_is_loose_not_a_synergy(self):
        ascension = card("asc", "Waterbender Ascension", "Enchantment",
                         [fact("effect", "draw", ["card", "draw"],
                               "If there are four or more quest counters on it, draw a card")])
        payoff = card("pay", "Nadir Kraken", "Creature — Kraken",
                      [fact("trigger", "react-to", ["card", "draw"], "Whenever you draw a card")])
        entry = graph_of(ascension, payoff)["cards"]["pay"]["fed_by"]
        self.assertEqual(ids(entry), set())
        self.assertTrue(all(group["conditional"] for group in entry if group["generic"]))

    def test_an_unconstrained_free_cast_cannot_feed_a_colour_restricted_trigger(self):
        marvo = card("marvo", "Marvo, Deep Operative", "Legendary Creature — Octopus",
                     [fact("effect", "cast-without-paying", ["cost-bypass", "spell"],
                           "you may cast a spell from your hand with mana value 8 or less without paying its mana cost")])
        eye = card("eye", "Kraken's Eye", "Artifact",
                   [fact("trigger", "react-to-cast", ["spell"], "Whenever a player casts a blue spell")])
        thing = card("thing", "Thing in the Ice", "Creature — Horror",
                     [fact("trigger", "react-to-cast", ["spell", "instant", "sorcery"],
                           "Whenever you cast an instant or sorcery spell")])
        graph = graph_of(marvo, eye, thing)
        self.assertEqual(ids(graph["cards"]["eye"]["fed_by"]), set())
        self.assertEqual(ids(graph["cards"]["thing"]["fed_by"]), set())

    def test_a_land_leaving_the_graveyard_does_not_feed_a_creature_card_trigger(self):
        sower = card("sower", "Turntimber Sower", "Creature — Elf",
                     [fact("effect", "return", ["graveyard", "land"],
                           "Return target land card from your graveyard to your hand", from_="graveyard", to="hand")])
        roots = card("roots", "Insidious Roots", "Enchantment",
                     [fact("trigger", "react-to-graveyard-leave", ["creature", "graveyard"],
                           "Whenever one or more creature cards leave your graveyard")])
        self.assertEqual(ids(graph_of(sower, roots)["cards"]["roots"]["fed_by"]), set())


if __name__ == "__main__":
    unittest.main()
