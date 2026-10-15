"""Pure PvE wave scripts: multi-batch pushes, a level ramp, and safe interludes.

What this module replaces
-------------------------
The roguelike schedule in `pve_roguelike` fires a handful of single cards at
evenly spaced ticks, all at level 11. The mode the specification describes is
the opposite: each wave arrives as **2-4 dense batches** three to five seconds
apart, each batch a *swarm* of many units, and the monsters' character level
climbs from 1 at the first wave to 15 at the finale.

How a batch is expressed
------------------------
The engine has no spawn primitive, so a batch is a set of ordinary card deploys
armed through `LocalBattleRuntime.scheduleCard`, each resolved by card id
against the wave owner's own hand. Two constraints from the native resolver
shape every table here and are enforced by `validate()`:

* `resolve_card_deploy_request` refuses a scheduled deploy unless the named card
  appears **exactly once** in the wave owner's hand at fire time. So a wave deck
  carries no duplicate ids, and a wave that wants ten skeletons of the same
  card must arm ten *separate* deploys of that one id on ten different cells.
* `decode` in `mobile_fair` and `MatchConfig` both reject any deck that is not
  exactly eight cards. So each dungeon's roster is eight cards and every card a
  wave names is one of them.

Level ramp
----------
The wave's monsters are spawned at a level that climbs with the wave index,
`level_for_wave(wave_index)` -> 1..15. The level is carried per spawn record and
handed to the engine by `scheduleCard(owner, card, x, y, tick, level)`; the
runtime rejects a level outside 1..15 rather than silently clamping it, so a
table that mis-states one fails loudly on the device instead of quietly spawning
level-11 monsters all run.
"""

import random

GRID_WIDTH = 18
GRID_HEIGHT = 32
CELL_UNITS = 1000
WORLD_HEIGHT = GRID_HEIGHT * CELL_UNITS
TICKS_PER_SECOND = 20

# The river sits between rows 15 and 16. Everything at row 17 or below is the
# enemy half, which is where the specification wants the spawn area: rows 18-24,
# i.e. y = 18000..24000. `SPAWN_ROW_MIN`/`SPAWN_ROW_MAX` are that window and
# `validate` refuses a table that spawns outside it.
BRIDGE_ROW = 15
SPAWN_ROW_MIN = 18
SPAWN_ROW_MAX = 24

LEFT_LANE = 2
CENTRE_LANE = 8
RIGHT_LANE = 14

# A wave is 2-4 batches. Each batch is 3-5 seconds after the previous one.
MIN_BATCHES = 2
MAX_BATCHES = 4
BATCH_GAP_TICKS_MIN = 3 * TICKS_PER_SECOND      # 60
BATCH_GAP_TICKS_MAX = 5 * TICKS_PER_SECOND      # 100

# The run is ten waves and the monsters climb from level 1 to level 15 across it.
WAVE_COUNT = 10
LEVEL_MIN = 1
LEVEL_MAX = 15

# The safe interlude between waves: the next wave's first batch is this many
# seconds after the previous wave is declared cleared, and the three-pick-one
# buff dialog runs inside it. Fifteen seconds is the specification's floor and
# also the dialog's countdown, so a player who reads every option to the last
# second still answers inside the window.
INTERLUDE_SECONDS_MIN = 10
INTERLUDE_SECONDS_MAX = 15
INTERLUDE_SECONDS = 15
INTERLUDE_TICKS = INTERLUDE_SECONDS * TICKS_PER_SECOND

# Ticks between a wave's batches plus the interlude: the schedule is a fixed
# spine, because the *arrival* of a wave is decided by the clear detection in
# the runner, not by a precomputed tick. `WAVE_SPACING` is the spacing the
# authored table is expanded onto, so it is also the upper bound on how long a
# wave may take to arrive: a wave whose batches span `BATCH_MAX_TICKS` and whose
# interlude is `INTERLUDE_TICKS` must still fit, or the precomputed spine would
# schedule the next wave's first batch before the interlude had elapsed.
WAVE_SPACING = 620
OPENING_DELAY = 100

# The longest a wave's own batches can span: four batches, three gaps of five
# seconds each.
BATCH_MAX_TICKS = (MAX_BATCHES - 1) * BATCH_GAP_TICKS_MAX

# The floor the spine has to respect for a cleared wave's interlude to fit
# before the next wave's first batch, measured from the last batch of a wave.
REQUIRED_SPACING = BATCH_MAX_TICKS + INTERLUDE_TICKS + 1
if WAVE_SPACING < REQUIRED_SPACING:
    raise ValueError("WAVE_SPACING %d is below the %d the batches and a %d-tick "
                     "interlude need" % (WAVE_SPACING, REQUIRED_SPACING,
                                         INTERLUDE_TICKS))

STAR_THRESHOLDS = ((0.75, 3), (0.35, 2))
STAR_FLOOR = 1


def cell_to_world(x, y):
    """Native world centre of an arena cell, matching arena.cell_to_world."""
    if not (0 <= x < GRID_WIDTH and 0 <= y < GRID_HEIGHT):
        raise ValueError("grid cell (%d,%d) is outside the %dx%d arena"
                         % (x, y, GRID_WIDTH, GRID_HEIGHT))
    return x * CELL_UNITS + CELL_UNITS // 2, y * CELL_UNITS + CELL_UNITS // 2


# --------------------------------------------------------------------------
# Card ids, all from the shipped competitive card_support table.
# --------------------------------------------------------------------------
GOBLINS = 26000002
SPEAR_GOBLINS = 26000019
BLOWDART_GOBLIN = 26000040
ANGRY_BARBARIANS = 26000043
SKELETONS = 26000010
SKELETON_ARMY = 26000012
BATS = 26000049
BALLOON = 26000006
GIANT_SKELETON = 26000020
BARBARIANS = 26000008
GOLEM = 26000009
BOWLER = 26000034
DARK_PRINCE = 26000027
MINIONS = 26000005
HOG_RIDER = 26000021
MEGA_KNIGHT = 26000055
ROYAL_GIANT = 26000024
INFERNO_DRAGON = 26000037
FIRECRACKER = 26000064

