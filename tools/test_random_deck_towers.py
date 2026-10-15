"""Random-deck tower troops must survive BattleEnvV1's tower binding.

`reset()` calls `_bind_tower_object_keys` before the live-battle tolerance is
enabled, so a tower troop that does not occupy the standard cells raises
"initial native state does not contain exactly one tower at (9000, 3000) for
owner 0" and the local battle aborts with the "battle cannot continue" dialog.

Royal Chef (159000004) replaces the king tower, so it must never be dealt by a
random-deck mode. This is the exact failure reported on device.
"""
import json
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
# on_device/python must precede build/mobile-semantics: the staged build tree
# holds a copy of mobile_fair.py that would otherwise shadow the source.
sys.path.insert(0, str(ROOT / "build/mobile-semantics"))
sys.path.insert(0, str(ROOT / "on_device/python"))

import mobile_fair
from mobile_fair import RANDOM_DECK_TOWER_TROOPS
from native_runner.battle_env import TOWER_LAYOUT
from native_runner.contracts import COMPETITIVE_TOWER_TROOP_IDS, ROYAL_CHEF_TOWER_TROOP_ID

DECK = [26000000, 26000001, 26000005, 28000001, 28000000, 26000003, 26000014, 26000018]
HUMAN = {"cards": DECK, "forms": [0] * 8}


class RandomDeckTowerTest(unittest.TestCase):
    def test_royal_chef_is_excluded_from_the_random_pool(self):
        self.assertNotIn(ROYAL_CHEF_TOWER_TROOP_ID, RANDOM_DECK_TOWER_TROOPS,
                         "Royal Chef replaces the king tower and breaks tower binding")
        self.assertEqual(ROYAL_CHEF_TOWER_TROOP_ID, 159000004)

    def test_random_pool_is_a_real_subset_of_the_competitive_pool(self):
        for tower in RANDOM_DECK_TOWER_TROOPS:
            with self.subTest(tower=tower):
                self.assertIn(tower, COMPETITIVE_TOWER_TROOP_IDS)
        self.assertGreaterEqual(len(RANDOM_DECK_TOWER_TROOPS), 3)

    def test_random_towers_occupy_the_princess_cells_the_layout_expects(self):
        # _tower_troop_id returns None for a king cell unless the troop is the
        # Royal Chef, so a random troop only ever lands on a princess tower.
        king_cells = {(x, y) for _id, _o, kind, x, y in TOWER_LAYOUT if kind == "king"}
        self.assertEqual(king_cells, {(9000, 3000), (9000, 29000)})

    def test_all_random_never_deals_royal_chef(self):
        for _ in range(500):
            payload = json.loads(
                mobile_fair.create_match(json.dumps(HUMAN), "general", "", game_mode="all_random")
            )
            battle = payload["battle"]
            for owner in (0, 1):
                with self.subTest(owner=owner):
                    self.assertNotEqual(
                        int(battle[f"deck{owner}"]["sc"][0]["d"]), ROYAL_CHEF_TOWER_TROOP_ID
                    )
                    self.assertIn(int(battle[f"deck{owner}"]["sc"][0]["d"]),
                                  RANDOM_DECK_TOWER_TROOPS)

    def test_random_modes_do_not_use_a_local_tower_tuple_anymore(self):
        # Guards against a second, divergent pool being reintroduced in the
        # random branches. The decode() validation whitelist must still accept
        # Royal Chef, because the player can pick it by hand.
        import inspect
        source = inspect.getsource(mobile_fair.create_match)
        self.assertNotIn("towers = (159000000", source,
                         "a local random pool must not reappear")
        # Every random-deck branch (all_random deals two, all_evolution two,
        # 2v2_random one per slot) must draw from the shared constant.
        self.assertGreaterEqual(
            source.count("random.choice(RANDOM_DECK_TOWER_TROOPS)"), 5,
            "a random-deck branch stopped using the shared tower pool",
        )
        self.assertEqual(
            source.count("RANDOM_DECK_TOWER_TROOPS"),
            source.count("random.choice(RANDOM_DECK_TOWER_TROOPS)"),
            "the tower pool is referenced outside a random.choice call",
        )
        # The hand-picked whitelist keeps all four competitive towers.
        self.assertRegex(
            source,
            r"tower not in \(159000000,\s*159000001,\s*159000002,\s*159000004\)",
            "players must still be able to pick the Royal Chef by hand",
        )

    def test_all_random_still_deals_three_distinct_towers_over_time(self):
        seen = set()
        for _ in range(300):
            payload = json.loads(
                mobile_fair.create_match(json.dumps(HUMAN), "general", "", game_mode="all_random")
            )
            for owner in (0, 1):
                seen.add(int(payload["battle"][f"deck{owner}"]["sc"][0]["d"]))
        self.assertEqual(seen, set(RANDOM_DECK_TOWER_TROOPS),
                         "all three remaining tower troops should still be reachable")


if __name__ == "__main__":
    unittest.main()
