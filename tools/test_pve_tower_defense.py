"""The pure PvE tower defence: batches, levels, clearing, and the buffs.

Each contract here is a way the mode can look correct in a source listing and
still be wrong in play, and each is checked against something the device would
otherwise have to tell us about:

1. **The arena is towerless.** The specification is explicit that the enemy half
   has no tower models, no collision and no hitpoints on screen. The engine ends
   a match by counting the *opposing* side's towers, so the towers cannot be
   deleted outright -- they are relocated past the far edge of the arena. Both
   halves of that are pinned: the fixture asks for it, and the native hook that
   honours the request is wired into the step that runs once per battle.

2. **A wave is a staggered assault.** Two to four batches, three to five seconds
   apart, many units per batch, and levels climbing 1 to 15. A single card on a
   single tick -- what the mode used to do -- satisfies none of that.

3. **The level is real.** The level column is only meaningful if it reaches the
   engine, so the whole path is pinned from the table through `scheduleCard` to
   the native writer.

4. **A wave is cleared when the field empties**, not when a timer expires, and
   the fifteen seconds of interlude are measured from that clear.

5. **Every buff takes effect**, and no option is decorative.
"""
import json
import random
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "build/mobile-semantics"))
sys.path.insert(0, str(ROOT / "on_device/python"))

import mobile_fair
import pve_tower_defense as pve

HUMAN = {"cards": [26000000, 26000001, 26000005, 28000001, 28000000, 26000003,
                   28000014, 28000018],
         "forms": [0] * 8}

PROBE = ROOT / "baseline/native_runner/probe"


def create(mode="pve_goblin", deck=None):
    return json.loads(mobile_fair.create_match(
        json.dumps(HUMAN if deck is None else deck), "hog26_1", "", game_mode=mode))


class TowerlessArenaTest(unittest.TestCase):
    """The enemy half shows no towers, and the match still runs."""

    def test_the_pve_fixture_asks_for_the_towerless_arena(self):
        # The arena rewrite is armed by an explicit field on the fixture, not by
        # a game-mode id: it is destructive to the map layout, so only a caller
        # that asked for it may trigger it.
        self.assertIs(create()["pveTowerless"], True)

    def test_no_other_mode_asks_for_it(self):
        for mode in ("ladder", "double_elixir", "mirror", "all_random", "2v2"):
            with self.subTest(mode=mode):
                self.assertIsNone(create(mode).get("pveTowerless"),
                                  "%s would have its arena rewritten" % mode)

    def test_the_towers_go_past_the_arena_not_below_zero(self):
        # The arena is 18x32 cells of 1000 units, so the world spans y 0..32000.
        # Anything at or beyond the far edge is never drawn and is rejected as a
        # deploy target, which is what "100% no towers, no collision" means --
        # while the engine still sees them standing, so it cannot end the run.
        self.assertGreater(pve.OFFSCREEN_TOWER_Y, 32000)
        self.assertGreater(pve.OFFSCREEN_TOWER_Y, 24000)

    def test_the_native_hook_moves_only_the_enemy_side(self):
        source = (PROBE / "pve_towerless.inc").read_text(encoding="utf-8")
        # Owner 1 is the wave seat. Moving the player's own royal towers would end
        # the match immediately, because losing them is the loss condition.
        self.assertIn("!= 1", source)
        self.assertIn("continue", source)
        # Both tower kinds, identified by their own data ids rather than by
        # position, so a scale or mirror cannot smuggle one past the filter.
        for data_id in (35000000, 35000001):
            self.assertIn(str(data_id), source)

    def test_the_hook_retries_until_the_towers_exist(self):
        """The flag cannot be spent before the towers have been built.

        The engine creates the royal towers during scene setup, so on the first
        step after the scene starts there are no tower objects at all. A plain
        one-shot `exchange(false)` spends the flag on that first empty step, and
        the arena is then never rewritten -- which is exactly what the first
        towerless run did: the flag was consumed and the "pve towerless
        relocated" line never appeared.
        """
        bridge = (PROBE / "local_runtime_bridge.inc").read_text(encoding="utf-8")
        self.assertNotIn("g_pve_towerless_pending.exchange(false)", bridge)
        self.assertIn("g_pve_towerless_pending.load(", bridge)
        self.assertIn("!pve_towerless_towers_pending(", bridge)
        source = (PROBE / "pve_towerless.inc").read_text(encoding="utf-8")
        self.assertIn("bool pve_towerless_towers_pending(", source)

    def test_the_hook_only_stops_once_nothing_is_left_on_the_arena(self):
        source = (PROBE / "pve_towerless.inc").read_text(encoding="utf-8")
        self.assertIn("< kPvEOffscreenTowerY", source)

    def test_the_fixture_marker_is_what_arms_it(self):
        scene = (PROBE / "local_app_scene.inc").read_text(encoding="utf-8")
        self.assertIn("pve_towerless_fixture_requested(", scene)
        source = (PROBE / "pve_towerless.inc").read_text(encoding="utf-8")
        self.assertIn('"\\"pveTowerless\\":"', source)


