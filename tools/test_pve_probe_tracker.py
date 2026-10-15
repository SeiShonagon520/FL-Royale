"""The Stage 1 position tracker must be a trustworthy instrument.

Stage 1's entire verdict rests on this one class: if it silently drops the
enemy half of the capture, records a stale tick twice, or tracks two units of
the same card as one, then "the monster did not move" would be an artefact of
the probe rather than a fact about the engine. So these tests drive the tracker
with hand-built captures shaped exactly like build_observation_json's output and
assert the (tick,x,y) series is assembled correctly.
"""
import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "on_device/python"))

import pve_probe_tracker


def capture(tick, objects):
    """One native capture frame, shaped like the ordinary observation."""
    return {"tick": tick, "objects": objects}


def troop(owner, native_id, card_id, x, y, hp=100):
    return {"slot": 0, "nativeObjectId": native_id, "owner": owner, "cardId": card_id,
            "x": x, "y": y, "targetX": x, "targetY": y,
            "objectIndex": -1, "secondaryIndex": -1, "hp": hp, "maxHp": hp}


def tower(owner, native_id, x, y, princess=False):
    return {"slot": 0, "nativeObjectId": native_id, "owner": owner,
            "cardId": pve_probe_tracker.PositionTracker.TOWER_PRINCESS if princess
            else pve_probe_tracker.PositionTracker.TOWER_KING,
            "x": x, "y": y, "targetX": x, "targetY": y,
            "objectIndex": -1, "secondaryIndex": -1, "hp": 3000, "maxHp": 3000}


GOBLIN = 26000002


class PositionTrackerTest(unittest.TestCase):
    def test_a_walking_enemy_unit_produces_a_tick_xy_series(self):
        tracker = pve_probe_tracker.PositionTracker(sample_every_ticks=20)
        # An owner-1 goblin advancing toward the player at the top: y decreases.
        for step, tick in enumerate((20, 40, 60, 80)):
            frame = capture(tick, [troop(1, 77, GOBLIN, 2500, 20000 - step * 1000)])
            tracker.maybe_sample(frame)
        report = tracker.report()
        self.assertEqual(len(report["tracks"]), 1)
        self.assertEqual(report["tracks"][0]["samples"],
                         [(20, 2500, 20000), (40, 2500, 19000),
                          (60, 2500, 18000), (80, 2500, 17000)])

    def test_advance_toward_the_player_is_detected_as_lower_y(self):
        tracker = pve_probe_tracker.PositionTracker(sample_every_ticks=20)
        for step, tick in enumerate((20, 40, 60)):
            tracker.maybe_sample(capture(tick, [troop(1, 77, GOBLIN, 2500, 20000 - step * 500)]))
        track = next(iter(tracker.tracks.values()))
        self.assertTrue(track.moved_toward_lower_y())
        self.assertTrue(track.monotonic_y())

    def test_a_stationary_unit_is_not_reported_as_advancing(self):
        # The failure this Stage is most worried about: a monster that never
        # finds a target looks exactly like a walking one if you only record that
        # samples exist.
        tracker = pve_probe_tracker.PositionTracker(sample_every_ticks=20)
        for tick in (20, 40, 60):
            tracker.maybe_sample(capture(tick, [troop(1, 77, GOBLIN, 2500, 20000)]))
        track = next(iter(tracker.tracks.values()))
        self.assertFalse(track.moved_toward_lower_y())
        self.assertEqual(len(track.samples), 3)

    def test_towers_are_not_tracked(self):
        # Towers never walk, so including them would dilute the series with
        # static points and hide a real mover behind a wall of noise.
        tracker = pve_probe_tracker.PositionTracker(sample_every_ticks=20)
        frame = capture(20, [tower(1, 1, 3500, 29000), tower(0, 2, 3500, 3000, princess=True)])
        tracker.maybe_sample(frame)
        self.assertEqual(tracker.report()["tracks"], [])

    def test_neutral_and_unknown_owners_are_ignored(self):
        tracker = pve_probe_tracker.PositionTracker(sample_every_ticks=20)
        frame = capture(20, [troop(-1, 5, GOBLIN, 2500, 20000), troop(2, 6, GOBLIN, 2500, 20000)])
        tracker.maybe_sample(frame)
        self.assertEqual(tracker.report()["tracks"], [])

    def test_two_goblins_on_the_same_side_stay_separate_series(self):
        # Keying by card id alone would merge them into one line and produce a
        # series that jumps between two units -- unreadable as evidence.
        tracker = pve_probe_tracker.PositionTracker(sample_every_ticks=20)
        frame = capture(20, [troop(1, 77, GOBLIN, 2500, 20000),
                             troop(1, 78, GOBLIN, 14500, 20000)])
        tracker.maybe_sample(frame)
        self.assertEqual(len(tracker.report()["tracks"]), 2)

    def test_one_unit_is_followed_across_ticks_by_native_id(self):
        tracker = pve_probe_tracker.PositionTracker(sample_every_ticks=20)
        tracker.maybe_sample(capture(20, [troop(1, 77, GOBLIN, 2500, 20000)]))
        tracker.maybe_sample(capture(40, [troop(1, 77, GOBLIN, 2500, 19000)]))
        report = tracker.report()
        self.assertEqual(len(report["tracks"]), 1)
        self.assertEqual(report["tracks"][0]["samples"], [(20, 2500, 20000), (40, 2500, 19000)])

    def test_sampling_respects_the_tick_cadence(self):
        tracker = pve_probe_tracker.PositionTracker(sample_every_ticks=20)
        self.assertTrue(tracker.maybe_sample(capture(20, [troop(1, 77, GOBLIN, 2500, 20000)])))
        # A frame 5 ticks later is inside the window and must not be recorded.
        self.assertFalse(tracker.maybe_sample(capture(25, [troop(1, 77, GOBLIN, 2500, 19500)])))
        self.assertTrue(tracker.maybe_sample(capture(40, [troop(1, 77, GOBLIN, 2500, 19000)])))
        track = next(iter(tracker.tracks.values()))
        self.assertEqual([s[0] for s in track.samples], [20, 40])

    def test_a_repeated_tick_is_never_double_counted(self):
        # The local app can hand back the same boundary twice while the world
        # catches up; recording it twice would fabricate a stationary interval.
        tracker = pve_probe_tracker.PositionTracker(sample_every_ticks=20)
        tracker.maybe_sample(capture(20, [troop(1, 77, GOBLIN, 2500, 20000)]))
        self.assertFalse(tracker.maybe_sample(capture(20, [troop(1, 77, GOBLIN, 2500, 20000)])))
        self.assertEqual(len(next(iter(tracker.tracks.values())).samples), 1)

    def test_a_null_object_slot_is_skipped(self):
        tracker = pve_probe_tracker.PositionTracker(sample_every_ticks=20)
        tracker.maybe_sample(capture(20, [{"slot": 3, "null": True},
                                          troop(1, 77, GOBLIN, 2500, 20000)]))
        self.assertEqual(len(tracker.report()["tracks"]), 1)

    def test_a_frame_without_objects_is_not_an_error(self):
        tracker = pve_probe_tracker.PositionTracker(sample_every_ticks=20)
        self.assertTrue(tracker.maybe_sample(capture(20, [])))
        self.assertEqual(tracker.report()["tracks"], [])


if __name__ == "__main__":
    unittest.main()