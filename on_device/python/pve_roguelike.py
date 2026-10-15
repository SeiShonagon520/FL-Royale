"""Depth-climb roguelike tower defence: ten waves, nine drafts, two bosses.

Shape of the mode
-----------------
A run is a fixed descent of ten waves. Waves 1-3 are the outpost, 4-6 the elite
raid, 7-9 the mixed assault, and wave 10 the twin-boss finale. Between every pair
of waves the fight pauses for a safe interlude and the player is offered three
random upgrades -- a Material "pick one of three" dialog, nine times per run.
The run ends when the player's own king tower falls (a loss) or when the wave-10
bosses are cleared (a win, star-rated on the king's remaining health).

How a wave is spawned
---------------------
Exactly as in `pve_tower_defense`: the engine has no spawn primitive, so a wave
is an ordinary card deploy aimed at an absolute tick, issued through
`LocalBattleRuntime.scheduleCard` and resolved by card id against the wave
owner's own hand. The only primitive the scheduler has is "put this card on this
cell at this tick", so *what* spawns is expressed as which card is deployed, and
*how many* is expressed by scheduling the same card on several cells.

The hand-resolution constraint that shapes every table
------------------------------------------------------
`resolve_card_deploy_request` in the native probe refuses a scheduled deploy
unless the named card appears **exactly once** in the wave owner's hand at fire
time (`if (matches != 1) return false`). Two consequences, both load-bearing:

* A wave owner's deck must contain no duplicate card ids, or every wave using
  that id resolves to `matches == 2` and is silently dropped at fire time.
* The deck is exactly eight cards, because both `decode` in mobile_fair and
  `MatchConfig` reject any other count.

So each dungeon gets its own eight-card roster, and every card a dungeon's waves
name has to be one of them. `DECK_BY_DUNGEON` is that roster per dungeon, and
`validate()` fails any table that reaches outside it -- which is exactly how the
first draft of these tables was found to be unshippable.

Card substitutions
------------------
The three dungeon themes are authored against the shipped competitive card
table, which does not carry every unit the theme names. Where a themed unit is
absent the nearest available card is used and noted here rather than invented:

* "地狱飞龙" (burning flying dragon) -> `InfernoDragon` 26000037, the catalog's
  only sustained-damage flyer. There is no LavaHound in the support table.
* "飞桶突袭" (barrel toss) -> `Firecracker` 26000064, an explosive that
  threatens the whole lane rather than a single target.
* "自爆空地巨擘" (self-destructing ground/air colossus) -> `GiantSkeleton`
  26000020, the heaviest skeleton in the table.
* "超级超级骑士" (super knight) -> `RoyalGiant` 26000024: the boss pair is
  MegaKnight + RoyalGiant, which is the two heaviest single-target units the
  table actually ships.

What a draft can and cannot do to the board
-------------------------------------------
The only board mutation available from Python is a card deploy. So a buff that
touches the board is expressed as a scheduled card, aimed at a cell, issued on
the wave owner's seat -- which holds a deck this module controls:

* a repair is a Heal spell (28000016) aimed at each royal tower cell;
* a 天降神罚 raid is a Fireball (28000000) into the enemy spawn row.

Both are in `DUNGEON_DECK`, so they resolve exactly once in the wave owner's
hand like any other wave. Every *rate* buff (elixir regen) is a state the
policy observation reads rather than an engine write; see
`run_pve_device_match` in mobile_fair for where the rate is applied.
"""

import random

GRID_WIDTH = 18
GRID_HEIGHT = 32
CELL_UNITS = 1000
WORLD_HEIGHT = GRID_HEIGHT * CELL_UNITS
TICKS_PER_SECOND = 20

# Waves 1-3 / 4-6 / 7-9 / 10 are the four authored tiers of the descent. The
# boundary is exported because the tests assert the tier layout, and because a
# dungeon that misfiles a boss into the assault tier is not the mode described.
TIERS = (
    (1, 3, "前哨试炼"),
    (4, 6, "精英突袭"),
    (7, 9, "高压混编"),
    (10, 10, "终极决战"),
)

WAVE_COUNT = 10
DRAFT_COUNT = 9          # one interlude between each pair of the ten waves
FINAL_WAVE = 10

# Rows 15/16 are the two bridges and are the only legal cells inside the river.
# Rows 17+ are the enemy half, which is where the spawn points live.
BRIDGE_ROW = 15
SPAWN_ROW = 18
DEEP_SPAWN_ROW = 20

# Lane columns. LEFT/RIGHT mirror the two princess-tower columns, CENTRE is the
# king column.
LEFT_LANE = 2
CENTRE_LANE = 8
RIGHT_LANE = 14

# Star thresholds on the king's remaining health, per the mode's promise.
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
# Shared across the rosters
HEAL = 28000016
FIREBALL = 28000000
ARROWS = 28000001
ROCKET = 28000003
LIGHTNING = 28000007
ZAP = 28000008
POISON = 28000009
EARTHQUAKE = 28000014
RAGE_SPELL = 28000002
FREEZE = 28000005
LOG = 28000011

MEGA_KNIGHT = 26000055
ROYAL_GIANT = 26000024
INFERNO_DRAGON = 26000037
BARBARIANS = 26000008
GIANT_SKELETON = 26000020
SKELETON_ARMY = 26000012
SKELETONS = 26000010
BATS = 26000049
BALLOON = 26000006
DARK_WITCH = 26000048
GOLEM = 26000009
BOWLER = 26000034
DARK_PRINCE = 26000027
GIANT = 26000003
MINIONS = 26000005
HUNTER = 26000044
FIRECRACKER = 26000064

