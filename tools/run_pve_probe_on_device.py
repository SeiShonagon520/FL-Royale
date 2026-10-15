"""Drive one PvE match on a connected device and pull the probe result back.

Stage 1's evidence is the (tick,x,y) series the PvE runner now records. That
series is returned inside the "Battle completed {...}" summary JSON, so this
script only has to reproduce the same launch sequence verify_mode_on_device.py
uses and then extract that one line.

The full binary ring buffer is archived to disk before it can rotate, because a
three-minute match plus boot easily overflows logcat and the failure detail is
in the part that gets dropped.
"""
import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

ADB = r"C:\Users\L\AppData\Local\Android\Sdk\platform-tools\adb.exe"
DEVICE = "127.0.0.1:7555"
PKG = "com.firstlight.offline"
LAUNCH = f"{PKG}/.ResourceBootstrapActivity"

TAP_BATTLE = (340, 940)
TAP_FIRST_OPPONENT = (150, 560)

BOOT_TIMEOUT = 240
BATTLE_TIMEOUT = 600


def adb(*args, binary=False, timeout=120):
    result = subprocess.run([ADB, "-s", DEVICE, *args], capture_output=True,
                            timeout=timeout)
    if binary:
        return result.stdout
    return result.stdout.decode("utf-8", "replace")


def logcat_clear():
    adb("logcat", "-c")


def logcat_text():
    return adb("logcat", "-d", "-v", "threadtime", timeout=180)


def archive_log(path):
    Path(path).write_bytes(adb("logcat", "-d", "-v", "threadtime", binary=True,
                               timeout=180))


def wait_for(pattern, timeout, poll=5):
    deadline = time.time() + timeout
    regex = re.compile(pattern)
    while time.time() < deadline:
        text = logcat_text()
        match = regex.search(text)
        if match:
            return match
        time.sleep(poll)
    return None


def run(label, timeout=BATTLE_TIMEOUT):
    logcat_clear()
    adb("shell", "am", "force-stop", PKG)
    time.sleep(2)
    adb("shell", "am", "start", "-n", LAUNCH, timeout=180)

    ready = wait_for(r"Native offline Home boot ready", BOOT_TIMEOUT)
    if not ready:
        archive_log(ROOT / f"user-provided/{label}_no_boot.log")
        print(f"[{label}] FAILED: home never booted")
        return None
    print(f"[{label}] home booted")

    # The lobby needs a moment before its buttons take input.
    time.sleep(25)
    adb("shell", "input", "tap", str(TAP_BATTLE[0]), str(TAP_BATTLE[1]))
    time.sleep(5)
    adb("shell", "input", "tap", str(TAP_FIRST_OPPONENT[0]), str(TAP_FIRST_OPPONENT[1]))

    match = wait_for(r"Battle completed (\{.*?\})\s*$", timeout, poll=10)
    archive_log(ROOT / f"user-provided/{label}_logcat.txt")
    if not match:
        failure = wait_for(r"Battle pipeline failed|Match preparation failed", 5, poll=2)
        print(f"[{label}] FAILED: no Battle completed"
              + (f"; saw {failure.group(0)!r}" if failure else ""))
        return None
    return json.loads(match.group(1))


TRACK_LINE = re.compile(r"PvE track (\d+) owner=(-?\d+) card=(\S+) obj=(\S+) \[(\d+)/(\d+)\] (.*)$")


def parse_tracks(text):
    """Rebuild the (tick,x,y) series from the chunked log lines.

    The series never fits in one log entry -- logcat silently drops anything over
    roughly 4 KB -- so the runner emits it as many bounded lines and the chunks
    are reassembled here. Samples are appended in arrival order, which the
    runner guarantees by writing each track's chunks sequentially.
    """
    tracks = {}
    for line in text.splitlines():
        match = TRACK_LINE.search(line)
        if not match:
            continue
        index, owner, card, obj, _part, _total, body = match.groups()
        entry = tracks.setdefault(int(index),
                                  {"owner": int(owner), "cardId": card,
                                   "nativeObjectId": obj, "samples": []})
        for token in body.split():
            tick, x, y = token.split(":")
            entry["samples"].append([int(tick), int(x), int(y)])
    return [tracks[key] for key in sorted(tracks)]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", default="pve_probe")
    parser.add_argument("--timeout", type=int, default=BATTLE_TIMEOUT)
    args = parser.parse_args()

    result = run(args.label, args.timeout)
    text = (ROOT / f"user-provided/{args.label}_logcat.txt").read_text(
        encoding="utf-8", errors="replace") if (
        ROOT / f"user-provided/{args.label}_logcat.txt").is_file() else ""
    tracks = parse_tracks(text)
    out = ROOT / f"user-provided/{args.label}_tracks.json"
    out.write_text(json.dumps(tracks, indent=1), encoding="utf-8")

    print(f"tracks recovered: {len(tracks)}")
    for entry in tracks:
        samples = entry["samples"]
        if not samples:
            continue
        print(f"  owner={entry['owner']} card={entry['cardId']} n={len(samples)} "
              f"first={samples[0]} last={samples[-1]}")
    print(f"tracks -> {out}")

    if result is None:
        return 1
    print("\nmatch summary: " + json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())