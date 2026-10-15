"""The all-evolution mode must put eight evolved cards into the match.

The per-card `el` mask in deck*.sp is what the engine reads, so the regression
this guards is a fixture that declares an evolution deck but only equips the
single evo slot the stock deck editor would choose.
"""
import json
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
# on_device/python must precede build/mobile-semantics: the staged tree holds a
# copy of mobile_fair.py that would otherwise shadow the source.
sys.path.insert(0, str(ROOT / "build/mobile-semantics"))
sys.path.insert(0, str(ROOT / "on_device/python"))

import mobile_fair
from mobile_fair import RANDOM_DECK_TOWER_TROOPS
from native_runner.competitive_data import load_competitive_data
from native_runner.contracts import ROYAL_CHEF_TOWER_TROOP_ID
from native_runner.training.v4.factory import production_semantic_bundle

DECK = [26000000, 26000001, 26000005, 28000001, 28000000, 26000003, 26000014, 26000018]
HUMAN = {"cards": DECK, "forms": [0] * 8}
EVOLUTION_BIT = 1


def evo_cards():
    support = {int(r["card_id"]): r
               for r in load_competitive_data("card_support")["supported_cards"]}
    return {c for c, r in support.items() if int(r["form_availability"]) & EVOLUTION_BIT}


class AllEvolutionModeTest(unittest.TestCase):
    def setUp(self):
        self.evo = evo_cards()
        self.assertGreaterEqual(len(self.evo), 8, "not enough evolution cards to build a deck")

    def build(self):
        return json.loads(
            mobile_fair.create_match(json.dumps(HUMAN), "general", "", game_mode="all_evolution")
        )

    def test_mode_is_registered_and_reachable(self):
        self.assertIn("all_evolution", mobile_fair.GAME_MODES)
        picker = (ROOT / "on_device/java/com/firstlight/offline/GameModePicker.java").read_text(
            encoding="utf-8")
        self.assertIn('new Mode("all_evolution"', picker)

    def test_every_one_of_the_eight_cards_carries_the_evolution_bit(self):
        for _ in range(30):
            battle = self.build()["battle"]
            for owner in (0, 1):
                with self.subTest(owner=owner):
                    sp = battle[f"deck{owner}"]["sp"]
                    self.assertEqual(len(sp), 8)
                    masks = [int(row.get("el", 0)) for row in sp]
                    self.assertTrue(
                        all(m & EVOLUTION_BIT for m in masks),
                        f"only {sum(1 for m in masks if m & EVOLUTION_BIT)}/8 cards are evolutions: {masks}",
                    )

    def test_only_cards_with_an_evolution_are_dealt(self):
        for _ in range(30):
            battle = self.build()["battle"]
            for owner in (0, 1):
                for row in battle[f"deck{owner}"]["sp"]:
                    self.assertIn(row["d"], self.evo)

    def test_both_players_get_the_same_deck(self):
        battle = self.build()["battle"]
        self.assertEqual(battle["deck0"]["sp"], battle["deck1"]["sp"])

    def test_the_deck_stays_inside_the_semantic_subset(self):
        sub = production_semantic_bundle().semantic_subset_contract
        for _ in range(20):
            battle = self.build()["battle"]
            for owner in (0, 1):
                sp = battle[f"deck{owner}"]["sp"]
                sub.assert_deck([row["d"] for row in sp],
                                form_availability=[int(row.get("el", 0)) for row in sp],
                                label=f"deck{owner}")

    def test_form_choices_record_every_evolution(self):
        # deck*.cf is how the scene builder is told which cards are equipped;
        # eight entries means the engine is asked for eight evolutions.
        battle = self.build()["battle"]
        for owner in (0, 1):
            cf = battle[f"deck{owner}"].get("cf", {})
            with self.subTest(owner=owner):
                self.assertEqual(len(cf), 8, f"only {len(cf)} form choices recorded")
                self.assertTrue(all(v == 1 for v in cf.values()))

    def test_tower_is_never_the_royal_chef(self):
        battle = self.build()["battle"]
        for owner in (0, 1):
            tower = int(battle[f"deck{owner}"]["sc"][0]["d"])
            self.assertNotEqual(tower, ROYAL_CHEF_TOWER_TROOP_ID)
            self.assertIn(tower, RANDOM_DECK_TOWER_TROOPS)

    def test_normal_battles_keep_the_single_evo_slot_rule(self):
        # The all-evolution bypass must not leak into ordinary play.
        source = mobile_fair.__file__
        self.assertIn("allEvolved", pathlib.Path(source).read_text(encoding="utf-8"))
        battle = json.loads(
            mobile_fair.create_match(json.dumps(HUMAN), "general", "", game_mode="ladder")
        )["battle"]
        masks = [int(row.get("el", 0)) for row in battle["deck0"]["sp"]]
        self.assertTrue(all(m == 0 for m in masks),
                        "a plain ladder deck should equip no forms")
        with self.assertRaises(ValueError):
            mobile_fair.create_match(
                json.dumps({"cards": sorted(self.evo)[:8],
                            "forms": [int(0)] * 8,
                            "active": [1] * 8}),
                "general", "", game_mode="ladder")


if __name__ == "__main__":
    unittest.main()