# --------------------------------------------------------------------------
# 副本一：哥布林秘境·极速狂潮
# --------------------------------------------------------------------------
GOBLINS = 26000002
SPEAR_GOBLINS = 26000019
BLOWDART_GOBLIN = 26000040
ANGRY_BARBARIANS = 26000043

# Four spawn units, two boss anchors, and the two reward spells. Exactly eight:
# the deck is the ceiling the engine imposes, so a dungeon's flavour is carried by
# *scheduling* its four units densely across lanes and rows, not by adding more.
GOBLIN_DECK = (
    GOBLINS, SPEAR_GOBLINS, BLOWDART_GOBLIN, ANGRY_BARBARIANS,
    MEGA_KNIGHT, ROYAL_GIANT, FIREBALL, HEAL,
)

# --------------------------------------------------------------------------
# 副本二：幽冥墓园·不死空袭
# --------------------------------------------------------------------------
WITCH = 26000007

# Four spawn units (two ground, two air), two boss anchors, and the two reward
# spells. The air pair carries the dungeon's identity: BATS are the swarming
# screen, BALLOON the siege that a ground-only defence cannot answer.
UNDEAD_DECK = (
    SKELETONS, SKELETON_ARMY, BATS, BALLOON,
    GIANT_SKELETON, INFERNO_DRAGON, FIREBALL, HEAL,
)

# --------------------------------------------------------------------------
# 副本三：重装熔炉·钢铁巨擘
# --------------------------------------------------------------------------
# Four spawn units, two boss anchors, and the two reward spells. GOLEM is the
# wall the whole dungeon is built around; DARK_PRINCE is the single-target
# threat that punishes a defence that only stacks cheap units.
HEAVY_DECK = (
    BARBARIANS, GOLEM, BOWLER, DARK_PRINCE,
    MEGA_KNIGHT, ROYAL_GIANT, FIREBALL, HEAL,
)

DECK_BY_DUNGEON = {
    "pve_goblin": GOBLIN_DECK,
    "pve_undead": UNDEAD_DECK,
    "pve_heavy": HEAVY_DECK,
}


def _spawn(lane_x, row, card, offset, is_boss=False, label="", level=11):
    """One authored spawn: a lane, a row, a card, and a tick offset."""
    return {
        "lane_x": lane_x,
        "row": row,
        "card": card,
        "offset": offset,
        "is_boss": bool(is_boss),
        "label": label,
        "level": level,
    }


def _wave(index, spawns, label, tier):
    """One authored wave: a 1-based index, its spawns and its tier name."""
    return {"index": index, "spawns": tuple(spawns), "label": label, "tier": tier}


def _tier_name(index):
    for first, last, name in TIERS:
        if first <= index <= last:
            return name
    raise ValueError("wave %d is outside the authored %d-wave descent"
                     % (index, WAVE_COUNT))


