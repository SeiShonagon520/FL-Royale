"""Local deck/form boundary checks against the pinned native match schema."""
import json
from pathlib import Path
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"build/mobile-semantics"))
from mobile_fair import create_match

class LocalMatchConfigurationTest(unittest.TestCase):
    def test_independent_opponent_and_native_form_choices(self):
        human={"cards":[26000000,26000014,26000018,28000001,28000000,26000003,26000001,26000005],
               "forms":[3,3,2,0,0,3,1,2],"choices":{"MiniPekka":2}}
        # Use the catalog's actual capability masks, including future base-only cards.
        from native_runner.competitive_data import load_competitive_data
        support={int(row["card_id"]):row for row in load_competitive_data("card_support")["supported_cards"]}
        human["forms"]=[int(support[card]["form_availability"]) for card in human["cards"]]
        hero_name=str(support[26000018]["name"])
        human["choices"]={hero_name:2}
        human["active"]=[1,2,2,0,0,0,0,0]
        enemy=[26000021,26000014,26000038,26000030,26000010,27000000,28000000,28000011]
        payload=json.loads(create_match(json.dumps(human),"general",json.dumps(enemy)))
        battle=payload["battle"]
        self.assertEqual([card["d"] for card in battle["deck1"]["sp"]],enemy)
        self.assertEqual(battle["deck0"]["cf"][hero_name],2)
        self.assertEqual([row.get("el",0) for row in battle["deck0"]["sp"]],[1,2,2,0,0,0,0,0])
        self.assertEqual((battle["lvlcap"],battle["cardlvlmin"]),(11,11))
        self.assertEqual([row["kt"] for row in battle["hbd"]],[11,11])
        self.assertEqual((payload["cmd"],payload["evt"]),([],[]))
        # A later player edit must not alter an explicitly saved opponent deck.
        human["cards"][0]=26000002
        human["forms"][0]=int(support[26000002]["form_availability"])
        human["active"][0]=0
        later=json.loads(create_match(json.dumps(human),"general",json.dumps(enemy)))
        self.assertEqual(later["battle"]["deck1"],battle["deck1"])

    def test_native_game_modes(self):
        human={"cards":[26000000,26000001,26000005,28000001,28000000,26000003,26000014,26000018],
               "forms":[0]*8}
        enemy=[26000021,26000014,26000038,26000030,26000010,27000000,28000000,28000011]
        modes={
            "ladder": 72000006,
            "double_elixir": 72000001,
            "triple_elixir": 72000476,
            "7x_elixir": 72000493,
            "sudden_death": 72000483,
            "ramp_up": 72000500,
            "mirror": 72000489,
            "all_random": 72000491,
            "2v2": 72000014,
        }
        for mode_key, expected_id in modes.items():
            payload=json.loads(create_match(json.dumps(human),"general",json.dumps(enemy),game_mode=mode_key))
            self.assertEqual(payload["battle"]["gamemode"],expected_id)
            deck0=[card["d"] for card in payload["battle"]["deck0"]["sp"]]
            deck1=[card["d"] for card in payload["battle"]["deck1"]["sp"]]
            self.assertEqual(len(deck0),8)
            self.assertEqual(len(deck1),8)
            if mode_key=="mirror":
                self.assertEqual(deck0,deck1)
            elif mode_key=="all_random":
                self.assertEqual(len(set(deck0)),8)
                self.assertEqual(len(set(deck1)),8)
            elif mode_key=="2v2":
                self.assertEqual(payload["battle"]["plt"],2)
                self.assertEqual(payload["battle"]["t1s"],2)
                self.assertEqual(payload["battle"]["t2s"],2)
                self.assertIn("deck2",payload["battle"])
                self.assertIn("deck3",payload["battle"])
                self.assertIn("avatar3",payload["battle"])
                self.assertEqual(len(payload["battle"]["hbd"]),4)

    def test_2v2_custom_teammate_and_random_mode(self):
        human={"cards":[26000000,26000001,26000005,28000001,28000000,26000003,26000014,26000018],
               "forms":[0]*8}
        teammate=[26000038,26000030,26000010,27000000,28000000,28000011,26000021,26000014]
        enemy1=[26000002,26000006,26000007,26000008,26000009,26000011,26000012,26000013]

        # 1. Test custom teammate and custom enemy in 2v2
        payload=json.loads(create_match(
            json.dumps(human), "general", json.dumps(enemy1),
            game_mode="2v2", teammate_json=json.dumps(teammate)
        ))
        battle=payload["battle"]
        self.assertEqual(battle["gamemode"],72000014)
        self.assertEqual([c["d"] for c in battle["deck0"]["sp"]], human["cards"])
        self.assertEqual([c["d"] for c in battle["deck1"]["sp"]], teammate)
        self.assertEqual([c["d"] for c in battle["deck2"]["sp"]], enemy1)
        self.assertEqual(len(battle["deck3"]["sp"]), 8)
        self.assertEqual(battle["avatar1"]["name"], "AI 队友 (Teammate)")

        # 2. Test 2v2_random mode
        rand_payload=json.loads(create_match(
            json.dumps(human), "general", json.dumps(enemy1),
            game_mode="2v2_random"
        ))
        rand_battle=rand_payload["battle"]
        self.assertEqual(rand_battle["gamemode"],72000014)
        self.assertEqual(rand_battle["plt"],2)
        self.assertEqual(len(rand_battle["hbd"]),4)
        for i in range(4):
            cards=[c["d"] for c in rand_battle[f"deck{i}"]["sp"]]
            self.assertEqual(len(cards), 8)
            self.assertEqual(len(set(cards)), 8)  # Distinct cards per player
            tower_id=rand_battle[f"deck{i}"]["sc"][0]["d"]
            self.assertIn(tower_id, (159000000, 159000001, 159000002, 159000004))

    def test_unsupported_form_and_incomplete_deck_rejected(self):
        cards=[26000000,26000001,26000005,28000001,28000000,26000003,26000014,26000018]
        for value in (cards[:-1],cards[:7]+cards[:1],{"cards":cards,"forms":[4]*8}):
            with self.assertRaises(ValueError): create_match(json.dumps(value),"general")

if __name__=="__main__": unittest.main()
