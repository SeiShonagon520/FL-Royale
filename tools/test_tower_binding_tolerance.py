"""Regression guard: a battle must not fail on the very first capture.

Reproduced on device (ladder, all_random and all_evolution all failed) with
"BattleEnvError: initial native state does not contain exactly one tower at
(9000, 3000) for owner 0". The first capture is taken while the world is still
being built, so no tower exists at the fixed cells yet.
"""
import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
STAGED = ROOT / "build/mobile-semantics/native_runner/battle_env.py"
SOURCE = (ROOT / "tools/patch_mode_pacing.py").read_text(encoding="utf-8")


@unittest.skipUnless(STAGED.is_file(), "staged battle_env not present")
class TowerBindingToleranceTest(unittest.TestCase):
    def setUp(self):
        self.text = STAGED.read_text(encoding="utf-8")

    def test_missing_towers_bind_a_placeholder_instead_of_raising(self):
        body = self.text[self.text.index("def _bind_tower_object_keys"):]
        body = body[:body.index("def _tower_identity_for_item")]
        self.assertIn("if len(candidates) == 0:", body,
                      "a not-yet-spawned tower must not abort the battle")
        self.assertIn("bound[entity_id] = -(int(entity_id) + 1)", body)
        # The tolerance must come before the raise, and only cover the empty case.
        self.assertLess(body.index("if len(candidates) == 0:"), body.index("raise BattleEnvError("))

    def test_ambiguous_towers_still_fail_closed(self):
        body = self.text[self.text.index("def _bind_tower_object_keys"):]
        body = body[:body.index("def _tower_identity_for_item")]
        after = body[body.index("if len(candidates) == 0:"):]
        self.assertIn("raise BattleEnvError(", after,
                      "more than one candidate at a fixed cell is a real error")
        self.assertNotIn("len(candidates) == 0", after[after.index("raise BattleEnvError("):],
                         "the empty case must not reach the raise")

    def test_placeholder_identities_stay_unique(self):
        # -(entity_id + 1) is distinct per cell, so the uniqueness check holds.
        body = self.text[self.text.index("def _bind_tower_object_keys"):]
        body = body[:body.index("def _tower_identity_for_item")]
        self.assertIn("if len(set(bound.values())) != len(TOWER_LAYOUT):", body)

    def test_the_patch_is_declared_in_the_build_patch(self):
        self.assertIn("ORIGINAL_TOWER_BIND", SOURCE)
        self.assertIn("REPLACED_TOWER_BIND", SOURCE)
        self.assertIn("(ORIGINAL_TOWER_BIND, REPLACED_TOWER_BIND)", SOURCE)

    def test_tolerance_is_no_longer_gated_on_live_battle(self):
        # The old guard meant the tolerance only applied after reset(), so the
        # first frame of every battle raised instead.
        body = self.text[self.text.index("def _bind_tower_object_keys"):]
        body = body[:body.index("def _tower_identity_for_item")]
        self.assertNotIn('_is_live_battle', body.split("raise BattleEnvError(")[0],
                         "the empty-tower tolerance must not depend on live state")


if __name__ == "__main__":
    unittest.main()
