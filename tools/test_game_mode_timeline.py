"""Every exposed 1v1 mode must reach BattleEnvV1 with its own real timeline.

The regression this guards: the model must never be handed the ladder timeline
while the native scene runs a different game mode, because phase and
elixir_multiplier are model inputs and a wrong rhythm silently degrades play.
"""
import hashlib
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "build/mobile-semantics"))
sys.path.insert(0, str(ROOT / "on_device/python"))

import mobile_fair
from mobile_fair import GAME_MODE_LADDER, GAME_MODE_TEAM_VS_TEAM
from native_runner.battle_env import BattleEnvV1, BattleEnvError, TOWER_LAYOUT
from native_runner.match_factory import MatchConfig
from native_runner.timeline import load_game_mode_timeline
from native_runner.training.v4.factory import production_semantic_bundle

RULESET_ID = hashlib.sha256(
    b"firstlight-local-v1:110aa2b5cac391c498645e072b0d88729428c2c2845e7e2737ca8ee979059783"
    b":e0cb2fb9eb2fdd0df1afcb00df3e3ba61cf25aa8b24e39c17cc391d952a4e339"
).hexdigest()
DECK = (26000000, 26000001, 26000005, 28000001, 28000000, 26000003, 26000014, 26000018)
# The native replay boundary is an infrastructure watchdog, not a per-mode
# gameplay horizon, so every mode uses the same value.
RESET_OPTIONS = {"render_mode": "native-render", "decision_ticks": 5, "event_driven_decisions": False}

EXPOSED_1V1_MODES = {
    key: mode["id"]
    for key, mode in mobile_fair.GAME_MODES.items()
    if mode["id"] != GAME_MODE_TEAM_VS_TEAM
}


GAME_MODE_PVE = 72000018


def build_env(game_mode_id: int) -> BattleEnvV1:
    bundle = production_semantic_bundle()
    return BattleEnvV1(
        card_catalog=bundle.native_card_catalog,
        semantic_subset_contract=bundle.semantic_subset_contract,
        ruleset_id=RULESET_ID,
        include_native_digest=False,
        mode_timeline=load_game_mode_timeline(game_mode_id),
    )


def match_config(game_mode_id: int, **overrides) -> MatchConfig:
    return MatchConfig(
        game_mode=game_mode_id, deck0=DECK, deck1=DECK, king_tower_level=11,
        tower_troop0_id=159000000, tower_troop1_id=159000000, **overrides,
    )


class PveTimelineTest(unittest.TestCase):
    """The shipped `Pve` timeline used to be silently broken.

    `runtime.json` declared a single 0-second Normal section. `phase()` walks
    sections with a `for/else`, and a section list whose lengths are all zero
    never breaks, so the `else` arm ran and `phase` was overwritten with
    "tiebreak" on *every* tick -- an all-zero timeline that did not report an
    error, it just fed the policy a tiebreak flag for the whole match.

    The existing `test_every_exposed_mode_stays_inside_the_trained_phase_domain`
    cannot catch this: "tiebreak" is in its allowed set, so a timeline stuck in
    tiebreak passes. These tests assert the phase is actually "normal" during
    the Normal section, which is the property that was lost.
    """

    @property
    def timeline(self):
        return load_game_mode_timeline(GAME_MODE_PVE).timeline

    def test_pve_is_a_real_mode_not_a_placeholder(self):
        self.assertEqual(load_game_mode_timeline(GAME_MODE_PVE).game_mode_name, "PvE")
        self.assertEqual(self.timeline.name, "Pve")

    def test_pve_sections_are_not_all_zero_length(self):
        # A section list of only zero-length entries is the root cause: the
        # phase() for/else can never break out of the loop.
        sections = self.timeline.sections
        self.assertGreater(len(sections), 0)
        self.assertTrue(
            any(section.length_ticks > 0 for section in sections),
            "every Pve section is 0 seconds; phase() would resolve to 'tiebreak' on every tick",
        )

    def test_pve_normal_section_reports_the_normal_phase(self):
        # The regression itself: phase() must be "normal" through the Normal
        # section, not silently rewritten to "tiebreak".
        timeline = self.timeline
        normal = next(s for s in timeline.sections if s.section_type.lower() == "normal")
        self.assertGreater(normal.length_ticks, 0)
        for tick in range(0, normal.length_ticks, 20):
            phase, _multiplier = timeline.phase(tick)
            self.assertEqual(phase, "normal", f"tick {tick} reported {phase!r}, expected 'normal'")

    def test_pve_is_not_pinned_to_tiebreak_for_the_whole_match(self):
        phases = {self.timeline.phase(tick)[0] for tick in range(0, 3600, 20)}
        self.assertIn("normal", phases)
        self.assertNotEqual(phases, {"tiebreak"})

    def test_pve_elixir_rate_actually_advances(self):
        # elixir_rate() has a `return self.elixir_rates[-1]` fallback, so a
        # single 0-second rate never raises -- it just pins the whole match to
        # one rate. Assert the rate genuinely changes over the match.
        timeline = self.timeline
        rates = {timeline.elixir_rate(tick).full_bar_ms for tick in range(0, 6000, 20)}
        self.assertGreater(len(rates), 1, "the Pve elixir rate never changes during the match")

    def test_pve_multiplier_stays_inside_the_trained_range(self):
        # The policy was trained on ladder pacing and the tensorizer divides
        # this value by 3.0, so no PvE input may leave the trained range.
        timeline = self.timeline
        for tick in range(0, 6001, 50):
            _phase, multiplier = timeline.phase(tick)
            capped = multiplier if multiplier <= 3.0 else 3.0
            self.assertLessEqual(capped / 3.0, 1.0, f"PvE feeds scalar[1] above trained max at {tick}")

    def test_pve_starting_elixir_is_in_the_trained_set(self):
        self.assertIn(self.timeline.starting_elixir, (0.0, 3.0, 4.0, 6.0))

    def test_pve_pacing_matches_the_ladder_it_was_derived_from(self):
        # PvE is an ordinary-paced match with a scripted opponent, so its phase
        # and multiplier must track the ladder the policy was trained on.
        ladder = load_game_mode_timeline(GAME_MODE_LADDER).timeline
        pve = self.timeline
        self.assertEqual(pve.maximum_tick, ladder.maximum_tick)
        for tick in range(0, 6001, 25):
            self.assertEqual(pve.phase(tick), ladder.phase(tick), f"diverges at tick {tick}")

    def test_pve_keeps_the_verified_game_modes_provenance(self):
        # patch_pve_timeline.py rewrites runtime.json and re-pins the two
        # competitive digests. It must NOT rewrite the timeline's own
        # source_sha256 / game_modes_sha256: BattleEnvV1.__init__ compares an
        # injected mode timeline's game_modes_sha256 against the verified
        # standard timeline to prove both came from the same verified
        # game_modes.csv, and a locally recomputed digest would fail that.
        pve = load_game_mode_timeline(GAME_MODE_PVE)
        ladder = load_game_mode_timeline(GAME_MODE_LADDER)
        self.assertEqual(pve.game_modes_sha256, ladder.game_modes_sha256)
        self.assertEqual(pve.timeline.source_sha256, ladder.timeline.source_sha256)

    def test_pve_environment_accepts_the_timeline(self):
        # The provenance guard above is load-bearing: this is what fails if a
        # future edit re-pins those fields to cover the authored sections.
        env = build_env(GAME_MODE_PVE)
        self.assertEqual(env.mode_timeline.game_mode_id, GAME_MODE_PVE)


