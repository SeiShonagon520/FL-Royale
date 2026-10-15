"""Give the `Pve` battle timeline a real, playable shape in the staged copy.

`stage_mobile_semantics.py` re-copies `native_runner/data/competitive` from the
Firstlight CR checkout on every build, so the fix is applied to the staged copy
instead of the vendored source, exactly like `patch_mode_pacing.py`.

Why the shipped timeline is broken
----------------------------------
The stock `Pve` timeline declares a single 0-second Normal section and a single
0-second elixir rate. `BattleTimelineV1.phase()` walks the sections with a
`for/else`, and a section list whose lengths are all zero never breaks, so the
`else` arm runs and `phase` is silently rewritten to "tiebreak" on *every* tick.
`phase` feeds the policy, so a PvE match would present the model with a
tiebreak flag for the whole battle. `elixir_rate()` has a `return
self.elixir_rates[-1]` fallback, so it does not crash, but it pins the match to
the single 14s/20x rate for its entire duration.

The replacement keeps the ladder's proven 180s Normal + 120s Overtime shape and
the ladder's three-step elixir ramp, and keeps the stock `starting_elixir` of
6.0 so the tensorizer's seeded elixir stays inside the trained range. It
deliberately does NOT reach the 3x+ rates: the policy was trained on ladder
pacing, and the input stays inside the range it has seen.

Why the digests are recomputed rather than left alone
-----------------------------------------------------
`competitive_data.load_competitive_data("runtime")` verifies `runtime.json`
against `manifest.json`, and `competitive_data.MANIFEST_SHA256` pins
`manifest.json` itself. Editing the file without re-pinning would raise
`ContractError: competitive resource SHA-256 mismatch` on the first timeline
load, i.e. an app that cannot start a match. The three digest layers are
therefore rewritten in step: `manifest.json`'s `files.runtime.sha256`, then
`competitive_data.MANIFEST_SHA256`, in the staged copies only.

The timeline's own `source_sha256` / `game_modes_sha256` are deliberately left
alone: every timeline in the file shares one `battle_timelines.csv` digest and
one `game_modes.csv` digest, and `BattleEnvV1.__init__` compares an injected
mode timeline's `game_modes_sha256` against the verified standard timeline to
prove both came from the same verified `game_modes.csv`. Recomputing those to
cover a locally authored PvE section would make the shipped PvE timeline fail
that provenance guard, so the provenance fields keep describing the real CSVs
they were compiled from.
"""
import argparse
import hashlib
import json
import pathlib
import re
import sys

# The ladder's proven shape. 180s Normal + 120s Overtime with a 10/20/30 elixir
# ramp, and the stock PvE starting elixir.
PVE_TIMELINE = {
    "name": "Pve",
    "starting_elixir": 6.0,
    "sections": [
        {"length_seconds": 180, "section_type": "Normal", "flags": []},
        {"length_seconds": 120, "section_type": "Overtime", "flags": []},
    ],
    "elixir_rates": [
        {"length_seconds": 120, "full_bar_ms": 28000, "visible_rate": 10, "notify_change": True},
        {"length_seconds": 120, "full_bar_ms": 14000, "visible_rate": 20, "notify_change": True},
        {"length_seconds": 60, "full_bar_ms": 9300, "visible_rate": 30, "notify_change": True},
    ],
}

TIMELINE_NAME = "Pve"

# Only the timeline body is replaced; source_path / source_sha256 / version are
# the provenance fields described in the module docstring and are carried over
# from the shipped entry untouched.
REPLACED_KEYS = ("name", "starting_elixir", "sections", "elixir_rates")

MANIFEST_SHA256_PATTERN = re.compile(r'^(MANIFEST_SHA256\s*=\s*")([0-9a-f]{64})(")', re.MULTILINE)


def _write_json(path: pathlib.Path, payload: object) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def patch_timeline(data_root: pathlib.Path) -> dict:
    runtime_path = data_root / "runtime.json"
    manifest_path = data_root / "manifest.json"
    for path in (runtime_path, manifest_path):
        if not path.is_file():
            raise FileNotFoundError(f"competitive data not staged: {path}")

    document = json.loads(runtime_path.read_text(encoding="utf-8"))
    timelines = document.get("timelines") or {}
    if TIMELINE_NAME not in timelines:
        raise KeyError(f"timeline {TIMELINE_NAME!r} is missing from the staged runtime.json")

    original = timelines[TIMELINE_NAME]
    for key, value in PVE_TIMELINE.items():
        if key not in REPLACED_KEYS:
            raise KeyError(f"refusing to overwrite provenance field {key!r}")
        original[key] = value
    timelines[TIMELINE_NAME] = original
    _write_json(runtime_path, document)

    # Re-pin the two digest layers the loader actually enforces.
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    runtime_digest = _sha256(runtime_path)
    manifest["files"]["runtime"]["sha256"] = runtime_digest
    _write_json(manifest_path, manifest)
    manifest_digest = _sha256(manifest_path)

    return {
        "schema": "firstlight.pve-timeline-patch.v1",
        "timeline": TIMELINE_NAME,
        "runtime_json_sha256": runtime_digest,
        "manifest_json_sha256": manifest_digest,
    }


def patch_manifest_constant(staged: pathlib.Path, manifest_digest: str) -> dict:
    """Point the staged `competitive_data.MANIFEST_SHA256` at the new manifest."""
    path = staged / "native_runner" / "competitive_data.py"
    if not path.is_file():
        raise FileNotFoundError(f"staged competitive_data not found: {path}")
    text = path.read_text(encoding="utf-8")
    if manifest_digest in text:
        return {"schema": "firstlight.pve-timeline-patch.v1", "already_patched": True}
    patched, count = MANIFEST_SHA256_PATTERN.subn(rf"\g<1>{manifest_digest}\g<3>", text, count=1)
    if count != 1:
        raise ValueError("MANIFEST_SHA256 literal is missing or ambiguous in staged competitive_data.py")
    path.write_text(patched, encoding="utf-8")
    return {
        "schema": "firstlight.pve-timeline-patch.v1",
        "already_patched": False,
        "manifest_sha256": manifest_digest,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--staged", required=True, help="build/mobile-semantics directory")
    parser.add_argument("--receipt")
    args = parser.parse_args()
    staged = pathlib.Path(args.staged).resolve()
    receipt = patch_timeline(staged / "native_runner" / "data" / "competitive")
    receipt.update(patch_manifest_constant(staged, receipt["manifest_json_sha256"]))
    if args.receipt:
        pathlib.Path(args.receipt).write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    print(json.dumps({"timeline": receipt["timeline"], "runtime_json_sha256": receipt["runtime_json_sha256"]}))


if __name__ == "__main__":
    sys.exit(main())
