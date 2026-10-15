"""Java GameModePicker ids must match the Python GAME_MODES catalog exactly.
A mismatch silently falls back to ladder pacing, which is the bug this guards."""
import re, sys
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "build/mobile-semantics"))
sys.path.insert(0, str(ROOT / "on_device/python"))
import mobile_fair

JAVA = ROOT / "on_device/java/com/firstlight/offline/GameModePicker.java"
JAVA_TEXT = JAVA.read_text(encoding="utf-8")
ROUTER_TEXT = (ROOT / "on_device/java/com/firstlight/offline/BattlePickerRouter.java").read_text(encoding="utf-8")
RUNTIME_TEXT = (ROOT / "android_runtime_smoke/localapp/src/main/java/com/firstlight/offline"
                       "/LocalBattleRuntime.java").read_text(encoding="utf-8")
# list.add(new Mode("id", gameModeId, ...))
JAVA_MODES = dict(
    (m.group(1), int(m.group(2)))
    for m in re.finditer(r'new Mode\("([^"]+)",\s*(\d+),', JAVA_TEXT)
)


class ModeCatalogParityTest(unittest.TestCase):
    def test_ui_only_exposes_modes_the_python_catalog_supports(self):
        # The Java picker is the player-visible surface. Every mode it offers
        # must exist in GAME_MODES, otherwise create_match silently falls back
        # to ladder pacing.
        self.assertTrue(JAVA_MODES, "no modes parsed from GameModePicker.java")
        self.assertLessEqual(
            set(JAVA_MODES), set(mobile_fair.GAME_MODES),
            "the UI offers modes the Python catalog does not know",
        )

    def test_every_exposed_mode_has_a_loadable_timeline(self):
        from native_runner.timeline import load_game_mode_timeline
        for key, mode_id in JAVA_MODES.items():
            with self.subTest(mode=key):
                self.assertEqual(load_game_mode_timeline(mode_id).game_mode_id, mode_id)

    def test_exposed_modes_all_use_the_verified_1v1_arena(self):
        # 2v2 runs the stock 2v2 arena (location 15000020) and is scored by the
        # native crown tally, which is team aware. Every other mode is a 1v1
        # pace variant on the standard arena.
        from mobile_fair import GAME_MODE_TEAM_VS_TEAM
        self.assertIn(
            GAME_MODE_TEAM_VS_TEAM, JAVA_MODES.values(),
            "the 2v2 co-op entry must be reachable from the picker",
        )
        for key, mode_id in JAVA_MODES.items():
            if mode_id == GAME_MODE_TEAM_VS_TEAM:
                continue
            with self.subTest(mode=key):
                self.assertNotEqual(mode_id, GAME_MODE_TEAM_VS_TEAM)
        self.assertGreaterEqual(len(JAVA_MODES), 8)

    def test_every_supported_1v1_mode_is_reachable_from_the_ui(self):
        """A mode the player cannot pick is a mode that does not exist.

        This compares by KEY, not by game-mode id: all_random and all_evolution
        deliberately share one native game mode, so an id-based comparison
        cannot tell them apart and silently passes on a real mismatch.

        `2v2` is the one team mode the picker exposes; `2v2_random` stays out
        because create_match's random branch does not build the teammate seat
        the co-op match needs.
        """
        from mobile_fair import GAME_MODE_TEAM_VS_TEAM
        supported = {
            key
            for key, mode in mobile_fair.GAME_MODES.items()
            if key != "2v2_random"
        }
        self.assertEqual(
            set(JAVA_MODES), supported,
            "the UI and the Python catalog disagree; a mode on one side only "
            "cannot be selected or cannot be created",
        )

    def test_modes_sharing_a_native_id_are_still_distinct_entries(self):
        # all_random and all_evolution both use 72000491. They must stay two
        # separate keys so create_match can branch on the key.
        shared = {}
        for key, mode_id in JAVA_MODES.items():
            shared.setdefault(mode_id, []).append(key)
        for mode_id, keys in shared.items():
            with self.subTest(game_mode=mode_id):
                self.assertEqual(
                    len(keys), len(set(keys)),
                    "two UI entries collapsed onto one key",
                )

    def test_the_picker_is_actually_reachable_from_the_lobby(self):
        """Listing a mode is not offering it.

        GameModePicker shipped as a complete, correct, fully populated class that
        nothing ever called, so every mode in it -- all three PvE schedules
        included -- was unreachable while this file's other tests stayed green.
        A catalogue test cannot catch that on its own, so pin the call site: the
        lobby opens a chooser from BattlePickerRouter.showOnUiThread, and the
        mode list has to sit in front of it.
        """
        router = ROOT / "on_device/java/com/firstlight/offline/BattlePickerRouter.java"
        text = ROUTER_TEXT
        self.assertIn(
            "GameModePicker.show(",
            text,
            "GameModePicker is never shown, so every mode it lists is dead code",
        )
        # It must wrap the opponent list, not replace it -- the AI choice is
        # still needed to start a battle.
        picker_at = text.index("GameModePicker.show(")
        opponent_at = text.index("AiOpponentPicker.showForBattleWithDuel(")
        self.assertLess(picker_at, opponent_at,
                        "the mode list must come before the opponent list")

    def test_the_mode_list_scrolls_and_stays_clickable(self):
        """setView() silently replaces setAdapter()'s click handling.

        The picker moved from setAdapter to a ListView so the nineteen rows
        scroll, but that swap also drops the built-in OnItemClickListener -- a
        list with no listener still renders and scrolls and simply does
        nothing when tapped. Pin both halves: it must be a scrolling ListView,
        and it must wire its own click handler.
        """
        self.assertIn("new ListView(activity)", JAVA_TEXT)
        self.assertIn("setOnItemClickListener(", JAVA_TEXT,
                      "the scrolling list has no click handler, so taps do nothing")
        self.assertIn("setVerticalScrollBarEnabled(true)", JAVA_TEXT)
        # The mode has to be remembered on click, or the choice never persists.
        self.assertIn("rememberSelectedMode(activity, chosen.id)", JAVA_TEXT)

    def test_the_current_mode_is_pre_selected_in_the_scrollable_list(self):
        # With nineteen rows the list opens scrolled to the top by default, so
        # the selected mode would be off-screen for anyone not on the first page.
        self.assertIn("list.setSelection(selectedIndex)", JAVA_TEXT)

    def test_a_scripted_mode_skips_the_opponent_picker(self):
        """A wave-driven mode has no opponent, so it must not ask for one.

        PvE deploys its waves from the schedule; the opponent list is a question
        with no answer there. The mode still needs a policy for the player's own
        side, so it reuses the already-selected model instead of prompting.
        """
        self.assertIn("public final boolean scripted", JAVA_TEXT)
        self.assertIn("GameModePicker.isScripted(", ROUTER_TEXT)
        # The scripted branch has to reach preload() on its own, without ever
        # entering the opponent chooser.
        branch = ROUTER_TEXT.split("if (GameModePicker.isScripted(", 1)[1]
        branch = branch.split("AiOpponentPicker.showForBattleWithDuel(", 1)[0]
        self.assertIn("preload(", branch,
                      "a scripted mode must start its match without the opponent list")
        self.assertIn("selectedModelId(", branch)

    def test_the_three_roguelike_dungeons_are_offered(self):
        # The three roguelike climbs are three separate player-facing entries,
        # so each one has to survive all the way to the picker.
        for key in mobile_fair.ROGUELIKE_MODES:
            with self.subTest(mode=key):
                self.assertIn(key, JAVA_MODES)

    def test_the_three_pve_modes_are_flagged_scripted(self):
        for key in mobile_fair.ROGUELIKE_MODES:
            with self.subTest(mode=key):
                entry = re.search(
                    r'new Mode\("%s",[^;]*?\);' % re.escape(key), JAVA_TEXT, re.S)
                self.assertIsNotNone(entry, "no picker entry for %s" % key)
                self.assertIn("true", entry.group(0),
                              "%s is a wave-driven mode and must be flagged "
                              "scripted so the router skips the opponent list" % key)

    def test_the_roguelike_dungeons_replace_the_retired_pve_schedules(self):
        """The old endless/stage/boss schedules are gone, not merely renamed.

        A retired key that survives in the catalog would still be creatable by
        create_match, so it would stay reachable as a mode with no picker entry
        behind it -- the exact "registered but unreachable" defect this file
        exists to catch.
        """
        for retired in ("pve", "pve_stage", "pve_boss"):
            with self.subTest(mode=retired):
                self.assertNotIn(retired, mobile_fair.GAME_MODES)
                self.assertNotIn(retired, JAVA_MODES)

    def test_the_ally_toggle_is_reachable_from_the_picker_route(self):
        """The 2v2-vs-solo choice has to be asked somewhere the player sees.

        The run's arena is chosen by a deck flag set in prepareMatch, so a
        checkbox nothing calls would leave every climb silently solo.
        """
        self.assertIn("GameModePicker.askAiAlly(", ROUTER_TEXT)
        self.assertIn("aiAllyEnabled(", JAVA_TEXT)
        self.assertIn("withAiAlly(deck,context)", RUNTIME_TEXT)
        # ...and only for a climb, so no other mode grows a meaningless question.
        branch = ROUTER_TEXT.split("if (GameModePicker.isScripted(", 1)[1]
        self.assertLess(branch.index("GameModePicker.askAiAlly("),
                        branch.index("AiOpponentPicker.showForBattleWithDuel("))


if __name__ == "__main__":
    unittest.main()
