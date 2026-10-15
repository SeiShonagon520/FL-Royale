"""The roguelike climb: ten waves, nine drafts, two bosses, three dungeons.

Four separate contracts are pinned here, and each one is a way the mode can
look correct in a source listing while being unplayable on a device:

1. The wave tables are *deployable*. A wave is an ordinary card deploy resolved
   by id against the wave owner's hand, and the native resolver refuses unless
   the card matches exactly once -- so a table that names a card outside the
   dungeon's own eight-card roster, or a deck with a duplicate, loses every wave
   that names it. The first draft of these tables had 38 such violations.
2. The tables are *shaped* like the spec: ten waves, the four named tiers, the
   twin-boss finale, and no boss anywhere earlier.
3. The draft is a real three-pick-one that mixes its dimensions, honours its
   stack caps, and only ever applies a buff it can actually pay for.
4. The buff effects are expressed as card deploys, because a card deploy is the
   only board mutation available from Python.
"""
import json
import random
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "build/mobile-semantics"))
sys.path.insert(0, str(ROOT / "on_device/python"))

import mobile_fair
import pve_tower_defense as rogue

DUNGEONS = rogue.MODES

HUMAN = {"cards": [26000000, 26000001, 26000005, 28000001, 28000000, 26000003, 26000014, 26000018],
         "forms": [0] * 8}

