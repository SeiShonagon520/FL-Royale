"""2v2 Tactical Multi-Agent Bot Engine (TeamBot) for FL-Royale.

Controls:
- Player 1: AI Teammate (cooperative synergy, defensive assistance, anti-spell overlap)
- Player 2: Enemy AI 1 (aggressive lane pusher)
- Player 3: Enemy AI 2 (tactical control, defense & spell punisher)
"""
from __future__ import annotations

import random
from typing import Any, Mapping

# Standard Clash Royale Card Global IDs
CARDS = {
    # Tanks / Ground Melee
    "KNIGHT": 26000000, "GIANT": 26000003, "PEKKA": 26000004, "MINI_PEKKA": 26000018,
    "VALKYRIE": 26000011, "HOG_RIDER": 26000021, "GOLEM": 26000009, "MEGA_KNIGHT": 26000055,
    "PRINCE": 26000016, "DARK_PRINCE": 26000027, "ROYAL_GIANT": 26000024,
    # Ranged / DPS
    "ARCHERS": 26000001, "MUSKETEER": 26000014, "BABY_DRAGON": 26000015, "WIZARD": 26000017,
    "WITCH": 26000007, "ELECTRO_WIZARD": 26000042, "DART_GOBLIN": 26000040, "FIRECRACKER": 26000064,
    # Swarm / Distract
    "SKELETONS": 26000012, "MINIONS": 26000005, "GOBLINS": 26000002, "SPEAR_GOBLINS": 26000019,
    "BATS": 26000049, "MINION_HORDE": 26000022, "GOBLIN_GANG": 26000041,
    # Spells
    "FIREBALL": 28000000, "ARROWS": 28000001, "ZAP": 28000008, "LOG": 28000011,
    "LIGHTNING": 28000007, "ROCKET": 28000003, "POISON": 28000009,
    # Buildings
    "CANNON": 27000000, "TESLA": 27000006, "INFERNO_TOWER": 27000003, "TOMBSTONE": 27000004
}

TANKS = {CARDS["GIANT"], CARDS["KNIGHT"], CARDS["PEKKA"], CARDS["VALKYRIE"],
         CARDS["HOG_RIDER"], CARDS["GOLEM"], CARDS["MEGA_KNIGHT"], CARDS["PRINCE"],
         CARDS["MINI_PEKKA"], CARDS["ROYAL_GIANT"]}
RANGED = {CARDS["ARCHERS"], CARDS["MUSKETEER"], CARDS["BABY_DRAGON"], CARDS["WIZARD"],
          CARDS["WITCH"], CARDS["ELECTRO_WIZARD"], CARDS["DART_GOBLIN"], CARDS["FIRECRACKER"]}
SWARMS = {CARDS["SKELETONS"], CARDS["MINIONS"], CARDS["GOBLINS"], CARDS["SPEAR_GOBLINS"],
          CARDS["BATS"], CARDS["MINION_HORDE"], CARDS["GOBLIN_GANG"]}
SPELLS = {CARDS["FIREBALL"], CARDS["ARROWS"], CARDS["ZAP"], CARDS["LOG"],
          CARDS["LIGHTNING"], CARDS["ROCKET"], CARDS["POISON"]}
BUILDINGS = {CARDS["CANNON"], CARDS["TESLA"], CARDS["INFERNO_TOWER"], CARDS["TOMBSTONE"]}
# Cards that can actually reach a flying unit. Ground melee, buildings and
# splash spells cannot, so a teammate holding only these has to let air through
# and let the player deal with it rather than burning elixir on a ground card.
ANTI_AIR = {CARDS["BABY_DRAGON"], CARDS["MINIONS"], CARDS["BATS"], CARDS["SPEAR_GOBLINS"],
            CARDS["ARCHERS"], CARDS["MUSKETEER"], CARDS["FIRECRACKER"], CARDS["DART_GOBLIN"],
            CARDS["WIZARD"], CARDS["INFERNO_TOWER"], CARDS["TESLA"], CARDS["CANNON"],
            CARDS["ELECTRO_WIZARD"], CARDS["WITCH"]}

# Pre-defined Archetype Decks for 2v2
TEAMMATE_DECK = (CARDS["VALKYRIE"], CARDS["MUSKETEER"], CARDS["BABY_DRAGON"], CARDS["MINI_PEKKA"],
                 CARDS["FIREBALL"], CARDS["ARROWS"], CARDS["BATS"], CARDS["KNIGHT"])
ENEMY1_DECK = (CARDS["GIANT"], CARDS["WIZARD"], CARDS["ARCHERS"], CARDS["MINIONS"],
               CARDS["ARROWS"], CARDS["PRINCE"], CARDS["SKELETONS"], CARDS["FIREBALL"])
