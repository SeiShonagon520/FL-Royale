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

    def test_unsupported_form_and_incomplete_deck_rejected(self):
        cards=[26000000,26000001,26000005,28000001,28000000,26000003,26000014,26000018]
        for value in (cards[:-1],cards[:7]+cards[:1],{"cards":cards,"forms":[4]*8}):
            with self.assertRaises(ValueError): create_match(json.dumps(value),"general")

if __name__=="__main__": unittest.main()
