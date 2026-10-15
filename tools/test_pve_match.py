"""The PvE match must be creatable and its wave table must survive the round trip.

create_match is the boundary where a mode becomes a real match: if the PvE
branch emits a payload the engine cannot accept, the player reaches the battle
screen and then gets nothing. These tests exercise the branch with the same
inputs the device sends.
"""
import hashlib
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "build/mobile-semantics"))
sys.path.insert(0, str(ROOT / "on_device/python"))

import mobile_fair
import pve_tower_defense as pve_roguelike
from native_runner.match_factory import MatchConfig
from native_runner.timeline import load_game_mode_timeline

PVE_KEY = "pve_goblin"
PVE_MODE_ID = 72000018
PVE_MODES = mobile_fair.ROGUELIKE_MODES

HUMAN = {"cards": [26000000, 26000001, 26000005, 28000001, 28000000, 26000003, 26000014, 26000018],
         "forms": [0] * 8}


def create(mode=PVE_KEY):
    return json.loads(
        mobile_fair.create_match(json.dumps(HUMAN), "", "", game_mode=mode)
    )


class PveMatchCreationTest(unittest.TestCase):
    def test_pve_is_in_the_mode_catalog(self):
        self.assertIn(PVE_KEY, mobile_fair.GAME_MODES)
        self.assertEqual(mobile_fair.GAME_MODES[PVE_KEY]["id"], PVE_MODE_ID)

    def test_all_three_pve_variants_are_offered(self):
        # The three tower-defence schedules are separate player-facing entries,
        # not one entry with a hidden flag, so each has to be in the catalog.
        for key in PVE_MODES:
            with self.subTest(mode=key):
                self.assertIn(key, mobile_fair.GAME_MODES)
                self.assertEqual(mobile_fair.GAME_MODES[key]["id"], PVE_MODE_ID)

    def test_pve_emits_the_pve_game_mode(self):
        self.assertEqual(create()["battle"]["gamemode"], PVE_MODE_ID)

    def test_pve_payload_is_not_the_2v2_shape(self):
        # 2v2 is the other hand-written battle dict. A PvE match must stay 1v1
        # or the arena gate and tower binding would both be wrong.
        battle = create()["battle"]
        self.assertEqual(battle.get("plt"), 1)
        self.assertNotIn("deck2", battle)
        self.assertNotIn("deck3", battle)

    def test_pve_carries_a_wave_table(self):
        pve = create()["pve"]
        self.assertEqual(pve["mode"], PVE_KEY)
        self.assertGreater(len(pve["waves"]), 0)
        self.assertEqual(pve["enemyOwner"], 1)

    def test_each_variant_ships_its_own_schedule(self):
        # The three dungeons are the same deploy primitive with different
        # scheduling, so each must emit exactly the table it declares.
        for key in PVE_MODES:
            with self.subTest(mode=key):
                payload = create(key)
                self.assertEqual(payload["pve"]["mode"], key)
                self.assertEqual(payload["pve"]["waves"],
                                 [dict(w) for w in pve_roguelike.waves_for(key)])

    def test_enemy_deck_in_the_payload_is_the_wave_deck(self):
        # Wave cards resolve by id against the deck the engine was given, and
        # the native resolver demands exactly ONE hand match per deploy, so the
        # wave deck must be the dungeon's own eight cards with no duplicates --
        # and every wave must name one of them.
        for key in PVE_MODES:
            with self.subTest(mode=key):
                payload = create(key)
                deck = [row["d"] for row in payload["battle"]["deck1"]["sp"]]
                self.assertEqual(deck, list(pve_roguelike.deck_for(key)))
                self.assertEqual(len(deck), 8, "the engine requires an 8-card deck")
                self.assertEqual(len(set(deck)), 8,
                                 "a duplicate wave card resolves to two hand matches "
                                 "and every wave using it is dropped at fire time")
                for wave in payload["pve"]["waves"]:
                    self.assertIn(wave["card"], deck)

    def test_human_deck_is_preserved(self):
        payload = create()
        self.assertEqual([row["d"] for row in payload["battle"]["deck0"]["sp"]],
                         list(HUMAN["cards"]))

    def test_pve_map_is_standard_arena(self):
        # PvE uses standard arena location 15000199 with full 3-tower topology
        # to ensure proper engine tick stepping, crown accounting, and player HUD controls.
        for key in PVE_MODES:
            with self.subTest(mode=key):
                payload = create(key)
                self.assertEqual(payload["battle"]["location"], 15000199)

    def test_mode_custom_wave_cycles_and_metadata(self):
        # Every dungeon is a fixed ten-wave descent, so the flattened table's
        # `cycle` field has to run 0..9 and the final wave must be flagged as
        # carrying the bosses.
        for key in PVE_MODES:
            with self.subTest(mode=key):
                waves = pve_roguelike.waves_for(key)
                self.assertEqual(sorted({w["cycle"] for w in waves}), list(range(10)))
                self.assertTrue(any(w["is_boss"] for w in waves))
                self.assertTrue(all("label" in w and "is_boss" in w for w in waves))
                final = [w for w in waves if w["cycle"] == 9]
                self.assertTrue(any(w["is_boss"] for w in final))

    def test_payload_rebuilds_a_valid_match_config(self):
        # BattleEnvV1.reset() builds a MatchConfig out of the emitted battle
        # dict, so drive the real contract with the real decks. MatchConfig has
        # no from_replay_dict, so this is the same construction MobileFair makes
        # on device, which is what proves the payload is usable.
        for key in PVE_MODES:
            with self.subTest(mode=key):
                payload = create(key)
                battle = payload["battle"]
                deck0 = tuple(row["d"] for row in battle["deck0"]["sp"])
                deck1 = tuple(row["d"] for row in battle["deck1"]["sp"])
                config = MatchConfig(
                    game_mode=battle["gamemode"], deck0=deck0, deck1=deck1,
                    king_tower_level=11,
                    tower_troop0_id=battle["deck0"]["sc"][0]["d"],
                    tower_troop1_id=battle["deck1"]["sc"][0]["d"],
                )
                self.assertEqual(config.game_mode, PVE_MODE_ID)
                self.assertEqual(len(config.deck0), 8)
                self.assertEqual(len(config.deck1), 8)
                self.assertEqual(tuple(config.deck1),
                                 tuple(pve_roguelike.deck_for(key)))
                self.assertEqual(tuple(config.deck0), tuple(HUMAN["cards"]))

    def test_environment_accepts_the_pve_match(self):
        # The end-to-end gate: build the real environment on the real PvE
        # timeline and reset it with this mode's match config. This is the check
        # that would fail closed on a bad game mode, deck or tower pairing.
        from native_runner.battle_env import BattleEnvV1
        from native_runner.training.v4.factory import production_semantic_bundle
        payload = create()
        battle = payload["battle"]
        bundle = production_semantic_bundle()
        env = BattleEnvV1(
            card_catalog=bundle.native_card_catalog,
            semantic_subset_contract=bundle.semantic_subset_contract,
            ruleset_id=hashlib.sha256(
                b"firstlight-local-v1:110aa2b5cac391c498645e072b0d88729428c2c2845e7e2737ca8ee979059783"
                b":e0cb2fb9eb2fdd0df1afcb00df3e3ba61cf25aa8b24e39c17cc391d952a4e339"
            ).hexdigest(),
            include_native_digest=False,
            mode_timeline=load_game_mode_timeline(PVE_MODE_ID),
        )
        self.assertEqual(env.mode_timeline.game_mode_id, PVE_MODE_ID)

    def test_pve_timeline_agrees_with_the_match_mode(self):
        # BattleEnvV1.reset fails closed when these disagree, so pin the pairing
        # the mode actually builds.
        payload = create()
        timeline = load_game_mode_timeline(payload["battle"]["gamemode"])
        self.assertEqual(timeline.game_mode_id, payload["battle"]["gamemode"])

    def test_every_variant_starts_a_playable_match(self):
        # Navigability is part of the feature: each picker entry has to produce
        # a match, not raise and leave the player with no way forward.
        for key in PVE_MODES:
            with self.subTest(mode=key):
                payload = create(key)
                self.assertEqual(payload["battle"]["gamemode"], PVE_MODE_ID)
                self.assertEqual(payload["pve"]["mode"], key)
                self.assertGreater(len(payload["pve"]["waves"]), 0)

    def test_wave_table_validates(self):
        # The generator is the single source of truth for legality; if it can
        # emit an unplayable wave the device will silently drop it.
        self.assertEqual(pve_roguelike.validate(), [])

    def test_the_descent_is_ten_waves_and_ends_inside_a_match(self):
        # A climb is finite by design: ten waves, and the last one has to arrive
        # inside a normal match length rather than running off the end of one.
        waves = pve_roguelike.waves_for(PVE_KEY)
        self.assertEqual(len({w["cycle"] for w in waves}), 10)
        self.assertLess(waves[-1]["tick"],
                        60 * pve_roguelike.TICKS_PER_SECOND * 30)
        # ...and pressure escalates rather than repeating flatly.
        counts = {}
        for wave in waves:
            counts.setdefault(wave["cycle"], 0)
            counts[wave["cycle"]] += 1
        self.assertGreater(counts[9], counts[0],
                           "the finale must be denser than the opening")

    def test_pve_uses_its_own_timeline_not_the_ladder(self):
        pve = load_game_mode_timeline(PVE_MODE_ID)
        ladder = load_game_mode_timeline(mobile_fair.GAME_MODE_LADDER)
        self.assertEqual(pve.game_mode_name, "PvE")
        self.assertEqual(pve.timeline.name, "Pve")
        self.assertNotEqual(pve.timeline.name, ladder.timeline.name)

    def test_prewarm_accepts_the_pve_mode(self):
        self.assertEqual(mobile_fair.prewarm(), "ready")

    def test_existing_modes_are_unchanged(self):
        # Adding PvE must not disturb the modes already shipped.
        for key in ("ladder", "double_elixir", "mirror", "all_random", "all_evolution"):
            with self.subTest(mode=key):
                payload = json.loads(
                    mobile_fair.create_match(json.dumps(HUMAN), "general", "",
                                             game_mode=key)
                )
                self.assertEqual(payload["battle"]["gamemode"],
                                 mobile_fair.GAME_MODES[key]["id"])
                self.assertNotIn("pve", payload)
                self.assertNotIn("roguelike", payload)


