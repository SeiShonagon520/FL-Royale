"""Stage 3: the AI teammate must cooperate, not double up on the human.

Two behaviours are specified and neither was implemented:

1. The teammate answers air with something that can reach it. The archetype
   decks are mostly ground-only, so an unanswered air push walks past the
   teammate and into the player's king tower.
2. The teammate does not cast the same spell the player is already casting on
   the same cluster. Both holding fireballs on one push burns half the team's
   elixir and leaves the rest of the wave unanswered.

These are board-level rules, so they are exercised through the real
`BotAgent.decide_action` entry point rather than by matching source text.
"""
import inspect
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "on_device" / "python"))

from team_bot import ANTI_AIR, CARDS, SPELLS, SWARMS, TANKS, BotAgent  # noqa: E402

HOME_MIDLINE = 16000


def frame(hand, elixir=5.0, objects=()):
    """A four-seat capture with the same hand on every seat."""
    players = [{"elixir": elixir, "hand": list(hand)} for _ in range(4)]
    return {"players": players, "objects": list(objects)}


def named(card_id):
    for name, value in CARDS.items():
        if value == card_id:
            return name
    return str(card_id)


class TeammateAirDefenceTest(unittest.TestCase):
    def test_air_is_answered_with_a_card_that_can_reach_it(self):
        bot = BotAgent(owner=1, role="teammate")
        hand = [
            {"cardId": CARDS["VALKYRIE"], "cost": 4, "handIndex": 0},
            {"cardId": CARDS["BABY_DRAGON"], "cost": 4, "handIndex": 1},
        ]
        ordinary = frame(hand, objects=[
            {"owner": 2, "hp": 800, "cardId": CARDS["BABY_DRAGON"],
             "x": 9000, "y": 8000, "isFlying": True},
        ])
        action = bot.decide_action(200, ordinary)
        self.assertIsNotNone(action)
        played = action["hand_index"]["cardId"]
        self.assertIn(played, ANTI_AIR,
                      "the teammate answered air with a ground-only card")
        self.assertLess(action["y"], HOME_MIDLINE,
                        "the answer must land on our own half")

    def test_air_is_prioritised_over_the_splash_preference(self):
        """A swarm of ground units must not distract from a nearer air push."""
        bot = BotAgent(owner=1, role="teammate")
        hand = [
            {"cardId": CARDS["FIREBALL"], "cost": 4, "handIndex": 0},
            {"cardId": CARDS["BABY_DRAGON"], "cost": 4, "handIndex": 1},
        ]
        ordinary = frame(hand, elixir=9.0, objects=[
            # Two ground units would otherwise trigger the spell branch.
            {"owner": 2, "hp": 300, "cardId": CARDS["MINIONS"], "x": 4000, "y": 9000},
            {"owner": 2, "hp": 300, "cardId": CARDS["MINIONS"], "x": 4400, "y": 9200},
            # ...and the air is the actual emergency.
            {"owner": 3, "hp": 500, "cardId": CARDS["MINIONS"],
             "x": 12000, "y": 3000, "isFlying": True},
        ])
        action = bot.decide_action(200, ordinary)
        self.assertIsNotNone(action)
        self.assertEqual(action["hand_index"]["cardId"], CARDS["BABY_DRAGON"],
                         "the teammate spellcast instead of answering air")

    def test_a_ground_card_is_still_used_when_no_anti_air_is_held(self):
        # Better a ground defender than a wasted card; the player can cover air.
        bot = BotAgent(owner=1, role="teammate")
        hand = [{"cardId": CARDS["VALKYRIE"], "cost": 4, "handIndex": 0}]
        ordinary = frame(hand, objects=[
            {"owner": 2, "hp": 800, "cardId": CARDS["HOG_RIDER"], "x": 9000, "y": 6000},
        ])
        action = bot.decide_action(200, ordinary)
        self.assertIsNotNone(action)
        self.assertEqual(action["hand_index"]["cardId"], CARDS["VALKYRIE"])