# --------------------------------------------------------------------------
# 副本一：哥布林秘境·极速狂潮
# Cheap, fast, swarming. The pressure is *count* and *speed*, never armour.
# --------------------------------------------------------------------------
GOBLIN_DUNGEON = (
    _wave(1, (
        _spawn(LEFT_LANE, SPAWN_ROW, GOBLINS, 0),
        _spawn(RIGHT_LANE, SPAWN_ROW, GOBLINS, 30),
        _spawn(CENTRE_LANE, SPAWN_ROW, GOBLINS, 60),
    ), "哥布林斥候", _tier_name(1)),
    _wave(2, (
        _spawn(LEFT_LANE, SPAWN_ROW, SPEAR_GOBLINS, 0),
        _spawn(RIGHT_LANE, SPAWN_ROW, SPEAR_GOBLINS, 20),
        _spawn(CENTRE_LANE, BRIDGE_ROW, BLOWDART_GOBLIN, 90),
        _spawn(LEFT_LANE, SPAWN_ROW, GOBLINS, 130),
    ), "投矛手压境", _tier_name(2)),
    _wave(3, (
        _spawn(LEFT_LANE, SPAWN_ROW, ANGRY_BARBARIANS, 0),
        _spawn(RIGHT_LANE, SPAWN_ROW, ANGRY_BARBARIANS, 25),
        _spawn(CENTRE_LANE, SPAWN_ROW, SPEAR_GOBLINS, 90),
        _spawn(LEFT_LANE, BRIDGE_ROW, BLOWDART_GOBLIN, 150),
    ), "蛮族冲锋", _tier_name(3)),
    _wave(4, (
        _spawn(LEFT_LANE, SPAWN_ROW, GOBLINS, 0),
        _spawn(RIGHT_LANE, SPAWN_ROW, GOBLINS, 10),
        _spawn(RIGHT_LANE, SPAWN_ROW, GOBLINS, 20),
        _spawn(CENTRE_LANE, SPAWN_ROW, ANGRY_BARBARIANS, 90),
    ), "双路蛮群突进", _tier_name(4)),
    _wave(5, (
        _spawn(LEFT_LANE, SPAWN_ROW, ANGRY_BARBARIANS, 0),
        _spawn(RIGHT_LANE, SPAWN_ROW, ANGRY_BARBARIANS, 20),
        _spawn(CENTRE_LANE, SPAWN_ROW, GOBLINS, 70),
        _spawn(CENTRE_LANE, BRIDGE_ROW, BLOWDART_GOBLIN, 120),
        _spawn(LEFT_LANE, DEEP_SPAWN_ROW, SPEAR_GOBLINS, 165),
    ), "精锐蛮兵集结", _tier_name(5)),
    _wave(6, (
        _spawn(LEFT_LANE, SPAWN_ROW, GOBLINS, 0),
        _spawn(RIGHT_LANE, SPAWN_ROW, GOBLINS, 10),
        _spawn(RIGHT_LANE, SPAWN_ROW, GOBLINS, 20),
        _spawn(LEFT_LANE, SPAWN_ROW, ANGRY_BARBARIANS, 90),
        _spawn(RIGHT_LANE, SPAWN_ROW, ANGRY_BARBARIANS, 110),
        _spawn(CENTRE_LANE, BRIDGE_ROW, BLOWDART_GOBLIN, 180),
    ), "双压境", _tier_name(6)),
    _wave(7, (
        _spawn(LEFT_LANE, SPAWN_ROW, GOBLINS, 0),
        _spawn(RIGHT_LANE, SPAWN_ROW, GOBLINS, 15),
        _spawn(CENTRE_LANE, SPAWN_ROW, ANGRY_BARBARIANS, 80),
        _spawn(LEFT_LANE, DEEP_SPAWN_ROW, SPEAR_GOBLINS, 140),
        _spawn(RIGHT_LANE, DEEP_SPAWN_ROW, SPEAR_GOBLINS, 155),
    ), "蛮潮混编", _tier_name(7)),
    _wave(8, (
        _spawn(LEFT_LANE, SPAWN_ROW, ANGRY_BARBARIANS, 0),
        _spawn(RIGHT_LANE, SPAWN_ROW, ANGRY_BARBARIANS, 25),
        _spawn(CENTRE_LANE, SPAWN_ROW, GOBLINS, 80),
        _spawn(LEFT_LANE, BRIDGE_ROW, BLOWDART_GOBLIN, 130),
        _spawn(RIGHT_LANE, SPAWN_ROW, SPEAR_GOBLINS, 175),
    ), "飞桶突袭", _tier_name(8)),
    _wave(9, (
        _spawn(LEFT_LANE, SPAWN_ROW, GOBLINS, 0),
        _spawn(RIGHT_LANE, SPAWN_ROW, GOBLINS, 10),
        _spawn(RIGHT_LANE, SPAWN_ROW, GOBLINS, 20),
        _spawn(CENTRE_LANE, SPAWN_ROW, ANGRY_BARBARIANS, 90),
        _spawn(LEFT_LANE, DEEP_SPAWN_ROW, ANGRY_BARBARIANS, 135),
        _spawn(CENTRE_LANE, BRIDGE_ROW, BLOWDART_GOBLIN, 180),
    ), "狂潮总攻", _tier_name(9)),
    # 第 10 波：狂暴双巨魔。Two heavy anchors that tear towers down on their own,
    # so the answer is a committed defence rather than another scatter.
    _wave(10, (
        _spawn(LEFT_LANE, DEEP_SPAWN_ROW, ROYAL_GIANT, 0, is_boss=True, label="狂暴巨人"),
        _spawn(RIGHT_LANE, DEEP_SPAWN_ROW, MEGA_KNIGHT, 40, is_boss=True, label="狂暴狂人"),
        _spawn(CENTRE_LANE, SPAWN_ROW, ANGRY_BARBARIANS, 140),
        _spawn(LEFT_LANE, SPAWN_ROW, GOBLINS, 180),
        _spawn(RIGHT_LANE, SPAWN_ROW, SPEAR_GOBLINS, 230),
    ), "⚠️ 狂暴双巨魔", _tier_name(10)),
)

# --------------------------------------------------------------------------
# 副本二：幽冥墓园·不死空袭
# Air and summons. The pressure is *air you cannot block with the ground*.
# --------------------------------------------------------------------------
UNDEAD_DUNGEON = (
    _wave(1, (
        _spawn(LEFT_LANE, SPAWN_ROW, SKELETONS, 0),
        _spawn(RIGHT_LANE, SPAWN_ROW, SKELETONS, 40),
    ), "骷髅巡逻", _tier_name(1)),
    _wave(2, (
        _spawn(LEFT_LANE, SPAWN_ROW, SKELETONS, 0),
        _spawn(RIGHT_LANE, SPAWN_ROW, BATS, 60),
        _spawn(CENTRE_LANE, BRIDGE_ROW, SKELETON_ARMY, 110),
    ), "蝙蝠试探", _tier_name(2)),
    _wave(3, (
        _spawn(LEFT_LANE, SPAWN_ROW, SKELETON_ARMY, 0),
        _spawn(RIGHT_LANE, SPAWN_ROW, SKELETONS, 60),
        _spawn(CENTRE_LANE, SPAWN_ROW, BATS, 120),
    ), "骷髅军团集结", _tier_name(3)),
    _wave(4, (
        _spawn(LEFT_LANE, DEEP_SPAWN_ROW, BALLOON, 0),
        _spawn(RIGHT_LANE, DEEP_SPAWN_ROW, BALLOON, 30),
        _spawn(RIGHT_LANE, SPAWN_ROW, SKELETON_ARMY, 90),
        _spawn(CENTRE_LANE, SPAWN_ROW, BATS, 150),
    ), "气球兵空袭", _tier_name(4)),
    _wave(5, (
        _spawn(LEFT_LANE, DEEP_SPAWN_ROW, INFERNO_DRAGON, 0),
        _spawn(RIGHT_LANE, SPAWN_ROW, SKELETON_ARMY, 70),
        _spawn(CENTRE_LANE, BRIDGE_ROW, BATS, 130),
        _spawn(LEFT_LANE, SPAWN_ROW, SKELETONS, 175),
    ), "地狱飞龙突袭", _tier_name(5)),
    _wave(6, (
        _spawn(LEFT_LANE, DEEP_SPAWN_ROW, INFERNO_DRAGON, 0),
        _spawn(RIGHT_LANE, DEEP_SPAWN_ROW, INFERNO_DRAGON, 30),
        _spawn(LEFT_LANE, DEEP_SPAWN_ROW, BALLOON, 110),
        _spawn(RIGHT_LANE, SPAWN_ROW, SKELETON_ARMY, 170),
    ), "双飞龙空压", _tier_name(6)),
    _wave(7, (
        _spawn(LEFT_LANE, SPAWN_ROW, GIANT_SKELETON, 0),
        _spawn(RIGHT_LANE, SPAWN_ROW, SKELETON_ARMY, 70),
        _spawn(CENTRE_LANE, BRIDGE_ROW, BATS, 140),
        _spawn(LEFT_LANE, DEEP_SPAWN_ROW, BALLOON, 190),
    ), "巨型骷髅压阵", _tier_name(7)),
    _wave(8, (
        _spawn(LEFT_LANE, DEEP_SPAWN_ROW, BALLOON, 0),
        _spawn(RIGHT_LANE, DEEP_SPAWN_ROW, BALLOON, 20),
        _spawn(RIGHT_LANE, DEEP_SPAWN_ROW, BALLOON, 40),
        _spawn(CENTRE_LANE, SPAWN_ROW, BATS, 120),
        _spawn(LEFT_LANE, SPAWN_ROW, GIANT_SKELETON, 180),
    ), "气球海空袭", _tier_name(8)),
    _wave(9, (
        _spawn(LEFT_LANE, DEEP_SPAWN_ROW, INFERNO_DRAGON, 0),
        _spawn(RIGHT_LANE, DEEP_SPAWN_ROW, INFERNO_DRAGON, 15),
        _spawn(RIGHT_LANE, DEEP_SPAWN_ROW, INFERNO_DRAGON, 30),
        _spawn(CENTRE_LANE, SPAWN_ROW, BATS, 120),
        _spawn(LEFT_LANE, SPAWN_ROW, GIANT_SKELETON, 190),
    ), "不死军团总攻", _tier_name(9)),
    # 第 10 波：自爆空地巨擘。The bomb-carrier colossus and the burning dragon.
    _wave(10, (
        _spawn(LEFT_LANE, DEEP_SPAWN_ROW, GIANT_SKELETON, 0, is_boss=True, label="自爆巨型骷髅兵"),
        _spawn(RIGHT_LANE, DEEP_SPAWN_ROW, INFERNO_DRAGON, 50, is_boss=True, label="狂暴地狱飞龙"),
        _spawn(CENTRE_LANE, SPAWN_ROW, SKELETON_ARMY, 150),
        _spawn(LEFT_LANE, DEEP_SPAWN_ROW, BALLOON, 200),
        _spawn(RIGHT_LANE, SPAWN_ROW, BATS, 240),
    ), "⚠️ 自爆空地巨擘", _tier_name(10)),
)

