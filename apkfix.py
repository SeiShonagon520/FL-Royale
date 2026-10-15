"""Verify the two battle-start fixes are actually in the shipped APK."""
import io, marshal, pathlib, types, zipfile

APK = pathlib.Path("dist/fl-royale-1.8-r1.apk")
log = []
def emit(s): log.append(s)

with zipfile.ZipFile(APK) as z:
    imy = zipfile.ZipFile(io.BytesIO(z.read("assets/chaquopy/app.imy")))
    src = imy.read("native_runner/battle_env.py").decode("utf-8", "replace")

    emit("=== fix 1: tower-binding tolerance ===")
    emit("  'if len(candidates) == 0:' present : %s"
         % ("if len(candidates) == 0:" in src))
    emit("  gated on _is_live_battle        : %s"
         % ('getattr(self, "_is_live_battle", False) and len(candidates) == 0' in src))

    emit("")
    emit("=== fix 2: multiplier clamp is an inline literal ===")
    emit("  inline clamp present             : %s"
         % ("multiplier if multiplier <= 3.0 else 3.0" in src))
    emit("  stale constant reference gone   : %s"
         % ("POLICY_ELIXIR_MULTIPLIER_CAP" not in src))

    emit("")
    emit("=== compiled bytecode agrees with the source ===")
    code = marshal.loads(imy.read("native_runner/battle_env.pyc")[16:])
    consts, names = set(), set()
    def walk(c):
        consts.update(c.co_consts)
        names.update(c.co_names)
        for k in c.co_consts:
            if isinstance(k, types.CodeType):
                walk(k)
    walk(code)
    emit("  module still names the old cap   : %s"
         % ("POLICY_ELIXIR_MULTIPLIER_CAP" in names or
            any(isinstance(c, str) and "POLICY_ELIXIR" in c for c in consts)))

    emit("")
    emit("=== the compile is loadable (a bad .pyc would fail here) ===")
    emit("  module name                     : %s" % code.co_name)
    emit("  compiled successfully           : True")

    emit("")
    emit("=== other battle-start guards are in place ===")
    emit("  ladder-only pin removed         : %s"
         % ("currently guarantees canonical masks/towers only" not in src))
    emit("  game-mode pairing gate          : %s"
         % ("game mode does not match the environment timeline" in src))
    emit("  provenance guard                : %s"
         % ("must come from the same verified game_modes.csv" in src))
    emit("  arena guard                     : %s"
         % ("fails closed outside verified standard-layout" in src))

pathlib.Path("apkfix.txt").write_text("\n".join(log), encoding="utf-8")
print("ok")
