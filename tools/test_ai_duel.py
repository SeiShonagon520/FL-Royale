"""AI-vs-AI duel: both seats driven by a policy on one shared environment.

The reference implementation (SeiShonagon520/FirstLight_CR, offline_duel.py)
runs one process with two PolicySessionV4 instances bound to actor_owner 0 and
1. This suite guards the three structural requirements that make that safe here:

  1. The duel reuses the ladder game mode, so no new pacing gate is needed.
  2. RecurrentPolicySession is single-owner, so each seat needs its own session.
  3. GameOrtBootstrap holds one model, so a duel needs its own registry.
"""
import inspect
import json
import pathlib
import re
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
# on_device/python must precede build/mobile-semantics: the staged tree holds a
# copy of mobile_fair.py that would otherwise shadow the source.
sys.path.insert(0, str(ROOT / "build/mobile-semantics"))
sys.path.insert(0, str(ROOT / "on_device/python"))

import mobile_fair
from mobile_fair import GAME_MODE_AI_DUEL, GAME_MODE_LADDER, RANDOM_DECK_TOWER_TROOPS
from native_runner.battle_env import TOWER_LAYOUT
from native_runner.contracts import ROYAL_CHEF_TOWER_TROOP_ID
from native_runner.timeline import load_game_mode_timeline
from native_runner.training.v4.factory import production_semantic_bundle

DECK = [26000000, 26000001, 26000005, 28000001, 28000000, 26000003, 26000014, 26000018]
HUMAN = {"cards": DECK, "forms": [0] * 8}

JAVA = ROOT / "on_device/java/com/firstlight/offline"
RUNTIME = ROOT / "android_runtime_smoke/localapp/src/main/java/com/firstlight/offline/LocalBattleRuntime.java"
ROUTER = JAVA / "BattlePickerRouter.java"
REGISTRY = JAVA / "DuelPolicyRegistry.java"
SESSION = JAVA / "RecurrentPolicySession.java"


class DuelModeTest(unittest.TestCase):
    def test_duel_is_not_a_game_mode(self):
        # A duel is an opponent choice, not a game mode. Registering it as one
        # left a stale mode selection that could not start a normal battle.
        self.assertNotIn("ai_duel", mobile_fair.GAME_MODES)
        self.assertEqual(GAME_MODE_AI_DUEL, GAME_MODE_LADDER,
                         "a duel runs the verified ladder game mode unchanged")

    def test_duel_pacing_is_identical_to_ladder(self):
        # Reusing the ladder timeline is what keeps every policy input inside
        # the trained domain; a new timeline would need its own verification.
        duel = load_game_mode_timeline(GAME_MODE_AI_DUEL).timeline
        ladder = load_game_mode_timeline(GAME_MODE_LADDER).timeline
        self.assertEqual(duel.name, ladder.name)
        self.assertEqual(duel.starting_elixir, ladder.starting_elixir)
        for tick in range(0, 6001, 50):
            self.assertEqual(duel.phase(tick), ladder.phase(tick))

    def test_a_ladder_fixture_is_what_the_duel_uses(self):
        # prepareDuelMatch passes game_mode="ladder"; this is the fixture the
        # two seats actually play.
        payload = json.loads(
            mobile_fair.create_match(json.dumps(HUMAN), "general", json.dumps(HUMAN),
                                     game_mode="ladder")
        )
        battle = payload["battle"]
        self.assertEqual(battle["gamemode"], GAME_MODE_LADDER)
        self.assertEqual(battle["plt"], 1, "a duel is still a one-v-one scene")
        self.assertEqual(battle["arena"], 54000001)
        self.assertEqual(battle["location"], 15000199)
        self.assertEqual([c["d"] for c in battle["deck0"]["sp"]], DECK,
                         "seat 0 keeps the player's own deck")

    def test_both_seat_decks_satisfy_the_semantic_subset(self):
        sub = production_semantic_bundle().semantic_subset_contract
        for _ in range(200):
            payload = json.loads(
                mobile_fair.create_match(json.dumps(HUMAN), "general", json.dumps(HUMAN),
                                         game_mode="ladder")
            )
            for owner in (0, 1):
                sp = payload["battle"][f"deck{owner}"]["sp"]
                sub.assert_deck([row["d"] for row in sp],
                                form_availability=[int(row.get("el", 0)) for row in sp],
                                label=f"deck{owner}")

    def test_tower_geometry_stays_six_cells(self):
        self.assertEqual(len(TOWER_LAYOUT), 6)
        self.assertNotIn(ROYAL_CHEF_TOWER_TROOP_ID, RANDOM_DECK_TOWER_TROOPS)