HEAL = 28000016
RAGE_SPELL = 28000002

# The two reinforcement spells a buff can hand to the board. Both live in the
# wave seat's deck, which is the only seat whose deck this module owns: the
# player brought their own eight cards, so a reward issued on their seat could
# not name a card the engine holds. A reward deployed by the wave seat lands on
# the board exactly the same way a reward from the player would.
#
# The roster is therefore four spawn units + two boss anchors + these two
# spells = eight, which is the ceiling `MatchConfig` and `decode` both impose.
REWARD_CARDS = (HEAL, RAGE_SPELL)

# --------------------------------------------------------------------------
# Level ramp
# --------------------------------------------------------------------------
def level_for_wave(wave_index):
    """The monster character level for a 1-based wave index.

    Linear from `LEVEL_MIN` at wave 1 to `LEVEL_MAX` at wave `WAVE_COUNT`, so
    the run's ten waves cover 1..15 exactly and each step is either 1 or 2. A
    boss is one level above its wave's floor, clamped at the cap, so the finale
    genuinely fields level-15 units.
    """
    if not 1 <= wave_index <= WAVE_COUNT:
        raise ValueError("wave index %r is outside 1..%d" % (wave_index, WAVE_COUNT))
    span = LEVEL_MAX - LEVEL_MIN
    return LEVEL_MIN + round(span * (wave_index - 1) / (WAVE_COUNT - 1))


def _boss_level(wave_index):
    return min(LEVEL_MAX, level_for_wave(wave_index) + 1)


# --------------------------------------------------------------------------
# Table primitives
# --------------------------------------------------------------------------
def _spawn(lane_x, row, card, level, is_boss=False, label=""):
    """One authored unit inside a batch."""
    return {
        "lane_x": lane_x,
        "row": row,
        "card": card,
        "level": int(level),
        "is_boss": bool(is_boss),
        "label": label,
    }


def _one(lane_x, row, card, level, is_boss=False, label=""):
    """A batch holding exactly one unit."""
    return [_spawn(lane_x, row, card, level, is_boss, label)]


def _batch(spawns):
    """One authored batch: a list of units that land together."""
    return tuple(spawns)


def _wave(index, batches, label, tier):
    """One authored wave: a 1-based index, its batches and its tier name."""
    if not batches:
        raise ValueError("wave %d has no batches" % index)
    return {"index": index, "batches": tuple(tuple(b) for b in batches),
            "label": label, "tier": tier}


TIERS = (
    (1, 3, "前哨试炼"),
    (4, 6, "精英突袭"),
    (7, 9, "高压混编"),
    (10, 10, "终极决战"),
)


def _tier_name(index):
    for first, last, name in TIERS:
        if first <= index <= last:
            return name
    raise ValueError("wave %d is outside the authored %d-wave descent"
                     % (index, WAVE_COUNT))


def _fan(lane, count, step=2, low=1, high=16):
    """`count` distinct columns centred on `lane`, never clamped into a repeat.

    The columns are computed first and then shifted as a *group* so the fan keeps
    its spacing even at the arena edges. Clamping each column individually is
    what produced two units on the same cell: a fan of five from column 2 ran off
    the left edge, the out-of-range members all clamped to 1, and the engine
    refuses an overlapping deploy.
    """
    spacing = max(1, int(step))
    raw = [lane + (index - (count - 1) / 2.0) * spacing for index in range(count)]
    shift = 0
    if raw and raw[0] < low:
        shift = low - raw[0]
    elif raw and raw[-1] > high:
        shift = high - raw[-1]
    out = []
    for value in raw:
        column = int(round(value + shift))
        out.append(max(low, min(high, column)))
    return out


def _swarm(lane, row, card, count, level, step=2):
    """`count` copies of one card fanned across columns on a single row."""
    return [_spawn(column, row, card, level)
            for column in _fan(lane, count, step)]


def _both_lanes(row, card, count, level, step=2):
    """The same swarm down both lanes -- the wave's characteristic pincer."""
    return _swarm(LEFT_LANE, row, card, count, level, step) + \
        _swarm(RIGHT_LANE, row, card, count, level, step)


# --------------------------------------------------------------------------
# The three dungeons
#
# Roster per dungeon is exactly eight cards: four spawn units the waves use, two
# boss anchors and two reward spells. The flavour is carried by *how densely*
# each wave schedules those units, not by adding cards.
# --------------------------------------------------------------------------
GOBLIN_DECK = (GOBLINS, SPEAR_GOBLINS, BLOWDART_GOBLIN, ANGRY_BARBARIANS,
               MEGA_KNIGHT, ROYAL_GIANT, RAGE_SPELL, HEAL)
UNDEAD_DECK = (SKELETONS, SKELETON_ARMY, BATS, BALLOON,
               GIANT_SKELETON, INFERNO_DRAGON, RAGE_SPELL, HEAL)
HEAVY_DECK = (BARBARIANS, GOLEM, BOWLER, DARK_PRINCE,
              MEGA_KNIGHT, ROYAL_GIANT, RAGE_SPELL, HEAL)

DECK_BY_DUNGEON = {
    "pve_goblin": GOBLIN_DECK,
    "pve_undead": UNDEAD_DECK,
    "pve_heavy": HEAVY_DECK,
}


