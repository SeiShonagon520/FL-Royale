"""Contract guard for the AI duel handoff.

The failure that made the duel unplayable was a native-contract violation, not
a logic bug: `battle_picker_bridge.cpp` drops a ready request unless the aiId is
one of the five catalog models AND the generation is strictly positive. Passing
anything else left `g_active_request_id` set for the whole process, so the
native Battle button stayed dead even after restarting the game.

These assertions are static on purpose: the native side is not executed here, so
the contract is verified by reading both ends of it.
"""
import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
JAVA = ROOT / "on_device/java/com/firstlight/offline"
BRIDGE = ROOT / "baseline/native_runner/probe/battle_picker_bridge.cpp"
SCENE = ROOT / "baseline/native_runner/probe/local_app_scene.inc"
ROUTER = (JAVA / "BattlePickerRouter.java").read_text(encoding="utf-8")
RUNTIME = (ROOT / "android_runtime_smoke/localapp/src/main/java/com/firstlight/offline"
                 / "LocalBattleRuntime.java").read_text(encoding="utf-8")
SETUP = (JAVA / "AiDuelSetup.java").read_text(encoding="utf-8")
PICKER = (JAVA / "AiOpponentPicker.java").read_text(encoding="utf-8")
REGISTRY = (JAVA / "DuelPolicyRegistry.java").read_text(encoding="utf-8")
BRIDGE_SRC = BRIDGE.read_text(encoding="utf-8")


def _native_whitelist() -> set:
    body = BRIDGE_SRC[BRIDGE_SRC.index("bool ValidAiId"):]
    body = body[:body.index("\n}")]
    return set(re.findall(r'strcmp\(id,\s*"([^"]+)"\)\s*==\s*0', body))


class NativeHandoffContractTest(unittest.TestCase):
    def setUp(self):
        self.ready_calls = re.findall(r"nativeOnBattleReady\(([^;]*)\);", ROUTER)

    def test_native_still_only_accepts_the_five_models(self):
        # The whitelist must NOT have been widened to admit a synthetic id.
        self.assertEqual(
            _native_whitelist(),
            {"general", "il", "active_il", "hog26_proactive", "hog26_measured"},
            "the native ValidAiId whitelist must stay exactly the five models",
        )

    def test_native_requires_a_positive_generation(self):
        self.assertIn("model_generation <= 0", BRIDGE_SRC,
                      "the generation guard is what this suite defends")

    def test_duel_handoff_never_sends_a_synthetic_ai_id(self):
        for call in self.ready_calls:
            self.assertNotIn('"ai_duel"', call,
                             "a literal ai_duel is rejected by the native whitelist")
            self.assertNotIn('""', call)

    def test_duel_handoff_generation_is_positive_and_derived(self):
        # The call site uses a local; the value must come from the registry and
        # must never be a hard-coded zero.
        body = ROUTER[ROUTER.index("private static void finishDuelReady"):]
        body = body[:body.index("\n    /**")]
        self.assertIn("DuelPolicyRegistry.nextGeneration()", body)
        self.assertIn("nativeOnBattleReady(requestId, seat0, generation)", body)
        self.assertNotRegex(body, r"nativeOnBattleReady\([^)]*,\s*0L?\s*\)",
                            "a hard-coded zero generation is rejected by native")

    def test_duel_handoff_uses_a_real_model_id(self):
        self.assertRegex(ROUTER, r"finishDuelReady\(Activity activity, long requestId, String seat0\)")
        self.assertIn("nativeOnBattleReady(requestId, seat0, generation)", ROUTER)

    def test_generation_is_validated_before_handoff(self):
        self.assertRegex(ROUTER, r"generation <= 0\)")
        self.assertIn("Duel handoff token is invalid", ROUTER)

    def test_registry_generation_starts_positive_and_increases(self):
        self.assertIn("private static long generation = 1L;", REGISTRY)
        self.assertRegex(REGISTRY, r"return \+\+generation;")


