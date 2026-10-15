"""The official PvE wave table must stay loadable and legal on the 1v1 arena.

These tests are the regression guard for the Stage 1 verdict: the engine does
NOT execute pve_waves.csv on its own, so the official script is interpreted in
Python and expressed as absolute-tick card deploys. The generator is
tools/build_official_pve_waves.py and its output is on_device/python/
pve_official_waves.py.

What is locked here, and why each one matters:

* every exported spawn is a real native card id, on the deploy lattice, and
  legal for the enemy seat -- a coordinate the engine would refuse is a wave
  that silently never appears;
* the excluded waves are recorded by name rather than dropped, so an
  unexplained shrink of the export set is visible;
* the tower-footprint clashes are pinned, because they are the single hard
  blocker on a true single-sided PvE map and must not be "fixed" by quietly
  moving an official spawn point.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "build/mobile-semantics"))
sys.path.insert(0, str(ROOT / "on_device/python"))

import pve_official_waves as official
from native_runner import arena
from native_runner.training.v4.factory import production_semantic_bundle


def _specs():
    return {int(s.card_id): s for s in production_semantic_bundle().native_card_catalog.specs}


class OfficialWaveTableTest(unittest.TestCase):
    def test_the_generator_and_its_output_both_exist(self):
        self.assertTrue((ROOT / "tools/build_official_pve_waves.py").exists())
        self.assertTrue((ROOT / "on_device/python/pve_official_waves.py").exists())

    def test_the_official_table_was_actually_exported(self):
        # A generator that silently exports nothing would pass every other test
        # in this file, so the size of the export is asserted directly.
        self.assertGreaterEqual(len(official.WAVES_BY_NAME), 12)
        self.assertGreaterEqual(len(official.EXCLUDED_WAVES), 30)

    def test_every_exported_spawn_is_a_real_native_card(self):
        specs = _specs()
        for name, wave in official.WAVES_BY_NAME.items():
            for spawn in wave["spawns"]:
                with self.subTest(wave=name, card=spawn["card"]):
                    self.assertIn(spawn["card"], specs)

    def test_every_exported_spawn_sits_on_the_deploy_lattice(self):
        # The arena is a lattice of 1000-unit cells deployed at cell*1000+500.
        # The official table writes round thousands, so without the snap the
        # engine refuses the deploy: 1 of 83 coordinates was legal before the
        # transform and 63 of 83 after it.
        for name, wave in official.WAVES_BY_NAME.items():
            for spawn in wave["spawns"]:
                with self.subTest(wave=name, x=spawn["x"], y=spawn["y"]):
                    self.assertEqual(spawn["x"] % arena.CELL_UNITS,
                                     arena.CELL_UNITS // 2)
                    self.assertEqual(spawn["y"] % arena.CELL_UNITS,
                                     arena.CELL_UNITS // 2)
                    cx, cy = arena.world_to_cell(spawn["x"], spawn["y"])
                    self.assertEqual(spawn["x"], arena.cell_to_world((cx, cy))[0])
                    self.assertEqual(spawn["y"], arena.cell_to_world((cx, cy))[1])

    def test_every_exported_spawn_is_legal_for_the_enemy_seat(self):
        specs = _specs()
        for name, wave in official.WAVES_BY_NAME.items():
            for spawn in wave["spawns"]:
                with self.subTest(wave=name, card=spawn["card"]):
                    self.assertTrue(
                        arena.card_placement_legal_world(
                            specs[spawn["card"]], 1, spawn["x"], spawn["y"]
                        ),
                        f"{name} card {spawn['card']} at "
                        f"({spawn['x']},{spawn['y']}) is not a legal enemy deploy",
                    )

    def test_spawns_land_in_the_enemy_half(self):
        # A wave authored in the player's own territory would be rejected by the
        # enemy placement mask, so this is also a cheap mirror check.
        for name, wave in official.WAVES_BY_NAME.items():
            for spawn in wave["spawns"]:
                with self.subTest(wave=name):
                    self.assertGreaterEqual(spawn["y"], arena.GRID_HEIGHT * arena.CELL_UNITS // 2)

    def test_the_coordinate_transform_is_the_mirror_it_claims(self):
        # official y is measured from the player's own king tower; the exported
        # value must be its reflection through a 32000-unit arena, applied to the
        # snapped cell centre (the snap and the mirror compose, so the expected
        # value is derived the same way the generator derives it).
        cell = official.CELL_UNITS
        for name, wave in official.WAVES_BY_NAME.items():
            for spawn in wave["spawns"]:
                with self.subTest(wave=name):
                    snapped = (spawn["official_y"] // cell) * cell + cell // 2
                    self.assertEqual(spawn["y"], official.WORLD_HEIGHT - snapped)
                    snapped_x = (spawn["official_x"] // cell) * cell + cell // 2
                    self.assertEqual(spawn["x"], snapped_x)

    def test_time_in_line_is_milliseconds_and_converts_to_ticks(self):
        # The official table writes milliseconds; the engine runs at 20 ticks/s.
        # A caller that forgets the conversion schedules every wave 50x late.
        seen = 0
        for wave in official.WAVES_BY_NAME.values():
            for spawn in wave["spawns"]:
                cond = spawn["time_in_line_ms"]
                if ">=" not in cond:
                    continue
                tail = cond.split(">=", 1)[1].strip()
                if not tail.isdigit():
                    continue
                seen += 1
                ms = int(tail)
                tick = ms * official.TICKS_PER_SECOND // 1000
                self.assertGreater(tick, 0)
                # The bound is the match length, not the arena: a 30s wave is
                # tick 600, well inside a 7200-tick match.
                self.assertLessEqual(tick, 7200)
                self.assertEqual(tick, round(ms * official.TICKS_PER_SECOND / 1000))
        self.assertGreater(seen, 0, "no numeric time_in_line conditions were exported")

    def test_standalone_links_point_at_exported_waves_or_are_recorded(self):
        for name, wave in official.WAVES_BY_NAME.items():
            for target in wave["standalone"]:
                with self.subTest(wave=name, target=target):
                    self.assertTrue(
                        target in official.WAVES_BY_NAME
                        or target in official.EXCLUDED_WAVES,
                        f"{name} chains to unknown wave {target}",
                    )

    def test_the_tutorial_waves_are_excluded_for_the_documented_reason(self):
        # The prompt's acceptance case, Tutorial1, cannot be used as-is: its
        # Skeletons spawn at official (3500,7500), which is cell (3,24) after the
        # transform -- the enemy princess-tower footprint. It must be recorded
        # rather than emitted, so the blocker stays visible.
        self.assertIn("Tutorial1", official.EXCLUDED_WAVES)
        self.assertTrue(
            any(entry[0] == "Tutorial1" for entry in official.TOWER_FOOTPRINT_CLASHES),
            "Tutorial1's exclusion reason is no longer a tower-footprint clash",
        )

    def test_every_tower_clash_really_is_inside_a_tower_footprint(self):
        # This is the claim Stage 3 rests on, so it is asserted rather than
        # asserted-in-a-comment: all 20 remaining illegal coordinates are the
        # two 3x3 enemy princess-tower footprints and nothing else.
        cells = set()
        for centre_x, centre_y in ((3500, 25500), (14500, 25500)):
            cx, cy = arena.world_to_cell(centre_x, centre_y)
            for y in range(cy - 1, cy + 2):
                for x in range(cx - 1, cx + 2):
                    cells.add((x, y))
        self.assertEqual(len(cells), 18, "expected two 3x3 tower footprints")
        for entry in official.TOWER_FOOTPRINT_CLASHES:
            _name, _card, _ox, _oy, _ax, _ay, cx, cy = entry
            with self.subTest(cell=(cx, cy)):
                self.assertIn((cx, cy), cells)

    def test_tower_footprint_clashes_are_the_only_illegal_spawns(self):
        # Guards the export boundary. 20 illegal spawn *actions* collapse to 18
        # distinct (wave, card, coordinate) records, because two waves share the
        # same two tower-lane points. Both numbers are pinned: a change in either
        # means the upstream table or the transform moved, and the Stage 3
        # conclusion has to be re-derived rather than assumed.
        clashes = official.TOWER_FOOTPRINT_CLASHES
        self.assertEqual(len(clashes), 18, "distinct clash records changed")
        self.assertEqual(len(set(clashes)), len(clashes), "clash records must be distinct")
        # Every clash names a real, excluded wave and has the documented shape.
        for entry in clashes:
            with self.subTest(entry=entry):
                self.assertEqual(len(entry), 8)
                self.assertIn(entry[0], official.EXCLUDED_WAVES)
                self.assertIsInstance(entry[1], str)   # card name
                for value in entry[2:]:
                    self.assertIsInstance(value, int)

    def test_the_exported_action_types_stay_inside_the_expressible_subset(self):
        # Anything outside this set needs engine state we cannot observe
        # (SetVar*, PauseLogic, TriggerDefenseDeploy, SpawnFromTowerN).
        allowed = {"Spawn", "RestartWave", "EndWave", "SpawnWaveStandalone"}
        for name, wave in official.WAVES_BY_NAME.items():
            with self.subTest(wave=name):
                self.assertLessEqual(set(wave["action_types"]), allowed)


if __name__ == "__main__":
    unittest.main()
