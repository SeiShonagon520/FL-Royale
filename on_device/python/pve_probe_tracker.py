"""Per-tick position tracking for the PvE path probe.

The engine has no pathfinding code of its own in the probe: unit movement lives
in libg.so. Whether a monster spawned on an enemy tower-less side still walks
toward the player is an empirical question, and this module is the instrument
that answers it.

`native_runner`'s capture already serialises every live object with its world
`x`/`y`, `owner`, `cardId` and hp on every tick (build_observation_json in
cr_replay_probe.cpp). So tracking is a matter of sampling that per-tick object
list and keeping a compact `(tick, x, y)` series per tracked unit, which is
exactly the evidence Stage 1 asks for. Sampling every SAMPLE_EVERY_TICKS ticks
keeps the log small enough to paste into a report verbatim.
"""

# Stage 1 samples one point per second of match time. The engine runs 20
# ticks/second, so 20 ticks is the smallest cadence that still reads as a clean
# per-second series without flooding logcat.
SAMPLE_EVERY_TICKS = 20


class ObjectTrack:
    """The per-tick position series of one tracked unit."""

    __slots__ = ("owner", "card_id", "samples", "native_object_id")

    def __init__(self, owner, card_id, native_object_id):
        self.owner = owner
        self.card_id = card_id
        self.native_object_id = native_object_id
        # Each sample is (tick, x, y).
        self.samples = []

    def record(self, tick, x, y):
        self.samples.append((int(tick), int(x), int(y)))

    @property
    def first(self):
        return self.samples[0] if self.samples else None

    @property
    def last(self):
        return self.samples[-1] if self.samples else None

    def moved_toward_lower_y(self):
        """True when the unit's y decreased across the whole series (moving up-screen).

        Owner 0 (the player) sits at the top of the arena, so a monster walking
        toward it must see its y decrease. This is a coarse directional check;
        the full series is exported for the exact judgement.
        """
        if len(self.samples) < 2:
            return False
        return self.samples[-1][2] < self.samples[0][2]

    def monotonic_y(self):
        """True when y only ever decreases (never reverses) across the series."""
        if len(self.samples) < 2:
            return False
        for before, after in zip(self.samples, self.samples[1:]):
            if after[2] > before[2]:
                return False
        return True

    def to_json(self):
        return {
            "owner": self.owner,
            "cardId": self.card_id,
            "nativeObjectId": self.native_object_id,
            "samples": self.samples,
        }


class PositionTracker:
    """Samples per-tick object positions out of a native capture.

    The tracker keys a unit by (owner, nativeObjectId) so two units of the same
    card on the same side stay separate series, and it follows a unit by its
    native id across ticks so a single monster produces one continuous line
    rather than restarting whenever its card re-enters the object list.
    """

    # Tower troop ids, used to keep towers out of the tracked series: the question
    # is about walking *monsters*, and towers never move, so including them
    # would only add static points. Kept as ids rather than names because the
    # capture reports ids.
    TOWER_KING = 35000000
    TOWER_PRINCESS = 35000001

    def __init__(self, sample_every_ticks=SAMPLE_EVERY_TICKS):
        self.sample_every_ticks = int(sample_every_ticks)
        self.tracks = {}
        self._last_sampled_tick = None

    @staticmethod
    def _tower_data_id(obj):
        """The tower id of an object, or None when it is not a tower.

        Delegates to the verdict module so both agree on what a tower is. That
        agreement matters: the tracker previously matched on "cardId", which the
        engine leaves at -1 for tower characters, so it logged all six towers as
        walking units and the probe reported exactly the evidence it exists to
        refute.
        """
        from pve_tower_verdict import _tower_id
        return _tower_id(obj)

    @staticmethod
    def _is_walking_unit(obj):
        if obj.get("null"):
            return False
        owner = obj.get("owner")
        # Owner -1 is neutral scenery; 0/1 are the two battle sides; anything
        # else (2 = towers/crown-owned, 3) is not a deployed troop.
        if owner not in (0, 1):
            return False
        card_id = obj.get("cardId")
        if card_id is None:
            return False
        # Towers are a different data id family and never walk toward a tower.
        # They are identified by dataGlobalId, not cardId: the engine leaves
        # cardId at -1 for tower characters, so a check on cardId alone would
        # never exclude them and the tracker would log towers as walking units.
        if PositionTracker._tower_data_id(obj) is not None:
            return False
        x = obj.get("x")
        y = obj.get("y")
        return x is not None and y is not None

    def maybe_sample(self, capture):
        """Record this tick's walking units if the sampling cadence says so.

        Returns True when a sample was taken, so callers can gate any
        companion logging on the same cadence.
        """
        tick = capture.get("tick")
        if tick is None:
            return False
        tick = int(tick)
        # The capture's first frame can repeat a tick; only advance the sampler
        # when the tick actually moves forward so one tick is never double-counted.
        if self._last_sampled_tick is not None and tick <= self._last_sampled_tick:
            return False
        if self._last_sampled_tick is not None:
            if (tick - self._last_sampled_tick) < self.sample_every_ticks:
                return False
        self._last_sampled_tick = tick
        for obj in capture.get("objects") or []:
            if not self._is_walking_unit(obj):
                continue
            key = (obj.get("owner"), obj.get("nativeObjectId"))
            track = self.tracks.get(key)
            if track is None:
                track = ObjectTrack(obj.get("owner"), obj.get("cardId"),
                                    obj.get("nativeObjectId"))
                self.tracks[key] = track
            track.record(tick, obj.get("x"), obj.get("y"))
        return True

    def report(self):
        """A JSON-ready dict of every tracked series."""
        return {
            "sampleEveryTicks": self.sample_every_ticks,
            "tracks": [t.to_json() for t in self.tracks.values()],
        }