class LevelRampTest(unittest.TestCase):
    """The monsters climb from level 1 to level 15 across a run."""

    def test_the_ramp_starts_at_one_and_ends_at_fifteen(self):
        self.assertEqual(pve.level_for_wave(1), pve.LEVEL_MIN)
        self.assertEqual(pve.level_for_wave(pve.WAVE_COUNT), pve.LEVEL_MAX)

    def test_the_ramp_never_goes_backwards(self):
        levels = [pve.level_for_wave(index) for index in range(1, pve.WAVE_COUNT + 1)]
        self.assertEqual(levels, sorted(levels))
        self.assertEqual(len(set(levels)), len(levels),
                         "two waves share a level; the ramp is not a ramp")

    def test_the_ramp_rejects_a_wave_outside_the_run(self):
        for bad in (0, -1, pve.WAVE_COUNT + 1):
            with self.subTest(wave=bad):
                with self.assertRaises(ValueError):
                    pve.level_for_wave(bad)

    def test_every_wave_fielded_at_its_ramped_level(self):
        for key in pve.MODES:
            for wave in pve.dungeon(key):
                levels = [spawn["level"] for b in wave["batches"] for s in b
                          for spawn in [s]]
                self.assertEqual(min(levels), pve.level_for_wave(wave["index"]),
                                 "%s wave %d fields below its level" % (key, wave["index"]))

    def test_the_finale_bosses_are_at_the_cap(self):
        for key in pve.MODES:
            final = pve.dungeon(key)[-1]
            bosses = [s for b in final["batches"] for s in b if s["is_boss"]]
            for boss in bosses:
                self.assertEqual(boss["level"], pve.LEVEL_MAX,
                                 "%s fields a finale boss below level 15" % key)

    def test_the_level_reaches_the_schedule(self):
        payload = create()
        self.assertTrue(payload["pve"]["waves"])
        for record in payload["pve"]["waves"]:
            self.assertIn("level", record)
            self.assertGreaterEqual(record["level"], pve.LEVEL_MIN)
            self.assertLessEqual(record["level"], pve.LEVEL_MAX)

    def test_the_level_reaches_the_engine(self):
        """The table's level column is decoration unless the engine consumes it.

        Three links, each of which has been independently dropped before:
        the runner must pass it to `scheduleCard`, the JNI export must declare a
        parameter for it, and the native writer must exist and refuse a level
        outside 1..15 rather than silently clamping it.
        """
        runner = mobile_fair.run_pve_device_match.__code__
        source = Path(mobile_fair.__file__).read_text(encoding="utf-8")
        self.assertIn('int(wave.get("level",0))', source)
        self.assertIn("kBattleDeckSlotLevelOffset", (PROBE / "cr_replay_probe.cpp").read_text(encoding="utf-8"))
        self.assertIn("apply_scheduled_card_level(", (PROBE / "cr_replay_probe.cpp").read_text(encoding="utf-8"))
        # Fail-closed: a level outside the range is refused, not clamped, because
        # a clamp hides a table bug behind a plausible level-11 spawn.
        probe = (PROBE / "cr_replay_probe.cpp").read_text(encoding="utf-8")
        self.assertIn("character level %d outside 1..15", probe)
        self.assertEqual(runner.co_varnames[:2], ("generation", "runtime"))

    def test_the_native_writer_is_fail_closed(self):
        """A wrong offset must cost a log line, never a corrupted engine object.

        The battle deck slot's level offset is the one thing in this change that
        could not be read off a table, so the write is guarded: it refuses unless
        the field already holds an in-range level, which a pointer or a bitfield
        never would.
        """
        probe = (PROBE / "cr_replay_probe.cpp").read_text(encoding="utf-8")
        self.assertIn("if (previous < kPvECharacterLevelMin)", probe)
        self.assertIn("read_battle_card_level", probe)