def _goblin_waves():
    """Cheap, fast, swarming. Pressure is count and speed, never armour."""
    out = []
    for index in range(1, WAVE_COUNT + 1):
        level = level_for_wave(index)
        batches = [
            # Batch 1 opens down both lanes: the probe wave.
            _batch(_swarm(LEFT_LANE, SPAWN_ROW_MIN, GOBLINS, 4, level) +
                   _swarm(RIGHT_LANE, SPAWN_ROW_MIN, GOBLINS, 3, level)),
            # Batch 2 brings the spear line with a single cheap ranged escort.
            _batch(_swarm(LEFT_LANE, SPAWN_ROW_MIN + 2, SPEAR_GOBLINS, 3, level) +
                   _swarm(RIGHT_LANE, SPAWN_ROW_MIN + 2, SPEAR_GOBLINS, 3, level) +
                   _one(CENTRE_LANE, SPAWN_ROW_MIN + 1, BLOWDART_GOBLIN, level)),
            # Batch 3 is the barbarian charge down the centre.
            _batch(_swarm(CENTRE_LANE, SPAWN_ROW_MIN, ANGRY_BARBARIANS, 4, level) +
                   _both_lanes(SPAWN_ROW_MIN + 1, GOBLINS, 2, level)),
        ]
        if index >= 7:
            # The late waves gain a fourth batch: the pincer widens.
            batches.append(_batch(
                _swarm(LEFT_LANE, SPAWN_ROW_MIN + 3, SPEAR_GOBLINS, 4, level) +
                _swarm(RIGHT_LANE, SPAWN_ROW_MIN + 3, SPEAR_GOBLINS, 4, level)))
        label = "哥布林狂潮"
        if index == WAVE_COUNT:
            batches[0] = _batch(
                _swarm(LEFT_LANE, SPAWN_ROW_MIN, GOBLINS, 5, level) +
                _swarm(RIGHT_LANE, SPAWN_ROW_MIN, GOBLINS, 5, level))
            batches[1] = _batch(
                _one(LEFT_LANE, SPAWN_ROW_MAX, ROYAL_GIANT, _boss_level(index),
                     is_boss=True, label="狂暴巨人") +
                _swarm(RIGHT_LANE, SPAWN_ROW_MIN + 1, ANGRY_BARBARIANS, 4, level))
            batches[2] = _batch(
                _one(RIGHT_LANE, SPAWN_ROW_MAX, MEGA_KNIGHT, _boss_level(index),
                     is_boss=True, label="狂暴狂人") +
                _swarm(LEFT_LANE, SPAWN_ROW_MIN + 2, SPEAR_GOBLINS, 4, level))
            label = "⚠️ 狂暴双巨魔"
        elif index == 5:
            batches[2] = _batch(
                _swarm(CENTRE_LANE, SPAWN_ROW_MIN, ANGRY_BARBARIANS, 5, level) +
                _one(LEFT_LANE, SPAWN_ROW_MIN + 1, BLOWDART_GOBLIN, level))
            label = "蛮族狂攻"
        out.append(_wave(index, batches, label, _tier_name(index)))
    return tuple(out)


def _undead_waves():
    """Air and summons. The pressure is air a ground-only defence cannot block."""
    out = []
    for index in range(1, WAVE_COUNT + 1):
        level = level_for_wave(index)
        batches = [
            _batch(_swarm(LEFT_LANE, SPAWN_ROW_MIN, SKELETONS, 4, level) +
                   _swarm(RIGHT_LANE, SPAWN_ROW_MIN, SKELETONS, 4, level)),
            _batch(_swarm(CENTRE_LANE, SPAWN_ROW_MIN + 1, BATS, 6, level) +
                   _one(LEFT_LANE, SPAWN_ROW_MIN + 2, SKELETON_ARMY, level)),
            _batch(_swarm(RIGHT_LANE, SPAWN_ROW_MIN, SKELETON_ARMY, 4, level) +
                   _swarm(LEFT_LANE, SPAWN_ROW_MIN + 1, SKELETONS, 4, level)),
        ]
        if index >= 5:
            batches.insert(2, _batch(
                _swarm(LEFT_LANE, SPAWN_ROW_MIN, BALLOON, 2, level) +
                _swarm(RIGHT_LANE, SPAWN_ROW_MIN, BALLOON, 2, level)))
        label = "不死空袭"
        if index == WAVE_COUNT:
            batches[1] = _batch(
                _one(LEFT_LANE, SPAWN_ROW_MAX, GIANT_SKELETON, _boss_level(index),
                     is_boss=True, label="自爆巨型骷髅兵") +
                _swarm(RIGHT_LANE, SPAWN_ROW_MIN, BALLOON, 3, level))
            batches[2] = _batch(
                _one(RIGHT_LANE, SPAWN_ROW_MAX, INFERNO_DRAGON, _boss_level(index),
                     is_boss=True, label="狂暴地狱飞龙") +
                _swarm(LEFT_LANE, SPAWN_ROW_MIN + 1, BATS, 6, level))
            batches[3] = _batch(
                _swarm(CENTRE_LANE, SPAWN_ROW_MIN, SKELETON_ARMY, 4, level) +
                _swarm(RIGHT_LANE, SPAWN_ROW_MIN + 2, SKELETONS, 4, level))
            label = "⚠️ 自爆空地巨擘"
        elif index == 5:
            batches[2] = _batch(
                _one(CENTRE_LANE, SPAWN_ROW_MAX, INFERNO_DRAGON, level) +
                _swarm(LEFT_LANE, SPAWN_ROW_MIN + 1, BATS, 5, level))
            label = "地狱飞龙突袭"
        out.append(_wave(index, batches, label, _tier_name(index)))
    return tuple(out)