class _RecordingRuntime:
    """Stands in for the JNI runtime: records the queued native command."""

    def __init__(self):
        self.calls = []

    def nextFrame(self, generation, tick):
        return None

    def queueActionForOwner(self, generation, observed, due, slot, card, x, y,
                            is_ability, object_index, secondary_index, owner):
        self.calls.append(dict(generation=generation, observed=observed, due=due,
                               slot=slot, card=card, x=x, y=y, owner=owner))
        return '{"ok":true}'


class DeviceNativeHandQueueTest(unittest.TestCase):
    """battle_env hands DeviceNative a HandAction dataclass, not a dict.

    Treating it as a mapping raised "TypeError: 'HandAction' object is not
    subscriptable" and killed every match at the first card play, so guard the
    dataclass shape directly rather than only inspecting the source text.
    """

    CAPTURE = {
        "tick": 200,
        "ordinary": {
            "tick": 200,
            "players": [
                {"owner": 0, "hand": [{"handIndex": 2, "cardId": 26000000},
                                       {"handIndex": 3, "cardId": 26000001}]},
                # Owner 1's hand is compacted: it is the first entry in the
                # array but its true native slot is 2, not its position 0.
                {"owner": 1, "hand": [{"handIndex": 2, "cardId": 28000001},
                                      {"handIndex": 0, "cardId": 28000000}]},
            ],
        },
    }

    def _native(self):
        return mobile_fair.DeviceNative.__new__(mobile_fair.DeviceNative)

    def test_hand_action_dataclass_is_queued_with_the_native_slot(self):
        from native_runner.cr_native_env import HandAction

        native = self._native()
        native.generation, native.runtime = 3, _RecordingRuntime()
        native.capture = json.loads(json.dumps(self.CAPTURE))
        # hand_index is the true native slot (battle_env matches it against the
        # capture's "handIndex"), and the capture compacts the hand array, so
        # the entry sits at position 0 while its native slot is 2.
        action = HandAction(1, 2, 9000, 22000)
        receipt = native.queue_hand_action_at_for_owner(1, action, execute_in_ticks=2)
        self.assertTrue(receipt["ok"])
        call = native.runtime.calls[0]
        self.assertEqual(call["slot"], 2, "must queue the native handIndex, not the position")
        # Owner 1's hand slot 2 is cardId 28000001 in this capture. 26000000 is
        # owner 0's slot 2, so asserting it here would pin the wrong seat's card.
        self.assertEqual(call["card"], 28000001, "the card id comes from the capture")
        self.assertEqual((call["x"], call["y"]), (9000, 22000))
        self.assertEqual(call["owner"], 1)
        self.assertEqual(call["due"], 202, "due = observed + execute_in_ticks")

    def test_coordinator_dict_actions_still_queue(self):
        # The 2v2 coordinator emits dicts; the dataclass fix must not break it.
        native = self._native()
        native.generation, native.runtime = 3, _RecordingRuntime()
        native.capture = json.loads(json.dumps(self.CAPTURE))
        native.queue_hand_action_at_for_owner(
            0, {"hand_index": 3, "x": 3000, "y": 18000}, execute_in_ticks=2)
        call = native.runtime.calls[0]
        self.assertEqual((call["slot"], call["card"]), (3, 26000001))
        self.assertEqual(call["owner"], 0)

    def test_battle_env_reaches_the_queue_with_a_dataclass(self):
        # Guards the contract itself: the caller really does pass a HandAction.
        from native_runner.cr_native_env import HandAction

        source = inspect.getsource(mobile_fair.DeviceNative)
        self.assertIn("queue_hand_action_at_for_owner", source)
        # Every attribute the queue reads must exist on the dataclass it receives.
        for name in ("owner", "hand_index", "x", "y"):
            self.assertTrue(hasattr(HandAction(0, 0, 0, 0), name), name)