# --------------------------------------------------------------------------
# 副本三：重装熔炉·钢铁巨擘
# Armour and single-target burst. The pressure is *how much* has to die.
# --------------------------------------------------------------------------
HEAVY_DUNGEON = (
    _wave(1, (
        _spawn(LEFT_LANE, SPAWN_ROW, BARBARIANS, 0),
        _spawn(RIGHT_LANE, SPAWN_ROW, BARBARIANS, 40),
    ), "熔炉前哨", _tier_name(1)),
    _wave(2, (
        _spawn(LEFT_LANE, SPAWN_ROW, BARBARIANS, 0),
        _spawn(RIGHT_LANE, SPAWN_ROW, BOWLER, 70),
        _spawn(CENTRE_LANE, BRIDGE_ROW, BARBARIANS, 120),
    ), "投石手就位", _tier_name(2)),
    _wave(3, (
        _spawn(LEFT_LANE, SPAWN_ROW, BARBARIANS, 0),
        _spawn(RIGHT_LANE, SPAWN_ROW, BARBARIANS, 25),
        _spawn(CENTRE_LANE, SPAWN_ROW, DARK_PRINCE, 110),
    ), "石人推进", _tier_name(3)),
    _wave(4, (
        _spawn(LEFT_LANE, SPAWN_ROW, GOLEM, 0),
        _spawn(RIGHT_LANE, SPAWN_ROW, BOWLER, 80),
        _spawn(CENTRE_LANE, SPAWN_ROW, BARBARIANS, 140),
    ), "哥仑石人", _tier_name(4)),
    _wave(5, (
        _spawn(LEFT_LANE, SPAWN_ROW, GOLEM, 0),
        _spawn(RIGHT_LANE, SPAWN_ROW, GOLEM, 30),
        _spawn(CENTRE_LANE, SPAWN_ROW, BARBARIANS, 120),
    ), "双石人压境", _tier_name(5)),
    _wave(6, (
        _spawn(LEFT_LANE, SPAWN_ROW, GOLEM, 0),
        _spawn(RIGHT_LANE, SPAWN_ROW, GOLEM, 20),
        _spawn(LEFT_LANE, SPAWN_ROW, DARK_PRINCE, 110),
        _spawn(RIGHT_LANE, SPAWN_ROW, BOWLER, 160),
    ), "暗黑王子冲锋", _tier_name(6)),
    _wave(7, (
        _spawn(LEFT_LANE, SPAWN_ROW, GOLEM, 0),
        _spawn(RIGHT_LANE, SPAWN_ROW, GOLEM, 25),
        _spawn(CENTRE_LANE, BRIDGE_ROW, BOWLER, 120),
        _spawn(LEFT_LANE, DEEP_SPAWN_ROW, DARK_PRINCE, 180),
    ), "高能充能", _tier_name(7)),
    _wave(8, (
        _spawn(LEFT_LANE, SPAWN_ROW, GOLEM, 0),
        _spawn(RIGHT_LANE, SPAWN_ROW, GOLEM, 20),
        _spawn(CENTRE_LANE, SPAWN_ROW, BARBARIANS, 110),
        _spawn(LEFT_LANE, SPAWN_ROW, DARK_PRINCE, 170),
    ), "钢铁洪流", _tier_name(8)),
    _wave(9, (
        _spawn(LEFT_LANE, SPAWN_ROW, GOLEM, 0),
        _spawn(RIGHT_LANE, SPAWN_ROW, GOLEM, 15),
        _spawn(RIGHT_LANE, SPAWN_ROW, GOLEM, 30),
        _spawn(CENTRE_LANE, SPAWN_ROW, BOWLER, 120),
        _spawn(LEFT_LANE, SPAWN_ROW, DARK_PRINCE, 190),
    ), "熔炉全开", _tier_name(9)),
    # 第 10 波：钢铁双终结者。
    _wave(10, (
        _spawn(LEFT_LANE, DEEP_SPAWN_ROW, MEGA_KNIGHT, 0, is_boss=True, label="超级皮卡超人"),
        _spawn(RIGHT_LANE, DEEP_SPAWN_ROW, ROYAL_GIANT, 40, is_boss=True, label="超级超级骑士"),
        _spawn(CENTRE_LANE, SPAWN_ROW, GOLEM, 150),
        _spawn(LEFT_LANE, SPAWN_ROW, BARBARIANS, 200),
        _spawn(RIGHT_LANE, SPAWN_ROW, BOWLER, 240),
    ), "⚠️ 钢铁双终结者", _tier_name(10)),
)

