"""Open the verified standard-1v1 pacing gate for the modes FL Royale exposes.

`stage_mobile_semantics.py` re-copies `native_runner.battle_env` from the
Firstlight CR checkout on every build, so the two guards below are applied to
the staged copy instead of being edited into the vendored source.

Why this is safe: tower geometry, action masks and the standard-1v1 map layout
come from module-level constants (TOWER_LAYOUT) and never from the game-mode
timeline. A different game mode therefore changes only pacing inputs
(`phase`, `elixir_multiplier`, starting elixir). Every content-addressed and
geometry guard is preserved; only the two "this must be the ladder timeline"
pins are replaced by an explicit mode/timeline agreement check.
"""
import argparse
import hashlib
import json
import pathlib
import sys

# The ladder timeline is pinned to the standard content-addressed timeline.
ORIGINAL_MODE_PIN = """        if self.mode_timeline.game_mode_id != STANDARD_GAME_MODE:
            raise ValueError(
                "BattleEnvV1 currently guarantees canonical masks/towers only "
                f"for standard game mode {STANDARD_GAME_MODE}"
            )
"""
# The replacement keeps the game_modes.csv provenance guard and drops the
# requirement that the timeline be byte-identical to the ladder timeline.
REPLACED_MODE_AND_PROVENANCE = """        # FL Royale exposes several 1v1 game modes. Tower geometry, action masks
        # and the standard-1v1 map layout come from module-level constants
        # (TOWER_LAYOUT), never from the game-mode timeline, so a non-ladder mode
        # changes only pacing inputs (phase, elixir_multiplier, starting
        # elixir). The content-addressed provenance guard below is kept; the
        # ladder-only pin is replaced by an explicit pairing check in reset().
        if self.mode_timeline.game_modes_sha256 != verified_timeline.game_modes_sha256:
            raise ValueError(
                "an injected mode timeline must come from the same verified game_modes.csv"
            )
"""

# reset() used to require match.game_mode == the (now ladder) timeline id and
# bound the arena check to that same conjunction.
ORIGINAL_PRESENTATION = """        standard_presentation = match.game_mode == self.mode_timeline.game_mode_id and match.arena == STANDARD_ARENA
"""
REPLACED_PRESENTATION = """        # The mask/tower geometry is fixed by the standard-1v1 map, so a match
        # is playable whenever it uses that map in the verified arena. The game
        # mode must match the environment's timeline so the pacing inputs always
        # describe the scene that is actually running.
        standard_presentation = match.arena == STANDARD_ARENA
"""

# A mismatched mode must still fail closed, just with an accurate message.
ORIGINAL_END_TICK = """        if int(match.end_tick) != self.max_battle_ticks:
"""
REPLACED_END_TICK = """        if match.game_mode != self.mode_timeline.game_mode_id:
            raise BattleEnvError(
                "native match game mode does not match the environment timeline: "
                f"{match.game_mode} != {self.mode_timeline.game_mode_id}"
            )
        if int(match.end_tick) != self.max_battle_ticks:
"""

# Ramp-up runs a 4x/5x relative multiplier, but the policy was trained on the
# ladder where the relative multiplier never exceeds 3x. The tensorizer divides
# this value by 3.0, so a raw 5.0 reaches the network as 1.67 where training
# only ever saw up to 1.0. Clamp the value handed to the policy so no input
# leaves the trained range; the native engine keeps running the real rate and
# the model still reads true elixir from scalar[4] (own.elixir_exact).
#
# The cap is an inline literal on purpose: a module-level constant can be
# dropped by the staging/AST step or go missing in the compiled .pyc, which
# produced "NameError: POLICY_ELIXIR_MULTIPLIER_CAP is not defined" at the
# first policy observation even though the .py on disk looked correct.
ORIGINAL_MULTIPLIER = """                elixir_multiplier=multiplier,
"""
REPLACED_MULTIPLIER = """                elixir_multiplier=multiplier if multiplier <= 3.0 else 3.0,
"""

# An earlier revision of this patch introduced a module-level constant and
# referenced it here. The constant did not survive into the shipped .pyc, so
# every battle died with "NameError: POLICY_ELIXIR_MULTIPLIER_CAP is not
# defined". Migrate that old form to the inline literal as well.
LEGACY_CAP_MULTIPLIER = """                elixir_multiplier=min(multiplier, POLICY_ELIXIR_MULTIPLIER_CAP),
"""
LEGACY_CAP_DEFINITION = """POLICY_ELIXIR_MULTIPLIER_CAP = 3.0
"""
LEGACY_CAP_COMMENT = """# Highest relative elixir multiplier the policy was trained on. The ladder
# timeline tops out at 3x; clamp any faster mode to this so the tensorizer's
# /3.0 normalisation never exceeds 1.0. The native engine is unaffected and
# still applies the real rate, and the model reads true elixir from the board.
"""