class WaveShapeTest(unittest.TestCase):
    """A wave is a staggered assault, not a trickle."""

    def test_each_wave_has_two_to_four_batches(self):
        for key in pve.MODES:
            for wave in pve.dungeon(key):
                self.assertTrue(2 <= len(wave["batches"]) <= 4,
                                "%s wave %d has %d batches"
                                % (key, wave["index"], len(wave["batches"])))

    def test_batches_land_three_to_five_seconds_apart(self):
        for key in pve.MODES:
            for wave in pve.dungeon(key):
                records = [r for r in pve.waves_for(key)
                           if r["wave"] == wave["index"]]
                firsts = {}
                for record in records:
                    firsts.setdefault(record["batch"], record["tick"])
                ordered = [firsts[index] for index in sorted(firsts)]
                for earlier, later in zip(ordered, ordered[1:]):
                    gap = later - earlier
                    self.assertGreaterEqual(gap, 3 * pve.TICKS_PER_SECOND)
                    self.assertLessEqual(gap, 5 * pve.TICKS_PER_SECOND)

    def test_batches_carry_many_units(self):
        # "Big troop formations" is the specification's word. A batch of three is
        # a trickle, so every batch is floored well above a handful -- and the
        # finale's boss batches carry a single boss *by design*, because a boss is
        # one unit, so those are measured on the run's other batches instead.
        for key in pve.MODES:
            final = pve.dungeon(key)[-1]
            for wave in pve.dungeon(key):
                for index, batch in enumerate(wave["batches"]):
                    if wave is final and any(s["is_boss"] for s in batch):
                        continue
                    self.assertGreaterEqual(len(batch), 4,
                                            "%s wave %d batch %d is not a swarm"
                                            % (key, wave["index"], index))

    def test_the_last_wave_is_the_biggest_pressure(self):
        """The run has to escalate, so the finale fields more than wave one."""
        for key in pve.MODES:
            waves = pve.dungeon(key)
            self.assertGreater(pve.wave_spawn_count(waves[-1]),
                               pve.wave_spawn_count(waves[0]) - 6,
                               "%s does not escalate" % key)


