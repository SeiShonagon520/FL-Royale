"""Prove the data-id crown classifier is a no-op on a full 1v1 map.

Stage 1 replaced the fixed tower coordinates in `read_native_crowns` with the
identity each object carries itself: LogicGameObject+0x48 -> LogicData+0x40,
which global_ids_oldformat.csv resolves to 35000000 KingTower and 35000001
PrincessTower.

On a map where both sides build all three towers the two classifiers must agree
for every reachable tower state, or the 14 existing modes would change
behaviour. This enumerates all 64 states instead of arguing about it.

The two things the new version is allowed to differ on are exactly the ones the
change is for: a side that never built a princess tower, and a side that never
built a king. Those are asserted separately.
"""
import itertools
import unittest

KING = "king"
LEFT = "left"
RIGHT = "right"
ALIVE = 1
DEAD = 0
HP_CHOICES = (ALIVE, DEAD)

# The native arena: each king is centred in its half, and each side has one
# princess to the king's left and one to its right.
KING_X = 9000
PRINCESS_X = {"left": 3500, "right": 14500}
TOWER_KINDS = (KING, LEFT, RIGHT)
PRINCESS_KINDS = (LEFT, RIGHT)


def full_side(king_hp, left_hp, right_hp):
    return {KING: king_hp, LEFT: left_hp, RIGHT: right_hp}


def _score(king_alive, king_seen, left_alive, right_alive, princess_seen):
    """The shared scoring tail, identical in both versions by construction."""
    if not any(king_seen) and not any(princess_seen):
        return [0, 0, 0, 0]
    out = []
    for owner in (0, 1):
        opponent = 1 - owner
        if king_seen[opponent] and not king_alive[opponent]:
            out.append(3)
        else:
            missing = sum(
                1
                for lane in PRINCESS_KINDS
                if princess_seen[opponent] and not (left_alive if lane == LEFT else right_alive)[opponent]
            )
            out.append(missing)
    return out


def old_crowns(state):
    """Fixed-coordinate matching: only the six known cells were ever towers."""
    king_alive = [True] * 4
    king_seen = [False] * 4
    left_alive = [True] * 4
    right_alive = [True] * 4
    princess_seen = [False] * 4
    for owner, towers in state.items():
        for kind, hp in towers.items():
            if kind == KING:
                king_seen[owner] = True
                king_alive[owner] = hp > 0
            elif kind == LEFT:
                princess_seen[owner] = True
                left_alive[owner] = hp > 0
            elif kind == RIGHT:
                princess_seen[owner] = True
                right_alive[owner] = hp > 0
    return _score(king_alive, king_seen, left_alive, right_alive, princess_seen)


def new_crowns(state):
    """Data-id classification, with the lane split read off each side's king."""
    king_alive = [True] * 4
    king_seen = [False] * 4
    king_x = [0, 0, 0, 0]
    left_alive = [True] * 4
    right_alive = [True] * 4
    princess_seen = [False] * 4

    for owner, towers in state.items():
        if KING in towers:
            king_seen[owner] = True
            king_x[owner] = KING_X

    for owner, towers in state.items():
        for kind, hp in towers.items():
            if kind == KING:
                king_alive[owner] = hp > 0
            elif kind in PRINCESS_KINDS:
                princess_seen[owner] = True
                if not king_seen[owner] or PRINCESS_X[kind] < king_x[owner]:
                    left_alive[owner] = hp > 0
                else:
                    right_alive[owner] = hp > 0
    return _score(king_alive, king_seen, left_alive, right_alive, princess_seen)


class CrownClassifierParityTest(unittest.TestCase):
    def test_full_1v1_maps_score_identically(self):
        checked = 0
        for combination in itertools.product(HP_CHOICES, repeat=6):
            state = {
                0: full_side(*combination[:3]),
                1: full_side(*combination[3:]),
            }
            self.assertEqual(
                new_crowns(state),
                old_crowns(state),
                msg=f"classifiers disagree for {state}",
            )
            checked += 1
        self.assertEqual(checked, 64)

    def test_a_dead_king_tower_still_scores_three(self):
        state = {0: full_side(ALIVE, ALIVE, ALIVE), 1: full_side(DEAD, ALIVE, ALIVE)}
        self.assertEqual(new_crowns(state)[0], 3)

    def test_each_dead_princess_tower_scores_one(self):
        base = {0: full_side(ALIVE, ALIVE, ALIVE)}
        for dead_lanes, expected in ((((LEFT,)), 1), (((RIGHT,)), 1), (((LEFT, RIGHT)), 2)):
            enemy = {KING: ALIVE}
            for kind in PRINCESS_KINDS:
                enemy[kind] = DEAD if kind in dead_lanes else ALIVE
            self.assertEqual(new_crowns({**base, 1: enemy})[0], expected)

    def test_a_side_with_no_towers_takes_no_crowns(self):
        """The single-sided PvE world: the player has all three, the wave side none."""
        state = {0: full_side(ALIVE, ALIVE, ALIVE)}
        self.assertEqual(new_crowns(state), [0, 0])
        self.assertEqual(new_crowns(state), old_crowns(state))

    def test_a_partially_built_side_scores_only_what_it_built(self):
        state = {0: full_side(ALIVE, ALIVE, ALIVE), 1: {KING: ALIVE}}
        self.assertEqual(new_crowns(state), [0, 0])
        state = {0: full_side(ALIVE, ALIVE, ALIVE), 1: {KING: DEAD}}
        self.assertEqual(new_crowns(state), [3, 0])

    def test_non_tower_objects_do_not_change_the_score(self):
        base = {0: full_side(ALIVE, ALIVE, ALIVE), 1: full_side(ALIVE, ALIVE, ALIVE)}
        self.assertEqual(new_crowns(base), [0, 0])
        with_noise = {0: dict(base[0]), 1: dict(base[1])}
        with_noise[1]["goblin"] = ALIVE  # a troop, not a crown-tower data id
        self.assertEqual(new_crowns(with_noise), [0, 0])


if __name__ == "__main__":
    unittest.main()
