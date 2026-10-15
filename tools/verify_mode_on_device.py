"""Drive one game mode on the device end-to-end and report the verdict.

Sets the picker's persisted mode directly (the emulator is rooted, so the
prefs file is writable), then taps 对战 -> Firstlight and watches the log until
the battle either completes or fails.

Usage: python tools/verify_mode_on_device.py <mode_key> [more keys...]
"""
import pathlib
import re
import subprocess
import sys
import tempfile
import time

ADB = r"C:\Users\L\AppData\Local\Android\Sdk\platform-tools\adb.exe"
DEV = "127.0.0.1:7555"
PKG = "com.firstlight.offline"
LAUNCH = f"{PKG}/.ResourceBootstrapActivity"
PREFS = "/data/data/com.firstlight.offline/shared_prefs/firstlight_offline_game_mode.xml"
TAP_BATTLE = (340, 940)
TAP_FIRST_OPPONENT = (150, 560)
# A match that reaches its terminal state takes ~3 minutes of wall clock at
# the emulator's render speed; give it room before declaring a hang.
BATTLE_TIMEOUT = 420


def adb(*args, timeout=180):
    return subprocess.run([ADB, "-s", DEV, *args], capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout)


def adb_bytes(*args, timeout=300):
    """Binary form for the full logcat ring buffer, which is far too large to
    decode as text on a GBK console and must be archived verbatim."""
    return subprocess.run([ADB, "-s", DEV, *args], capture_output=True, timeout=timeout).stdout


def set_mode(key):
    """Rewrite the picker preference so no blind list-scrolling is needed.

    The payload is staged locally and pushed, because the XML quoting does not
    survive a trip through the device shell.
    """
    adb("shell", "am", "force-stop", PKG)
    staged = pathlib.Path(tempfile.gettempdir()) / "fl_mode_pref.xml"
    staged.write_text(
        '<?xml version=\'1.0\' encoding=\'utf-8\' standalone=\'yes\' ?>'
        f'<map><string name="selected_mode">{key}</string></map>',
        encoding="utf-8",
    )
    adb("push", str(staged), f"/data/local/tmp/fl_mode_pref.xml")
    result = adb("shell", "cp", "/data/local/tmp/fl_mode_pref.xml", PREFS)
    check = adb("shell", "cat", PREFS)
    if key not in check.stdout:
        raise RuntimeError(f"mode not persisted ({result.returncode}): {check.stdout!r} {check.stderr!r}")


def start_match():
    adb("logcat", "-c")
    adb("shell", "am", "start", "-n", LAUNCH)
    # The runtime logs a line once the offline Home scene is live; that is a
    # more dependable readiness signal than window focus on this emulator.
    for _ in range(40):
        time.sleep(5)
        if "Native offline Home boot ready" in adb("logcat", "-d", "-v", "time", "FirstlightLocal:V", "*:S").stdout:
            break
    else:
        raise RuntimeError("app never reached the lobby")
    time.sleep(25)         # let the Home scene finish its first frames
    adb("shell", "input", "tap", *map(str, TAP_BATTLE))
    time.sleep(5)
    adb("shell", "input", "tap", *map(str, TAP_FIRST_OPPONENT))


def wait_for_battle(key):
    deadline = time.time() + BATTLE_TIMEOUT
    started = False
    while time.time() < deadline:
        out = adb("logcat", "-d", "-v", "time", "FirstlightLocal:V", "*:S").stdout
        if "Local fixture ready" in out or "Match preparation failed" in out:
            started = True
        for line in out.splitlines():
            if "Battle completed" in line:
                return "completed", line.split("Battle completed", 1)[1].strip()
            if "Battle pipeline failed" in line or "Match preparation failed" in line:
                # Persist the ring buffer now: the emulator rotates logcat long
                # before the next mode's battle, so the stack would be lost.
                keep = pathlib.Path(tempfile.gettempdir()) / f"fl_fail_{key}.log"
                keep.write_bytes(adb_bytes("logcat", "-d", "-v", "threadtime"))
                return "failed", f"{line.strip()} (trace: {keep})"
        time.sleep(6)
    return ("timeout" if started else "no-start"), ""


def resolved_mode():
    """Read back the game mode the running match actually resolved to."""
    out = adb("logcat", "-d", "-v", "time", "FirstlightLocal:V", "*:S").stdout
    found = re.search(r"Match game mode resolved to (\d+)", out)
    return int(found.group(1)) if found else None


def verify(key):
    set_mode(key)
    start_match()
    status, detail = wait_for_battle(key)
    print(f"{key:22s} {status:10s} mode={resolved_mode()} {detail[:200]}")
    return key, status, detail


if __name__ == "__main__":
    wanted = sys.argv[1:] or ["ladder"]
    results = [verify(key) for key in wanted]
    print()
    ok = [k for k, s, _ in results if s == "completed"]
    bad = [(k, s, d) for k, s, d in results if s != "completed"]
    print(f"completed: {len(ok)}/{len(results)}")
    for k, s, d in bad:
        print(f"  NOT OK {k}: {s} {d[:300]}")
