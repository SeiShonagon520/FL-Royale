"""A normal deck must be able to start a match.

Reproduced on device: every battle died with
"ValueError: deck role counts violate the V4 match contract" raised by the
tensorizer, because BattleEnvV1.reset() forwarded the catalog *support* mask
(deck*.sp[].el) as deck{owner}_form_availability. The tensorizer reads that
field as the EQUIPPED set and allows at most 2 evolutions, 2 hero cards and 3
in total; a typical skeleton deck can evolve five of its eight cards.
"""
import json
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "build/mobile-semantics"))
sys.path.insert(0, str(ROOT / "on_device/python"))

import mobile_fair
from native_runner.competitive_data import load_competitive_data
from native_runner.training.v4.factory import deck_roles_from_form_availability

SUPPORT = {int(r["card_id"]): r
           for r in load_competitive_data("card_support")["supported_cards"]}

# The exact deck the failing match used, taken from the device log.
SKELETON_DECK = [26000007, 26000069, 26000013, 26000012,
                 26000056, 26000025, 26000010, 28000010]

MAX_EVOLUTIONS = 2
MAX_HEROES = 2
MAX_TOTAL = 3


def contract_ok(masks):
    roles = deck_roles_from_form_availability(SKELETON_DECK, tuple(masks))
    evo = sum(1 for v in roles.values() if v[1])
    hero = sum(1 for v in roles.values() if v[0])
    return evo <= MAX_EVOLUTIONS and hero <= MAX_HEROES and evo + hero <= MAX_TOTAL, evo, hero


def fixture(mode, deck, forms=None, active=None):
    human = {
        "cards": deck,
        "forms": forms if forms is not None else [0] * 8,
        "tower": 159000000,
    }
    if active is not None:
        human["active"] = active
    return json.loads(
        mobile_fair.create_match(json.dumps(human), "general", "", game_mode=mode)
    )["battle"]


class DeckRoleContractTest(unittest.TestCase):
    def test_the_support_mask_alone_does_violate_the_contract(self):
        # Documents why the fix was needed; if this ever passes, the guard in
        # MobileFair is no longer load-bearing.
        support_mask = tuple(int(SUPPORT[c]["form_availability"]) for c in SKELETON_DECK)
        ok, evo, hero = contract_ok(support_mask)
        self.assertFalse(ok, "the support mask unexpectedly satisfies the contract")
        self.assertGreater(evo, MAX_EVOLUTIONS)

    def test_every_exposed_mode_reports_equipped_forms_only(self):
        # The policy view is always clamped to the V4 contract, whatever the
        # engine is told. "all_evolution" hands the engine eight evolutions, so
        # this is the case that would otherwise abort on the first decision.
        for mode in sorted(mobile_fair.GAME_MODES):
            if mode in ("2v2", "2v2_random"):
                continue
            with self.subTest(mode=mode):
                battle = fixture(mode, SKELETON_DECK)
                masks = mobile_fair.policy_form_masks(battle["deck0"])
                ok, evo, hero = contract_ok(masks)
                self.assertTrue(ok, f"{mode}: evo={evo} hero={hero}")
                masks1 = mobile_fair.policy_form_masks(battle["deck1"])
                ok1, evo1, hero1 = contract_ok(masks1)
                self.assertTrue(ok1, f"{mode} owner 1: evo={evo1} hero={hero1}")

    def test_all_evolution_keeps_the_engine_deck_intact(self):
        # The clamp is a policy-view concern only; the engine must still see
        # eight evolutions or the mode is not doing what it says.
        battle = fixture("all_evolution", SKELETON_DECK)
        cf = battle["deck0"].get("cf") or {}
        self.assertEqual(len(cf), 8, f"engine should see 8 equipped forms, got {cf}")
        masks = mobile_fair.policy_form_masks(battle["deck0"])
        ok, evo, hero = contract_ok(masks)
        self.assertTrue(ok, f"policy view still violates the contract: {masks}")
        self.assertLessEqual(evo, MAX_EVOLUTIONS)

    def test_an_equipped_deck_still_reports_its_forms(self):
        # The fix must not silently drop the forms the player did equip.
        forms = [int(SUPPORT[c]["form_availability"]) for c in SKELETON_DECK]
        active = [0] * 8
        active[0] = 1  # evolve the first card
        battle = fixture("ladder", SKELETON_DECK, forms=forms, active=active)
        cf = battle["deck0"].get("cf") or {}
        self.assertEqual(len(cf), 1, f"expected one equipped form, got {cf}")
        masks = [0] * 8
        for slot, row in enumerate(battle["deck0"]["sp"]):
            form = cf.get(SUPPORT[row["d"]]["name"], 0)
            masks[slot] = 1 if form == 1 else 2 if form == 2 else 0
        ok, evo, hero = contract_ok(masks)
        self.assertTrue(ok)
        self.assertEqual(evo, 1)

    def test_mobile_fair_derives_masks_from_cf_not_sp(self):
        # Static guard: the policy view must come from deck*.cf, never from the
        # engine's sp[].el support mask.
        import inspect
        init_source = inspect.getsource(mobile_fair.MobileFair.__init__)
        self.assertIn("policy_form_masks(", init_source)
        self.assertNotIn('row.get("el"', init_source)

        helper = inspect.getsource(mobile_fair.policy_form_masks)
        self.assertIn('get("cf")', helper)
        self.assertNotIn('get("el"', helper,
                         "policy_form_masks must ignore the engine support mask")

    def test_policy_form_masks_clamps_surplus_forms(self):
        deck = {
            "sp": [{"d": 26000013}, {"d": 26000001}, {"d": 26000000}, {"d": 26000018}],
            "cf": {"Bomber": 1, "Archer": 1, "Knight": 1, "MiniPekka": 1},
        }
        masks = mobile_fair.policy_form_masks(deck)
        # Four evolutions requested; the contract keeps at most two.
        self.assertEqual(sum(1 for m in masks if m == 1), 2)
        roles = deck_roles_from_form_availability(
            [26000013, 26000001, 26000000, 26000018] + [26000000] * 4, masks + (0,) * 4)
        evo = sum(1 for v in roles.values() if v[1])
        self.assertLessEqual(evo, MAX_EVOLUTIONS)

    def test_policy_form_masks_keeps_heroes_over_evolutions(self):
        deck = {
            "sp": [{"d": 26000013}, {"d": 26000001}, {"d": 26000069}],
            "cf": {"Bomber": 1, "Archer": 1, "SkeletonKing": 2},
        }
        masks = mobile_fair.policy_form_masks(deck)
        self.assertEqual(masks[2], 2, "the hero card must survive the clamp")
        self.assertEqual(sum(1 for m in masks if m), 3,
                         "at most three forms in total")

    def test_all_evolution_mode_still_reports_eight_cards_to_the_engine(self):
        # The engine keeps the full mask; only the policy view is narrowed.
        battle = fixture("all_evolution", SKELETON_DECK)
        masks = [int(r.get("el", 0)) for r in battle["deck0"]["sp"]]
        self.assertEqual(len(masks), 8)


if __name__ == "__main__":
    unittest.main()