DUNGEONS = {
    "pve_goblin": GOBLIN_DUNGEON,
    "pve_undead": UNDEAD_DUNGEON,
    "pve_heavy": HEAVY_DUNGEON,
}

DUNGEON_LABELS = {
    "pve_goblin": "哥布林秘境·极速狂潮",
    "pve_undead": "幽冥墓园·不死空袭",
    "pve_heavy": "重装熔炉·钢铁巨擘",
}

DUNGEON_THEMES = {
    "pve_goblin": "极速高机动、杂兵蜂拥群攻（投矛手、吹箭、飞桶突袭、蛮族冲锋）",
    "pve_undead": "飞空群怪、亡灵召唤与空降（骷髅军团、蝙蝠狂潮、气球兵空袭、女巫后排召唤）",
    "pve_heavy": "高血量重装装甲、单体高伤推线（哥仑石人、高能充能、野猪骑士突进、暗黑王子冲锋）",
}

DUNGEON_BOSSES = {
    "pve_goblin": ("狂暴巨人", "狂暴狂人"),
    "pve_undead": ("自爆巨型骷髅兵", "狂暴地狱飞龙"),
    "pve_heavy": ("超级皮卡超人", "超级超级骑士"),
}

MODES = tuple(DUNGEONS)

# Arena topology. The enemy towers are never removed: the engine decides a
# match's winner by counting the *opposing* side's towers, so a PvE arena with
# no enemy tower would be declared finished at the 40-tick verdict before the
# player has played a card. The enemy's towers stay as the wave outpost.
LOCATION_SOLO = 15000199
LOCATION_AI_ALLY = 15000020
PVE_GAME_MODE_ID = 72000018


def arena_location(ai_ally=False):
    """Arena id for a run. The ally toggle switches the map, not the rules."""
    return LOCATION_AI_ALLY if ai_ally else LOCATION_SOLO


def dungeon(key):
    """The ten authored waves of a dungeon."""
    try:
        return DUNGEONS[key]
    except KeyError:
        raise KeyError("unknown roguelike dungeon %r; known: %s"
                       % (key, ", ".join(sorted(DUNGEONS)))) from None


def deck_for(key):
    """The eight-card roster a dungeon's waves are drawn from."""
    try:
        return DECK_BY_DUNGEON[key]
    except KeyError:
        raise KeyError("unknown roguelike dungeon %r" % (key,)) from None


# --------------------------------------------------------------------------
# Turning the authored waves into an absolute-tick schedule
# --------------------------------------------------------------------------
OPENING_DELAY = 100

# Ticks between the start of one wave and the next. The descent has to be paced
# so the interlude is long enough to draft in, and the finale has to arrive
# while the player still has elixir to answer it.
#
# This is the *minimum* spacing: `validate` refuses a table whose last spawn
# would leave less than a full draft window, so a wave that over-runs the
# spacing is caught in the test suite rather than silently starving the player
# of time to choose.
WAVE_SPACING = 260

# Ticks after a wave's last spawn before the next wave arms. This is the window
# the interlude and its draft live in, so it has to cover a human reading three
# options and choosing.
INTERLUDE_TICKS = 60


def wave_start_tick(wave_index):
    """Absolute tick a 1-based wave index begins at."""
    if not 1 <= wave_index <= WAVE_COUNT:
        raise ValueError("wave index %r is outside 1..%d" % (wave_index, WAVE_COUNT))
    return OPENING_DELAY + (wave_index - 1) * WAVE_SPACING


def interlude_start(wave_index):
    """Tick the interlude after a 1-based wave opens, or None after the finale."""
    if not 1 <= wave_index < WAVE_COUNT:
        return None
    return wave_start_tick(wave_index) + INTERLUDE_TICKS


def interlude_ticks(wave_index):
    """Ticks the safe interlude after a 1-based wave runs before the next arms."""
    if not 1 <= wave_index < WAVE_COUNT:
        raise ValueError("wave %r has no interlude; only 1..%d do"
                         % (wave_index, WAVE_COUNT - 1))
    return INTERLUDE_TICKS