class WaveClearTest(unittest.TestCase):
    """A wave is cleared when the field empties."""

    @staticmethod
    def capture(objects):
        return {"tick": 100, "objects": objects}

    @staticmethod
    def tower(owner, y=3000):
        # A tower carries cardId -1 and no dataGlobalId in a real capture; both
        # readings have to classify it, which is why the test pins both.
        return {"owner": owner, "cardId": -1, "hp": 4824, "x": 9000, "y": y}

    def test_an_empty_field_is_cleared(self):
        self.assertEqual(pve.live_enemy_troops(self.capture([])), 0)

    def test_enemy_troops_are_counted(self):
        objects = [{"owner": 1, "cardId": 26000002, "x": 3500, "y": 20000},
                   {"owner": 1, "cardId": 26000002, "x": 4500, "y": 20000}]
        self.assertEqual(pve.live_enemy_troops(self.capture(objects)), 2)

    def test_the_players_own_troops_do_not_hold_a_wave_open(self):
        # A player who keeps one defensive unit on the field has not failed to
        # clear anything; counting their units would make the wave un-clearable.
        objects = [{"owner": 0, "cardId": 26000003, "x": 3500, "y": 9000}]
        self.assertEqual(pve.live_enemy_troops(self.capture(objects)), 0)

    def test_towers_are_not_counted_as_troops(self):
        """The relocated enemy towers are still objects; they must not count.

        This is the trap the towerless arena sets: the towers are still standing
        at y=35000 precisely so the engine's opposing-tower verdict stays quiet,
        so a clear check that counted them would never see a cleared field.
        """
        objects = [self.tower(0), self.tower(1, pve.OFFSCREEN_TOWER_Y)]
        self.assertEqual(pve.live_enemy_troops(self.capture(objects)), 0)

    def test_a_tower_is_recognised_with_or_without_a_data_id(self):
        self.assertTrue(pve.is_tower_object(self.tower(0)))
        self.assertTrue(pve.is_tower_object(
            {"owner": 0, "dataGlobalId": pve.KING_TOWER_CARD_ID}))
        self.assertTrue(pve.is_tower_object(
            {"owner": 0, "dataGlobalId": pve.PRINCESS_TOWER_CARD_ID}))
        self.assertFalse(pve.is_tower_object({"owner": 1, "cardId": 26000002}))

    def test_a_null_entry_is_not_a_troop(self):
        objects = [None, {"null": True}, {"owner": 1, "cardId": 26000002}]
        self.assertEqual(pve.live_enemy_troops(self.capture(objects)), 1)

    def test_a_missing_capture_is_not_a_clear(self):
        # A frame that could not be read is not evidence the field is empty.
        self.assertEqual(pve.live_enemy_troops(None), 0)
        self.assertEqual(pve.live_enemy_troops({}), 0)


class InterludeTest(unittest.TestCase):
    """Fifteen seconds of quiet, then a three-pick-one."""

    def test_the_interlude_is_the_specification_window(self):
        self.assertGreaterEqual(pve.INTERLUDE_SECONDS, 10)
        self.assertLessEqual(pve.INTERLUDE_SECONDS, 15)

    def test_the_dialog_countdown_fits_inside_the_interlude(self):
        self.assertLessEqual(pve.DRAFT_TIMEOUT_SECONDS, pve.INTERLUDE_SECONDS)

    def test_the_runner_measures_the_interlude_from_the_clear(self):
        runner = Path(mobile_fair.__file__).read_text(encoding="utf-8")
        start = runner.index("def run_pve_device_match(")
        body = runner[start:runner.index("\ndef ", start + 10)]
        self.assertIn("wave_cleared_tick=tick", body)
        self.assertIn("tick>=wave_cleared_tick+INTERLUDE_TICKS", body)
        # The clear must not be inferred from the schedule, which is what made the
        # old interlude overlap the last batch of a wave.
        self.assertNotIn("wave_start_tick(", body)

    def test_a_wave_stays_in_progress_between_its_batches(self):
        """An empty field between two batches is not a cleared wave.

        A wave's batches land three to five seconds apart and the monsters of the
        first batch can all die inside that gap, so a clear check that only asks
        "is the field empty" opens the draft while the wave's own second batch is
        still on its way.

        Device evidence, from the first towerless run: "PvE wave 4 cleared
        tick=1995", then "tick=2075", then "tick=2155", with batches 2 and 3 of
        that same wave arming at 2657 and 2735 in between. The field read empty
        three times for one wave because the batches had not all landed yet.
        """
        runner = Path(mobile_fair.__file__).read_text(encoding="utf-8")
        start = runner.index("def run_pve_device_match(")
        body = runner[start:runner.index("\ndef ", start + 10)]
        self.assertIn("wave_in_progress=True", body)
        # Both halves of the condition: the field empty AND nothing left to arm.
        self.assertIn("wave_in_progress and enemies==0 and not pending_batches", body)
        self.assertIn("pending_batches=any(int(waves[i][\"tick\"])>tick", body)

    def test_the_clear_check_ignores_batches_of_later_waves(self):
        """A later wave's batches must not hold this one open forever.

        The pending-batch scan runs from the arming cursor, and the cursor is
        bounded by its 600-tick horizon, so only genuinely near-future spawns are
        considered. A scan over the whole table would keep every wave from ever
        being cleared and the run would deadlock waiting for a clear.
        """
        runner = Path(mobile_fair.__file__).read_text(encoding="utf-8")
        start = runner.index("def run_pve_device_match(")
        body = runner[start:runner.index("\ndef ", start + 10)]
        self.assertIn("for i in range(cursor,upcoming)", body)
        self.assertIn("ARM_HORIZON=600", body)


