// A single-sided PvE arena: the enemy half has no towers to see, target or hit.
//
// Why the towers are moved rather than deleted
// ---------------------------------------------
// The engine ends a match by counting the *opposing* side's towers. A PvE arena
// with genuinely no enemy tower therefore looks like a finished duel to the
// engine, which declared a result at the 40-tick verdict before the player had
// played a card. That was the original reason the enemy towers were kept: three
// living towers on the far side kept the match alive. But the specification for
// this mode is explicit that the enemy half must be "100% absolutely no towers,
// with no tower model, no collision and no hitpoints anywhere on it", so the
// towers cannot stay where they are.
//
// The resolution is to move them rather than remove them: the engine still sees
// three living enemy towers, so the opposing-tower verdict never fires, and the
// player sees none of them, so the enemy half is visually and interactively
// empty exactly as specified.
//
// Why y = 35000
// -------------
// The arena is an 18x32 grid of 1000-unit cells, so the world spans y = 0..32000
// and anything at y = 35000 is past the far edge. `enqueue_local_scheduled_card`
// rejects a position outside `0 <= y < 32000`, so an off-screen tower is also not
// a legal deploy target, and the renderer's arena bounds exclude it, so it is
// never drawn. The tower's LogicData, owner, hitpoints and crown accounting are
// all untouched, which is what keeps the match alive.
//
// What is *not* touched
// ---------------------
// The player's own three towers are never moved: the player defends with real
// royal towers, and `pve_tower_verdict` reads their hitpoints to decide the run.
// Only owner-1 royal towers are relocated, and only while a PvE fixture is the
// live battle.

constexpr std::int32_t kPvEOffscreenTowerY = 35000;

// The arena's own height, in world units. Duplicated here rather than imported
// so this file can be read on its own; it matches the `0 <= y < 32000` bound
// `enqueue_local_scheduled_card` enforces.
constexpr std::int32_t kArenaWorldHeight = 32000;

// The tower data ids this treats as royal towers, shared with the crown
// classifier so the two can never disagree about what a tower is.
constexpr std::uint32_t kTowerlessKingTowerDataId = 35000000;
constexpr std::uint32_t kTowerlessPrincessTowerDataId = 35000001;

// Move the enemy side's royal towers past the far edge of the arena.
//
// Idempotent and fail-closed: a tower already at or beyond the off-screen line is
// left alone, and a tower the classifier does not recognise is never written. The
// function runs once per PvE battle rather than per tick, because a tower's
// position is read by pathing and by the renderer's culling, and rewriting it
// every tick would fight those systems for no benefit.
std::int32_t relocate_enemy_towers_offscreen(void *world) {
  if (world == nullptr) {
    return 0;
  }
  void *const player_zero = read_object_field<void *>(world, 0xe0);
  void *const object_manager =
      player_zero == nullptr ? nullptr : read_object_field<void *>(player_zero, 0x10);
  if (object_manager == nullptr) {
    return 0;
  }
  void **const objects = read_object_field<void **>(object_manager, 0x08);
  const std::int32_t capacity = read_object_field<std::int32_t>(object_manager, 0x10);
  const std::int32_t count = read_object_field<std::int32_t>(object_manager, 0x14);
  if (count < 0 || capacity < count || capacity > 100000 || (count != 0 && objects == nullptr)) {
    return 0;
  }
  std::int32_t moved = 0;
  for (std::int32_t index = 0; index < count; ++index) {
    void *const object = objects[index];
    if (object == nullptr || read_object_field<std::int32_t>(object, 0xac) != -1) {
      continue;
    }
    // Owner 1 only. The player's own royal towers are the defence the run is
    // scored on, and moving them would end the match immediately.
    if (read_object_field<std::int32_t>(object, 0x78) != 1) {
      continue;
    }
    std::uint32_t data_global_id = 0;
    if (!read_tower_data_global_id(object, &data_global_id)) {
      continue;
    }
    if (data_global_id != kTowerlessKingTowerDataId &&
        data_global_id != kTowerlessPrincessTowerDataId) {
      continue;
    }
    const std::int32_t y = read_object_field<std::int32_t>(object, 0x80);
    if (y >= kPvEOffscreenTowerY) {
      continue;
    }
    std::memcpy(static_cast<std::uint8_t *>(object) + 0x80, &kPvEOffscreenTowerY,
                sizeof(kPvEOffscreenTowerY));
    ++moved;
  }
  if (moved > 0) {
    LOGI("pve towerless relocated %d enemy towers to y=%d (arena height %d)", moved,
         kPvEOffscreenTowerY, kArenaWorldHeight);
  }
  return moved;
}

// Whether any enemy royal tower is still sitting on the playable arena.
//
// The caller uses this to decide whether it is done trying: a scene that has not
// built its towers yet still reports "pending", so the one-shot flag is not spent
// before there is anything to move.
bool pve_towerless_towers_pending(void *world) {
  if (world == nullptr) {
    return true;
  }
  // A scene that cannot be read at all still reports "pending" so the caller
  // keeps retrying: the towers appear a few steps after the scene starts.
  void *const player_zero = read_object_field<void *>(world, 0xe0);
  void *const object_manager =
      player_zero == nullptr ? nullptr : read_object_field<void *>(player_zero, 0x10);
  if (object_manager == nullptr) {
    LOGI("pve towerless arena not readable yet, will retry");
    return true;
  }
  void **const objects = read_object_field<void **>(object_manager, 0x08);
  const std::int32_t capacity = read_object_field<std::int32_t>(object_manager, 0x10);
  const std::int32_t count = read_object_field<std::int32_t>(object_manager, 0x14);
  if (count < 0 || capacity < count || capacity > 100000 || (count != 0 && objects == nullptr)) {
    return true;
  }
  for (std::int32_t index = 0; index < count; ++index) {
    void *const object = objects[index];
    if (object == nullptr || read_object_field<std::int32_t>(object, 0xac) != -1) {
      continue;
    }
    if (read_object_field<std::int32_t>(object, 0x78) != 1) {
      continue;
    }
    std::uint32_t data_global_id = 0;
    if (!read_tower_data_global_id(object, &data_global_id)) {
      continue;
    }
    if (data_global_id != kTowerlessKingTowerDataId &&
        data_global_id != kTowerlessPrincessTowerDataId) {
      continue;
    }
    if (read_object_field<std::int32_t>(object, 0x80) < kPvEOffscreenTowerY) {
      return true;
    }
  }
  return false;
}

// Whether a match fixture asks for the towerless arena.
//
// The marker is a top-level `"pveTowerless": true` on the fixture JSON, written by
// `mobile_fair.create_match` for a single-sided PvE match. Reading a marker rather
// than a game-mode id is deliberate: the arena rewrite is destructive to the map
// layout, so it must only ever be armed by a caller that asked for it, and a mode
// id is a weaker signal than an explicit field on the fixture it produced.
bool pve_towerless_fixture_requested(const char *fixture_json) {
  if (fixture_json == nullptr) {
    return false;
  }
  constexpr const char *kKey = "\"pveTowerless\":";
  const char *value = std::strstr(fixture_json, kKey);
  if (value == nullptr) {
    return false;
  }
  value += std::strlen(kKey);
  // Skip whitespace so `"pveTowerless": true` is accepted as well as the compact
  // `true` that `json.dumps(..., separators=(",",":"))` produces.
  while (*value == ' ' || *value == '\t' || *value == '\n' || *value == '\r') {
    ++value;
  }
  return std::strncmp(value, "true", 4) == 0;
}