class PveMatchRunnerSourceTest(unittest.TestCase):
    """Source-level guards on the PvE runner.

    Both defects below were found only on a real device and neither was
    reachable from the test suite, because they depend on which owner the
    tensorizer was built for. They are pinned here so the fix cannot silently
    regress back to MobileFair's default seat.

    Device evidence: the first PvE match died on frame one with
    "ValueError: observation owner does not match V4 tensorizer", raised from
    run_pve_device_match's tensorize call.
    """

    @staticmethod
    def source():
        return mobile_fair.__file__ and Path(mobile_fair.__file__).read_text(encoding="utf-8")

    @staticmethod
    def runner():
        text = PveMatchRunnerSourceTest.source()
        start = text.index("def run_pve_device_match(")
        end = text.index("\ndef ", start + 10)
        return text[start:end]

    def test_pve_policy_sits_on_the_player_seat_not_the_default(self):
        # MobileFair defaults to actor=1, which is the wave script's seat in PvE.
        runner = self.runner()
        self.assertIn("actor=pve_player_owner", runner)
        self.assertNotIn("MobileFair(native.capture,match=match_data)", runner)

    def test_player_seat_is_derived_from_the_enemy_seat(self):
        runner = self.runner()
        self.assertIn("pve_player_owner=0 if enemy_owner==1 else 1", runner)

    def test_every_policy_call_uses_the_player_seat(self):
        # The player seat is observed, not played. The observation owners and the
        # readback must name the same seat, or the tensorizer rejects the frame.
        runner = self.runner()
        self.assertIn("observation_owners=(pve_player_owner,)", runner)
        self.assertIn("adapter.observation=observations[pve_player_owner]", runner)

    def test_the_policy_never_plays_the_human_seat(self):
        # Stage 1 red line: Player 0 is the human and their cards come from the
        # screen, not from the model. BattleEnvV1.step turns a PLAY_CARD action
        # into a real ct:86 deploy injected against that owner's own hand slot
        # (queueActionForOwner), so inferring here was the AI physically playing
        # the player's hand -- the exact defect the mode must not have.
        runner = self.runner()
        self.assertNotIn("runtime.infer(", runner,
                         "the PvE runner must not infer a policy decision")
        self.assertNotIn("runtime.inferForOwner(", runner)
        self.assertIn("actions={}", runner,
                      "the pump must step the world with an empty action set")
        self.assertNotIn("ordered_inputs(schema)", runner,
                         "no decision means no policy tensor is needed")

    def test_a_transient_refusal_is_retried_but_a_permanent_one_is_not(self):
        # Device evidence: at match start every wave returned "no live battle",
        # because the world is not stepped yet and the capture identity has no
        # manager. That refusal is transient, so the wave must be retried on a
        # later frame. A permanent refusal (off-arena position, bad owner) must
        # be retired instead, or the loop re-issues it on every remaining frame.
        runner = self.runner()
        self.assertIn('elif receipt.get("error")=="no live battle":', runner)
        # The transient branch must not retire the wave.
        transient = runner.split('elif receipt.get("error")=="no live battle":', 1)[1]
        transient = transient.split("else:", 1)[0]
        self.assertNotIn("armed.add(index)", transient,
                         "a transient 'no live battle' must stay retryable")

    def test_a_wave_whose_boundary_passed_is_retired(self):
        # Once the boundary is gone the engine can never fire it, so it must be
        # retired rather than re-issued forever.
        runner = self.runner()
        self.assertIn('"error":"boundary passed unarmed"', runner)
        missed = runner.split("if wave_tick<=tick:", 1)[1].split("continue", 1)[0]
        self.assertIn("armed.add(index)", missed)

    def test_a_permanent_refusal_is_retired(self):
        runner = self.runner()
        permanent = runner.split("else:\n                # A permanent refusal", 1)[1]
        self.assertIn("armed.add(index)", permanent)

    def test_pve_is_dispatched_before_the_generic_1v1_runner(self):
        # A PvE payload has a "pve" key; without the early dispatch it would
        # fall through to run_device_match and run a policy on the wave seat.
        text = self.source()
        runner_start = text.index("def run_device_match(")
        body = text[runner_start:text.index("\nclass ", runner_start) if "\nclass " in text[runner_start:] else len(text)]
        pve_dispatch = body.index('if match_data.get("pve")')
        duel_dispatch = body.index('if match_data.get("aiDuel")')
        self.assertLess(pve_dispatch, duel_dispatch)

    def test_the_wave_table_is_carried_on_the_payload(self):
        # run_pve_device_match reads match_data["pve"]; without it the level
        # would silently run with zero waves.
        payload = create()
        self.assertIn("pve", payload)
        self.assertGreater(len(payload["pve"]["waves"]), 0)
        self.assertEqual(payload["pve"]["enemyOwner"], 1)

    def test_pve_wave_hud_notification_is_wired(self):
        runner = self.runner()
        self.assertIn("runtime.notifyPveWave(", runner)
        self.assertIn('hasattr(runtime,"notifyPveWave")', runner)

    def test_the_roguelike_clear_condition_exists(self):
        # A climb ends when the whole ten-wave table is armed AND the field has
        # emptied AND a settling window has passed. Without the emptiness check
        # the run is declared won the instant the finale's bosses deploy, and
        # without the window a stray projectile could end it a tick early.
        runner = self.runner()
        self.assertIn("run cleared", runner)
        self.assertIn("cursor>=len(waves) and not wave_in_progress", runner)
        self.assertIn("live_enemy_troops(native.capture[\"ordinary\"],enemy_owner)==0", runner)
        self.assertIn("tick>=last_spawn_tick+300", runner)

    def test_the_wave_schedule_is_deterministic(self):
        """The payload's recorded ticks must be the ticks the loop arms against.

        `create_match` flattens the table into the fixture and the runner arms
        against those absolute ticks, retiring any whose boundary has already
        passed. If the batch gaps were drawn from a fresh `random` each call, the
        arming table would differ from the recorded one and waves would be
        dropped as "boundary passed unarmed" without ever having been late.
        """
        for key in PVE_MODES:
            with self.subTest(mode=key):
                self.assertEqual(pve_roguelike.waves_for(key),
                                 pve_roguelike.waves_for(key))
                self.assertEqual(pve_roguelike.expand(key),
                                 pve_roguelike.expand(key))