def _heavy_waves():
    """Armour and single-target burst. The pressure is how much has to die."""
    out = []
    for index in range(1, WAVE_COUNT + 1):
        level = level_for_wave(index)
        batches = [
            _batch(_swarm(LEFT_LANE, SPAWN_ROW_MIN, BARBARIANS, 4, level) +
                   _swarm(RIGHT_LANE, SPAWN_ROW_MIN, BARBARIANS, 4, level)),
            # The golem wall plus its escort: a lone golem pair with one prince is
            # a trickle, not the "big formation" the specification asks for.
            _batch(_swarm(LEFT_LANE, SPAWN_ROW_MIN + 2, GOLEM, 2, level) +
                   _one(CENTRE_LANE, SPAWN_ROW_MIN, DARK_PRINCE, level) +
                   _swarm(RIGHT_LANE, SPAWN_ROW_MIN + 2, BARBARIANS, 3, level)),
            _batch(_swarm(RIGHT_LANE, SPAWN_ROW_MIN, BARBARIANS, 5, level) +
                   _swarm(LEFT_LANE, SPAWN_ROW_MIN + 1, BOWLER, 3, level)),
        ]
        if index >= 7:
            batches.append(_batch(
                _swarm(CENTRE_LANE, SPAWN_ROW_MIN + 2, GOLEM, 3, level) +
                _one(RIGHT_LANE, SPAWN_ROW_MAX, DARK_PRINCE, level)))
        label = "钢铁推进"
        if index == WAVE_COUNT:
            batches[0] = _batch(
                _swarm(LEFT_LANE, SPAWN_ROW_MIN, BARBARIANS, 5, level) +
                _swarm(RIGHT_LANE, SPAWN_ROW_MIN, BARBARIANS, 5, level))
            batches[1] = _batch(
                _one(LEFT_LANE, SPAWN_ROW_MAX, MEGA_KNIGHT, _boss_level(index),
                     is_boss=True, label="超级皮卡超人") +
                _swarm(RIGHT_LANE, SPAWN_ROW_MIN + 1, GOLEM, 3, level))
            batches[2] = _batch(
                _one(RIGHT_LANE, SPAWN_ROW_MAX, ROYAL_GIANT, _boss_level(index),
                     is_boss=True, label="超级超级骑士") +
                _one(CENTRE_LANE, SPAWN_ROW_MIN, BOWLER, level))
            batches[3] = _batch(
                _swarm(LEFT_LANE, SPAWN_ROW_MIN + 2, BARBARIANS, 5, level) +
                _swarm(RIGHT_LANE, SPAWN_ROW_MIN + 2, BARBARIANS, 5, level))
            label = "⚠️ 钢铁双终结者"
        elif index == 5:
            batches[1] = _batch(
                _swarm(LEFT_LANE, SPAWN_ROW_MIN + 2, GOLEM, 3, level) +
                _one(CENTRE_LANE, SPAWN_ROW_MAX, GOLEM, level))
            label = "哥仑石人压境"
        out.append(_wave(index, batches, label, _tier_name(index)))
    return tuple(out)


DUNGEONS = {
    "pve_goblin": _goblin_waves(),
    "pve_undead": _undead_waves(),
    "pve_heavy": _heavy_waves(),
}

DUNGEON_LABELS = {
    "pve_goblin": "哥布林秘境·极速狂潮",
    "pve_undead": "幽冥墓园·不死空袭",
    "pve_heavy": "重装熔炉·钢铁巨擘",
}

DUNGEON_THEMES = {
    "pve_goblin": "成批蛮族与哥布林蜂拥突进，波次内多批梯次压上",
    "pve_undead": "气球兵与蝙蝠的空袭波次，地面单位无法拦截",
    "pve_heavy": "哥仑石人与暗黑王子的重装压线，血量极高",
}

DUNGEON_BOSSES = {
    "pve_goblin": ("狂暴巨人", "狂暴狂人"),
    "pve_undead": ("自爆巨型骷髅兵", "狂暴地狱飞龙"),
    "pve_heavy": ("超级皮卡超人", "超级超级骑士"),
}

MODES = tuple(DUNGEONS)

# Arena topology. The enemy half keeps its towers in the engine's topology but
# the local runtime relocates them off the playable area; see the "PvE towerless
# arena" section of on_device/python/mobile_fair.py and the native hook of the
# same name in baseline/native_runner/probe/pve_towerless.inc. The location id
# is the standard single-sided PvE arena.
LOCATION_SOLO = 15000199
LOCATION_AI_ALLY = 15000020
PVE_GAME_MODE_ID = 72000018

# The specification's towerless arena: the enemy side's royal towers are moved
# to y = 35000, which is past the 32000-unit world height, so they are never
# rendered and never a legal deploy target, while still existing as engine
# objects so the 40-tick opposing-tower verdict cannot fire.
OFFSCREEN_TOWER_Y = 35000


def arena_location(ai_ally=False):
    """Arena id for a run. The ally toggle switches the map, not the rules."""
    return LOCATION_AI_ALLY if ai_ally else LOCATION_SOLO


def dungeon(key):
    """The ten authored waves of a dungeon."""
    try:
        return DUNGEONS[key]
    except KeyError:
        raise KeyError("unknown PvE dungeon %r; known: %s"
                       % (key, ", ".join(sorted(DUNGEONS)))) from None


def deck_for(key):
    """The eight-card roster a dungeon's waves are drawn from."""
    try:
        return DECK_BY_DUNGEON[key]
    except KeyError:
        raise KeyError("unknown PvE dungeon %r" % (key,)) from None


