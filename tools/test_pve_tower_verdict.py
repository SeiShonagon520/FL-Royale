"""The PvE defence verdict must be derived from the player's own king tower.

The engine ends every match the duel way: it counts the opposing side's towers
and names a winner once yours are gone. On a wave-driven defence that is exactly
inverted -- there is no enemy tower to destroy, and the run is lost when the
player's own king falls. These tests pin the replacement rule and the channel
that carries it to the result dialog, because the first device run of this
feature reported 胜利 for a match the player had lost.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "build/mobile-semantics"))
sys.path.insert(0, str(ROOT / "on_device/python"))

import pve_tower_verdict

RUNTIME_JAVA = ROOT / "android_runtime_smoke/localapp/src/main/java/com/firstlight/offline/LocalBattleRuntime.java"


def frame(tick, objects):
    return {"tick": tick, "objects": objects}


def king(owner, hp, card=pve_tower_verdict.KING_TOWER_CARD_ID):
    return {"owner": owner, "cardId": card, "hp": hp, "x": 9000, "y": 3000,
            "nativeObjectId": owner * 100}


def princess(owner, hp=2000):
    return {"owner": owner, "cardId": pve_tower_verdict.PRINCESS_TOWER_CARD_ID,
            "hp": hp, "x": 3500, "y": 6500, "nativeObjectId": owner * 100 + 1}


class TowerIdentityFieldTest(unittest.TestCase):
    """Towers must be found even though the capture carries no id for them.

    Device evidence, and the reason the first real PvE match ended at tick 30
    as a loss with all six towers standing on screen: "cardId" reads
    LogicGameObject+0xac, which the engine leaves at -1 for tower characters,
    and "dataGlobalId" is absent from the objects the offline capture emits
    altogether. Both were tried and both matched nothing.

    These tuples are the verbatim object list logged from a live match.
    """

    # Logged from a real PvE match at tick 30, as (owner, cardId, hp, x, y).
    REAL_TOWERS = [
        {"owner": 0, "cardId": -1, "hp": 4824, "x": 9000, "y": 3000},
        {"owner": 0, "cardId": -1, "hp": 3052, "x": 3500, "y": 6500},
        {"owner": 0, "cardId": -1, "hp": 3052, "x": 14500, "y": 6500},
        {"owner": 1, "cardId": -1, "hp": 4824, "x": 9000, "y": 29000},
        {"owner": 1, "cardId": -1, "hp": 3052, "x": 3500, "y": 25500},
        {"owner": 1, "cardId": -1, "hp": 3052, "x": 14500, "y": 25500},
    ]

    def test_a_real_capture_is_not_a_loss(self):
        verdict = pve_tower_verdict.read_player_king(
            frame(30, [dict(o) for o in self.REAL_TOWERS]))
        self.assertTrue(verdict.seen)
        self.assertFalse(verdict.lost,
                         "a standing king tower read from a real capture was "
                         "reported as destroyed")
        self.assertEqual(verdict.hp, 4824)

    def test_the_king_is_distinguished_from_the_princess_towers(self):
        self.assertEqual(
            pve_tower_verdict._tower_kind(self.REAL_TOWERS[0]), "king")
        self.assertEqual(
            pve_tower_verdict._tower_kind(self.REAL_TOWERS[1]), "princess")
        self.assertEqual(
            pve_tower_verdict._tower_kind(self.REAL_TOWERS[3]), "king")

    def test_a_demolished_king_is_still_a_loss(self):
        objects = [dict(o) for o in self.REAL_TOWERS]
        objects[0]["hp"] = 0
        verdict = pve_tower_verdict.read_player_king(frame(500, objects))
        self.assertTrue(verdict.lost)

    def test_the_enemy_king_is_not_the_players_king(self):
        objects = [dict(o) for o in self.REAL_TOWERS]
        verdict = pve_tower_verdict.read_player_king(frame(30, objects),
                                                    player_owner=0)
        self.assertEqual(verdict.hp, 4824)

    def test_a_deployed_troop_on_the_bridge_is_not_a_tower(self):
        # Wave spawns land at y=18500 and river pushes at y=15500; neither is an
        # anchor, so geometry cannot mistake a monster for a tower.
        for x, y in ((3500, 18500), (9000, 15500), (14500, 18500)):
            with self.subTest(pos=(x, y)):
                self.assertIsNone(
                    pve_tower_verdict._tower_kind(
                        {"owner": 1, "cardId": 26000002, "hp": 300,
                         "x": x, "y": y}))

    def test_the_tracker_does_not_count_towers_as_walking_units(self):
        from pve_probe_tracker import PositionTracker
        for tower in self.REAL_TOWERS:
            with self.subTest(tower=(tower["x"], tower["y"])):
                self.assertFalse(PositionTracker._is_walking_unit(tower),
                                 "a tower was classified as a walking unit")

    def test_a_real_troop_is_still_tracked(self):
        from pve_probe_tracker import PositionTracker
        troop = {"owner": 1, "cardId": 26000002, "hp": 300,
                 "x": 3500, "y": 18500}
        self.assertTrue(PositionTracker._is_walking_unit(troop))


class KingTowerReadTest(unittest.TestCase):
    def test_a_standing_king_is_not_a_loss(self):
        verdict = pve_tower_verdict.read_player_king(frame(300, [king(0, 4000)]))
        self.assertTrue(verdict.seen)
        self.assertTrue(verdict.king_alive)
        self.assertFalse(verdict.lost)
        self.assertEqual(pve_tower_verdict.pve_winner(verdict), 0)

    def test_a_fallen_king_is_a_loss(self):
        verdict = pve_tower_verdict.read_player_king(frame(900, [king(0, 0)]))
        self.assertTrue(verdict.lost)
        self.assertEqual(pve_tower_verdict.pve_winner(verdict), 1,
                         "a broken king must report a loss, not the engine's win")

    def test_the_enemy_king_is_never_the_players(self):
        # The duel bug in one line: the engine counts the other side's towers.
        # Reading the player's own king must ignore a dead enemy king entirely.
        verdict = pve_tower_verdict.read_player_king(
            frame(900, [king(0, 4000), king(1, 0)]))
        self.assertFalse(verdict.lost)
        self.assertEqual(pve_tower_verdict.pve_winner(verdict), 0)

    def test_princess_towers_are_not_king_towers(self):
        # Losing a princess tower must not end the run; only the king does.
        verdict = pve_tower_verdict.read_player_king(
            frame(600, [princess(0, 0), king(0, 4000)]))
        self.assertFalse(verdict.lost)

    def test_an_unobserved_world_is_not_a_loss(self):
        # "No king tower in this frame" and "the world was never observed" are
        # different states. Treating an empty capture as a loss would end the
        # match on frame one.
        for capture in ({}, {"tick": 5, "objects": []}, None):
            with self.subTest(capture=capture):
                verdict = pve_tower_verdict.read_player_king(capture)
                self.assertFalse(verdict.lost)
                self.assertEqual(pve_tower_verdict.pve_winner(verdict), 0)

    def test_a_null_object_is_not_a_tower(self):
        verdict = pve_tower_verdict.read_player_king(
            frame(300, [{"null": True, "owner": 0}, king(0, 3000)]))
        self.assertTrue(verdict.king_alive)

    def test_the_verdict_survives_a_json_round_trip(self):
        verdict = pve_tower_verdict.read_player_king(frame(1200, [king(0, 0)]))
        import json
        payload = json.loads(json.dumps(verdict.to_json()))
        self.assertTrue(payload["lost"])
        self.assertEqual(payload["tick"], 1200)


class ScriptedResultChannelTest(unittest.TestCase):
    """The verdict is useless unless it reaches the dialog."""

    @staticmethod
    def source():
        return RUNTIME_JAVA.read_text(encoding="utf-8")

    def test_java_exposes_a_scripted_result_override(self):
        self.assertIn("public static void setScriptedResult(", self.source())

    def test_show_result_prefers_the_scripted_verdict(self):
        text = self.source()
        show = text.split("public static void showResult(", 1)[1]
        # The override has to be consulted before the engine's winner is used.
        self.assertIn("scriptedResultGeneration==generation", show)
        self.assertLess(show.index("scriptedResultGeneration==generation"),
                        show.index('winner==0?"胜利"'),
                        "the scripted verdict must be applied before the duel titles")

    def test_the_override_is_consumed_so_the_next_match_is_not_mislabelled(self):
        text = self.source()
        show = text.split("public static void showResult(", 1)[1]
        # One verdict per match: a stale override would label a later duel.
        self.assertIn("scriptedResultGeneration=0", show)

    def test_python_installs_the_verdict_before_returning(self):
        runner = (ROOT / "on_device/python/mobile_fair.py").read_text(encoding="utf-8")
        start = runner.index("def run_pve_device_match(")
        body = runner[start:runner.index("\ndef ", start + 10)]
        self.assertIn("runtime.setScriptedResult(", body)
        self.assertIn("read_player_king(", body)
        # The defence must end on the king's fall rather than on the engine's
        # opposing-tower condition.
        self.assertIn("if verdict.lost and lost_tick is None:", body)

    def test_a_defence_that_outran_the_engine_still_reaches_the_dialog(self):
        """The verdict must be raised, not just installed.

        The result dialog is raised by the native bridge and only when the
        engine's own `finalized` flag goes true. Neither PvE exit is that
        condition -- the run ends when the player's king falls or when the
        stage schedule runs out -- so on both of those paths the engine never
        finalizes, the scripted verdict is stored and never consumed, and the
        player is left on a frozen battle with no dialog and no way home.
        """
        runner = (ROOT / "on_device/python/mobile_fair.py").read_text(encoding="utf-8")
        start = runner.index("def run_pve_device_match(")
        body = runner[start:runner.index("\ndef ", start + 10)]
        self.assertIn("runtime.showScriptedResult(generation)", body)
        # Only when the engine has not already finalized; otherwise this would
        # race the native call and could raise the dialog twice.
        self.assertIn("if not adapter.observation.terminal.ended:", body)
        # It has to follow the verdict it is meant to display.
        self.assertLess(body.index("runtime.setScriptedResult("),
                        body.index("runtime.showScriptedResult("))

    def test_the_scripted_dialog_is_generation_guarded(self):
        source = RUNTIME_JAVA.read_text(encoding="utf-8")
        start = source.index("public static void showScriptedResult(")
        body = source[start:source.index("\n    }", start)]
        # A verdict from a finished match must never surface over a later one,
        # and a match the player already backed out of must stay backed out.
        self.assertIn("generation!=activeGeneration", body)
        self.assertIn("generation==abandonedGeneration", body)
        self.assertIn("generation<=0", body)

    def test_only_one_result_dialog_is_raised_per_battle(self):
        """The bridge's late verdict must not re-trap the player.

        A defence raises its own dialog from Python, which the player dismisses
        and which resets activeGeneration to 0. The native bridge then finalizes
        and calls showResult again; with activeGeneration now 0 the generation
        guard no longer matched, so a second "对局结束" dialog appeared over the
        lobby and 返回大厅 did nothing. Observed on a real device: three taps, no
        return to the lobby.
        """
        source = RUNTIME_JAVA.read_text(encoding="utf-8")
        show = source[source.index("public static void showResult("):]
        show = show[:show.index("private static void showResultForced(")]
        self.assertIn("if(generation>0 && resultShown) return;", show)
        # The flag has to be re-armed for the next battle, or every match after
        # the first would end with no dialog at all.
        created = source[source.index("public static void onNativeBattleCreated("):]
        created = created[:created.index("worker.execute(")]
        self.assertIn("resultShown=false;", created)

    def test_the_scripted_verdict_supersedes_the_bridges(self):
        """The engine can finalize first, and its verdict is the wrong one.

        On a defence the player destroys their own towers, which is exactly the
        engine's loss condition, so the bridge can finalize on the same frame the
        king falls and raise "对局结束" before Python installs the defence
        verdict. The scripted path must therefore replace the dialog rather than
        be dropped by the once-per-battle guard.
        """
        source = RUNTIME_JAVA.read_text(encoding="utf-8")
        scripted = source[source.index("public static void showScriptedResult("):]
        scripted = scripted[:scripted.index("\n    }")]
        self.assertIn("showResultForced(generation,Integer.MIN_VALUE,true)",
                      scripted)
        forced = source[source.index(
            "private static void showResultForced(long generation,int winner,boolean replace)"):]
        forced = forced[:forced.index("\n    }")]
        # Replacing has to dismiss the standing dialog first, otherwise the
        # "already showing" guard discards the authoritative verdict.
        self.assertIn("if(replace && resultDialog!=null && resultDialog.isShowing())",
                      forced)
        self.assertIn("resultDialog.dismiss();", forced)

    def test_java_exposes_notify_pve_wave(self):
        self.assertIn("public static void notifyPveWave(", self.source())

    def test_python_calculates_star_rating_on_victory(self):
        runner = (ROOT / "on_device/python/mobile_fair.py").read_text(encoding="utf-8")
        start = runner.index("def run_pve_device_match(")
        body = runner[start:runner.index("\ndef ", start + 10)]
        self.assertIn("hp_ratio", body)
        self.assertIn('"stars":stars if pve_winner(verdict)==0 else 0', body)


if __name__ == "__main__":
    unittest.main()