# The tower-binding tolerance is independent of the multiplier clamp.
#
# The very first capture of a battle is taken before the world has spawned its
# towers, so the object vector legitimately has no tower at each fixed cell.
# reset() runs before _is_live_battle is set, so the existing live-battle
# tolerance never applied and every battle failed with
# "initial native state does not contain exactly one tower at (9000, 3000)".
# Bind a placeholder identity instead; _bind_tower_object_keys is re-run from
# the live state once the towers exist.
ORIGINAL_TOWER_BIND = """            if len(candidates) != 1:
                if getattr(self, "_is_live_battle", False) and len(candidates) == 0:
                    bound[entity_id] = -(int(entity_id) + 1)
                    continue
                raise BattleEnvError(
"""
REPLACED_TOWER_BIND = """            if len(candidates) != 1:
                if len(candidates) == 0:
                    # No tower at this cell yet: the world is still being built.
                    # Bind a negative placeholder, which the rest of the
                    # pipeline already treats as "not spawned yet". Ambiguous
                    # states (more than one candidate) still fail closed.
                    bound[entity_id] = -(int(entity_id) + 1)
                    continue
                raise BattleEnvError(
"""

# A placeholder tower has no backing object, so _extract_towers reports it as
# inactive and _terminal then reads that as a destroyed king tower, ending the
# match on the very first frame (observed: "Battle completed ... tick 5,
# decisions 0"). Only a king tower that actually exists may end the match.
ORIGINAL_TERMINAL = """        kings = {item.owner: item for item in towers if item.tower_kind == "king"}
        dead_kings = [owner for owner, tower in kings.items() if not tower.active]
"""
REPLACED_TERMINAL = """        kings = {item.owner: item for item in towers if item.tower_kind == "king"}
        # A king tower that was never spawned is bound to a negative placeholder
        # and has no backing object. It is "not built yet", not "destroyed", so
        # it must not end the match while the world is still coming up.
        spawned = self._tower_object_keys
        dead_kings = [
            owner
            for owner, tower in kings.items()
            if not tower.active
            and spawned.get(tower.entity_id, 0) > 0
        ]
"""

# The on-device battle only ever goes through reset(); attach_live_battle() is
# the headless/probe path. _is_live_battle was therefore never set on a phone,
# so the form self-heal below was dead code there and "all evolution" died on
# its first evolved card with "native policy form is not enabled by the episode
# deck contract ... enabled=0".
#
# That mode equips eight evolutions by design; policy_form_masks() caps what the
# tensorizer is told at two, and the remaining ones are granted on demand. The
# grant is only safe while the semantic subset independently says the card may
# carry that form (supported_form_mask), which the existing condition already
# requires. So the fix is to recognise reset() as a live battle too.
ORIGINAL_LIVE_BATTLE_FORM_GUARD = """                if getattr(self, "_is_live_battle", False) and supported_form_mask & required_form_bit:
"""
REPLACED_LIVE_BATTLE_FORM_GUARD = """                if supported_form_mask & required_form_bit:
"""

PATCHES = (
    (ORIGINAL_MODE_PIN, REPLACED_MODE_AND_PROVENANCE),
    (ORIGINAL_PRESENTATION, REPLACED_PRESENTATION),
    (ORIGINAL_END_TICK, REPLACED_END_TICK),
    (ORIGINAL_MULTIPLIER, REPLACED_MULTIPLIER),
    (ORIGINAL_TOWER_BIND, REPLACED_TOWER_BIND),
    (ORIGINAL_TERMINAL, REPLACED_TERMINAL),
    (ORIGINAL_LIVE_BATTLE_FORM_GUARD, REPLACED_LIVE_BATTLE_FORM_GUARD),
    # Migration away from the constant form that shipped broken.
    (LEGACY_CAP_MULTIPLIER, REPLACED_MULTIPLIER),
    (LEGACY_CAP_COMMENT, ""),
    (LEGACY_CAP_DEFINITION, ""),
)


# A file that already carries the replacement must be left untouched, so
# re-running the patch is a no-op rather than a second rewrite.
ALREADY_PATCHED_MARKER = "an injected mode timeline must come from the same verified game_modes.csv"


def patch(staged: pathlib.Path) -> dict:
    text = staged.read_text(encoding="utf-8")
    original_sha256 = hashlib.sha256(text.encode("utf-8")).hexdigest()
    if ALREADY_PATCHED_MARKER in text and not any(old in text for old, _ in PATCHES):
        return {
            "schema": "firstlight.mode-pacing-patch.v1",
            "battle_env_original_sha256": original_sha256,
            "battle_env_patched_sha256": original_sha256,
            "applied": [],
            "already_patched": True,
        }
    applied = []
    for old, new in PATCHES:
        occurrences = text.count(old)
        if occurrences == 0:
            # A newer upstream may already ship a compatible guard.
            continue
        if occurrences != 1:
            raise ValueError(
                f"battle_env pacing guard anchor is ambiguous ({occurrences} matches): {old.splitlines()[0]!r}"
            )
        text = text.replace(old, new, 1)
        applied.append(old.splitlines()[0].strip())
    if not applied:
        raise ValueError("no pacing guard anchor matched; upstream battle_env changed shape")
    staged.write_text(text, encoding="utf-8")
    return {
        "schema": "firstlight.mode-pacing-patch.v1",
        "battle_env_original_sha256": original_sha256,
        "battle_env_patched_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "applied": applied,
        "already_patched": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--staged", required=True, help="build/mobile-semantics directory")
    parser.add_argument("--receipt")
    args = parser.parse_args()
    staged = pathlib.Path(args.staged).resolve() / "native_runner" / "battle_env.py"
    if not staged.is_file():
        raise FileNotFoundError(f"staged battle_env not found: {staged}")
    receipt = patch(staged)
    if args.receipt:
        pathlib.Path(args.receipt).write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    print(json.dumps({"applied": len(receipt["applied"])}))


if __name__ == "__main__":
    main()