def wave_start_tick(wave_index):
    """Absolute tick a 1-based wave index begins at.

    This is the *fall-back* arrival, used only when a wave has not reported a
    clear. The runner's clear detection is what normally schedules the next wave,
    so that the 15-second interlude measures from the moment the field emptied.
    """
    if not 1 <= wave_index <= WAVE_COUNT:
        raise ValueError("wave index %r is outside 1..%d" % (wave_index, WAVE_COUNT))
    return OPENING_DELAY + (wave_index - 1) * WAVE_SPACING


def wave_span(key, wave_index):
    """(first batch tick, last spawn tick) of a 1-based wave.

    Both ends are derived from the authored table so the arming loop's horizon
    and the runner's clear check read the same numbers.
    """
    if not 1 <= wave_index <= WAVE_COUNT:
        raise ValueError("wave index %r is outside 1..%d" % (wave_index, WAVE_COUNT))
    for wave in dungeon(key):
        if wave["index"] == wave_index:
            start = wave_start_tick(wave_index)
            return start, start + batch_span(wave)
    raise KeyError("dungeon %r has no wave %d" % (key, wave_index))


def batch_gap(batch_index, rng=None):
    """Ticks between batch `batch_index` and the next one (3-5 seconds)."""
    if batch_index <= 0:
        raise ValueError("batch index must be 1-based")
    rng = rng or random
    return rng.randint(BATCH_GAP_TICKS_MIN, BATCH_GAP_TICKS_MAX)


# --------------------------------------------------------------------------
# Wave-cleared detection
# --------------------------------------------------------------------------
# The tower data ids. A tower is one of these and nothing else, which is what
# makes "the enemy half is empty" answerable from the object list alone. The
# enemy side's towers are relocated off-screen rather than deleted (see the
# towerless arena notes), so they are still in the list -- and are still counted
# as the opponent's towers, which is what keeps the engine from declaring the
# match finished while the run is still going.
KING_TOWER_CARD_ID = 35000000
PRINCESS_TOWER_CARD_ID = 35000001
TOWER_DATA_IDS = (KING_TOWER_CARD_ID, PRINCESS_TOWER_CARD_ID)

# A deployed troop is never a tower, and the engine marks a tower's cardId -1.
# See pve_tower_verdict for the device evidence behind both readings.
CARD_ID_OF_A_TOWER = -1


def is_tower_object(obj):
    """True when one capture object is a royal tower."""
    if not isinstance(obj, dict) or obj.get("null"):
        return False
    for field in ("dataGlobalId", "cardId"):
        value = obj.get(field)
        if value is None:
            continue
        try:
            value = int(value)
        except (TypeError, ValueError):
            continue
        if value in TOWER_DATA_IDS:
            return True
    # Towers carry cardId -1; a deployed troop carries its own card id. A troop
    # that has lost its card id is still not a tower, so this only excludes.
    return obj.get("cardId") == CARD_ID_OF_A_TOWER


def live_enemy_troops(capture, enemy_owner=1):
    """How many of the wave seat's units are still on the field.

    Counted from the raw capture rather than the policy observation because the
    observation is filtered to the player's own owner and would drop every enemy
    unit -- which is exactly the half this measures.

    Only the wave seat's units count. The player's own troops are not holding a
    wave open: a player who keeps a defensive unit on the field has not failed to
    clear anything, and counting their units would make a wave un-clearable for
    as long as they hold a single troop.
    """
    if not capture:
        return 0
    count = 0
    for obj in capture.get("objects") or ():
        if not obj or obj.get("null") or is_tower_object(obj):
            continue
        try:
            owner = int(obj.get("owner"))
        except (TypeError, ValueError):
            continue
        if owner != int(enemy_owner):
            continue
        count += 1
    return count


def wave_seed(wave_index):
    """The seed a wave's batch gaps are drawn from.

    Every `expand` has to produce byte-identical records: the schedule is
    *armed*, not just described, and the arming cursor retires a wave whose tick
    has already gone by. A fresh `random` draw per expand made the arming time
    differ from the recorded time, so the first frame armed a slightly different
    table than the one the payload carries -- "boundary passed unarmed" on a wave
    that had not actually been missed. Deriving the seed from the wave index
    makes the expansion a pure function of the table.
    """
    return 0xC0FFEE + int(wave_index)


def batch_span(wave):
    """Total ticks from a wave's first batch to its last unit's deploy."""
    rng = random.Random(wave_seed(wave["index"]))
    elapsed = 0
    for position in range(len(wave["batches"]) - 1):
        elapsed += batch_gap(position + 1, rng)
    return elapsed


def wave_spawn_count(wave):
    """Total units a wave puts on the field, batches included."""
    return sum(len(batch) for batch in wave["batches"])


def expand(key):
    """Flatten a dungeon's waves into absolute-tick spawn records.

    Each record is the shape the arming loop consumes: `tick`, `card`, `x`, `y`,
    `level`, `wave`, `batch`, `is_boss`, `label`. `batch` is the 0-based batch
    index so the runner can report "batch 3 of 4" and detect that the last batch
    has fired. The gap after each batch is drawn from the 3-5 second window
    against `wave_seed`, so two calls with the same table produce the same
    schedule -- which matters because the caller arms against these ticks.
    """
    out = []
    for wave in dungeon(key):
        rng = random.Random(wave_seed(wave["index"]))
        start = wave_start_tick(wave["index"])
        elapsed = 0
        for batch_index, batch in enumerate(wave["batches"]):
            if batch_index:
                elapsed += batch_gap(batch_index, rng)
            for spawn in batch:
                x, y = cell_to_world(spawn["lane_x"], spawn["row"])
                out.append({
                    "tick": start + elapsed,
                    "card": spawn["card"],
                    "x": x,
                    "y": y,
                    "level": spawn["level"],
                    "wave": wave["index"],
                    "batch": batch_index,
                    "batches": len(wave["batches"]),
                    "cycle": wave["index"] - 1,
                    "is_boss": spawn["is_boss"],
                    "label": spawn["label"] or wave["label"],
                })
    out.sort(key=lambda record: (record["tick"], record["x"]))
    return out


