"""A device that cannot build the offline Home must not sit on the stock 96%
loading screen forever, and must not fail silently.

The native scene builder now publishes a reason code and a retry count, skips a
card it cannot create, and after several attempts retries with a core-only
collection so the player still reaches the lobby. The Java layer turns the
permanent reasons into a dialog.
"""
import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
PROBE = ROOT / "baseline/native_runner/probe"
HOME = (PROBE / "cold_home_scene.inc").read_text(encoding="utf-8")
BRIDGE = (PROBE / "local_runtime_bridge.inc").read_text(encoding="utf-8")
JNI = (PROBE / "local_app_jni.inc").read_text(encoding="utf-8")
RUNTIME = (ROOT / "android_runtime_smoke/localapp/src/main/java/com/firstlight/offline"
                 / "LocalBattleRuntime.java").read_text(encoding="utf-8")


class HomeBootstrapDiagnosticsTest(unittest.TestCase):
    def test_every_abort_path_publishes_a_reason(self):
        # Each `return false` inside the scene builder must be preceded by a
        # status publish, either on the same statement or just before it.
        body = HOME[HOME.index("bool start_cold_home_scene"):]
        for match in re.finditer(r"return false;", body):
            window = body[max(0, match.start() - 400):match.start()]
            with self.subTest(context=" ".join(window[-90:].split())):
                self.assertIn(
                    "publish_cold_home_status(",
                    window,
                    "an abort path returns without publishing a reason, so a "
                    "failing device would still sit on 96% with no explanation",
                )

    def test_unreadable_objects_are_logged(self):
        body = HOME[HOME.index("bool start_cold_home_scene"):]
        self.assertIn("cold-home aborted: messaging object unreadable", body)
        self.assertIn("cold-home aborted: profile object unreadable", body)

    def test_status_is_published_only_on_change(self):
        # Called per frame; must not spam Java every frame.
        self.assertIn("g_cold_home_status.exchange(status", HOME)
        self.assertIn("if (previous == status) return;", HOME)

    def test_success_is_published(self):
        body = HOME[HOME.index("bool start_cold_home_scene"):]
        self.assertIn("publish_cold_home_status(kColdHomeOk)", body)

    def test_jni_callback_is_registered(self):
        self.assertIn("g_local_home_status", BRIDGE)
        self.assertIn('GetStaticMethodID(type,"onHomeBootStatus","(II)V")', JNI)

    def test_card_failure_no_longer_aborts_the_whole_scene(self):
        body = HOME[HOME.index("bool start_cold_home_scene"):]
        loop = body[body.index("for (const auto& entry : kLocalCards)"):]
        loop = loop[:loop.index("LOGI(\"cold-home collection cards=")]
        self.assertIn("if (!degraded) return false;", loop)
        self.assertIn("continue;", loop,
                      "a degraded retry must skip the bad card, not give up")


class HomeBootstrapDegradeTest(unittest.TestCase):
    def test_degrade_triggers_after_enough_attempts(self):
        self.assertIn("constexpr std::int32_t kColdHomeDegradeAfterAttempts", HOME)
        self.assertRegex(HOME, r"const bool degraded = attempt > kColdHomeDegradeAfterAttempts;")

    def test_degraded_build_uses_a_smaller_card_limit(self):
        body = HOME[HOME.index("const std::int32_t attempt ="):]
        body = body[:body.index("std::int32_t skipped = 0;")]
        self.assertIn("kColdHomeCoreCardCount", body)
        self.assertIn("kLocalCardTotal", body)

    def test_card_total_uses_array_extent_not_size(self):
        # kLocalCards is a C array; .size() does not compile.
        self.assertIn("sizeof(kLocalCards) / sizeof(kLocalCards[0])", HOME)
        self.assertNotIn("kLocalCards.size()", HOME)

    def test_degraded_success_is_reported(self):
        self.assertIn("publish_cold_home_status(kColdHomeDegraded)", HOME)

    def test_degraded_keeps_a_usable_collection(self):
        # A degraded run must still produce cards, or the lobby is unusable.
        body = HOME[HOME.index("if (skipped > 0 && built > 0)"):]
        self.assertIn("kColdHomeDegraded", body)
        self.assertNotIn("return false", body[:body.index("LOGI(\"cold-home collection cards=")])


class HomeProblemDialogTest(unittest.TestCase):
    def test_native_callback_exists_in_java(self):
        self.assertIn("public static void onHomeBootStatus(int status,int attempts)", RUNTIME)

    def test_transient_states_do_not_interrupt_the_player(self):
        body = RUNTIME[RUNTIME.index("public static void onHomeBootStatus"):]
        body = body[:body.index("private static void showHomeBootProblem")]
        self.assertIn("if(status==0 || status==4) return;", body)
        self.assertRegex(body, r"if\(attempts<3\) return;")

    def test_permanent_failures_show_a_dialog(self):
        self.assertIn("showHomeBootProblem", RUNTIME)
        self.assertIn("离线大厅启动失败", RUNTIME)
        self.assertIn("logcat", RUNTIME)

    def test_dialog_offers_a_way_out(self):
        body = RUNTIME[RUNTIME.index("private static void showHomeBootProblem"):]
        self.assertIn("setPositiveButton(\"重试\"", body)
        self.assertIn("setNegativeButton(\"退出\"", body)

    def test_dialog_is_shown_once(self):
        body = RUNTIME[RUNTIME.index("private static void showHomeBootProblem"):]
        self.assertIn("homeProblemDialog!=null && homeProblemDialog.isShowing()", body)
        self.assertIn("homeProblemDialog=null", body)

    def test_ui_thread_is_respected(self):
        self.assertIn("current.runOnUiThread(() -> showHomeBootProblem", RUNTIME)

    def test_reason_codes_match_the_native_enum(self):
        native_codes = set(re.findall(r"kColdHome(\w+)\s*=", HOME))
        self.assertTrue({"Ok", "MessagingUnreadable", "ProfileUnreadable",
                          "CardCreationFailed", "Degraded"} <= native_codes)
        for code in ("case 1:", "case 2:", "case 3:"):
            self.assertIn(code, RUNTIME, f"dialog does not explain status {code}")


if __name__ == "__main__":
    unittest.main()