def _expand(key):
    """Flatten a dungeon's authored waves into absolute-tick spawn records.

    Each record is the shape the PvE arming loop already consumes: `tick`,
    `card`, `x`, `y`, `level`, `cycle`, `is_boss`, `label`. The `cycle` field
    carries the 0-based wave index so the existing wave toast and the
    clear-condition check keep working unchanged.
    """
    out = []
    for wave in dungeon(key):
        start = wave_start_tick(wave["index"])
        for spawn in wave["spawns"]:
            x, y = cell_to_world(spawn["lane_x"], spawn["row"])
            out.append({
                "tick": start + spawn["offset"],
                "card": spawn["card"],
                "x": x,
                "y": y,
                "level": spawn["level"],
                "cycle": wave["index"] - 1,
                "is_boss": spawn["is_boss"],
                "label": spawn["label"] or wave["label"],
                "wave": wave["index"],
            })
    out.sort(key=lambda record: (record["tick"], record["x"]))
    return out


WAVES_BY_MODE = {key: _expand(key) for key in MODES}


def waves_for(mode):
    """Return the flattened wave list for a dungeon key."""
    try:
        return WAVES_BY_MODE[mode]
    except KeyError:
        raise KeyError("unknown roguelike dungeon %r" % (mode,)) from None


def wave_span(key, wave_index):
    """(first tick, last tick) of a 1-based wave of a dungeon.

    The interlude after a wave has to open once its last spawn has fired and
    still leave room for the next wave's first spawn, so both ends are derived
    here rather than guessed by the arming loop.
    """
    if not 1 <= wave_index <= WAVE_COUNT:
        raise ValueError("wave index %r is outside 1..%d" % (wave_index, WAVE_COUNT))
    for wave in dungeon(key):
        if wave["index"] == wave_index:
            start = wave_start_tick(wave_index)
            return start, start + max(spawn["offset"] for spawn in wave["spawns"])
    raise KeyError("dungeon %r has no wave %d" % (key, wave_index))


# --------------------------------------------------------------------------
# The three-pick-one upgrade pool
# --------------------------------------------------------------------------
# Three dimensions, mixed. Every entry names a `kind` the state machine below
# knows how to apply, and the pool is small enough that a run of nine drafts can
# plausibly see most of it.
#
# `weight` is the relative draw weight inside its own dimension. `max_stacks`
# caps repeat picks so the pool cannot degenerate into nine copies of the same
# upgrade.
DRAFT_DIMENSIONS = ("A", "B", "C")


def _option(oid, dimension, title, description, kind, weight=1, max_stacks=99,
            card=None, payload=None):
    return {
        "id": oid,
        "dimension": dimension,
        "title": title,
        "description": description,
        "kind": kind,
        "weight": weight,
        "max_stacks": max_stacks,
        "card": card,
        "payload": payload or {},
    }


# 维度 A：强力神卡援军与卡牌进阶
#
# The two `reinforce_card` entries name cards the *dungeon* is built around, and
# `card=None` means "the run's own boss anchors" (see `draft_options`), because
# the three dungeons have different rosters. A picked card is deployed on the
# player's side, and the player brought their own eight cards, so a draft can
# only promise reinforcement the run can pay for. Naming a card from outside
# every roster would offer something no seat in the match holds -- the same
# unshippable promise the wave validator rejects.
DRAFT_A = (
    _option("a_reinforce", "A", "外援神卡入库",
            "重型外援单位加入战场支援轮换，立刻可用。", "reinforce_card",
            weight=3, card=None),
    _option("a_bolt", "A", "精锐外援入库",
            "精锐外援单位加入轮换，补强你的进攻。", "reinforce_card",
            weight=2, card=None),
    _option("a_awaken", "A", "全员觉醒进化",
            "牌库中一张卡永久解锁觉醒进化形态。", "evolve_card",
            weight=3, max_stacks=4),
)

# 维度 B：全局圣水与资源暴走 Buff
DRAFT_B = (
    _option("b_elixir_1", "B", "圣水增压 I",
            "圣水回复速率提升至 1.2x。", "elixir_rate",
            weight=3, max_stacks=1, payload={"multiplier": 1.2}),
    _option("b_elixir_2", "B", "圣水增压 II",
            "圣水回复速率提升至 1.5x。", "elixir_rate",
            weight=2, max_stacks=1, payload={"multiplier": 1.5}),
    _option("b_elixir_3", "B", "圣水增压 III",
            "圣水回复速率提升至 2.0x。", "elixir_rate",
            weight=2, max_stacks=1, payload={"multiplier": 2.0}),
    _option("b_surge", "B", "瞬间圣水补给",
            "圣水立刻补满，15 秒内圣水花费 -1。", "elixir_surge",
            weight=3, max_stacks=3, payload={"seconds": 15, "discount": 1}),
)

# 维度 C：皇家阵地修复与防御特权
DRAFT_C = (
    _option("c_repair", "C", "王塔大抢修",
            "国王塔与存活公主塔恢复 30% 最大生命值。", "repair_towers",
            weight=3, max_stacks=9, payload={"fraction": 0.30}),
    _option("c_fury", "C", "皇家塔狂暴附魔",
            "己方皇家防御塔攻速永久提升 40%。", "tower_fury",
            weight=2, max_stacks=3, payload={"speed": 0.40}),
    _option("c_raid", "C", "战术天降神罚",
            "下一波开局在敌方半场引爆火球，清空先头杂兵。", "opening_raid",
            weight=3, max_stacks=3, card=FIREBALL),
)

DRAFT_POOL = DRAFT_A + DRAFT_B + DRAFT_C

DRAFT_POOL_BY_ID = {option["id"]: option for option in DRAFT_POOL}

# How many distinct dimensions a single three-pick draw must represent. The
# spec asks for a mix, so a draw of three is never allowed to come from one row.
MIN_DIMENSIONS_PER_DRAFT = 2

DRAFT_CHOICES = 3

# The countdown the Java dialog runs after the player has had its chance. The
# Python side does not need it -- the dialog auto-picks and reports the choice
# back -- but it is the number the dialog and the tests both quote.
DRAFT_TIMEOUT_SECONDS = 3