def waves_for(mode):
    """The flattened wave list for a dungeon key."""
    return expand(mode)


# --------------------------------------------------------------------------
# The three-pick-one buff pool
# --------------------------------------------------------------------------
# Every entry names a `kind` the runner knows how to apply. Each kind maps onto
# a primitive that genuinely exists, so no option is decorative:
#
#   elixir_rate       writes the player's elixir multiplier into the native
#                     player object (see the native hook of the same name)
#   repair_towers     a Heal spell per royal tower cell, armed on the wave seat
#   reinforce_card    the card joins the player's cycle for the rest of the run
#   rage_aura         a Rage aura over the player's half, armed on the wave seat
#
# `weight` is the relative draw weight; `max_stacks` caps repeats.
DRAFT_DIMENSIONS = ("A", "B", "C", "D")


def _option(oid, dimension, title, description, kind, weight=1, max_stacks=99,
            card=None, payload=None):
    return {"id": oid, "dimension": dimension, "title": title,
            "description": description, "kind": kind, "weight": weight,
            "max_stacks": max_stacks, "card": card, "payload": payload or {}}


DRAFT_A = (
    _option("a_elixir_1", "A", "圣水增压 I", "圣水回复速率提升至 1.2x。",
            "elixir_rate", weight=3, max_stacks=1, payload={"multiplier": 1.2}),
    _option("a_elixir_2", "A", "圣水增压 II", "圣水回复速率提升至 1.5x。",
            "elixir_rate", weight=2, max_stacks=1, payload={"multiplier": 1.5}),
    _option("a_elixir_3", "A", "圣水增压 III", "圣水回复速率提升至 2.0x。",
            "elixir_rate", weight=2, max_stacks=1, payload={"multiplier": 2.0}),
)

DRAFT_B = (
    _option("b_repair", "B", "国王塔抢修",
            "国王塔与存活的公主塔立刻恢复 30% 最大生命值。",
            "repair_towers", weight=3, max_stacks=9, payload={"fraction": 0.30}),
    _option("b_rage", "B", "全场狂暴",
            "我方半场覆盖狂暴光环，友军攻速与移速提升 35%。",
            "rage_aura", weight=3, max_stacks=3, card=28000002,
            payload={"speed": 0.35}),
)

DRAFT_C = (
    _option("c_reinforce_heavy", "C", "外援神卡·重装",
            "重型外援单位加入你的手牌轮换，立刻可用。",
            "reinforce_card", weight=3, card=None),
    _option("c_reinforce_boss", "C", "外援神卡·首领",
            "首领级外援单位加入你的手牌轮换，立刻可用。",
            "reinforce_card", weight=2, card=None),
)

DRAFT_D = (
    _option("d_elixir_now", "D", "瞬间圣水补给",
            "圣水立刻补满。", "elixir_fill", weight=3, max_stacks=9),
)

DRAFT_POOL = DRAFT_A + DRAFT_B + DRAFT_C + DRAFT_D
DRAFT_POOL_BY_ID = {option["id"]: option for option in DRAFT_POOL}

MIN_DIMENSIONS_PER_DRAFT = 2
DRAFT_CHOICES = 3

# One interlude between each pair of the ten waves, so nine drafts per run.
DRAFT_COUNT = WAVE_COUNT - 1

# The dialog's countdown, in seconds. It equals the interlude's floor so the
# dialog always resolves before the next wave's first batch is due.
DRAFT_TIMEOUT_SECONDS = INTERLUDE_SECONDS


def _draw(options, rng, count):
    """Weighted sample of `count` distinct options."""
    remaining = list(options)
    picked = []
    for _ in range(min(count, len(remaining))):
        total = sum(max(0, option["weight"]) for option in remaining)
        if total <= 0:
            picked.extend(remaining[:count - len(picked)])
            break
        threshold = rng.uniform(0, total)
        upto = 0.0
        chosen = remaining[-1]
        for option in remaining:
            upto += max(0, option["weight"])
            if threshold <= upto:
                chosen = option
                break
        picked.append(chosen)
        remaining.remove(chosen)
    return picked


def reinforcement_card(state, rank=0):
    """The card a `reinforce_card` pick grants.

    Rank 0 is the dungeon's first boss anchor and rank 1 its second, so the two
    reinforcement options offer the two heaviest units the run already has to
    answer. The card comes from the run's own roster, so the deploy that grants
    it can actually resolve.
    """
    bosses = [spawn["card"] for spawn in dungeon(state["dungeon"])[-1]["batches"][0]
              if spawn["is_boss"]]
    if not bosses:
        bosses = [spawn["card"] for batch in dungeon(state["dungeon"])[-1]["batches"]
                  for spawn in batch if spawn["is_boss"]]
    if not bosses:
        return None
    return bosses[min(rank, len(bosses) - 1)]


def eligible(pool_option, state):
    """True when an upgrade can still be taken given the run so far."""
    taken = state["stacks"].get(pool_option["id"], 0)
    if taken >= pool_option["max_stacks"]:
        return False
    kind = pool_option["kind"]
    if kind == "elixir_rate":
        # The multipliers are a ladder, not a set: only the next rung up is on
        # offer, so a dialog never presents two contradictory rates.
        return float(pool_option["payload"]["multiplier"]) > float(state["elixir_rate"])
    if kind == "reinforce_card":
        rank = 1 if pool_option["id"] == "c_reinforce_boss" else 0
        card = reinforcement_card(state, rank)
        if card is None or card in state["extra_cards"]:
            return False
    return True