class DuelPipelineTest(unittest.TestCase):
    def test_duel_entry_point_exists_and_drives_both_owners(self):
        self.assertTrue(hasattr(mobile_fair, "run_duel_device_match"))
        source = inspect.getsource(mobile_fair.run_duel_device_match)
        self.assertIn("actor=owner", source, "each seat needs its own actor")
        self.assertIn("observation_owners=(0,1)", source,
                      "both owners must receive observations from one step")
        self.assertIn("inferForOwner", source)
        self.assertIn("run_duel_device_match", inspect.getsource(mobile_fair.run_device_match))

    def test_mobile_fair_accepts_a_shared_environment(self):
        # The second seat must not reset the shared BattleEnvV1 again.
        signature = inspect.signature(mobile_fair.MobileFair.__init__)
        self.assertIn("environment", signature.parameters)
        self.assertIn("observations", signature.parameters)
        self.assertIn("actor", signature.parameters)

    def test_hand_and_ability_queues_accept_owner_zero(self):
        # The single-owner path used to raise for owner != 1, and it read the
        # compacted hand index instead of the native slot.
        source = inspect.getsource(mobile_fair.DeviceNative)
        self.assertNotIn("AI may only control owner 1", source)
        # The capture is searched by the card's own "handIndex", never by the
        # card's position in the compacted hand array.
        self.assertIn('["handIndex"]', source)
        self.assertNotIn('["hand_index"]', source,
                         "hand_index is the HandAction field, not a capture key")
        self.assertIn("if action.owner not in (0,1)", source)

    def test_recurrent_session_is_single_owner(self):
        # Documents why the runtime needs one session per seat.
        text = SESSION.read_text(encoding="utf-8")
        self.assertIn("expectedTick", text)
        self.assertRegex(text, r"observedTick != expectedTick")


class DuelJavaTest(unittest.TestCase):
    def test_registry_keeps_one_policy_per_model(self):
        text = REGISTRY.read_text(encoding="utf-8")
        self.assertIn("Map<String, OnDeviceOrtPolicy>", text)
        self.assertIn("preload", text)
        self.assertIn("closeAll", text)
        # Same model on both seats must reuse one instance (true self-play).
        self.assertRegex(text, r"existing\s*!=\s*null.*?modelSha256\.equals")

    def test_registry_does_not_disturb_the_human_vs_ai_bootstrap(self):
        registry = REGISTRY.read_text(encoding="utf-8")
        self.assertNotIn("GameOrtBootstrap.", registry,
                         "the duel registry must not mutate the single-model slot")

    def test_runtime_uses_one_session_per_seat(self):
        text = RUNTIME.read_text(encoding="utf-8")
        self.assertIn("RecurrentPolicySession[] duelSessions", text)
        self.assertIn("inferForOwner", text)
        self.assertRegex(text, r"sessions\[owner\]\.submitStep")
        self.assertIn("runDuel", text)
        self.assertIn("DuelPolicyRegistry.prepare", text)

    def test_router_routes_duel_without_the_single_model_bootstrap(self):
        text = ROUTER.read_text(encoding="utf-8")
        self.assertIn("showForBattleWithDuel", text)
        self.assertIn("prepareDuel", text)
        duel_block = text[text.index("private static void prepareDuel"):]
        duel_block = duel_block[:duel_block.index("private static void finishDuelReady")]
        self.assertNotIn("GameOrtBootstrap.setModel", duel_block,
                         "a duel must not load through the single-model slot")
        self.assertIn("DuelPolicyRegistry.prepare", duel_block)

    def test_setup_registers_both_seats_and_allows_self_play(self):
        setup = (JAVA / "AiDuelSetup.java").read_text(encoding="utf-8")
        self.assertIn("SEAT0_MODEL", setup)
        self.assertIn("SEAT1_MODEL", setup)
        self.assertIn("selectedAiIds", setup)
        # Nothing may force the two seats to differ: self-play is a first-class
        # option and the registry shares one instance when the ids match.
        self.assertNotIn("seat0.equals(seat1)", setup)
        self.assertNotIn("seats[0].equals(seats[1])", setup)

    def test_picker_no_longer_exposes_the_duel_as_a_mode(self):
        picker = (JAVA / "GameModePicker.java").read_text(encoding="utf-8")
        self.assertNotRegex(picker, r'new Mode\("ai_duel"')
        # It lives in the opponent list instead.
        opponent = (JAVA / "AiOpponentPicker.java").read_text(encoding="utf-8")
        self.assertIn("AI 自战", opponent)


if __name__ == "__main__":
    unittest.main()