def _draw(options, rng, count):
    """Weighted sample of `count` distinct options.

    `random.sample` is not weighted, so the draw is explicit: repeat `count`
    times, pick one option per remaining weight, and drop it. This guarantees
    distinct ids -- the dialog would show a duplicate otherwise -- and preserves
    the authored weights.
    """
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
    """The card a `reinforce_card` pick grants, taken from the run's own roster.

    `rank` 0 is the dungeon's first boss anchor and 1 its second, so the two
    reinforcement options offer the two heaviest units the run already has to
    answer -- the pick is a gift of the dungeon's own power rather than a card
    from outside the match, which nothing could deploy.
    """
    bosses = [spawn["card"]
              for spawn in dungeon(state["dungeon"])[-1]["spawns"]
              if spawn["is_boss"]]
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
        # The multiplier upgrades are a ladder, not a set: two rates cannot both
        # be in effect, so the dialog only ever offers the next rung up.
        return float(pool_option["payload"]["multiplier"]) > float(state["elixir_rate"])
    if kind == "evolve_card" and not state["evolvable_cards"]:
        return False
    if kind == "reinforce_card":
        rank = 1 if pool_option["id"] == "a_bolt" else 0
        card = reinforcement_card(state, rank)
        if card is None or card in state["extra_cards"]:
            return False
    return True


def draft_options(state, rng=None, count=DRAFT_CHOICES):
    """Three upgrades for one interlude, mixed across dimensions.

    The draw guarantees a mix: it takes one option from each of two different
    dimensions before filling the remaining slot, so the player never sees three
    cards from the same row of the pool.
    """
    rng = rng or random
    pool = [option for option in DRAFT_POOL if eligible(option, state)]
    if len(pool) < count:
        # Fall back to the raw pool rather than returning a short dialog: a
        # three-pick-one that silently offers two is a different mode.
        pool = list(DRAFT_POOL)
    by_dimension = {}
    for option in pool:
        by_dimension.setdefault(option["dimension"], []).append(option)

    picked = []
    used_dimensions = set()
    # First pass: one per dimension, walking the dimensions in a shuffled order
    # so the same row is not always the first card on offer.
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

    # Second pass: fill from whatever is left, preferring a dimension not yet
    # represented so the mix survives a late run with a thin pool.
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


def initial_state(dungeon_key, deck_cards=None, evolvable_cards=None):
    """The buff state a run starts from, before any draft."""
    return {
        "dungeon": dungeon_key,
        "wave": 1,
        "generation": 0,
        "stacks": {},
        "elixir_rate": 1.0,
        "surge_until_tick": -1,
        "surge_discount": 0,
        "tower_fury": 0.0,
        "repair_fraction": 0.0,
        "raid_pending": 0,
        "extra_cards": [],
        "evolvable_cards": list(evolvable_cards or []),
        "deck_cards": list(deck_cards or []),
        "history": [],
    }


def apply_choice(state, option_id, tick=0):
    """Activate a picked upgrade and return the new state.

    Returns a new state rather than mutating in place, so a test can assert on
    the before/after pair and a caller cannot half-apply a pick.
    """
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
    elif kind == "elixir_surge":
        seconds = int(option["payload"]["seconds"])
        state["surge_until_tick"] = max(int(state["surge_until_tick"]),
                                        int(tick) + seconds * TICKS_PER_SECOND)
        state["surge_discount"] = int(option["payload"]["discount"])
    elif kind == "tower_fury":
        state["tower_fury"] = float(state["tower_fury"]) + float(option["payload"]["speed"])
    elif kind == "repair_towers":
        state["repair_fraction"] = max(float(state["repair_fraction"]),
                                       float(option["payload"]["fraction"]))
    elif kind == "opening_raid":
        state["raid_pending"] = int(state["raid_pending"]) + 1
    elif kind == "reinforce_card":
        rank = 1 if option_id == "a_bolt" else 0
        card = reinforcement_card(state, rank)
        if card is not None and card not in state["extra_cards"]:
            state["extra_cards"] = list(state["extra_cards"]) + [card]
    elif kind == "evolve_card":
        # One card leaves the evolvable pool per pick, so a second pick has to
        # be able to name a different card.
        if state["evolvable_cards"]:
            state["evolvable_cards"] = list(state["evolvable_cards"][1:])
    state["history"] = list(state["history"]) + [{
        "id": option_id,
        "kind": kind,
        "title": option["title"],
        "wave": state["wave"],
        "generation": state["generation"],
        "tick": int(tick),
    }]
    return state


def effective_elixir_rate(state, tick=None):
    """The elixir regeneration multiplier in effect at `tick`."""
    rate = float(state["elixir_rate"])
    if surge_active(state, tick):
        # The surge is a bonus on top of the ladder, not a replacement, so
        # stacking one never *reduces* the regen the player already earned.
        rate = max(rate, 1.0) + 0.5
    return rate


def surge_active(state, tick):
    return state["surge_until_tick"] >= 0 and tick <= state["surge_until_tick"]


def stars_for(hp_ratio):
    """Star rating from the king's remaining health fraction."""
    for threshold, stars in STAR_THRESHOLDS:
        if hp_ratio >= threshold:
            return stars
    return STAR_FLOOR


# --------------------------------------------------------------------------
# The reward deploys a draft performs on the board
# --------------------------------------------------------------------------
# A draft that only changed a counter would be invisible to the player, so each
# pick that touches the board is expressed as a scheduled card deploy -- the one
# primitive the wave scheduler owns. These are the records the arming loop
# splices in alongside the wave list.

