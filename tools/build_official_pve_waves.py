"""Generate on_device/python/pve_official_waves.py from the decrypted official table.

The engine does not execute pve_waves.csv by itself (verified on device: with
gamemode 72000018 and no Python wave arming, the board stays empty). So the
official wave script has to be interpreted here and expressed as the only
deploy primitive the local app has -- an absolute-tick card deploy via
LocalBattleRuntime.scheduleCard.

This module is GENERATED. Re-run it after any change to the upstream table:

    python tools\\build_official_pve_waves.py

Coordinate transform
--------------------
The official table is authored on the full PvE map, whose player half spans
y=1000..14000. Our arena is 18x32 with the river at row 15/16, so the player
half is rows 0..14. Two conversions are therefore required and both are load
bearing:

* `cell_center` -- the arena is a lattice of 1000-unit cells whose deploy point
  is `cell*1000+500`. The official coordinates are round thousands (6000, 8000),
  which sit on a cell *corner*, half a cell off the deploy lattice, and the
  engine refuses a deploy there. Snapping to the cell centre is what makes these
  coordinates legal at all: 63/83 of them are legal after snapping, 1/83 before.
* `mirror_y` -- official y is measured from the player's own king tower; ours is
  measured from the same edge for owner 0, so the enemy-side waves must be
  reflected through the arena: `y -> 32000 - y`.

The remaining 20 of 83 land inside the enemy princess-tower footprint (cells
x in {2,3,4} and {13,14,15}, y in {24,25,26}), which the engine rejects because
the 1v1 map still builds the opponent's two towers. Those are recorded in
`TOWER_FOOTPRINT_CLASHES` rather than silently nudged, because moving them would
invent a wave the official script never specified. They are the one hard blocker
on a true single-sided PvE map; see the stage table.
"""

import csv
import io
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "build", "mobile-semantics"))
sys.path.insert(0, os.path.join(ROOT, "on_device", "python"))

from native_runner import arena  # noqa: E402
from native_runner.training.v4.factory import production_semantic_bundle  # noqa: E402

OUT_PATH = os.path.join(ROOT, "on_device", "python", "pve_official_waves.py")

GRID_WIDTH = arena.GRID_WIDTH          # 18
GRID_HEIGHT = arena.GRID_HEIGHT        # 32
CELL_UNITS = arena.CELL_UNITS          # 1000
WORLD_HEIGHT = GRID_HEIGHT * CELL_UNITS  # 32000

