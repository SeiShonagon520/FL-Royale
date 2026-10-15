"""The probe report must survive logcat, and must round-trip back off the device.

Two device-only facts drove this file:

1. logcat silently drops a single entry whose payload exceeds roughly 4 KB. The
   first version folded the whole (tick,x,y) series into the summary JSON that
   Java passes to Log.i, and on the device the match ended with `result=1`
   ("local-app return Home"), the app returned to the lobby, and no
   `Battle completed` line was ever written. The evidence did not exist.
2. The reassembly on the host has to invert the chunking exactly, or the series
   comes back scrambled -- and a scrambled series would still "look like" data.

These tests pin both halves against the same sample set.
"""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "on_device/python"))
sys.path.insert(0, str(ROOT / "tools"))

import mobile_fair
import pve_probe_tracker
import run_pve_probe_on_device


class FakeLog:
    """Stands in for android.util.Log, and enforces logcat's real size limit.

    The limit is the whole point of the test: a recorder that does not reproduce
    the drop would happily accept the very payload that was lost on the device.
    """

    LIMIT = 4000

    def __init__(self, limit=LIMIT):
        self.lines = []
        self.dropped = []
        self.limit = limit

    def i(self, tag, message):
        if len(message) > self.limit:
            self.dropped.append(len(message))
            return
        self.lines.append(message)

    def w(self, tag, message):
        self.lines.append(message)


def build_tracks(track_count=3, samples_each=200):
    """A report shaped like a real one: several units, a long series each."""
    tracker = pve_probe_tracker.PositionTracker(sample_every_ticks=20)
    for tick in range(20, 20 + samples_each * 20, 20):
        for unit in range(track_count):
            tracker.maybe_sample({
                "tick": tick,
                "objects": [{
                    "owner": 1, "nativeObjectId": 100 + unit, "cardId": 26000002,
                    "x": 2500 + unit * 1000, "y": 20000 - tick // 2,
                }],
            })
    return tracker.report()


class ProbeReportEmissionTest(unittest.TestCase):
    def test_a_large_report_is_emitted_without_dropping_any_line(self):
        # The device failure: ~60 KB of series in one entry, silently discarded.
        report = build_tracks(track_count=3, samples_each=200)
        log = FakeLog()
        mobile_fair._emit_probe_report(log, report)
        self.assertEqual(log.dropped, [], f"logcat would drop: {log.dropped}")
        self.assertGreater(len(log.lines), 1, "a big report must span many lines")

    def test_every_sample_survives_the_chunking(self):
        report = build_tracks(track_count=3, samples_each=200)
        log = FakeLog()
        mobile_fair._emit_probe_report(log, report)
        rebuilt = run_pve_probe_on_device.parse_tracks("\n".join(log.lines))
        self.assertEqual(len(rebuilt), len(report["tracks"]))
        for original, restored in zip(report["tracks"], rebuilt):
            self.assertEqual(restored["owner"], original["owner"])
            self.assertEqual(restored["cardId"], str(original["cardId"]))
            self.assertEqual(restored["samples"],
                             [list(s) for s in original["samples"]])

    def test_a_single_very_long_track_is_split_rather_than_dropped(self):
        # One unit that survives the whole match is the worst case for chunking.
        report = build_tracks(track_count=1, samples_each=400)
        log = FakeLog()
        mobile_fair._emit_probe_report(log, report)
        self.assertEqual(log.dropped, [])
        rebuilt = run_pve_probe_on_device.parse_tracks("\n".join(log.lines))
        self.assertEqual(len(rebuilt[0]["samples"]), 400)

    def test_the_report_is_delimited(self):
        # Without explicit begin/end markers a truncated stream is
        # indistinguishable from a complete one.
        report = build_tracks(track_count=2, samples_each=5)
        log = FakeLog()
        mobile_fair._emit_probe_report(log, report)
        self.assertTrue(log.lines[0].startswith("PvE probe begin"))
        self.assertEqual(log.lines[-1], "PvE probe end")

    def test_no_track_line_exceeds_the_logcat_budget(self):
        report = build_tracks(track_count=4, samples_each=300)
        log = FakeLog()
        mobile_fair._emit_probe_report(log, report)
        for line in log.lines:
            self.assertLessEqual(len(line), mobile_fair.PROBE_LOG_BUDGET)

    def test_an_empty_report_still_brackets_cleanly(self):
        log = FakeLog()
        mobile_fair._emit_probe_report(log, {"tracks": [], "sampleEveryTicks": 20})
        self.assertEqual(log.dropped, [])
        self.assertEqual(run_pve_probe_on_device.parse_tracks("\n".join(log.lines)), [])


if __name__ == "__main__":
    unittest.main()