def draft_options(state, rng=None, count=DRAFT_CHOICES):
    """Three upgrades for one interlude, mixed across dimensions."""
    rng = rng or random
    pool = [option for option in DRAFT_POOL if eligible(option, state)]
    if len(pool) < count:
        pool = list(DRAFT_POOL)
    by_dimension = {}
    for option in pool:
        by_dimension.setdefault(option["dimension"], []).append(option)

    picked = []
    used_dimensions = set()
    dimension_order = list(by_dimension)
    rng.shuffle(dimension_order)
    for dimension in dimension_order:
        if len(picked) >= count or len(used_dimensions) >= MIN_DIMENSIONS_PER_DRAFT:
            break
        candidates = [option for option in by_dimension[dimension]
                      if option not in picked]
        if not candidates:
            continue
        picked.append(_draw(candidates, rng, 1)[0])
        used_dimensions.add(dimension)
    while len(picked) < count:
        candidates = [option for option in pool if option not in picked]
        if not candidates:
            break
        fresh = [option for option in candidates
                 if option["dimension"] not in used_dimensions]
        chosen = _draw(fresh or candidates, rng, 1)[0]
        picked.append(chosen)
        used_dimensions.add(chosen["dimension"])
    return picked[:count]


def initial_state(dungeon_key, evolvable_cards=None):
    """The buff state a run starts from, before any draft."""
    return {
        "dungeon": dungeon_key,
        "wave": 1,
        "generation": 0,
        "stacks": {},
        "elixir_rate": 1.0,
        "rage_speed": 0.0,
        "repair_fraction": 0.0,
        "elixir_fill_pending": 0,
        "extra_cards": [],
        "evolvable_cards": list(evolvable_cards or []),
        "history": [],
    }


def apply_choice(state, option_id, tick=0):
    """Activate a picked upgrade and return the new state."""
    option = DRAFT_POOL_BY_ID.get(option_id)
    if option is None:
        raise KeyError("unknown draft option %r" % (option_id,))
    stacks = dict(state["stacks"])
    stacks[option_id] = stacks.get(option_id, 0) + 1
    state = dict(state)
    state["stacks"] = stacks
    kind = option["kind"]
    if kind == "elixir_rate":
        state["elixir_rate"] = float(option["payload"]["multiplier"])
    elif kind == "repair_towers":
        state["repair_fraction"] = max(float(state["repair_fraction"]),
                                       float(option["payload"]["fraction"]))
    elif kind == "rage_aura":
        state["rage_speed"] = float(state["rage_speed"]) + \
            float(option["payload"]["speed"])
    elif kind == "elixir_fill":
        state["elixir_fill_pending"] = int(state["elixir_fill_pending"]) + 1
    elif kind == "reinforce_card":
        rank = 1 if option_id == "c_reinforce_boss" else 0
        card = reinforcement_card(state, rank)
        if card is not None and card not in state["extra_cards"]:
            state["extra_cards"] = list(state["extra_cards"]) + [card]
    state["history"] = list(state["history"]) + [{
        "id": option_id, "kind": kind, "title": option["title"],
        "wave": state["wave"], "generation": state["generation"], "tick": int(tick),
    }]
    return state


def effective_elixir_rate(state, tick=None):
    """The elixir regeneration multiplier in effect."""
    return float(state["elixir_rate"])


def stars_for(hp_ratio):
    """Star rating from the king's remaining health fraction."""
    for threshold, stars in STAR_THRESHOLDS:
        if hp_ratio >= threshold:
            return stars
    return STAR_FLOOR


# --------------------------------------------------------------------------
# The board-touching effects a draft pays out
# --------------------------------------------------------------------------
KING_ROW = 3
PRINCESS_ROW = 6
REWARD_TICK_OFFSET = 3

RAGE_SPELL = 28000002


def royal_tower_cells():
    """The three royal tower cells, king first."""
    return ((CENTRE_LANE, KING_ROW),
            (LEFT_LANE, PRINCESS_ROW),
            (RIGHT_LANE, PRINCESS_ROW))


def _reward(card, lane, row, wave_index, tick, offset, label, reward):
    """One board-touching buff deploy.

    A reward is the *player's* buff, so it is always deployed at `LEVEL_MAX`
    rather than at the wave's monster level: a Heal or a Rage that lands at level
    3 in the opening waves would visibly do less than the same buff at level 15,
    which would make an option's strength depend on when it was picked.
    """
    x, y = cell_to_world(lane, row)
    return {"tick": int(tick), "offset": int(offset), "card": card, "x": x, "y": y,
            "level": LEVEL_MAX, "cycle": wave_index - 1, "is_boss": False,
            "label": label, "wave": wave_index, "reward": reward}


def repair_deploys(state, wave_index, tick=0):
    """A Heal spell per royal tower for 国王塔抢修."""
    if not state.get("repair_fraction"):
        return []
    return [_reward(HEAL, lane, row, wave_index, tick, REWARD_TICK_OFFSET + order,
                    "国王塔抢修", "repair")
            for order, (lane, row) in enumerate(royal_tower_cells())]


def rage_deploys(state, wave_index, tick=0):
    """Rage auras over the player's half for 全场狂暴."""
    if not state.get("rage_speed"):
        return []
    # A permanent aura cannot be expressed as a single spell, so it is armed as a
    # sweep across the player's three lanes each interlude: what the player can
    # observe is their own half glowing with Rage and their units hitting
    # faster, which is the promise the option makes.
    return [_reward(RAGE_SPELL, lane, row, wave_index, tick,
                    REWARD_TICK_OFFSET + order, "全场狂暴", "rage")
            for order, (lane, row) in enumerate(((LEFT_LANE, 8), (CENTRE_LANE, 8),
                                                 (RIGHT_LANE, 8)))]