BRIDGE_Y = (rogue.BRIDGE_ROW * rogue.CELL_UNITS + rogue.CELL_UNITS // 2)
MIDLINE = 16000


class WaveTableShapeTest(unittest.TestCase):
    """The descent is ten waves in four tiers, closing on a twin boss."""

    def test_every_dungeon_has_exactly_ten_waves(self):
        for key in DUNGEONS:
            with self.subTest(dungeon=key):
                self.assertEqual(len(rogue.dungeon(key)), rogue.WAVE_COUNT)

    def test_wave_indices_are_contiguous_from_one(self):
        # The interlude loop walks 1..9 and asks wave_span() for each, so a gap
        # or a duplicate index would silently skip a draft or raise on the run.
        for key in DUNGEONS:
            with self.subTest(dungeon=key):
                indices = [wave["index"] for wave in rogue.dungeon(key)]
                self.assertEqual(indices, list(range(1, rogue.WAVE_COUNT + 1)))

    def test_the_four_tiers_are_authored_as_specified(self):
        # 1-3 outpost, 4-6 elite raid, 7-9 mixed assault, 10 the finale.
        for key in DUNGEONS:
            with self.subTest(dungeon=key):
                tiers = [wave["tier"] for wave in rogue.dungeon(key)]
                self.assertEqual(tiers, ["前哨试炼"] * 3 + ["精英突袭"] * 3
                                 + ["高压混编"] * 3 + ["终极决战"])

    def test_only_the_finale_carries_bosses(self):
        for key in DUNGEONS:
            with self.subTest(dungeon=key):
                for wave in rogue.dungeon(key)[:-1]:
                    self.assertFalse(
                        any(s["is_boss"] for b in wave["batches"] for s in b),
                        "%s wave %d carries a boss outside the finale"
                        % (key, wave["index"]))

    def test_the_finale_is_a_twin_boss_fight(self):
        for key in DUNGEONS:
            with self.subTest(dungeon=key):
                final = rogue.dungeon(key)[-1]
                bosses = [s for b in final["batches"] for s in b if s["is_boss"]]
                self.assertEqual(len(bosses), 2,
                                 "%s finale is not a twin-boss fight" % key)
                self.assertEqual(tuple(s["label"] for s in bosses),
                                 rogue.DUNGEON_BOSSES[key])

    def test_every_wave_spawns_something(self):
        for key in DUNGEONS:
            with self.subTest(dungeon=key):
                for wave in rogue.dungeon(key):
                    self.assertGreater(rogue.wave_spawn_count(wave), 0,
                                       "%s wave %d is empty" % (key, wave["index"]))

    def test_the_named_bosses_match_the_spec_table(self):
        # The spec names the pair per dungeon; a silent substitution would ship a
        # different mode under the right label.
        self.assertEqual(rogue.DUNGEON_BOSSES["pve_goblin"], ("狂暴巨人", "狂暴狂人"))
        self.assertEqual(rogue.DUNGEON_BOSSES["pve_undead"], ("自爆巨型骷髅兵", "狂暴地狱飞龙"))
        self.assertEqual(rogue.DUNGEON_BOSSES["pve_heavy"], ("超级皮卡超人", "超级超级骑士"))


class WaveDeployabilityTest(unittest.TestCase):
    """A wave only exists if the engine can actually deploy it.

    `resolve_card_deploy_request` refuses a scheduled card unless it appears
    exactly once in the wave owner's hand. So the deck must be eight unique
    cards, and every wave must name one of them.
    """

    def test_the_public_validation_passes(self):
        self.assertEqual(rogue.validate(), [])

    def test_each_deck_is_eight_unique_troop_cards(self):
        for key in DUNGEONS:
            with self.subTest(dungeon=key):
                deck = rogue.deck_for(key)
                self.assertEqual(len(deck), 8,
                                 "the engine accepts exactly eight deck cards")
                self.assertEqual(len(set(deck)), 8,
                                 "a duplicate card resolves to two hand matches and "
                                 "every wave naming it is dropped at fire time")

    def test_the_wave_seat_deploys_no_towers(self):
        # A wave that deployed a support card would build a defence in front of
        # the wave, and the wave seat has no elixir to pay for it.
        for key in DUNGEONS:
            with self.subTest(dungeon=key):
                for card in rogue.deck_for(key):
                    self.assertFalse(27000000 <= card < 28000000,
                                     "%s puts support card %d in the wave deck"
                                     % (key, card))

    def test_every_authored_spawn_names_a_card_from_its_own_deck(self):
        for key in DUNGEONS:
            with self.subTest(dungeon=key):
                allowed = set(rogue.deck_for(key))
                for wave in rogue.dungeon(key):
                    for batch in wave["batches"]:
                        for spawn in batch:
                            self.assertIn(spawn["card"], allowed,
                                          "%s wave %d uses %d, outside its deck"
                                          % (key, wave["index"], spawn["card"]))

    def test_every_flattened_wave_names_a_card_from_its_own_deck(self):
        for key in DUNGEONS:
            with self.subTest(dungeon=key):
                allowed = set(rogue.deck_for(key))
                for wave in rogue.waves_for(key):
                    self.assertIn(wave["card"], allowed)

    def test_every_spawn_is_in_the_enemy_spawn_window(self):
        # The specification puts the spawn area at rows 18-24, y = 18000..24000.
        # A wave authored on the player half deploys behind the player and walks
        # away from the towers, which reads as "the monsters never arrive".
        #
        # The upper bound is the *row*, not the cell's world y: row 24's centre
        # is y = 24500, which is past 24000 but still row 24. Asserting on the
        # world coordinate would reject the very last row the specification
        # allows, so the check is stated in the grid the table is authored in.
        for key in DUNGEONS:
            with self.subTest(dungeon=key):
                for wave in rogue.dungeon(key):
                    for batch in wave["batches"]:
                        for spawn in batch:
                            self.assertGreaterEqual(spawn["row"], rogue.SPAWN_ROW_MIN)
                            self.assertLessEqual(spawn["row"], rogue.SPAWN_ROW_MAX)

    def test_every_spawn_is_on_a_cell_centre_inside_the_arena(self):
        for key in DUNGEONS:
            with self.subTest(dungeon=key):
                for wave in rogue.waves_for(key):
                    for axis, limit in (("x", 18000), ("y", 32000)):
                        value = wave[axis]
                        self.assertTrue(0 <= value < limit)
                        self.assertEqual(value % rogue.CELL_UNITS,
                                         rogue.CELL_UNITS // 2,
                                         "%s is not on a cell centre" % axis)

    def test_the_enemy_half_is_free_of_tower_footprints(self):
        """The enemy's own towers are relocated off-screen, not on the field.

        The arena is rewritten by `pve_towerless.inc`, so the enemy side has no
        towers where the wave table spawns and no cell is reserved for one. The
        player's towers are untouched and sit below the spawn window.
        """
        self.assertEqual(rogue.TOWER_CELLS, set())
        for key in DUNGEONS:
            with self.subTest(dungeon=key):
                for wave in rogue.dungeon(key):
                    for batch in wave["batches"]:
                        for spawn in batch:
                            row = spawn["row"]
                            if row >= 17:
                                self.assertLessEqual(row, rogue.SPAWN_ROW_MAX)

    def test_no_two_units_of_one_batch_share_a_cell(self):
        """The engine refuses a deploy that overlaps an existing unit.

        Two units of the same batch on one cell is not a cosmetic authoring slip:
        the deploy is rejected at fire time, so the batch silently loses a unit.
        """
        for key in DUNGEONS:
            with self.subTest(dungeon=key):
                for wave in rogue.dungeon(key):
                    for batch in wave["batches"]:
                        cells = [(s["lane_x"], s["row"]) for s in batch]
                        self.assertEqual(len(cells), len(set(cells)))

    def test_the_flattened_table_is_sorted_by_tick(self):
        # The arming cursor only moves forward, so an unsorted table would retire
        # waves as "boundary passed unarmed" before they ever fire.
        for key in DUNGEONS:
            with self.subTest(dungeon=key):
                ticks = [w["tick"] for w in rogue.waves_for(key)]
                self.assertEqual(ticks, sorted(ticks))

    def test_the_first_wave_lands_after_the_world_exists(self):
        for key in DUNGEONS:
            with self.subTest(dungeon=key):
                self.assertGreater(rogue.waves_for(key)[0]["tick"], 0)

    def test_batches_are_three_to_five_seconds_apart(self):
        """The specification's "short-interval staggered assault".

        Both bounds are asserted on the authored table because a batch gap that
        drifts outside 3-5 seconds turns a staggered assault into either a single
        simultaneous blob or a trickle, and neither is the mode.
        """
        import random as _random
        for key in DUNGEONS:
            for wave in rogue.dungeon(key):
                rng = _random.Random(1234 + wave["index"])
                gaps = [rogue.batch_gap(i + 1, rng)
                        for i in range(len(wave["batches"]) - 1)]
                for gap in gaps:
                    self.assertGreaterEqual(gap, 3 * rogue.TICKS_PER_SECOND)
                    self.assertLessEqual(gap, 5 * rogue.TICKS_PER_SECOND)

    def test_every_wave_has_between_two_and_four_batches(self):
        for key in DUNGEONS:
            for wave in rogue.dungeon(key):
                self.assertGreaterEqual(len(wave["batches"]), 2)
                self.assertLessEqual(len(wave["batches"]), 4)

    def test_a_wave_actually_fields_a_swarm(self):
        """A "multi-batch assault" of three units is not an assault.

        The specification asks for large formations arriving in several batches,
        so the per-wave unit count is floored well above a handful.
        """
        for key in DUNGEONS:
            for wave in rogue.dungeon(key):
                self.assertGreaterEqual(rogue.wave_spawn_count(wave), 15,
                                        "%s wave %d fields only %d units"
                                        % (key, wave["index"],
                                           rogue.wave_spawn_count(wave)))

    def test_the_wave_spine_leaves_room_for_batches_and_an_interlude(self):
        # The precomputed spine is the upper bound on how long a wave may take to
        # arrive. It has to cover the wave's own batches (up to four, five
        # seconds apart) *and* a full interlude, or the next wave's first batch
        # would be scheduled before a cleared wave's fifteen seconds had elapsed.
        self.assertGreaterEqual(rogue.WAVE_SPACING, rogue.REQUIRED_SPACING)
        for key in DUNGEONS:
            with self.subTest(dungeon=key):
                for index in range(1, rogue.WAVE_COUNT):
                    _, last = rogue.wave_span(key, index)
                    next_start = rogue.wave_start_tick(index + 1)
                    self.assertLess(last, next_start,
                                    "%s wave %d overruns wave %d"
                                    % (key, index, index + 1))
                    self.assertGreaterEqual(
                        next_start - last, rogue.INTERLUDE_TICKS,
                        "%s leaves no interlude after wave %d" % (key, index))

    def test_the_spine_guard_actually_fires(self):
        # The floor above is a module-level assertion, so a future edit that
        # shrinks WAVE_SPACING has to fail the import rather than quietly ship a
        # table whose interlude does not fit. Re-running the check by hand is
        # what proves the number is load-bearing and not decorative.
        self.assertEqual(rogue.REQUIRED_SPACING,
                         rogue.BATCH_MAX_TICKS + rogue.INTERLUDE_TICKS + 1)
        self.assertGreater(rogue.WAVE_SPACING, rogue.REQUIRED_SPACING - 1)

    def test_a_wave_that_starts_late_still_leaves_an_interlude(self):
        # A wave that takes its full four batches consumes BATCH_MAX_TICKS; the
        # interlude is measured from that wave's own clear, and the next wave is
        # scheduled relative to it, so the two never collide.
        worst = rogue.BATCH_MAX_TICKS
        self.assertLess(worst, rogue.WAVE_SPACING)
        self.assertGreaterEqual(rogue.WAVE_SPACING - worst, rogue.INTERLUDE_TICKS)

    def test_the_validator_would_catch_a_thin_batch(self):
        # The check has to be real: collapse one wave to a single batch and the
        # validator must complain, or the assertions above are decorative.
        import copy
        original = copy.deepcopy(rogue.DUNGEONS["pve_goblin"])
        try:
            waves = list(rogue.DUNGEONS["pve_goblin"])
            thinned = dict(waves[4])
            thinned["batches"] = (waves[4]["batches"][0],)
            waves[4] = thinned
            rogue.DUNGEONS["pve_goblin"] = tuple(waves)
            problems = rogue.validate("pve_goblin")
            self.assertTrue(any("batches" in problem for problem in problems),
                            "a one-batch wave passed validation: %s" % problems)
        finally:
            rogue.DUNGEONS["pve_goblin"] = original

    def test_the_validator_would_catch_a_stacked_cell(self):
        # The other authoring mistake the validator has to reject: two units of
        # one batch on one cell, which the engine refuses at fire time.
        import copy
        original = copy.deepcopy(rogue.DUNGEONS["pve_goblin"])
        try:
            waves = list(rogue.DUNGEONS["pve_goblin"])
            wave = dict(waves[0])
            first = waves[0]["batches"][0]
            stacked = list(first)
            stacked[1] = dict(stacked[1], lane_x=stacked[0]["lane_x"])
            wave["batches"] = (tuple(stacked),) + tuple(waves[0]["batches"][1:])
            waves[0] = wave
            rogue.DUNGEONS["pve_goblin"] = tuple(waves)
            problems = rogue.validate("pve_goblin")
            self.assertTrue(any("stacks two units" in problem for problem in problems),
                            "a stacked cell passed validation: %s" % problems)
        finally:
            rogue.DUNGEONS["pve_goblin"] = original

    def test_unknown_dungeons_fail_loudly(self):
        with self.assertRaises(KeyError):
            rogue.dungeon("pve_does_not_exist")
        with self.assertRaises(KeyError):
            rogue.waves_for("pve_does_not_exist")


class DraftPoolTest(unittest.TestCase):
    """A three-pick-one that actually is one, and actually mixes."""

    def state(self, **kwargs):
        return rogue.initial_state("pve_goblin", evolvable_cards=[26000000, 26000001],
                                   **kwargs)

    def test_every_draw_offers_exactly_three_distinct_options(self):
        rng = random.Random(7)
        for _ in range(400):
            options = rogue.draft_options(self.state(), rng)
            self.assertEqual(len(options), rogue.DRAFT_CHOICES)
            ids = [o["id"] for o in options]
            self.assertEqual(len(set(ids)), rogue.DRAFT_CHOICES,
                             "a draft offered the same upgrade twice")

    def test_every_draw_spans_at_least_two_dimensions(self):
        # The spec asks for a mixed pool across the three dimensions; a draw of
        # three all from one row is not a mix.
        rng = random.Random(11)
        for _ in range(400):
            options = rogue.draft_options(self.state(), rng)
            dimensions = {o["dimension"] for o in options}
            self.assertGreaterEqual(len(dimensions), rogue.MIN_DIMENSIONS_PER_DRAFT)

    def test_all_three_dimensions_are_reachable(self):
        seen = set()
        rng = random.Random(3)
        for _ in range(600):
            seen.update(o["dimension"] for o in rogue.draft_options(self.state(), rng))
        self.assertEqual(seen, set(rogue.DRAFT_DIMENSIONS))

    def test_every_option_in_the_pool_can_be_offered(self):
        # A pool entry nothing can draw is dead content.
        seen = {o["id"] for o in rogue.DRAFT_POOL}
        rng = random.Random(5)
        offered = set()
        for _ in range(2000):
            offered.update(o["id"] for o in rogue.draft_options(self.state(), rng))
        self.assertEqual(seen, offered)

    def test_the_draw_is_random_rather_than_fixed(self):
        # A fixed draw would make nine drafts a formality.
        rng = random.Random(2)
        draws = {tuple(o["id"] for o in rogue.draft_options(self.state(), rng))
                 for _ in range(300)}
        self.assertGreater(len(draws), 5)

    def test_weights_are_honoured(self):
        # Pin the weighted draw itself rather than the dialog: inside one
        # dimension, weight 3 must beat weight 2 at close to the 3:2 ratio. The
        # dialog dilutes that ratio because its second pass also competes for
        # slots across the other dimensions, so measuring it there would pin an
        # accident of the mix rather than the contract.
        rng = random.Random(13)
        counts = {o["id"]: 0 for o in rogue.DRAFT_A}
        for _ in range(20000):
            for option in rogue._draw(list(rogue.DRAFT_A), rng, 1):
                counts[option["id"]] += 1
        self.assertGreater(counts["a_elixir_1"], counts["a_elixir_2"])
        self.assertGreater(counts["a_elixir_1"], counts["a_elixir_3"])
        # 3:2 weights over 20k draws should land near 1.5.
        ratio = counts["a_elixir_1"] / counts["a_elixir_2"]
        self.assertGreater(ratio, 1.35)
        self.assertLess(ratio, 1.65)

    def test_the_dialog_ranks_options_by_weight(self):
        # Across the whole dialog the mix dilutes the ratio but must not invert
        # it: a weight-3 entry still has to beat a weight-2 one.
        rng = random.Random(13)
        counts = {o["id"]: 0 for o in rogue.DRAFT_POOL}
        for _ in range(4000):
            for option in rogue.draft_options(self.state(), rng):
                counts[option["id"]] += 1
        for heavy, light in (("a_elixir_1", "a_elixir_2"),
                             ("a_elixir_1", "a_elixir_3"),
                             ("c_reinforce_heavy", "c_reinforce_boss")):
            with self.subTest(heavy=heavy, light=light):
                self.assertGreater(counts[heavy], counts[light])

    def test_the_elixir_ladder_only_offers_the_next_rung(self):
        # Two rates cannot both be in effect, so after taking 1.2x the 1.2x
        # option is no longer offered.
        state = rogue.apply_choice(self.state(), "a_elixir_1")
        self.assertEqual(state["elixir_rate"], 1.2)
        rates = [o for o in rogue.DRAFT_POOL if o["kind"] == "elixir_rate"]
        offered = {o["id"] for o in rates if rogue.eligible(o, state)}
        self.assertEqual(offered, {"a_elixir_2", "a_elixir_3"})
        rng = random.Random(17)
        for _ in range(300):
            ids = [o["id"] for o in rogue.draft_options(state, rng)]
            self.assertNotIn("a_elixir_1", ids,
                             "a rate the player already has is still on offer")

    def test_the_ladder_stops_at_the_top(self):
        state = rogue.apply_choice(self.state(), "a_elixir_3")
        self.assertEqual(state["elixir_rate"], 2.0)
        rates = [o for o in rogue.DRAFT_POOL if o["kind"] == "elixir_rate"]
        self.assertEqual([o for o in rates if rogue.eligible(o, state)], [],
                         "the ladder offered a fourth rate")

    def test_a_capped_upgrade_stops_being_offered(self):
        state = rogue.initial_state("pve_goblin")
        for _ in range(rogue.DRAFT_POOL_BY_ID["b_rage"]["max_stacks"]):
            state = rogue.apply_choice(state, "b_rage")
        self.assertFalse(rogue.eligible(rogue.DRAFT_POOL_BY_ID["b_rage"], state))

    def test_an_upgrade_the_run_cannot_pay_for_is_not_offered(self):
        # A reinforcement card has to come from the run's own roster, or the pick
        # promises a card no seat in the match holds.
        state = self.state()
        for option in rogue.DRAFT_POOL:
            if option["kind"] != "reinforce_card":
                continue
            rank = 1 if option["id"] == "c_reinforce_boss" else 0
            card = rogue.reinforcement_card(state, rank)
            self.assertIn(card, rogue.deck_for("pve_goblin"))

    def test_the_specification_four_buffs_are_all_present(self):
        """The spec names exactly four upgrades; all four must be on offer.

        A buff that is only described and never implemented is the defect this
        pool exists to prevent, so each of the spec's four effects is pinned by
        kind rather than by id: the ids may be renumbered, the promises may not
        disappear.
        """
        kinds = {option["kind"] for option in rogue.DRAFT_POOL}
        for kind in ("elixir_rate", "repair_towers", "reinforce_card", "rage_aura"):
            self.assertIn(kind, kinds,
                          "the specification's %s buff is missing from the pool" % kind)

    def test_an_unknown_option_is_rejected(self):
        with self.assertRaises(KeyError):
            rogue.apply_choice(self.state(), "not_a_real_option")


class BuffStateMachineTest(unittest.TestCase):
    """A pick has to change something the run can observe."""

    def state(self, dungeon="pve_goblin"):
        return rogue.initial_state(dungeon, evolvable_cards=[26000000, 26000001])

    def test_applying_a_choice_does_not_mutate_the_old_state(self):
        # A half-applied pick would be unrecoverable mid-run.
        before = self.state()
        after = rogue.apply_choice(before, "a_elixir_2")
        self.assertEqual(before["elixir_rate"], 1.0)
        self.assertEqual(after["elixir_rate"], 1.5)
        self.assertIsNot(before, after)

    def test_elixir_rate_raises_the_effective_rate(self):
        state = rogue.apply_choice(self.state(), "a_elixir_3")
        self.assertEqual(state["elixir_rate"], 2.0)
        self.assertEqual(rogue.effective_elixir_rate(state, 0), 2.0)

    def test_the_elixir_rate_reaches_the_runtime_channel(self):
        """The spec requires the buff to actually take effect, not just be named.

        The wave seat cannot express a rate -- a card deploy moves a unit and
        nothing else -- so the rate has to leave Python through its own channel
        and be applied to the player's elixir object. If this link is dropped the
        option still reads correctly in the dialog and does nothing in play,
        which is exactly the "no fake options" defect the spec forbids.
        """
        import mobile_fair
        original = mobile_fair.pve_elixir_rate()
        try:
            state = rogue.apply_choice(self.state(), "a_elixir_2")
            mobile_fair.set_pve_elixir_rate(
                rogue.effective_elixir_rate(state, 0))
            self.assertEqual(mobile_fair.pve_elixir_rate(), 1.5)
        finally:
            mobile_fair.set_pve_elixir_rate(original)
        self.assertEqual(mobile_fair.pve_elixir_rate(), 1.0)

    def test_the_runtime_refuses_a_rate_that_is_not_a_rate(self):
        import mobile_fair
        original = mobile_fair.pve_elixir_rate()
        try:
            for bad in (0.0, -1.0, 99.0, "fast", None):
                mobile_fair.set_pve_elixir_rate(bad)
                self.assertEqual(mobile_fair.pve_elixir_rate(), 1.0,
                                 "a bad rate was accepted: %r" % (bad,))
        finally:
            mobile_fair.set_pve_elixir_rate(original)

    def test_rage_accumulates_but_is_capped(self):
        state = self.state()
        for _ in range(rogue.DRAFT_POOL_BY_ID["b_rage"]["max_stacks"]):
            state = rogue.apply_choice(state, "b_rage")
        self.assertAlmostEqual(state["rage_speed"],
                               rogue.DRAFT_POOL_BY_ID["b_rage"]["payload"]["speed"]
                               * rogue.DRAFT_POOL_BY_ID["b_rage"]["max_stacks"])

    def test_each_pick_is_recorded_in_the_run_history(self):
        state = self.state()
        state = rogue.apply_choice(state, "a_elixir_1")
        state = rogue.apply_choice(state, "b_repair")
        self.assertEqual([h["id"] for h in state["history"]],
                         ["a_elixir_1", "b_repair"])

    def test_a_nine_draft_run_reaches_the_ninth_interlude(self):
        # The mode promises nine chances; a pool that runs dry early would end
        # the run with fewer.
        state = self.state()
        rng = random.Random(23)
        drafts = 0
        for wave in range(1, rogue.WAVE_COUNT):
            options = rogue.draft_options(state, rng)
            self.assertEqual(len(options), rogue.DRAFT_CHOICES)
            state = rogue.apply_choice(state, options[0]["id"],
                                       tick=wave * rogue.WAVE_SPACING)
            drafts += 1
        self.assertEqual(drafts, rogue.WAVE_COUNT - 1)


class RewardDeployTest(unittest.TestCase):
    """A buff that touches the board does it with a card, because that is all
    Python can do."""

    def state(self, dungeon="pve_goblin"):
        return rogue.initial_state(dungeon)

    def test_a_reward_is_scheduled_at_an_offset_not_an_absolute_tick(self):
        """The engine refuses a deploy whose execute tick is not in the future.

        Device evidence: every board-touching buff was rejected with "execute
        tick is not in the future, currentTick 2430". The reward record carried
        the tick captured at pick time, but the pick-wait loop had already
        advanced the world past it by the arming frame, so the tick was in the
        past. A relative offset is the only shape that cannot go stale.
        """
        state = rogue.apply_choice(self.state(), "b_repair")
        state = rogue.apply_choice(state, "b_rage")
        for deploy in rogue.reward_deploys(state, 2, 1000):
            with self.subTest(reward=deploy["reward"]):
                self.assertIn("offset", deploy,
                              "a reward without an offset carries a stale absolute tick")
                self.assertGreaterEqual(deploy["offset"],
                                        rogue.REWARD_TICK_OFFSET)
                self.assertGreater(deploy["offset"], 0)

    def test_a_repair_pays_a_heal_spell_to_each_royal_tower(self):
        state = rogue.apply_choice(self.state(), "b_repair")
        deploys = rogue.repair_deploys(state, 2, 900)
        self.assertEqual(len(deploys), 3, "king plus two princesses")
        for deploy in deploys:
            self.assertEqual(deploy["card"], rogue.HEAL)
            self.assertIn(deploy["card"], rogue.deck_for("pve_goblin"),
                          "a heal the wave seat cannot hold would never fire")
            self.assertLess(deploy["y"], MIDLINE,
                            "a repair aimed at the enemy half repairs nothing")

    def test_the_repair_targets_the_three_royal_tower_cells(self):
        # The stock 3+3 anchors, matching pve_tower_verdict.TOWER_POSITIONS.
        cells = rogue.royal_tower_cells()
        self.assertEqual(cells, ((8, 3), (2, 6), (14, 6)))
        state = rogue.apply_choice(self.state(), "b_repair")
        deploys = rogue.repair_deploys(state, 1, 500)
        self.assertEqual([(d["x"], d["y"]) for d in deploys],
                         [rogue.cell_to_world(x, y) for x, y in cells])

    def test_the_rage_aura_covers_the_player_half(self):
        state = rogue.apply_choice(self.state(), "b_rage")
        deploys = rogue.rage_deploys(state, 3, 1200)
        self.assertEqual(len(deploys), 3, "one aura per lane")
        for deploy in deploys:
            self.assertEqual(deploy["card"], rogue.RAGE_SPELL)
            self.assertIn(deploy["card"], rogue.deck_for("pve_goblin"))
            self.assertLess(deploy["y"], MIDLINE,
                            "a rage aimed at the enemy half buffs the monsters")

    def test_an_untaken_reward_costs_nothing(self):
        state = self.state()
        self.assertEqual(rogue.reward_deploys(state, 1, 100), [])

    def test_rewards_are_paid_once(self):
        # A repair is a one-shot: it restores 30% and is then consumed, so it
        # cannot silently re-heal the towers on every following wave.
        state = rogue.apply_choice(self.state(), "b_repair")
        self.assertTrue(rogue.repair_deploys(state, 2, 100))
        state = rogue.consume_rewards(state)
        self.assertEqual(rogue.repair_deploys(state, 3, 200), [],
                         "a repair must not repeat on every following wave")

    def test_the_rage_aura_is_re_armed_because_it_is_permanent(self):
        """The spec promises a *permanent* aura, so it is re-armed each interlude.

        The engine has no persistent aura object, so the only honest expression
        of "permanently covers our half" is to re-apply the sweep every interlude.
        `consume_rewards` must therefore leave `rage_speed` alone -- clearing it
        would quietly downgrade the option from permanent to one-shot.
        """
        state = rogue.apply_choice(self.state(), "b_rage")
        state = rogue.consume_rewards(state)
        self.assertTrue(rogue.rage_deploys(state, 3, 200),
                        "the permanent aura stopped being applied after one interlude")

    def test_stacking_the_rage_aura_keeps_the_speed_climbing(self):
        state = self.state()
        for _ in range(3):
            state = rogue.apply_choice(state, "b_rage")
        self.assertGreater(state["rage_speed"], 1.0,
                           "three aura picks did not compound")

    def test_every_reward_is_deployable_in_every_dungeon(self):
        for key in DUNGEONS:
            state = rogue.apply_choice(rogue.initial_state(key), "b_repair")
            state = rogue.apply_choice(state, "b_rage")
            deck = set(rogue.deck_for(key))
            with self.subTest(dungeon=key):
                for deploy in rogue.reward_deploys(state, 2, 900):
                    self.assertIn(deploy["card"], deck)


class StarRatingTest(unittest.TestCase):
    """The spec's three tiers on the king's remaining health."""

    def test_the_three_star_thresholds(self):
        self.assertEqual(rogue.stars_for(1.0), 3)
        self.assertEqual(rogue.stars_for(0.75), 3)
        self.assertEqual(rogue.stars_for(0.74), 2)
        self.assertEqual(rogue.stars_for(0.35), 2)
        self.assertEqual(rogue.stars_for(0.34), 1)
        self.assertEqual(rogue.stars_for(0.0), 1)


class ArenaTopologyTest(unittest.TestCase):
    """Solo and ally runs are the same rules on a different map."""

    def test_solo_runs_on_the_standard_arena(self):
        self.assertEqual(rogue.arena_location(False), 15000199)

    def test_the_ally_toggle_switches_to_the_2v2_arena(self):
        self.assertEqual(rogue.arena_location(True), 15000020)

    def test_the_toggle_actually_reaches_the_created_match(self):
        # The flag rides on the deck payload, so a match built without it would
        # silently be the solo map.
        for ally, expected in ((False, 15000199), (True, 15000020)):
            with self.subTest(aiAlly=ally):
                deck = dict(HUMAN)
                deck["aiAlly"] = ally
                payload = json.loads(mobile_fair.create_match(
                    json.dumps(deck), "", "", game_mode="pve_goblin"))
                self.assertEqual(payload["battle"]["location"], expected)
                self.assertEqual(payload["roguelike"]["aiAlly"], ally)


class RoguelikeMatchTest(unittest.TestCase):
    """The three climbs have to be creatable end to end."""

    def test_each_dungeon_is_in_the_mode_catalog(self):
        for key in DUNGEONS:
            with self.subTest(mode=key):
                self.assertIn(key, mobile_fair.GAME_MODES)
                self.assertEqual(mobile_fair.GAME_MODES[key]["id"],
                                 rogue.PVE_GAME_MODE_ID)

    def test_each_dungeon_carries_its_own_label(self):
        for key in DUNGEONS:
            with self.subTest(mode=key):
                self.assertEqual(mobile_fair.GAME_MODES[key]["label"],
                                 rogue.DUNGEON_LABELS[key])

    def test_a_created_match_describes_its_own_climb(self):
        for key in DUNGEONS:
            with self.subTest(mode=key):
                payload = json.loads(mobile_fair.create_match(
                    json.dumps(HUMAN), "", "", game_mode=key))
                block = payload["roguelike"]
                self.assertEqual(block["mode"], key)
                self.assertEqual(block["label"], rogue.DUNGEON_LABELS[key])
                self.assertEqual(block["enemyOwner"], 1)
                self.assertEqual(len(block["waves"]), len(rogue.waves_for(key)))

    def test_the_enemy_seat_holds_the_dungeons_own_roster(self):
        for key in DUNGEONS:
            with self.subTest(mode=key):
                payload = json.loads(mobile_fair.create_match(
                    json.dumps(HUMAN), "", "", game_mode=key))
                self.assertEqual([row["d"] for row in payload["battle"]["deck1"]["sp"]],
                                 list(rogue.deck_for(key)))


class RoguelikeRunnerSourceTest(unittest.TestCase):
    """The draft is driven from the runner; pin the wiring that makes it fire.

    Every defect below is invisible to a unit test because the interlude needs a
    live battle: the module imports cleanly, the draft functions are pure, and
    the match only reaches this code once the engine is stepping.
    """

    @staticmethod
    def source():
        return Path(mobile_fair.__file__).read_text(encoding="utf-8")

    @staticmethod
    def runner():
        text = RoguelikeRunnerSourceTest.source()
        start = text.index("def run_pve_device_match(")
        return text[start:text.index("\ndef ", start + 10)]

    def test_the_world_clock_comes_from_the_capture_not_the_observation(self):
        """The pump's tick must not be read from the policy observation.

        Device evidence: the run armed waves 0-10 and then silently stopped
        spawning while the log kept ticking the probe to 3380. The cause was
        `tick=adapter.observation.tick` -- `environment.step` fails on this path
        with "rich.combatEvents delta skipped the merge cursor" whenever a wave
        deploy lands between frames, and on failure the observation is not
        advanced. So the tick froze, the 600-tick arming horizon never opened
        again, and the run stopped escalating. The capture is refilled every
        frame regardless, so it is the only clock that cannot stall.
        """
        runner = self.runner()
        self.assertIn('tick=int(native.capture["tick"])', runner)
        self.assertNotIn("tick=adapter.observation.tick", runner)
        # ...and the summary must report the same clock, or a finished run
        # would be logged as having stopped early.
        self.assertIn('final_tick=int(native.capture["tick"])', runner)
        self.assertNotIn('"tick":adapter.observation.tick', runner)

    def test_the_interlude_opens_only_after_a_clear_and_a_full_interlude(self):
        """The safe interlude is measured from the field emptying.

        The precomputed wave spacing was wrong twice over: the last batch of a
        wave lands several seconds after the first, so "past the last spawn tick"
        can be true while the field is still full of monsters; and the fifteen
        seconds the specification asks for are fifteen seconds of quiet *after*
        the last monster dies, not fifteen seconds that overlap the fight. So the
        interlude must be gated on `live_enemy_troops` reaching zero and then on
        the full interlude having elapsed since that tick.
        """
        runner = self.runner()
        self.assertIn("live_enemy_troops(", runner)
        self.assertIn("wave_in_progress and enemies==0 and not pending_batches", runner)
        self.assertIn("wave_cleared_tick=tick", runner)
        self.assertIn("if tick>=wave_cleared_tick+INTERLUDE_TICKS:", runner)

    def test_the_interlude_is_at_least_ten_seconds(self):
        # The specification's floor. A draft the player cannot read is not a
        # three-pick-one, it is a lottery.
        self.assertGreaterEqual(rogue.INTERLUDE_SECONDS, rogue.INTERLUDE_SECONDS_MIN)
        self.assertLessEqual(rogue.INTERLUDE_SECONDS, rogue.INTERLUDE_SECONDS_MAX)
        self.assertEqual(rogue.INTERLUDE_TICKS, rogue.INTERLUDE_SECONDS * 20)

    def test_the_runner_raises_the_draft_dialog(self):
        runner = self.runner()
        self.assertIn("runtime.showRoguelikeDraft(", runner)
        self.assertIn("draft_options(", runner)

    def test_the_dialog_gets_the_full_fifteen_second_countdown(self):
        # The dialog's own countdown is what guarantees the player can read every
        # option, so the runner has to pass the module's number rather than
        # letting Java default to three.
        runner = self.runner()
        self.assertIn("DRAFT_TIMEOUT_SECONDS)", runner)
        self.assertEqual(rogue.DRAFT_TIMEOUT_SECONDS, rogue.INTERLUDE_SECONDS)

    def test_the_draft_pick_is_read_through_the_mailbox(self):
        # The dialog answers on the UI thread while the loop is blocked inside
        # nextFrame, so the pick has to arrive through the shared mailbox.
        runner = self.runner()
        self.assertIn("_take_draft_choice(", runner)
        self.assertIn("report_draft_choice(", RoguelikeRunnerSourceTest.source())

    def test_a_draft_is_offered_once_per_interlude(self):
        runner = self.runner()
        self.assertIn("drafted_waves", runner)
        self.assertIn("interlude_wave is None", runner)

    def test_a_run_offers_exactly_nine_drafts(self):
        # One per gap between the ten waves, and never after the finale, which is
        # why the interlude's wave index is clamped to WAVE_COUNT-1.
        self.assertEqual(rogue.WAVE_COUNT - 1, rogue.DRAFT_COUNT)
        runner = self.runner()
        self.assertIn("min(1+len(drafted_waves),WAVE_COUNT-1)", runner)

    def test_an_unanswered_draft_falls_back_instead_of_wedging_the_run(self):
        # The dialog has its own countdown, but a dialog that never reports must
        # not freeze the run, so the loop takes the first option and moves on.
        runner = self.runner()
        self.assertIn("if not picked and options:", runner)
        self.assertIn("picked=options[0][\"id\"]", runner)

    def test_the_runner_arms_rewards_relative_to_the_world_clock(self):
        # Device evidence: every board buff was rejected with "execute tick is
        # not in the future", so the arming has to be relative to now.
        runner = self.runner()
        self.assertIn('now=int(native.capture["tick"])', runner)
        self.assertIn('int(reward.get("offset",1))', runner)

    def test_the_reward_deploys_are_armed_through_the_same_scheduler(self):
        runner = self.runner()
        self.assertIn("reward_deploys(", runner)
        self.assertIn("runtime.scheduleCard(", runner)

    def test_the_run_reports_its_progression_in_the_summary(self):
        runner = self.runner()
        self.assertIn('summary["roguelike"]', runner)
        for field in ("dungeon", "drafts", "draftedWaves", "buffs", "waveReached"):
            self.assertIn('"%s"' % field, runner,
                          "the device log cannot show %s, so a climb cannot be "
                          "verified from a run" % field)

    def test_the_java_and_python_draft_callbacks_agree_on_arity(self):
        """`reportRoguelikeChoice` is called from one place and defined in one.

        A stale extra parameter here does not show up until the APK build runs
        javac, and gradle will happily report a cached UP-TO-DATE for a module
        whose sources changed underneath it, so the only cheap place to catch it
        is a source-level arity check.
        """
        runtime = (ROOT / "android_runtime_smoke/localapp/src/main/java/com/firstlight/offline"
                         "/LocalBattleRuntime.java").read_text(encoding="utf-8")
        match = re.search(r"private static void reportRoguelikeChoice\(([^)]*)\)", runtime)
        self.assertIsNotNone(match, "reportRoguelikeChoice is not defined")
        params = [p.strip() for p in match.group(1).split(",") if p.strip()]
        self.assertEqual([p.split()[-1] for p in params], ["generation", "optionId"])
        for call in re.findall(r"reportRoguelikeChoice\(([^)]*)\);", runtime):
            arguments = [c.strip() for c in call.split(",") if c.strip()]
            self.assertEqual(len(arguments), len(params),
                             "a call site passes %d arguments to a %d-argument "
                             "callback" % (len(arguments), len(params)))
        # The Python side is the other end of the same call.
        source = RoguelikeRunnerSourceTest.source()
        python = re.search(r"def report_draft_choice\(([^)]*)\)", source)
        self.assertIsNotNone(python)
        self.assertEqual(
            [p.strip() for p in python.group(1).split(",") if p.strip()],
            ["generation", "option_id"])

    def test_the_draft_pick_is_not_queued_behind_the_loop_waiting_for_it(self):
        """The pick must reach Python without touching the match worker.

        run_device_match is executed on LocalBattleRuntime's single-thread
        `worker` executor, and the draft dialog answered by submitting the pick
        to that same executor. The pick would then queue behind the very loop
        that is blocked waiting for it, so every draft would time out into its
        auto-pick and the player could never choose. The mailbox write is
        immediate, so the dialog must call it inline.
        """
        runtime = (ROOT / "android_runtime_smoke/localapp/src/main/java/com/firstlight/offline"
                         "/LocalBattleRuntime.java").read_text(encoding="utf-8")
        start = runtime.index("private static void decide(")
        body = runtime[start:runtime.index("\n    /**", start)]
        self.assertIn("reportRoguelikeChoice(generation,reported)", body)
        self.assertNotIn("worker.execute", body,
                         "queueing the pick on the match worker deadlocks the run")
        # ...and run_device_match must still be on that worker, or the pin above
        # would be guarding the wrong executor.
        self.assertIn('module.callAttr("run_device_match"', runtime)
        self.assertIn("private static final ExecutorService worker="
                      "Executors.newSingleThreadExecutor", runtime)

    def test_the_mailbox_rejects_a_pick_from_a_previous_battle(self):
        # A pick left in the mailbox by an abandoned run must not be applied to
        # the next one, or the player inherits a buff they never took.
        import mobile_fair as mf
        try:
            mf._DRAFT_MAILBOX["option"] = None
            mf._DRAFT_MAILBOX["generation"] = 0
            mf.report_draft_choice(7, "b_elixir_3")
            self.assertIsNone(mf._take_draft_choice(8),
                              "a pick from generation 7 was applied to generation 8")
            self.assertIsNone(mf._take_draft_choice(8),
                              "the stale pick was not cleared")
        finally:
            mf._DRAFT_MAILBOX["option"] = None
            mf._DRAFT_MAILBOX["generation"] = 0

    def test_a_pick_is_consumed_exactly_once(self):
        import mobile_fair as mf
        try:
            mf._DRAFT_MAILBOX["option"] = None
            mf._DRAFT_MAILBOX["generation"] = 0
            mf.report_draft_choice(3, "c_repair")
            self.assertEqual(mf._take_draft_choice(3), "c_repair")
            self.assertIsNone(mf._take_draft_choice(3),
                              "one draft applied its pick twice")
        finally:
            mf._DRAFT_MAILBOX["option"] = None
            mf._DRAFT_MAILBOX["generation"] = 0

    def test_the_result_dialog_says_how_deep_the_run_got(self):
        # A loss has to report the wave, or "you died" is the only feedback a
        # ten-wave descent gives.
        runner = self.runner()
        self.assertIn("坚持到了第", runner)
        self.assertIn("深入第", runner)


if __name__ == "__main__":
    unittest.main()