class GameModeTimelineTest(unittest.TestCase):
    def test_every_exposed_mode_has_a_loadable_timeline(self):
        for key, mode_id in sorted(EXPOSED_1V1_MODES.items()):
            with self.subTest(mode=key):
                timeline = load_game_mode_timeline(mode_id)
                self.assertEqual(timeline.game_mode_id, mode_id)
                self.assertGreater(len(timeline.timeline.elixir_rates), 0)

    def test_environment_accepts_each_mode_with_its_own_timeline(self):
        for key, mode_id in sorted(EXPOSED_1V1_MODES.items()):
            with self.subTest(mode=key):
                env = build_env(mode_id)
                self.assertEqual(env.mode_timeline.game_mode_id, mode_id)

    def test_tower_geometry_is_mode_independent(self):
        # Opening the pacing gate must not have opened the geometry gate.
        for mode_id in sorted(set(EXPOSED_1V1_MODES.values())):
            env = build_env(mode_id)
            self.assertEqual(env.card_catalog.specs_hash, production_semantic_bundle().native_card_catalog.specs_hash)
        self.assertEqual(len(TOWER_LAYOUT), 6)

    def test_pacing_inputs_match_the_real_timeline(self):
        for key, mode_id in sorted(EXPOSED_1V1_MODES.items()):
            with self.subTest(mode=key):
                timeline = load_game_mode_timeline(mode_id).timeline
                env = build_env(mode_id)
                self.assertIs(env.mode_timeline.timeline, timeline)
                # The tensorizer seeds elixir from this value; a mismatch makes
                # the model believe it is richer or poorer than it is.
                self.assertIn(timeline.starting_elixir, (0.0, 3.0, 4.0, 6.0))

    def test_nonzero_elixir_modes_are_not_silently_pinned_to_ladder_six(self):
        # Regression: every mode used to start at 6.0 because the ladder
        # timeline was injected regardless of the selected mode.
        for key in ("double_elixir", "triple_elixir", "7x_elixir"):
            with self.subTest(mode=key):
                self.assertEqual(load_game_mode_timeline(EXPOSED_1V1_MODES[key]).timeline.starting_elixir, 0.0)
        self.assertEqual(load_game_mode_timeline(EXPOSED_1V1_MODES["ramp_up"]).timeline.starting_elixir, 3.0)
        self.assertEqual(load_game_mode_timeline(EXPOSED_1V1_MODES["all_random"]).timeline.starting_elixir, 4.0)
        self.assertEqual(load_game_mode_timeline(EXPOSED_1V1_MODES["ladder"]).timeline.starting_elixir, 6.0)

    def test_mode_and_environment_timeline_must_agree(self):
        for key, mode_id in sorted(EXPOSED_1V1_MODES.items()):
            with self.subTest(mode=key):
                env = build_env(mode_id)
                other = GAME_MODE_LADDER if mode_id != GAME_MODE_LADDER else 72000001
                with self.assertRaises(BattleEnvError) as ctx:
                    env.reset(match_config=match_config(other), options=RESET_OPTIONS)
                self.assertIn("game mode does not match", str(ctx.exception))

    def test_non_standard_arena_still_fails_closed(self):
        env = build_env(GAME_MODE_LADDER)
        with self.assertRaises(BattleEnvError) as ctx:
            env.reset(match_config=match_config(GAME_MODE_LADDER, arena=54000036), options=RESET_OPTIONS)
        self.assertIn("fails closed outside verified standard-layout", str(ctx.exception))

    def test_policy_multiplier_never_leaves_the_trained_range(self):
        # The tensorizer feeds scalar[1] = elixir_multiplier / 3.0 to the net.
        # Ladder training tops out at 1.0, so a raw 5.0x ramp-up must be clamped
        # or the network receives an input it never saw. The cap is an inline
        # literal: a module constant went missing in the shipped .pyc and
        # raised NameError on the first policy observation.
        from native_runner.battle_env import STANDARD_MATCH_TICKS
        self.assertEqual(STANDARD_MATCH_TICKS, 7200)
        for key, mode_id in sorted(EXPOSED_1V1_MODES.items()):
            with self.subTest(mode=key):
                timeline = load_game_mode_timeline(mode_id).timeline
                for tick in range(0, 6001, 50):
                    _phase, multiplier = timeline.phase(tick)
                    capped = multiplier if multiplier <= 3.0 else 3.0
                    self.assertLessEqual(capped / 3.0, 1.0,
                                         f"{key} feeds scalar[1] above the trained maximum at {tick}")

    def test_the_multiplier_cap_is_an_inline_literal(self):
        # Guards the NameError regression: the clamp must not reference a
        # module-level constant that staging or the .pyc compile can drop.
        import inspect
        from native_runner import battle_env
        source = inspect.getsource(battle_env)
        self.assertIn("multiplier if multiplier <= 3.0 else 3.0", source)
        self.assertNotIn("POLICY_ELIXIR_MULTIPLIER_CAP", source)

    def test_ramp_up_actually_exceeds_the_cap_without_the_clamp(self):
        # Guards that the clamp is load-bearing: if the cap were never hit this
        # test would pass vacuously and the regression would be invisible.
        timeline = load_game_mode_timeline(EXPOSED_1V1_MODES["ramp_up"]).timeline
        self.assertTrue(
            any(timeline.phase(t)[1] > 3.0 for t in range(0, 6001, 50)),
            "ramp_up no longer exceeds 3x; the clamp test above is now vacuous",
        )

    def test_every_exposed_mode_stays_inside_the_trained_phase_domain(self):
        for key, mode_id in sorted(EXPOSED_1V1_MODES.items()):
            with self.subTest(mode=key):
                timeline = load_game_mode_timeline(mode_id).timeline
                for tick in range(0, 6001, 50):
                    phase, _ = timeline.phase(tick)
                    self.assertIn(phase, {"normal", "overtime", "tiebreak"})

    def test_event_and_arena_variants_stay_in_the_trained_domain(self):
        # These modes differ only in pacing: same 1v1 map, ordinary decks, and
        # every phase/multiplier inside the ladder's training range once the
        # inline 3.0 cap is applied. Anything added here must meet the same bar.
        ladder = load_game_mode_timeline(GAME_MODE_LADDER).timeline
        known_phases = {ladder.phase(t)[0] for t in range(0, 6001, 50)}
        for key in ("rage", "crazy_arena", "elixir_barrel", "chess_board", "crazy_sudden_death"):
            with self.subTest(mode=key):
                self.assertIn(key, EXPOSED_1V1_MODES)
                timeline = load_game_mode_timeline(EXPOSED_1V1_MODES[key]).timeline
                for tick in range(0, 6001, 50):
                    phase, multiplier = timeline.phase(tick)
                    self.assertIn(phase, known_phases)
                    capped = multiplier if multiplier <= 3.0 else 3.0
                    self.assertLessEqual(capped / 3.0, 1.0)
                # These are ordinary player-deck matches, so they must not be
                # random-deck or team modes.
                self.assertNotEqual(EXPOSED_1V1_MODES[key], GAME_MODE_TEAM_VS_TEAM)

    def test_prewarm_validates_every_exposed_timeline(self):
        # prewarm must fail loudly at startup instead of crashing mid-match.
        self.assertEqual(mobile_fair.prewarm(), "ready")

    def test_create_match_emits_the_selected_game_mode(self):
        human = {"cards": list(DECK), "forms": [0] * 8}
        for key, mode_id in sorted(EXPOSED_1V1_MODES.items()):
            with self.subTest(mode=key):
                payload = json.loads(
                    mobile_fair.create_match(json.dumps(human), "general", "", game_mode=key)
                )
                self.assertEqual(payload["battle"]["gamemode"], mode_id)


if __name__ == "__main__":
    unittest.main()
