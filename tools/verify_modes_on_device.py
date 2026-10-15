# Batch-verify every exposed mode on the device: start a match, let it run,
# and confirm the battle reaches a real terminal state instead of erroring out.
# Usage:  python tools/verify_modes_on_device.py [mode_key ...]
import json
import pathlib
import re
import subprocess
import sys
import time

ADB = r"C:\Users\L\AppData\Local\Android\Sdk\platform-tools\adb.exe"
DEV = "127.0.0.1:7555"
PKG = "com.firstlight.offline"
LAUNCH = f"{PKG}/.ResourceBootstrapActivity"
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "on_device/python"))
sys.path.insert(0, str(ROOT / "build/mobile-semantics"))

import mobile_fair

SCREEN_W, SCREEN_H = 720, 1280
TAP_BATTLE = (340, 940)
TAP_MODE_ROW = (140, 480)
TAP_MODEL_ROW = (140, 570)


def adb(*args, **kw):
    return subprocess.run([ADB, "-s", DEV, *args], capture_output=True, text=True, timeout=kw.get("timeout", 120))


def logcat_clear():
    adb("logcat", "-c")


def logcat_dump():
    out = adb("logcat", "-d", "-v", "time", "FirstlightLocal:V", "AndroidRuntime:E", "*:S",
              timeout=180).stdout
    return out


def tap(x, y):
    adb("shell", "input", "tap", str(x), str(y))


def screenshot(name):
    adb("shell", "screencap", "-p", f"/sdcard/{name}.png")
    adb("pull", f"/sdcard/{name}.png", f"user-provided/{name}.png", timeout=180)


def restart_app():
    adb("shell", "am", "force-stop", PKG)
    time.sleep(1)
    adb("shell", "am", "start", "-n", LAUNCH)
    time.sleep(30)  # bootstrap + Home scene


def read_state():
    out = adb("shell", "dumpsys", "window", "mCurrentFocus", timeout=60).stdout
    return out.strip()


def open_mode_picker():
    tap(*TAP_BATTLE)
    time.sleep(2)
    tap(*TAP_MODE_ROW)
    time.sleep(2)


def pick_mode_by_label(label):
    """Scroll-free: the picker is a plain list; find the row by screenshot text is
    unreliable, so the caller passes an index instead."""
    raise NotImplementedError


MODE_ORDER = [
    "ladder", "double_elixir", "triple_elixir", "7x_elixir", "sudden_death",
    "ramp_up", "mirror", "all_random", "all_evolution", "rage", "crazy_arena",
    "elixir_barrel", "chess_board", "crazy_sudden_death",
]

if __name__ == "__main__":
    wanted = sys.argv[1:] or MODE_ORDER
    print("available modes:", [k for k in mobile_fair.GAME_MODES
                              if k not in ("2v2", "2v2_random")])
    print("to verify:", wanted)
