"""Win condition for the wave-driven tower-defence modes.

The engine resolves every mode the same way: it counts the opposing side's
towers and declares a winner when yours are gone. That is correct for a duel
and exactly backwards for a defence, where the player has no enemy tower to
destroy and the only thing that can end the run is losing their own king.

`BattleEnvV1._terminal` is upstream, read-only code that the build re-stages
from `native_runner/` on every run, so the verdict cannot be corrected there
without losing the edit on the next build. It is computed here instead, from
the same per-tick capture the probe already reads, and the runner uses it in
place of the engine's winner.

The rule is the one the mode promises: the run ends when the player's own king
tower falls, and surviving to the schedule's end is a win. A missing king tower
is treated as a loss rather than as "unknown", because a defence with no king
tower has already lost -- but a tower that is merely off-screen in an early
capture is not a loss, so the caller must only consult this once the world has
been observed at least once.
"""

from collections.abc import Mapping

KING_TOWER_CARD_ID = 35000000
PRINCESS_TOWER_CARD_ID = 35000001

# The support-card class every tower troop belongs to, and the id the tower
# troop itself is configured with. The capture reports the character data id
# (35000000/35000001) for the tower object, which is what identifies it.
TOWER_DATA_IDS = (KING_TOWER_CARD_ID, PRINCESS_TOWER_CARD_ID)

# Towers cannot be recognised by an identity field. Two attempts failed on a
# real device, and both are recorded here so neither is retried:
#
#   "cardId" reads LogicGameObject+0xac, which the engine leaves at -1 for
#   tower characters -- the native crown tally skips those objects for exactly
#   that reason.
#   "dataGlobalId" is simply absent from the objects the offline capture emits.
#   Logged from a live match: every one of the six towers came through as
#   (owner, cardId=-1, dataGlobalId=<absent>) -- e.g.
#   (0, -1, absent, hp=4824, x=9000, y=3000).
#
# So identity is read from geometry, which is what the reference environment
# (native_runner/battle_env.py TOWER_LAYOUT) does for the same arena. These are
# the standard 3+3 tower anchors on the 18x32 grid; the arena is fixed by the
# PvE fixture (location 15000199), so the layout is a constant, not a guess.
TOWER_POSITIONS = {
    (9000, 3000): "king",
    (3500, 6500): "princess",
    (14500, 6500): "princess",
    (9000, 29000): "king",
    (3500, 25500): "princess",
    (14500, 25500): "princess",
}

# A deployed troop can never sit on a tower anchor: the engine rejects a deploy
# that overlaps an existing tower. Matching on the anchor alone is therefore
# safe, and this tolerance only absorbs the rounding between the layout constant
# and the live value.
TOWER_POSITION_TOLERANCE = 1500


def _tower_kind(obj):
    """'king', 'princess' or None for one capture object."""
    if not isinstance(obj, Mapping):
        return None
    # Prefer an explicit identity when a capture does carry one.
    for field in ("dataGlobalId", "cardId"):
        value = obj.get(field)
        if value is None:
            continue
        try:
            value = int(value)
        except (TypeError, ValueError):
            continue
        if value == KING_TOWER_CARD_ID:
            return "king"
        if value == PRINCESS_TOWER_CARD_ID:
            return "princess"
    x, y = obj.get("x"), obj.get("y")
    if x is None or y is None:
        return None
    try:
        x, y = int(x), int(y)
    except (TypeError, ValueError):
        return None
    for (tx, ty), kind in TOWER_POSITIONS.items():
        if abs(x - tx) <= TOWER_POSITION_TOLERANCE and abs(y - ty) <= TOWER_POSITION_TOLERANCE:
            return kind
    return None


def _tower_id(obj):
    """The tower data id of one capture object, or None if it is not a tower."""
    kind = _tower_kind(obj)
    if kind == "king":
        return KING_TOWER_CARD_ID
    if kind == "princess":
        return PRINCESS_TOWER_CARD_ID
    return None


class TowerVerdict:
    """Whether the player still holds a king tower.

    `seen` separates three states that all look alike in a capture: the world was
    never observed, the world was observed before its towers existed, and the
    king tower was observed and is gone. Only the last one is a loss.
    """

    __slots__ = ("seen", "king_alive", "tick", "hp")

    def __init__(self, seen=False, king_alive=True, tick=-1, hp=-1):
        self.seen = seen
        self.king_alive = king_alive
        self.tick = tick
        self.hp = hp

    @property
    def lost(self):
        """True only when a king tower was actually observed and is down."""
        return self.seen and not self.king_alive

    def to_json(self):
        return {"seen": self.seen, "kingAlive": self.king_alive,
                "tick": self.tick, "hp": self.hp, "lost": self.lost}


def read_player_king(capture, player_owner=0, prev_verdict=None):
    """Read the player king's state out of one raw capture frame.

    The capture lists every live object with its owner, card id and hp. A tower
    is recognised by its character data id; the player's is the one whose owner
    is `player_owner`. A king with no hitpoints left is a fallen king.

    A capture that carries no usable object list has not observed the world yet,
    and returns an unseen verdict. So does a capture that has objects but none
    of them is a tower: the engine builds troops and towers on different frames,
    so the first frames of a match legitimately contain units and no tower yet.
    However, if towers were already seen in previous frames, a capture with no
    king tower remaining represents a destroyed king tower.
    """
    if not capture:
        return prev_verdict if (prev_verdict and prev_verdict.lost) else TowerVerdict()
    objects = [obj for obj in (capture.get("objects") or [])
               if obj and not obj.get("null")]
    tick = int(capture.get("tick", -1))
    if not objects:
        if prev_verdict and prev_verdict.seen:
            return TowerVerdict(seen=True, king_alive=False, tick=tick, hp=-1)
        return TowerVerdict()
    towers = [obj for obj in objects if _tower_kind(obj) is not None]
    if not towers:
        # Units are alive but no tower exists.
        # If the king was previously seen, all towers have fallen: this is a loss.
        if (prev_verdict and prev_verdict.seen) or (tick >= 30 and prev_verdict is not None):
            return TowerVerdict(seen=True, king_alive=False, tick=tick, hp=-1)
        return TowerVerdict()
    for obj in objects:
        if not obj or obj.get("null"):
            continue
        if obj.get("owner") != player_owner:
            continue
        if _tower_id(obj) != KING_TOWER_CARD_ID:
            continue
        hp = obj.get("hp")
        if hp is None:
            hp = obj.get("hitpoints")
        alive = True
        if hp is not None:
            try:
                alive = int(hp) > 0
            except (TypeError, ValueError):
                alive = True
        return TowerVerdict(seen=True, king_alive=alive, tick=tick,
                            hp=int(hp) if hp is not None else -1)
    return TowerVerdict(seen=True, king_alive=False, tick=tick, hp=-1)


def pve_winner(verdict):
    """Map a tower verdict onto the winner value the UI expects.

    `showResult` treats 0 as the human seat winning, so a defence that is still
    holding its king tower reports 0 and a broken one reports 1. The engine's
    own value is deliberately not consulted: in a defence it is counting the
    wrong side's towers.
    """
    return 1 if verdict.lost else 0