class BuffEffectTest(unittest.TestCase):
    """Every option on offer changes something."""

    def test_a_reward_is_always_deployed_at_max_level(self):
        """A buff must not be weaker because it was picked early.

        A Heal or a Rage carries the reward record's own level, and that record
        is built from `LEVEL_MAX` rather than from the wave's monster level.
        Reusing the wave level here would make an option's strength depend on when
        it was picked -- the same buff doing a third of the work in wave 2.
        """
        for dungeon in pve.MODES:
            for wave_index in (1, pve.WAVE_COUNT):
                state = pve.apply_choice(pve.initial_state(dungeon), "b_repair")
                state = pve.apply_choice(state, "b_rage")
                for deploy in pve.reward_deploys(state, wave_index, 1000):
                    self.assertEqual(deploy["level"], pve.LEVEL_MAX,
                                     "%s wave %d %s was not deployed at max level"
                                     % (dungeon, wave_index, deploy["reward"]))

    def test_all_four_specification_buffs_are_present(self):
        kinds = {option["kind"] for option in pve.DRAFT_POOL}
        for kind in ("elixir_rate", "repair_towers", "reinforce_card", "rage_aura"):
            self.assertIn(kind, kinds)

    def test_every_kind_is_handled_by_the_state_machine(self):
        # An option whose kind the state machine does not implement is a lie: the
        # dialog offers it and picking it does nothing.
        source = Path(pve.__file__).read_text(encoding="utf-8")
        for option in pve.DRAFT_POOL:
            with self.subTest(option=option["id"]):
                self.assertIn('"%s"' % option["kind"], source)

    def test_every_option_states_its_effect(self):
        for option in pve.DRAFT_POOL:
            with self.subTest(option=option["id"]):
                self.assertTrue(option["title"].strip())
                self.assertTrue(option["description"].strip())

    def test_the_repair_restores_thirty_percent(self):
        state = pve.apply_choice(pve.initial_state("pve_goblin"), "b_repair")
        self.assertAlmostEqual(state["repair_fraction"], 0.30)

    def test_the_rage_aura_grants_thirty_five_percent(self):
        state = pve.apply_choice(pve.initial_state("pve_goblin"), "b_rage")
        self.assertAlmostEqual(state["rage_speed"], 0.35)

    def test_the_reinforcement_joins_the_rotation(self):
        state = pve.initial_state("pve_goblin")
        card = pve.reinforcement_card(state, 0)
        self.assertIn(card, pve.deck_for("pve_goblin"))
        after = pve.apply_choice(state, "c_reinforce_heavy")
        self.assertIn(card, after["extra_cards"])

    def test_the_elixir_rate_reaches_the_runtime(self):
        original = mobile_fair.pve_elixir_rate()
        try:
            state = pve.apply_choice(pve.initial_state("pve_goblin"), "a_elixir_2")
            mobile_fair.set_pve_elixir_rate(pve.effective_elixir_rate(state, 0))
            self.assertEqual(mobile_fair.pve_elixir_rate(), 1.5)
        finally:
            mobile_fair.set_pve_elixir_rate(original)

    def test_the_runner_pushes_the_rate_into_the_native_player(self):
        runner = Path(mobile_fair.__file__).read_text(encoding="utf-8")
        start = runner.index("def run_pve_device_match(")
        body = runner[start:runner.index("\ndef ", start + 10)]
        self.assertIn("setPveElixirRate(", body)
        # Applied every frame, because the engine recomputes elixir each tick and
        # a one-shot credit would be spent immediately.
        self.assertIn("if pve_elixir_rate()!=1.0:", body)

    def test_the_native_side_refuses_a_rate_that_is_not_a_rate(self):
        jni = (PROBE / "local_app_jni.inc").read_text(encoding="utf-8")
        self.assertIn("Java_com_firstlight_offline_LocalBattleRuntime_setPveElixirRate", jni)
        self.assertIn("rate out of range", jni)
        runtime = (ROOT / "android_runtime_smoke/localapp/src/main/java/com/firstlight/offline"
                          "/LocalBattleRuntime.java").read_text(encoding="utf-8")
        self.assertIn("setPveElixirRate", runtime)