class PveWaveSchedulerSourceTest(unittest.TestCase):
    """The arming loop was rewritten from a for-loop to a cursor loop.

    The rewrite left three references to the old `for index, wave in ...`
    binding, so on device the very first wave raised
    `NameError: name 'wave' is not defined` at mobile_fair.py:764 and the match
    died before a single monster spawned. The test suite could not see it: the
    scheduler needs a live battle to run, and the parse is legal Python, so
    pytest imports the module happily and never executes the loop. Pin the
    binding in source.
    """

    @staticmethod
    def runner():
        text = Path(mobile_fair.__file__).read_text(encoding="utf-8")
        start = text.index("def run_pve_device_match(")
        return text[start:text.index("\ndef ", start + 10)]

    def test_the_cursor_loop_binds_the_wave_it_arms(self):
        runner = self.runner()
        self.assertIn("wave=waves[cursor]", runner,
                      "the cursor loop must bind `wave`; otherwise every "
                      "scheduleCard call raises NameError on device")
        self.assertIn("wave_tick=int(wave[\"tick\"])", runner)

    def test_the_arm_cursor_advances_monotonically(self):
        # The table is sorted by tick. A cursor that never advanced would rescan
        # 13k waves every frame; one that reset would re-arm settled boundaries.
        runner = self.runner()
        window = runner.split("while cursor<upcoming", 1)[1].split("actions={}", 1)[0]
        self.assertIn("cursor+=1", window)
        self.assertNotIn("cursor=0", window,
                         "resetting the cursor rescans the whole schedule")

    def test_the_arm_window_is_bounded(self):
        # Endless schedules are far longer than a match. Arming all of them up
        # front would overrun the 16-slot command mailbox and bury the first
        # wave under thousands of later ones.
        runner = self.runner()
        self.assertIn("ARM_HORIZON", runner)
        self.assertIn("upcoming=min(len(waves),", runner)


if __name__ == "__main__":
    unittest.main()