def reward_deploys(state, wave_index, tick):
    """Every board-touching effect a pending draft owes, in arming order."""
    return repair_deploys(state, wave_index, tick) + \
        rage_deploys(state, wave_index, tick)


def consume_rewards(state):
    """Clear the one-shot rewards a run has paid out."""
    state = dict(state)
    state["repair_fraction"] = 0.0
    state["elixir_fill_pending"] = 0
    return state


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------
# The enemy towers are relocated off-screen rather than deleted, so they are no
# longer a deploy obstacle and a wave may spawn anywhere in the enemy half. The
# table is checked against the *player's* towers instead, which are untouched.
TOWER_CELLS = set()


def validate(mode=None):
    """Return a list of problems; empty means the tables are playable."""
    problems = []
    for key in ((mode,) if mode else MODES):
        deck = deck_for(key)
        if len(set(deck)) != len(deck):
            problems.append("%s deck contains duplicate cards" % key)
        if len(deck) != 8:
            problems.append("%s deck holds %d cards; the engine accepts exactly 8"
                            % (key, len(deck)))
        for card in deck:
            if 27000000 <= card < 28000000:
                problems.append("%s deck contains support card %d; the wave seat "
                                "must not deploy towers" % (key, card))
        allowed = set(deck)
        waves = dungeon(key)
        if len(waves) != WAVE_COUNT:
            problems.append("%s has %d waves; the descent is %d"
                            % (key, len(waves), WAVE_COUNT))
        for position, wave in enumerate(waves, start=1):
            if wave["index"] != position:
                problems.append("%s wave %d is indexed %d; the table must be "
                                "contiguous from 1" % (key, position, wave["index"]))
            if wave["tier"] != _tier_name(position):
                problems.append("%s wave %d is filed under tier %r"
                                % (key, position, wave["tier"]))
            batches = wave["batches"]
            if not (MIN_BATCHES <= len(batches) <= MAX_BATCHES):
                problems.append("%s wave %d has %d batches; the spec asks for %d-%d"
                                % (key, position, len(batches), MIN_BATCHES,
                                   MAX_BATCHES))
            for batch_index, batch in enumerate(batches):
                if not batch:
                    problems.append("%s wave %d batch %d spawns nothing"
                                    % (key, position, batch_index))
                cells = set()
                for spawn in batch:
                    if spawn["card"] not in allowed:
                        problems.append(
                            "%s wave %d batch %d uses card %d outside its deck, so "
                            "the deploy could never resolve"
                            % (key, position, batch_index, spawn["card"]))
                    if not (SPAWN_ROW_MIN <= spawn["row"] <= SPAWN_ROW_MAX):
                        problems.append(
                            "%s wave %d batch %d spawns at row %d, outside the "
                            "enemy spawn window %d-%d"
                            % (key, position, batch_index, spawn["row"],
                               SPAWN_ROW_MIN, SPAWN_ROW_MAX))
                    if not (LEVEL_MIN <= spawn["level"] <= LEVEL_MAX):
                        problems.append(
                            "%s wave %d batch %d spawns level %d, outside 1..15"
                            % (key, position, batch_index, spawn["level"]))
                    cell = (spawn["lane_x"], spawn["row"])
                    if cell in cells:
                        problems.append(
                            "%s wave %d batch %d stacks two units on cell %r; the "
                            "engine rejects an overlapping deploy"
                            % (key, position, batch_index, cell))
                    cells.add(cell)
                    if cell in TOWER_CELLS:
                        problems.append("%s wave %d batch %d spawns inside a tower "
                                        "footprint at (%d,%d)"
                                        % (key, position, batch_index,
                                           spawn["lane_x"], spawn["row"]))
        final = waves[-1]
        bosses = [spawn for batch in final["batches"] for spawn in batch
                  if spawn["is_boss"]]
        if len(bosses) < 2:
            problems.append("%s final wave is not the twin-boss finale" % key)
        for earlier in waves[:-1]:
            if any(spawn["is_boss"] for batch in earlier["batches"]
                   for spawn in batch):
                problems.append("%s wave %d carries a boss outside the finale"
                                % (key, earlier["index"]))
        named = tuple(spawn["label"] for spawn in bosses)
        if named != DUNGEON_BOSSES[key]:
            problems.append("%s final bosses are %r; the spec names %r"
                            % (key, list(named), list(DUNGEON_BOSSES[key])))
    seen_ids = [option["id"] for option in DRAFT_POOL]
    if len(set(seen_ids)) != len(seen_ids):
        problems.append("DRAFT_POOL contains duplicate option ids")
    for dimension in DRAFT_DIMENSIONS:
        if not any(option["dimension"] == dimension for option in DRAFT_POOL):
            problems.append("draft pool dimension %r is empty" % dimension)
    for key in MODES:
        deck = set(deck_for(key))
        probe = initial_state(key)
        for option in DRAFT_POOL:
            if option["kind"] != "reinforce_card":
                continue
            rank = 1 if option["id"] == "c_reinforce_boss" else 0
            card = reinforcement_card(probe, rank)
            if card is None or card not in deck:
                problems.append("%s draft option %r grants card %r, which is not in "
                                "the %s deck" % (key, option["id"], card, key))
        for option in DRAFT_POOL:
            if option["card"] is not None and option["card"] not in deck:
                problems.append("%s draft option %r needs card %d, which is not in "
                                "the %s deck" % (key, option["id"], option["card"], key))
    return problems


def level_of_wave(key, wave_index):
    """The monster level a dungeon fields at a 1-based wave index."""
    for wave in dungeon(key):
        if wave["index"] == wave_index:
            levels = [spawn["level"] for batch in wave["batches"] for spawn in batch]
            return max(levels)
    raise KeyError("dungeon %r has no wave %d" % (key, wave_index))