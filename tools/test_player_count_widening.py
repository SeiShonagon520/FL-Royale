"""Regression guard for the kPlayerCount widening (2 -> 4).

`kPlayerCount` is the widest supported match size, so loops bounded by it will
visit owner slots that simply do not exist in a 1v1 world. `read_player_state`
returns false for an absent slot. Any caller that treats that as a corrupt
world rather than "not in this match" rejects every deployment in a normal 1v1
battle, which silently disables all AI card plays.
"""
import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
PROBE = ROOT / "baseline/native_runner/probe"

COMBAT = (PROBE / "combat_event_telemetry.inc").read_text(encoding="utf-8")
CROWNS = (PROBE / "cr_replay_probe.cpp").read_text(encoding="utf-8")
RESIDENT = (PROBE / "resident_multimatch.inc").read_text(encoding="utf-8")


class PlayerCountWideningTest(unittest.TestCase):
    def test_capture_combat_deployment_tolerates_absent_owner_slots(self):
        # The owner-discovery loop must skip unreadable slots, not fail closed.
        start = COMBAT.index("for (std::int32_t candidate_owner = 0;")
        body = COMBAT[start:start + 600]
        self.assertIn("!read_player_state", body)
        self.assertIn("continue;", body,
                      "absent owner slots must be skipped with continue")
        self.assertNotIn(
            "rejected at world-player",
            body,
            "a missing owner slot is not a corrupt world in a 1v1 match")
        self.assertNotIn("return false;", body[:body.index("if (candidate.player != owner_root)")],
                         "an absent slot must not fail the whole validation")

    def test_deployment_validation_still_fails_closed_on_real_problems(self):
        # Skipping absent slots must not weaken the checks that follow.
        after = COMBAT.split("if (owner < 0 || player.player != owner_root")[1][:400]
        self.assertIn("return false", after,
                      "a deployment with no matching owner must still be rejected")
        self.assertIn("read_card_state", COMBAT)

    def test_crown_derivation_handles_both_match_sizes(self):
        # Crown scoring must branch on the detected player count, not assume 2.
        self.assertIn("active_players", CROWNS)
        # A 1v1 world must still take the simple two-owner path.
        self.assertIn("(active_players == 2) ? false", CROWNS)

    def test_team_crowns_sum_over_every_opposing_player(self):
        """A team match must score against the whole opposing team.

        The tally used to read a single representative (`enemy_rep`), which is
        the identity of the opponent in 1v1 but only one of two enemies in a
        team match: owners 0/1 scored against owner 2 alone and 2/3 against
        owner 0 alone, so the fourth seat's towers were never consulted and a
        team could lose its partner's king for nothing.
        """
        self.assertNotIn("const std::int32_t enemy_rep", CROWNS,
                         "the representative-based tally must be gone")
        self.assertIn("player_crowns[kPlayerCount]", CROWNS)
        self.assertIn("total += player_crowns[opponent];", CROWNS)
        # Teams are {0,1} versus {2,3}, the seating the 2v2 fixture declares.
        self.assertIn("(owner < 2) == (opponent < 2)", CROWNS)

    def test_observation_reports_the_active_player_count(self):
        self.assertIn("activePlayers", CROWNS,
                      "the capture must tell the policy how many players exist")

    def test_match_size_is_decided_by_readable_slots_not_one_probe(self):
        # A stale slot-2 pointer in a 1v1 world must not be mistaken for a
        # four-player match: that would either fail the whole frame or emit a
        # header claiming four players while only two are written.
        probes = re.findall(
            r"active_players = 2;\n"
            r"\s*if \(read_object_field<void \*>\(world, 0xe0 \+ 2 \* sizeof\(void \*\)\) != nullptr &&\n"
            r"\s*read_object_field<void \*>\(world, 0xe0 \+ 3 \* sizeof\(void \*\)\) != nullptr\)",
            CROWNS)
        self.assertEqual(len(probes), 2,
                         "both read_native_crowns and read_battle_result must verify slots 2 and 3")
        self.assertNotIn(
            "0xe0 + 2 * sizeof(void *)) != nullptr) ? 4 : 2",
            CROWNS,
            "a single-slot probe is not a safe match-size decision")

    def test_observation_header_matches_the_emitted_player_count(self):
        # Inside build_observation_json the header field and the player loop
        # must read the same verified count.
        start = CROWNS.index("bool build_observation_json(")
        body = CROWNS[start:start + 6000]
        self.assertIn("std::int32_t active_players = 2;", body)
        self.assertIn("winner_json, active_players,", body,
                      "the JSON header must use the verified count")
        self.assertIn("for (std::int32_t owner = 0; owner < active_players; ++owner) {", body)

    def test_resident_multimatch_is_not_reachable_from_the_local_app(self):
        # That loop still fails closed on owner slots. It is only safe because
        # the resident/headless socket path is never entered by the offline APK.
        self.assertIn("for (std::int32_t owner = 0; owner < kPlayerCount; ++owner)", RESIDENT)
        for token in ("handle_resident_env_command", "parse_resident_env_prefix",
                      "resident_start_mode"):
            self.assertIn(token, RESIDENT)
        # Resident mode is only activated from the headless control command and
        # additionally demands Headless runner mode, which the offline APK
        # never enters (it runs NativeRender).
        start_mode = RESIDENT[RESIDENT.index("bool resident_start_mode"):]
        start_mode = start_mode[:start_mode.index("\nbool ")]
        self.assertIn("RunnerMode::Headless", start_mode,
                      "resident mode must stay gated behind the headless runner")


if __name__ == "__main__":
    unittest.main()