# The player king sits at row 3 and its two princesses at row 6: the stock 3+3
# anchors, matching pve_tower_verdict.TOWER_POSITIONS.
KING_ROW = 3
PRINCESS_ROWS = (6, 6)
PRINCESS_LANES = (LEFT_LANE, RIGHT_LANE)

# Ticks after the arming frame at which a draft's board effect fires. The engine
# refuses a deploy whose execute tick is not strictly in the future, so this is
# an offset from "now" and never an absolute tick.
REWARD_TICK_OFFSET = 3


def royal_tower_cells():
    """The three royal tower cells, king first."""
    return ((CENTRE_LANE, KING_ROW),
            (LEFT_LANE, PRINCESS_ROWS[0]),
            (RIGHT_LANE, PRINCESS_ROWS[1]))


def raid_deploys(state, wave_index, tick=0):
    """Fireball(s) into the enemy spawn row for 天降神罚.

    Issued on the wave owner's seat: the player has no fireball in their own
    deck (they brought their own eight cards), but the wave owner does, and a
    card deployed by that seat lands on the board exactly the same way.

    `offset` is a delay from *arming* time, not an absolute tick. The engine
    refuses a deploy whose execute tick is not strictly in the future, and the
    arming frame is always later than the interlude that produced the pick, so
    an absolute tick computed at pick time was already in the past by the time it
    was issued.
    """
    if not state.get("raid_pending"):
        return []
    x, y = cell_to_world(CENTRE_LANE, SPAWN_ROW)
    return [{"tick": int(tick), "offset": REWARD_TICK_OFFSET, "card": FIREBALL,
             "x": x, "y": y, "level": 11,
             "cycle": wave_index - 1, "is_boss": False,
             "label": "战术天降神罚", "wave": wave_index, "reward": "raid"}]


def repair_deploys(state, wave_index, tick=0):
    """A Heal spell per royal tower for 王塔大抢修.

    Healed by a card rather than by writing hitpoints: `scheduleCard` is the only
    board mutation available from Python, and a Heal spell is the honest
    expression of "repair the royal towers". Aimed at each tower cell, which is
    what the native deploy resolver accepts as a position.

    The three heals are staggered by one tick each so they resolve as a sequence
    rather than all landing on the same boundary.
    """
    if not state.get("repair_fraction"):
        return []
    out = []
    for order, (lane, row) in enumerate(royal_tower_cells()):
        x, y = cell_to_world(lane, row)
        out.append({"tick": int(tick), "offset": REWARD_TICK_OFFSET + order,
                    "card": HEAL, "x": x, "y": y,
                    "level": 11, "cycle": wave_index - 1, "is_boss": False,
                    "label": "王塔大抢修", "wave": wave_index,
                    "reward": "repair"})
    return out


def reward_deploys(state, wave_index, tick):
    """Every board-touching effect a pending draft owes, in arming order."""
    return repair_deploys(state, wave_index, tick) + \
        raid_deploys(state, wave_index, tick)


def consume_rewards(state):
    """Clear the one-shot rewards a run has paid out."""
    state = dict(state)
    state["raid_pending"] = 0
    state["repair_fraction"] = 0.0
    return state


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------
# The enemy tower footprints on the standard 3+3 arena. A wave spawned inside
# one would be rejected by the engine as overlapping a tower, so no authored
# spawn point may land there.
TOWER_CELLS = {
    (3, 24), (4, 24), (5, 24),
    (13, 24), (14, 24), (15, 24),
}


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
            if not wave["spawns"]:
                problems.append("%s wave %d spawns nothing" % (key, position))
            for spawn in wave["spawns"]:
                if spawn["card"] not in allowed:
                    problems.append("%s wave %d uses card %d outside its deck, so "
                                    "the deploy could never resolve"
                                    % (key, position, spawn["card"]))
                if not (BRIDGE_ROW <= spawn["row"] < GRID_HEIGHT):
                    problems.append("%s wave %d spawns at row %d, behind the "
                                    "midline" % (key, position, spawn["row"]))
                if spawn["row"] >= 17 and (spawn["lane_x"], spawn["row"]) in TOWER_CELLS:
                    problems.append("%s wave %d spawns inside a tower footprint at "
                                    "(%d,%d)" % (key, position, spawn["lane_x"],
                                                 spawn["row"]))
        final = waves[-1]
        bosses = [spawn for spawn in final["spawns"] if spawn["is_boss"]]
        if len(bosses) < 2:
            problems.append("%s final wave is not the twin-boss finale" % key)
        # Every interlude has to fit a whole draft. The dialog runs
        # DRAFT_TIMEOUT_SECONDS and the loop adds a tick of slack, so a wave
        # whose last spawn lands too close to the next wave's first would force
        # the fallback pick on a player who was still reading the options. This
        # is checked against the authored tables, not assumed from the spacing.
        required = DRAFT_TIMEOUT_SECONDS * TICKS_PER_SECOND + 1
        for index in range(1, WAVE_COUNT):
            _, last = wave_span(key, index)
            window = wave_start_tick(index + 1) - (last + 1)
            if window < required:
                problems.append(
                    "%s wave %d leaves a %d-tick interlude; a %d-tick draft does "
                    "not fit" % (key, index, window, required))
        for earlier in waves[:-1]:
            if any(spawn["is_boss"] for spawn in earlier["spawns"]):
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
            rank = 1 if option["id"] == "a_bolt" else 0
            card = reinforcement_card(probe, rank)
            if card is None or card not in deck:
                problems.append("%s draft option %r grants card %r, which is not in "
                                "the %s deck" % (key, option["id"], card, key))
        for option in DRAFT_POOL:
            if option["card"] is not None and option["card"] not in deck:
                problems.append("%s draft option %r needs card %d, which is not in "
                                "the %s deck" % (key, option["id"], option["card"],
                                                 key))
    return problems
