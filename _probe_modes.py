"""Per-mode verification: timeline load + create_match + tensorizer contract.

Run from the working tree (on_device/python wins over the staging copy).
"""
import json
import pathlib
import re
import sys
import traceback

ROOT = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "build/mobile-semantics"))
sys.path.insert(0, str(ROOT / "on_device/python"))

import mobile_fair
from native_runner.timeline import load_game_mode_timeline
from native_runner.training.v4.factory import production_semantic_bundle

print("mobile_fair from:", mobile_fair.__file__)

# Mode ids exposed by the Java picker, parsed from the live source.
JAVA = ROOT / "on_device/java/com/firstlight/offline/GameModePicker.java"
text = JAVA.read_text(encoding="utf-8")
java_modes = re.findall(r'new Mode\("([^"]+)",\s*(\d+)', text)
print("Java picker modes:", len(java_modes))

DECK = [26000000, 26000001, 26000005, 28000001, 28000000, 26000003, 26000014, 26000018]
HUMAN = {"cards": DECK, "forms": [0] * 8}

rows = []
for key, gid in java_modes:
    gid = int(gid)
    in_catalog = key in mobile_fair.GAME_MODES
    cat_id = mobile_fair.GAME_MODES.get(key, {}).get("id")
    # prewarm() skips team-vs-team, so mirror that.
    skip_timeline = cat_id == mobile_fair.GAME_MODE_TEAM_VS_TEAM
    tl = "-"
    if not skip_timeline:
        try:
            t = load_game_mode_timeline(gid)
            tl = "%s elixir=%s" % (t.timeline.name, t.timeline.starting_elixir)
        except Exception as e:
            tl = "FAIL: %s" % e
    fixture = "-"
    try:
        payload = json.loads(mobile_fair.create_match(json.dumps(HUMAN), "general",
                                                     json.dumps(HUMAN), game_mode=key))
        b = payload["battle"]
        sub = production_semantic_bundle().semantic_subset_contract
        for owner in (0, 1):
            sp = b["deck%d" % owner]["sp"]
            sub.assert_deck([r["d"] for r in sp],
                            form_availability=[int(r.get("el", 0)) for r in sp],
                            label="deck%d" % owner)
        # A silent ladder fallback is the drift failure mode: assert the id matches.
        drift = "" if b["gamemode"] == gid else "  <<< FELL BACK to %s" % b["gamemode"]
        fixture = "gamemode=%s ok%s" % (b["gamemode"], drift)
    except Exception as e:
        fixture = "FAIL: %s: %s" % (type(e).__name__, e)
    rows.append((key, gid, "yes" if in_catalog else "NO", cat_id, tl, fixture))

print()
print("%-20s %-9s %-6s %-9s %-28s %s" % ("mode", "javaId", "inPy", "pyId", "timeline", "create_match"))
for r in rows:
    print("%-20s %-9s %-6s %-9s %-28s %s" % r)

missing = [k for k, _ in java_modes if k not in mobile_fair.GAME_MODES]
print("\nJava modes absent from Python GAME_MODES:", missing or "none")
print("Python modes not exposed in Java picker:",
      [k for k in mobile_fair.GAME_MODES if k not in dict(java_modes)] or "none")
bad = [r for r in rows if r[4].startswith("FAIL") or r[5].startswith("FAIL")]
print("FAILING MODES:", [r[0] for r in bad] or "none")
