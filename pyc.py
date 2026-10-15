"""Does the shipped app.imy actually contain the new constant?

The source has it, so a stale .pyc is the suspect: stage_mobile_semantics
copies the .py, but the gradle python build may reuse a compiled artefact.
"""
import io, marshal, pathlib, types, zipfile

APK = pathlib.Path("dist/fl-royale-1.8-r1.apk")
log = []
def emit(s): log.append(s)

with zipfile.ZipFile(APK) as z:
    imy = zipfile.ZipFile(io.BytesIO(z.read("assets/chaquopy/app.imy")))
    names = imy.namelist()
    emit("=== battle_env entries in app.imy ===")
    for n in names:
        if "battle_env" in n:
            emit("  %-44s %8d bytes" % (n, imy.getinfo(n).file_size))

    raw = imy.read("native_runner/battle_env.pyc")
    code = marshal.loads(raw[16:])
    consts = set()
    def walk(c):
        consts.update(c.co_consts)
        for k in c.co_consts:
            if isinstance(k, types.CodeType):
                walk(k)
    walk(code)

    emit("")
    emit("=== does the compiled module know the cap? ===")
    emit("  3.0 in module constants: %s" % (3.0 in consts))
    emit("  (a bare 3.0 could come from elsewhere, so check the source too)")

    emit("")
    emit("=== staged .py source on disk ===")
    staged = pathlib.Path("build/mobile-semantics/native_runner/battle_env.py").read_text(encoding="utf-8")
    emit("  POLICY_ELIXIR_MULTIPLIER_CAP = 3.0 present: %s"
         % ("POLICY_ELIXIR_MULTIPLIER_CAP = 3.0" in staged))
    emit("  min(multiplier, POLICY...) present       : %s"
         % ("min(multiplier, POLICY_ELIXIR_MULTIPLIER_CAP)" in staged))
    emit("  _bind_tower_object_keys tolerance       : %s"
         % ("if len(candidates) == 0:" in staged))

    emit("")
    emit("=== gradle python build output (what actually gets packaged) ===")
    for p in pathlib.Path("android_runtime_smoke/localapp/build/python/sources/debug/native_runner").glob("battle_env*"):
        emit("  %-70s %8d  mtime=%s" % (p, p.stat().st_size, p.stat().st_mtime))
        if p.suffix == ".py":
            t = p.read_text(encoding="utf-8", errors="replace")
            emit("      has cap definition: %s" % ("POLICY_ELIXIR_MULTIPLIER_CAP = 3.0" in t))
            emit("      has min() call    : %s" % ("min(multiplier, POLICY_ELIXIR_MULTIPLIER_CAP)" in t))

pathlib.Path("pyc.txt").write_text("\n".join(log), encoding="utf-8")
print("ok")