class TeammateSpellOverlapTest(unittest.TestCase):
    HAND = [
        {"cardId": CARDS["FIREBALL"], "cost": 4, "handIndex": 0},
        {"cardId": CARDS["ARROWS"], "cost": 3, "handIndex": 1},
    ]

    def test_the_teammate_does_not_recast_the_players_cluster(self):
        bot = BotAgent(owner=1, role="teammate")
        ordinary = frame(self.HAND, objects=[
            # The player has already answered this cluster.
            {"owner": 0, "hp": 1, "cardId": CARDS["FIREBALL"], "x": 9000, "y": 7000},
            {"owner": 2, "hp": 500, "cardId": CARDS["MINIONS"], "x": 9000, "y": 7000},
            {"owner": 2, "hp": 500, "cardId": CARDS["MINIONS"], "x": 9500, "y": 7200},
        ])
        action = bot.decide_action(200, ordinary)
        if action is not None:
            played = action["hand_index"]["cardId"]
            self.assertNotIn(played, (CARDS["FIREBALL"], CARDS["ARROWS"]),
                             "double-cast the cluster the player already answered")

    def test_the_teammate_does_spell_a_different_cluster(self):
        bot = BotAgent(owner=1, role="teammate")
        ordinary = frame(self.HAND, objects=[
            # The player answered a cluster on the far left...
            {"owner": 0, "hp": 1, "cardId": CARDS["FIREBALL"], "x": 3000, "y": 9000},
            # ...so the right-hand push is the teammate's to answer.
            {"owner": 2, "hp": 500, "cardId": CARDS["MINIONS"], "x": 13000, "y": 7000},
            {"owner": 2, "hp": 500, "cardId": CARDS["MINIONS"], "x": 13500, "y": 7200},
        ])
        action = bot.decide_action(200, ordinary)
        self.assertIsNotNone(action, "an unclaimed cluster must still be answered")
        self.assertIn(action["hand_index"]["cardId"], SPELLS)

    def test_a_rejected_spell_cannot_return_through_the_affordability_fallback(self):
        """The overlap check used to be silently undone one line later.

        `_find_card_in_hand` falls back to "any affordable card" when its
        candidate set misses. The spell branch rejected an overlapping fireball
        and then handed the very same card to that fallback, so the double-cast
        the new code was written to prevent still happened on every frame.
        """
        bot = BotAgent(owner=1, role="teammate")
        hand = list(self.HAND)
        chosen = bot._find_card_in_hand(hand, 9.0, SWARMS | TANKS,
                                        excluded={CARDS["FIREBALL"], CARDS["ARROWS"]})
        self.assertIsNone(chosen,
                          "the fallback ignored the exclusion and returned a "
                          "spell the caller had just rejected")


class TeammateDecisionEntryPointTest(unittest.TestCase):
    """Both defects below were found only on a real 2v2 match.

    The suite exercises the teammate through `decide_action`, which never
    reached either bug, and neither is a parse error -- so the build shipped a
    module that imported cleanly and then died on the first frame the teammate
    considered a spell.
    """

    def test_the_spell_lookup_receives_the_tick_it_reads(self):
        # The overlap window is relative to the current tick. Referencing a
        # `tick` that was never passed in is a NameError at the first spell
        # decision, which takes down the whole match ("Battle pipeline failed").
        import inspect
        source = inspect.getsource(BotAgent._find_unclaimed_spell)
        self.assertIn("tick", inspect.signature(
            BotAgent._find_unclaimed_spell).parameters)
        # And the caller must actually supply it.
        decide = inspect.getsource(BotAgent._decide_teammate)
        self.assertIn("_find_unclaimed_spell(tick,", decide)

    def test_a_chosen_card_entry_carries_the_native_slot(self):
        """The 2v2 hand entry is a dict, not a bare slot index.

        `queue_hand_action_at_for_owner` reads the true native slot, because the
        capture compacts the hand array and a list position is not that slot.
        The coordinator selects a card and hands over the whole entry, so the
        bridge has to read "handIndex" out of it. Coercing the entry directly
        raised `int() argument must be ... not 'dict'` on every AI seat, which
        looked on screen like three bots that simply never played.
        """
        from mobile_fair import DeviceNative

        source = inspect.getsource(DeviceNative.queue_hand_action_at_for_owner)
        self.assertIn('hand_index["handIndex"] if isinstance(hand_index,dict)',
                      source)


class AntiAirClassificationTest(unittest.TestCase):
    def test_splash_spells_are_not_sold_as_anti_air(self):
        # Fireball and arrows cannot touch a Baby Dragon. Offering them here is
        # how an air push got "answered" for four elixir and no damage.
        self.assertFalse(ANTI_AIR & SPELLS)

    def test_ground_only_cards_are_excluded(self):
        for card_id in (CARDS["VALKYRIE"], CARDS["HOG_RIDER"],
                        CARDS["KNIGHT"], CARDS["PEKKA"]):
            with self.subTest(card=named(card_id)):
                self.assertNotIn(card_id, ANTI_AIR)


if __name__ == "__main__":
    unittest.main()