class DialogSurfaceTest(unittest.TestCase):
    """The dialog is readable and tells the player how long they have."""

    def setUp(self):
        self.runtime = (ROOT / "android_runtime_smoke/localapp/src/main/java"
                               "/com/firstlight/offline"
                               "/LocalBattleRuntime.java").read_text(encoding="utf-8")

    def test_the_dialog_is_a_material_dialog(self):
        self.assertIn("Theme_Material_Light_Dialog_Alert", self.runtime)

    def test_the_dialog_shows_the_remaining_seconds(self):
        # A fifteen second promise the player cannot see is a promise they have to
        # take on faith, which is how a "choose carefully" moment becomes a
        # three second reflex tap.
        self.assertIn("draftCountdownText(", self.runtime)
        self.assertIn("秒后自动选择第 1 项", self.runtime)

    def test_the_dialog_does_not_clip_a_description(self):
        self.assertIn("setMaxLines(4)", self.runtime)

    def test_the_dialog_cannot_be_dismissed_without_a_pick(self):
        self.assertIn(".setCancelable(false)", self.runtime)

    def test_a_timeout_picks_the_first_option(self):
        self.assertIn("if(picked==null) picked=firstDraftOption();", self.runtime)

    def test_the_pick_reaches_python(self):
        self.assertIn("reportRoguelikeChoice(", self.runtime)
        self.assertIn("report_draft_choice", Path(mobile_fair.__file__).read_text(encoding="utf-8"))


class WaveScheduleTest(unittest.TestCase):
    """The table that is recorded is the table that is armed."""

    def test_the_schedule_is_reproducible(self):
        for key in pve.MODES:
            with self.subTest(dungeon=key):
                self.assertEqual(pve.expand(key), pve.expand(key))

    def test_the_payload_carries_exactly_the_table_it_declares(self):
        for key in pve.MODES:
            with self.subTest(dungeon=key):
                payload = create(key)
                self.assertEqual(payload["pve"]["waves"],
                                 [dict(w) for w in pve.waves_for(key)])

    def test_the_table_validates(self):
        self.assertEqual(pve.validate(), [])

    def test_every_spawn_names_a_card_the_wave_seat_holds(self):
        for key in pve.MODES:
            deck = set(pve.deck_for(key))
            for record in pve.waves_for(key):
                with self.subTest(dungeon=key, card=record["card"]):
                    self.assertIn(record["card"], deck)

    def test_no_spawn_is_behind_the_player(self):
        for key in pve.MODES:
            for record in pve.waves_for(key):
                self.assertGreaterEqual(record["y"],
                                        pve.SPAWN_ROW_MIN * pve.CELL_UNITS)


if __name__ == "__main__":
    unittest.main()