ENEMY2_DECK = (CARDS["PEKKA"], CARDS["ELECTRO_WIZARD"], CARDS["CANNON"], CARDS["ZAP"],
               CARDS["FIREBALL"], CARDS["GOBLIN_GANG"], CARDS["DART_GOBLIN"], CARDS["KNIGHT"])

class BotAgent:
    def __init__(self, owner: int, role: str):
        self.owner = owner
        self.team = 0 if owner in (0, 1) else 1
        self.role = role  # "teammate", "attacker", "defender"
        self.last_action_tick = -100
        self.preferred_lane = 0  # 0: left, 1: right
        # Spells this agent has already committed, and spells the human has
        # already spent. The teammate reads the player's hand and its recent
        # plays so it never answers the same cluster the player is answering.
        self._recent_spells: list[tuple[int, int, int]] = []  # (tick, x, y)
        self._player_spells: list[tuple[int, int, int]] = []

    def decide_action(self, tick: int, ordinary: Mapping[str, Any]) -> dict[str, Any] | None:
        players = ordinary.get("players", [])
        if len(players) <= self.owner:
            return None
        player = players[self.owner]
        elixir = float(player.get("elixir", 0.0))
        hand = player.get("hand", [])
        if not hand:
            return None

        # Respect action cooldown (at least 15 ticks / 1.5s between plays)
        if tick - self.last_action_tick < 15:
            return None

        objects = ordinary.get("objects", [])
        # Filter objects
        friendly_units = [o for o in objects if o and o.get("hp") is not None and o.get("hp", 0) > 0 and
                          (0 if o.get("owner", 0) in (0, 1) else 1) == self.team]
        enemy_units = [o for o in objects if o and o.get("hp") is not None and o.get("hp", 0) > 0 and
                       (0 if o.get("owner", 0) in (0, 1) else 1) != self.team]

        # Read what the human has already committed so a spell is not doubled.
        # This runs even on cooldown frames: the player's plays are not subject
        # to the teammate's cooldown and must be tracked continuously.
        if self.role == "teammate":
            self._track_player_spells(tick, ordinary)
            action = self._decide_teammate(tick, elixir, hand, friendly_units, enemy_units)
        elif self.role == "attacker":
            action = self._decide_attacker(tick, elixir, hand, friendly_units, enemy_units)
        else:
            action = self._decide_defender(tick, elixir, hand, friendly_units, enemy_units)

        if action:
            self.last_action_tick = tick
            # `hand_index` carries the whole hand entry, not a bare slot: the
            # capture compacts the hand array, so the list position is not the
            # native slot and only the entry's own handIndex is authoritative.
            card = action.get("hand_index")
            card_id = card.get("cardId") if isinstance(card, Mapping) else None
            if card_id is not None and int(card_id) in SPELLS:
                self._recent_spells.append(
                    (tick, int(action.get("x", 0)), int(action.get("y", 0))))
        return action

    def _track_player_spells(self, tick: int, ordinary: Mapping[str, Any]) -> None:
        """Record the human's own spell plays so the teammate can avoid them.

        The human plays from the screen, so the only way to know what they have
        already committed is to read the objects their spells left behind. Each
        tracked spell keeps its position, which is what the overlap test needs:
        "the player already fireballed *there*" is a spatial fact, not a
        per-card fact, because either of them may hold a fireball next.
        """
        objects = ordinary.get("objects", []) or []
        players = ordinary.get("players", []) or []
        if not players:
            return
        human = players[0] if players else {}
        hand = human.get("hand", []) or []
        # Only a spell the player can currently see is one they could have just
        # played; a card rotated out of the hand is irrelevant to this frame.
        spell_ids = {int(c["cardId"]) for c in hand
                     if c and c.get("cardId") is not None
                     and int(c["cardId"]) in SPELLS}
        for obj in objects:
            if not obj or int(obj.get("owner", -1)) != 0:
                continue
            if int(obj.get("cardId", -1)) not in spell_ids:
                continue
            x, y = int(obj.get("x", 0)), int(obj.get("y", 0))
            if any(abs(px - x) < 2000 and abs(py - y) < 2000
                   for _, px, py in self._player_spells):
                continue
            self._player_spells.append((tick, x, y))
        # Bound the history: only spells from the last few seconds can still
        # overlap a decision being made now.
        self._player_spells = [s for s in self._player_spells if tick - s[0] <= 60]

    def _find_unclaimed_spell(self, tick: int, hand: list, elixir: float,
                              x: int, y: int,
                              excluded: set[int] | None = None) -> dict[str, Any] | None:
        """A spell worth playing that the player is not already playing.

        Overlap is judged by splash radius rather than by card identity: the
        teammate should take a *different* cluster from the one the player has
        already committed to, and if every candidate lands where the player is
        already answering, it is better to fall through to a defensive unit
        than to double the spell.

        `excluded` carries card ids already rejected this frame. Without it the
        caller immediately re-picks the same overlapping spell through the
        generic "any affordable card" fallback, which reintroduces exactly the
        double-cast this method exists to prevent.
        """
        excluded = excluded or set()
        for card in hand:
            if not card or card.get("cardId") is None:
                continue
            card_id = int(card["cardId"])
            cost = card.get("cost", 3) or 3
            if card_id not in SPELLS or card_id in excluded or elixir < cost:
                continue
            claimed = any(abs(px - x) < 3000 and abs(py - y) < 3000
                          for _, px, py in self._player_spells)
            claimed = claimed or any(
                tick >= t - 20 and abs(px - x) < 3000 and abs(py - y) < 3000
                for t, px, py in self._recent_spells)
            if not claimed:
                return card
        return None

    def _decide_teammate(self, tick: int, elixir: float, hand: list,
                          friendly: list, enemies: list) -> dict[str, Any] | None:
        # Team 0: Home territory is y < 16000
        threats_near_home = [e for e in enemies if e.get("y", 99999) < 16000]

        # 1. Urgent Defense: Enemies in home territory
        if threats_near_home:
            # Threat in home! Find target cluster
            target = min(threats_near_home, key=lambda e: e.get("y", 99999))
            tx, ty = target.get("x", 9000), target.get("y", 10000)

            # Air has to be answered with something that reaches it. Every other
            # card in these archetypes is ground-only, so an unanswered air push
            # walks straight through the teammate and into the player's king
            # tower -- and the player cannot see the threat as clearly as the
            # teammate can, because the teammate already tracks the board every
            # tick. Take the air first, ahead of any splash preference.
            air_threats = [e for e in threats_near_home if e.get("isFlying")]
            if air_threats:
                air = min(air_threats, key=lambda e: e.get("y", 99999))
                ax, ay = air.get("x", 9000), air.get("y", 10000)
                answer = self._find_card_in_hand(hand, elixir, ANTI_AIR)
                if answer is not None:
                    return {"hand_index": answer,
                            "x": max(2000, min(16000, ax)),
                            "y": max(3000, min(15500, ay - 1000))}

            # If swarm of enemies, prefer spells or splash -- but only a spell
            # the player is not already committing. Two fireballs on one cluster
            # wastes half the team's elixir and leaves the rest of the push
            # unanswered.
            rejected_spells: set[int] = set()
            if len(threats_near_home) >= 2:
                spell = self._find_unclaimed_spell(tick, hand, elixir, tx, ty, rejected_spells)
                if spell is not None:
                    return {"hand_index": spell, "x": tx, "y": ty}
                # Nothing unclaimed: remember what was refused so the defensive
                # branch below cannot re-pick it through its affordability
                # fallback and cast the overlapping spell after all.
                rejected_spells = {int(c["cardId"]) for c in hand
                                   if c and c.get("cardId") is not None
                                   and int(c["cardId"]) in SPELLS}

            # Drop melee/tank/distraction in front of target
            defend = self._find_card_in_hand(hand, elixir, SWARMS | TANKS | RANGED,
                                             excluded=rejected_spells)
            if defend is not None:
                # Place slightly between enemy and our king
                py = max(4000, ty - 2000)
                px = max(2000, min(16000, tx))
                return {"hand_index": defend, "x": px, "y": py}

        # 2. Synergy Support: Support human player's push
        human_pushers = [u for u in friendly if u.get("owner") == 0 and u.get("cardId") in TANKS and u.get("y", 0) >= 8000]
        if human_pushers and elixir >= 4.0:
            lead = max(human_pushers, key=lambda u: u.get("y", 0))
            lx, ly = lead.get("x", 9000), lead.get("y", 10000)
            ranged = self._find_card_in_hand(hand, elixir, RANGED)
            if ranged is not None:
                # Follow 2.5 tiles behind human's tank
                sy = max(3500, ly - 2500)
                sx = max(2000, min(16000, lx))
                return {"hand_index": ranged, "x": sx, "y": sy}

        # 3. Macro Play / Passive Cycling
        if elixir >= 8.5:
            # Determine lane with fewer enemies or standard preferred lane
            lane_x = 3500 if self.preferred_lane == 0 else 14500
            self.preferred_lane = 1 - self.preferred_lane

            # Cycle a tank or ranged card from the back
            cycle = self._find_card_in_hand(hand, elixir, TANKS | RANGED | SWARMS)
            if cycle is not None:
                return {"hand_index": cycle, "x": lane_x, "y": 3500}

        return None

    def _decide_attacker(self, tick: int, elixir: float, hand: list,
                          friendly: list, enemies: list) -> dict[str, Any] | None:
        # Team 1: Home territory is y > 16000
        threats_near_home = [e for e in enemies if e.get("y", 0) > 16000]

        # 1. Defend home if threatened
        if threats_near_home:
            target = max(threats_near_home, key=lambda e: e.get("y", 0))
            tx, ty = target.get("x", 9000), target.get("y", 22000)
            defend = self._find_card_in_hand(hand, elixir, TANKS | RANGED | SWARMS)
            if defend is not None:
                py = min(28000, ty + 2000)
                px = max(2000, min(16000, tx))
                return {"hand_index": defend, "x": px, "y": py}

        # 2. Aggressive Push when Elixir >= 7.0
        if elixir >= 7.0:
            lane_x = 3500 if self.preferred_lane == 0 else 14500
            self.preferred_lane = 1 - self.preferred_lane

            tank = self._find_card_in_hand(hand, elixir, TANKS)
            if tank is not None:
                return {"hand_index": tank, "x": lane_x, "y": 18000}

            push = self._find_card_in_hand(hand, elixir, RANGED | SWARMS)
            if push is not None:
                return {"hand_index": push, "x": lane_x, "y": 18000}

        return None

    def _decide_defender(self, tick: int, elixir: float, hand: list,
                          friendly: list, enemies: list) -> dict[str, Any] | None:
        # Team 1: Home territory is y > 16000
        threats_near_home = [e for e in enemies if e.get("y", 0) > 16000]

        # 1. Active Defense with Building or Spell
        if threats_near_home:
            if len(threats_near_home) >= 2:
                spell = self._find_card_in_hand(hand, elixir, SPELLS)
                if spell is not None:
                    target = max(threats_near_home, key=lambda e: e.get("y", 0))
                    return {"hand_index": spell, "x": target.get("x", 9000), "y": target.get("y", 22000)}

            bldg = self._find_card_in_hand(hand, elixir, BUILDINGS)
            if bldg is not None:
                return {"hand_index": bldg, "x": 9000, "y": 23500}

            guard = self._find_card_in_hand(hand, elixir, RANGED | SWARMS | TANKS)
            if guard is not None:
                target = max(threats_near_home, key=lambda e: e.get("y", 0))
                return {"hand_index": guard, "x": max(2000, min(16000, target.get("x", 9000))), "y": min(28000, target.get("y", 22000) + 2000)}

        # 2. Counter-push if elixir >= 8.5
        if elixir >= 8.5:
            lane_x = 14500 if self.preferred_lane == 0 else 3500
            self.preferred_lane = 1 - self.preferred_lane
            runner = self._find_card_in_hand(hand, elixir, TANKS | RANGED)
            if runner is not None:
                return {"hand_index": runner, "x": lane_x, "y": 28000}

        return None

    def _find_card_in_hand(self, hand: list, elixir: float, candidate_set: set[int],
                           excluded: set[int] | None = None) -> dict[str, Any] | None:
        """Return the hand entry itself; the caller queues its native slot.

        The capture compacts the hand array, so a list position is not the
        native slot index. The entry's "handIndex" is the real slot.

        `excluded` is honoured by *both* passes, including the affordability
        fallback. Honouring it only in the first pass would let a card the
        caller deliberately rejected be returned anyway by the fallback, which
        is how an unwanted spell used to be cast after being refused.
        """
        excluded = excluded or set()
        for card in hand:
            if not card or card.get("cardId") is None:
                continue
            cost = card.get("cost", 3) or 3
            if int(card["cardId"]) in candidate_set and int(card["cardId"]) not in excluded and elixir >= cost:
                return card
        # Fallback to any card that can be afforded
        for card in hand:
            if not card or card.get("cardId") is None:
                continue
            cost = card.get("cost", 3) or 3
            if int(card["cardId"]) not in excluded and elixir >= cost:
                return card
        return None

class Team2v2Coordinator:
    def __init__(self):
        self.bot1 = BotAgent(owner=1, role="teammate")
        self.bot2 = BotAgent(owner=2, role="attacker")
        self.bot3 = BotAgent(owner=3, role="defender")

    def step(self, tick: int, ordinary: Mapping[str, Any]) -> dict[int, dict[str, Any]]:
        actions = {}
        for bot in (self.bot1, self.bot2, self.bot3):
            action = bot.decide_action(tick, ordinary)
            if action:
                actions[bot.owner] = action
        return actions