# The arena deploy lattice is cell centres, so a coordinate must be
# cell*1000+500. The official table writes round thousands.
def cell_center(value):
    return (value // CELL_UNITS) * CELL_UNITS + CELL_UNITS // 2


def mirror_y(value):
    """Reflect an official player-half y into the enemy half of our arena."""
    return WORLD_HEIGHT - value


def to_arena_xy(official_x, official_y):
    return cell_center(official_x), mirror_y(cell_center(official_y))


# Action types that carry no deploy semantics: they are pure control flow or
# commentary. LogInfo is a message to the player and None is an inert marker row,
# so neither affects which units exist. They are tolerated rather than treated
# as an unimplemented feature.
IGNORED_ACTIONS = {"LogInfo", "None"}

# The subset that a pure absolute-tick deploy schedule can actually express.
# SpawnWaveStandalone names another wave and runs it as an independent loop, and
# RestartWave/EndWave are the loop bound, so all three are expressible.
SUPPORTED_ACTIONS = {"Spawn", "RestartWave", "EndWave", "SpawnWaveStandalone"}


def load_raw_waves():
    dump = subprocess.run(
        [sys.executable, os.path.join(ROOT, "tools", "dump_apk_table.py"), "pve_waves.csv"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=ROOT, timeout=600,
    )
    raw = dump.stdout + dump.stderr
    marker = "pve_waves.csv (307 lines)"
    if marker not in raw:
        raise RuntimeError(f"could not locate the pve_waves.csv dump:\n{raw[:2000]}")
    body = raw.split(marker, 1)[1]
    body = body[body.index("\n") + 1:]
    rows = list(csv.reader(io.StringIO(body)))
    header = [c.strip() for c in rows[0]]
    col = {name: header.index(name) for name in header}

    waves = {}
    order = []
    current = None
    for row in rows[2:]:
        if not row or not any(c.strip() for c in row):
            continue
        if row[col["Name"]].strip():
            current = {
                "name": row[col["Name"]].strip(),
                "end_condition": row[col["EndCondition"]].strip(),
                "kill_child": row[col["KillChildWavesOnEnd"]].strip(),
                "actions": [],
            }
            waves[current["name"]] = current
            order.append(current["name"])
        if current is None:
            continue
        current["actions"].append({
            "type": row[col["ActionType"]].strip(),
            "arg": row[col["ActionArgString"]].strip(),
            "enabled": row[col["ActionIsEnabled"]].strip().lower() == "true",
            "condition": row[col["ActionStartCondition"]].strip(),
            "count": row[col["ActionSpawnCount"]].strip(),
            "x": row[col["ActionSpawnX"]].strip(),
            "y": row[col["ActionSpawnY"]].strip(),
            "delay": row[col["ActionSpawnDelay"]].strip(),
            "level": row[col["ActionSpawnLevel"]].strip(),
        })
    return waves, order


def build():
    bundle = production_semantic_bundle()
    specs = {int(s.card_id): s for s in bundle.native_card_catalog.specs}
    name_to_id = {s.name: int(s.card_id) for s in bundle.native_card_catalog.specs}

    waves, order = load_raw_waves()
    exported = []
    skipped = []
    clashes = []

    for name in order:
        wave = waves[name]
        types = {a["type"] for a in wave["actions"]} - IGNORED_ACTIONS
        spawns = [a for a in wave["actions"] if a["type"] == "Spawn"]
        if not spawns or not types <= SUPPORTED_ACTIONS:
            skipped.append((name, sorted(types)))
            continue
        if not all(a["arg"] in name_to_id for a in spawns):
            skipped.append((name, ["unmapped card name"]))
            continue

        entries = []
        ok = True
        for action in spawns:
            ox, oy = int(action["x"]), int(action["y"])
            x, y = to_arena_xy(ox, oy)
            card_id = name_to_id[action["arg"]]
            legal = arena.card_placement_legal_world(specs[card_id], 1, x, y)
            if not legal:
                cx, cy = arena.world_to_cell(x, y)
                clashes.append((name, action["arg"], ox, oy, x, y, cx, cy))
                ok = False
            entries.append({
                "card": card_id,
                "x": x,
                "y": y,
                "count": int(action["count"] or 1),
                "official_x": ox,
                "official_y": oy,
                "condition": action["condition"],
                "level": action["level"],
            })
        if not ok:
            skipped.append((name, ["spawn inside the enemy tower footprint"]))
            continue

        links = [
            a["arg"] for a in wave["actions"]
            if a["type"] == "SpawnWaveStandalone" and a["arg"]
        ]
        exported.append({
            "name": name,
            "end_condition": wave["end_condition"],
            "restart_after": _restart_threshold(wave),
            "spawns": entries,
            "types": sorted(types),
            "standalone": links,
        })

    return exported, skipped, clashes


def _restart_threshold(wave):
    """The numeric bound of a RestartWave/EndWave `time_in_line >= N` condition."""
    for action in wave["actions"]:
        if action["type"] in ("RestartWave", "EndWave"):
            cond = action["condition"]
            if ">=" in cond:
                tail = cond.split(">=", 1)[1].strip()
                if tail.isdigit():
                    return int(tail)
    return None


def render(exported, skipped, clashes):
    lines = [
        '"""Official PvE wave scripts, generated from the decrypted pve_waves.csv.',
        "",
        "GENERATED FILE -- do not edit by hand. Regenerate with:",
        "",
        "    python tools\\\\build_official_pve_waves.py",
        "",
        "Source: assets/csv_logic/pve_waves.csv, content version 15.535.86.",
        f"Exported {len(exported)} of 50 named waves; {len(skipped)} are skipped",
        "because their action set is outside the deploy-expressible subset (Clan",
        "Boat tower logic, card-delivery campaign chains) or because a spawn",
        "point lands inside the 1v1 opponent tower footprint.",
        "",
        "`time_in_line` is in milliseconds, exactly as the official table writes it.",
        "The engine runs at 20 ticks/second, so a caller must convert:",
        "`tick = time_in_line_ms * 20 // 1000`.",
        '"""',
        "",
        "TICKS_PER_SECOND = 20",
        "",
        f"GRID_WIDTH = {GRID_WIDTH}",
        f"GRID_HEIGHT = {GRID_HEIGHT}",
        f"CELL_UNITS = {CELL_UNITS}",
        f"WORLD_HEIGHT = {WORLD_HEIGHT}",
        "",
        "WAVES_BY_NAME = {",
    ]
    for wave in exported:
        lines.append(f"    {wave['name']!r}: {{")
        lines.append(f"        'name': {wave['name']!r},")
        lines.append(f"        'end_condition': {wave['end_condition']!r},")
        lines.append(f"        'restart_after_ms': {wave['restart_after']!r},")
        lines.append(f"        'action_types': {wave['types']!r},")
        lines.append(f"        'standalone': {wave['standalone']!r},")
        lines.append("        'spawns': [")
        for s in wave["spawns"]:
            lines.append(
                f"            {{'card': {s['card']}, 'x': {s['x']}, 'y': {s['y']}, "
                f"'count': {s['count']}, 'time_in_line_ms': {s['condition']!r}, "
                f"'official_x': {s['official_x']}, 'official_y': {s['official_y']}}},"
            )
        lines.append("        ],")
        lines.append("    },")
    lines.append("}")
    lines.append("")
    lines.append(f"EXCLUDED_WAVES = {sorted(n for n, _ in skipped)!r}")
    lines.append("")
    lines.append("# Official spawns that are illegal on the 1v1 arena because they land")
    lines.append("# inside an opponent princess-tower footprint. Each entry is")
    lines.append("# (wave, card name, official x, official y, arena x, arena y, cell x, cell y).")
    lines.append("# These are NOT nudged: moving them would invent a wave the official")
    lines.append("# script never specified. They are the one hard blocker on a real")
    lines.append("# single-sided PvE map, and they are pinned by a test.")
    detail = sorted(set(clashes))
    lines.append("TOWER_FOOTPRINT_CLASHES = [")
    for entry in detail:
        lines.append(f"    {entry!r},")
    lines.append("]")
    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    exported, skipped, clashes = build()
    text = render(exported, skipped, clashes)
    with open(OUT_PATH, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    print(f"wrote {OUT_PATH}")
    print(f"  exported waves : {len(exported)}")
    print(f"  skipped waves  : {len(skipped)}")
    print(f"  tower clashes  : {len(clashes)}")
    for name, types in skipped:
        print(f"    skip {name:34s} {types}")