class DuelEntryPointTest(unittest.TestCase):
    def test_duel_is_an_opponent_choice_not_a_game_mode(self):
        # A stale mode selection must never be able to route a normal battle
        # into the duel pipeline.
        self.assertNotIn('"ai_duel".equals(mode.id)', RUNTIME)
        self.assertNotIn('new Mode("ai_duel"', (JAVA / "GameModePicker.java").read_text(encoding="utf-8"))
        self.assertIn("showForBattleWithDuel", ROUTER)
        self.assertIn("AI 自战", PICKER)

    def test_duel_intent_is_flagged_before_the_native_handoff(self):
        self.assertIn("public static void beginDuel()", RUNTIME)
        self.assertIn("if(duelRequested)", RUNTIME)
        self.assertIn("LocalBattleRuntime.beginDuel()", ROUTER)

    def test_duel_fixture_uses_the_ladder_game_mode(self):
        self.assertIn('module.callAttr("create_match",deck,aiIds[1],opponent,"ladder","")', RUNTIME)


class DuelNavigationTest(unittest.TestCase):
    def test_back_button_has_a_real_callback(self):
        # An empty negative button left pendingRequest set forever.
        self.assertIn('.setNegativeButton("返回", (d, which) -> { })', SETUP)

    def test_every_dismiss_path_returns_to_the_opponent_list(self):
        self.assertIn("setOnCancelListener", SETUP)
        self.assertIn("setOnDismissListener", SETUP)
        self.assertIn("onBack", SETUP)
        # The picker reopens itself as the back action.
        self.assertRegex(
            PICKER,
            r"showForBattleWithDuel\(activity, catalog, availableIds,\s*"
            r"listener, onDuelStart, onCancelled\)",
        )

    def test_duel_failure_falls_back_to_a_normal_battle(self):
        self.assertIn("showDuelFailureAndFallback", ROUTER)
        body = ROUTER[ROUTER.index("private static void showDuelFailureAndFallback"):]
        body = body[:body.index("\n    /** Ask native for a fresh battle request")]
        self.assertIn("cancel(requestId)", body)
        self.assertIn("requestFromNative", body)

    def test_all_duel_failure_branches_cancel_the_native_request(self):
        body = ROUTER[ROUTER.index("private static void prepareDuel"):]
        body = body[:body.index("private static void finishDuelReady")]
        for branch in re.findall(r"catch \([^)]*\) \{(.*?)\n\s*\}", body, re.S):
            with self.subTest(branch=branch.strip()[:60]):
                self.assertTrue(
                    "showDuelFailureAndFallback" in branch or "cancel(" in branch,
                    "a duel failure branch must release the native request",
                )

    def test_cancelling_the_opponent_list_releases_the_request(self):
        self.assertIn("() -> cancel(requestId)", ROUTER)

    def test_single_screen_setup_uses_spinners_not_nested_pickers(self):
        self.assertIn("Spinner", SETUP)
        self.assertNotIn("pickSeat", SETUP)
        # Only one dialog is built: the spinners are inline.
        self.assertEqual(SETUP.count("setPositiveButton"), 1)


class DuelSeatSelectionTest(unittest.TestCase):
    def test_both_seats_are_independent(self):
        body = SETUP[SETUP.index("public static String[] selectedAiIds"):]
        body = body[:body.index("public static String labelFor")]
        self.assertIn("SEAT0_MODEL", body)
        self.assertIn("SEAT1_MODEL", body)
        self.assertIn('new String[] {', body)
        # Two independent lookups, one per seat.
        self.assertEqual(body.count("catalog.defaultId"), 2)

    def test_self_play_is_allowed(self):
        self.assertNotIn("seat0.equals(seat1)", SETUP)
        self.assertNotIn("seats[0].equals(seats[1])", SETUP)

    def test_uninstalled_models_cannot_be_started(self):
        self.assertIn("请选择已安装的模型", SETUP)


class NativeUnchangedTest(unittest.TestCase):
    def test_the_duel_work_touched_no_native_source(self):
        # The whole redesign is Java/Python only; if this fails, the native
        # contract analysis above has to be redone.
        self.assertIn("model_generation <= 0", BRIDGE_SRC)
        self.assertEqual(_native_whitelist(),
                         {"general", "il", "active_il", "hog26_proactive", "hog26_measured"})

    def test_native_only_needs_request_id_from_the_handoff(self):
        # local_app_scene consumes request_id and calls prepareMatch; the
        # generation is log-only, which is why an arbitrary positive value is
        # safe to send.
        scene = SCENE.read_text(encoding="utf-8")
        self.assertIn("g_local_expected_request=ready.request_id", scene)
        self.assertIn("g_local_prepare_match,static_cast<jlong>(ready.request_id)", scene)


if __name__ == "__main__":
    unittest.main()
