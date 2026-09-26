// Explicit, one-shot research build. Runs on the render owner thread in a
// fresh offline process; the host runner always force-stops it on its deadline.
namespace clone_adapter {
constexpr const char* kRequest = "/data/data/" FIRSTLIGHT_PACKAGE_NAME "/files/cold_clone_steps.txt";
constexpr const char* kFixture = "/data/data/" FIRSTLIGHT_PACKAGE_NAME "/files/cold_clone.json";
bool admitted = false;
bool resources_verified = false;
long owner_tid = 0;
std::int64_t deadline_ns = 0;

bool isolated() {
  if (!resources_verified || syscall(__NR_gettid) != owner_tid ||
      process_monotonic_now_ns() >= deadline_ns || g_native_worker_running ||
      g_resident_mode_active || g_controlled_manager ||
      g_native_render_manager.load(std::memory_order_acquire)) return false;
  void* singleton = g_replay_manager_getter ? g_replay_manager_getter() : nullptr;
  if (!process_range_is_readable(singleton, 0x50)) return false;
  const int mode = read_object_field<std::int32_t>(singleton, 0x30);
  return mode != 3 && mode != 4 && mode != 8 && mode != 9 &&
         read_object_field<void*>(singleton, 0x48) == nullptr;
}

bool copy(const void* from, void* to, std::size_t bytes) {
  if (!process_range_is_readable(from, bytes)) return false;
  std::memcpy(to, from, bytes);
  return true;
}

void snapshot(void* manager, void** result) {
  cr_call_state_snapshot(manager, result, g_state_snapshot);
}

std::int32_t primitive(void* stream, std::size_t offset) {
  if (!process_range_is_readable(stream, 8)) return -1;
  void* table = read_object_field<void*>(stream, 0);
  if (!libg_address_has_segment_flags(table, offset + 8, PF_R)) return -1;
  void* fn = read_object_field<void*>(table, offset);
  if (!libg_address_has_segment_flags(fn, 4, PF_X)) return -1;
  return reinterpret_cast<ByteStreamReadValue>(fn)(stream);
}
std::int32_t read_byte(void* stream) { return primitive(stream, 0x150); }
std::int32_t read_boolean(void* stream) { return primitive(stream, 0xe0); }

// Independent of the snapshot serializer: inspect the known fresh fixture.
bool semantic(void* manager, std::uint64_t* digest) {
  if (!process_range_is_readable(manager, 0x230)) return false;
  void* world = read_object_field<void*>(manager, 0xa8);
  if (!process_range_is_readable(world, 0x300)) return false;
  std::uint64_t value = 1469598103934665603ULL;
  const auto mix = [&](std::uint32_t word) {
    for (int i = 0; i != 4; ++i) { value ^= (word >> (i * 8)) & 255; value *= 1099511628211ULL; }
  };
  constexpr int expected_cards[8] = {26000000,26000001,26000005,28000001,28000000,26000003,26000014,26000018};
  for (int owner = 0; owner != 2; ++owner) {
    void* player = read_object_field<void*>(world, 0xe0 + owner * 8);
    void* identity = read_object_field<void*>(world, 0x30 + owner * 8);
    void* deck = read_object_field<void*>(world, 0x88 + owner * 8);
    if (!process_range_is_readable(player, 0x300) || !process_range_is_readable(identity, 8) ||
        !process_range_is_readable(deck, 0x30)) return false;
    PlayerStateView p;
    if (!read_player_state(world, owner, &p) || p.account_id_high != 0 || p.account_id_low != unsigned(owner+1) ||
        p.deck_slots.count != 8 || p.hand.count != 4 || p.elixir_raw < 0 || p.elixir_raw > 10000000 ||
        !process_range_is_readable(p.deck_slots.data, 64) || !process_range_is_readable(p.hand.data, 16)) return false;
    mix(p.account_id_high); mix(p.account_id_low); mix(p.elixir_raw);
    for (int slot = 0; slot != 8; ++slot) {
      void* card_slot = static_cast<void**>(p.deck_slots.data)[slot];
      if (!process_range_is_readable(card_slot, 0x20)) return false;
      void* card = read_object_field<void*>(card_slot, 0x10);
      if (!process_range_is_readable(card, 0x44)) return false;
      CardStateView c;
      if (!read_card_state(p, slot, true, &c) || c.card_id != expected_cards[slot] || !c.selection_valid) return false;
      mix(c.card_id); mix(c.command_card_id); mix(c.card_parameter); mix(c.cost);
    }
    for (int i = 0; i != 4; ++i) {
      const int slot = static_cast<int*>(p.hand.data)[i];
      if (slot < 0 || slot >= 8) return false;
      mix(slot); mix(expected_cards[slot]);
    }
  }
  void* objects = read_object_field<void*>(manager, 0x40);
  if (!process_range_is_readable(objects, 0x18)) return false;
  NativeVectorView v;
  if (!read_native_vector(objects, 8, 128, &v) || v.count != 6 ||
      !process_range_is_readable(v.data, std::size_t(v.count) * 8)) return false;
  std::uint32_t seen = 0;
  for (int index = 0; index != v.count; ++index) {
    void* object = static_cast<void**>(v.data)[index];
    NativeEntityIdentityView id;
    std::int32_t hp = 0, maximum = 0;
    if (!process_range_is_readable(object, 0xb0) || !read_native_entity_identity(object, &id) ||
        id.native_object_id < 5000000 || id.native_object_id > 5000005 || id.owner < 0 || id.owner > 1 ||
        !read_hitpoints(object, &hp, &maximum) || hp <= 0 || hp != maximum) return false;
    const auto bit = 1u << (id.native_object_id - 5000000);
    if (seen & bit) return false;
    seen |= bit;
    mix(id.native_object_id); mix(id.owner); mix(hp); mix(maximum);
    mix(read_object_field<std::int32_t>(object, 0x7c)); mix(read_object_field<std::int32_t>(object, 0x80));
  }
  if (seen != 63) return false;
  *digest = value;
  return true;
}

void progress(const firstlight::cold_clone::Report& r) {
  LOGI("cold-clone stage=%u code=%u sourceTick=%d restoredTick=%d finalTick=%d sourceState=%d restoredState=%d "
       "worldState=%d bytes=%u consumed=%d aux=%d sourceDigest=%llu restoredDigest=%llu sourceDestroyed=%d cleanup=%d",
       unsigned(r.stage), unsigned(r.code), r.source_tick, r.restored_tick, r.final_tick,
       r.source_manager_state, r.restored_manager_state, r.world_state, r.payload_bytes, r.consumed_bytes,
       r.stream_aux_1c, (unsigned long long)r.source_digest, (unsigned long long)r.restored_digest,
       r.source_destroyed, r.cleaned_up);
}

bool exercise_commands(void* manager) {
  while (g_game_state_tick(manager) < 100) {
    if (!isolated()) return false;
    g_original_game_state_step(manager);
  }
  void* world = read_object_field<void*>(manager, 0xa8);
  void* queue = read_object_field<void*>(manager, 0x38);
  for (int owner = 0; owner != 2; ++owner) {
    PlayerStateView before;
    if (!read_player_state(world, owner, &before)) return false;
    DeployRequest deploy;
    int selected_hand = -1;
    for (int hand = 0; hand != 4; ++hand) {
      if (resolve_hand_deploy_request(manager, owner, hand, 3500, owner == 0 ? 10000 : 22000, &deploy) &&
          deploy.root_card_id / 1000000 == 26 && deploy.cost * 10000 <= before.elixir_raw) {
        selected_hand = hand; break;
      }
    }
    if (selected_hand < 0) return false;
    const int before_slot = static_cast<int*>(before.hand.data)[selected_hand];
    const int tick = g_game_state_tick(manager);
    char command[1024]{};
    if (!format_deploy_command(deploy, tick, command, sizeof(command)) || !inject_logic_command(manager, command)) return false;
    const int enqueued = read_object_field<int>(queue, 0x14);
    int consumed_tick = -1;
    int hand_cycle_tick = -1;
    PlayerStateView after;
    for (int step = 0; step != 45; ++step) {
      if (!isolated()) return false;
      g_original_game_state_step(manager);
      if (!read_player_state(world, owner, &after)) return false;
      if (consumed_tick < 0 && read_object_field<int>(queue, 0x14) == 0 && after.elixir_raw < before.elixir_raw)
        consumed_tick = g_game_state_tick(manager);
      if (hand_cycle_tick < 0 && static_cast<int*>(after.hand.data)[selected_hand] != before_slot)
        hand_cycle_tick = g_game_state_tick(manager);
    }
    if (!read_player_state(world, owner, &after)) return false;
    void* objects = read_object_field<void*>(manager, 0x40);
    NativeVectorView v;
    if (!read_native_vector(objects, 8, 512, &v)) return false;
    int matching = 0;
    for (int index = 0; index < v.count; ++index) {
      void* object = static_cast<void**>(v.data)[index];
      if (!process_range_is_readable(object, 0xb0)) continue;
      if (read_object_field<int>(object, 0x78) != owner || read_object_field<int>(object, 0xac) < 0) continue;
      // Ordinary reader's originating spell ID, independent of hand rotation.
      if (read_object_field<int>(object, 0xac) == deploy.root_card_id) ++matching;
    }
    LOGI("cold-clone command owner=%d card=%d cost=%d queued=%d enqueueTick=%d consumedTick=%d handCycleTick=%d finalTick=%d "
         "elixirBefore=%d elixirAfter=%d handChanged=%d matchingTroops=%d queueRemaining=%d",
         owner, deploy.root_card_id, deploy.cost, enqueued, tick, consumed_tick, hand_cycle_tick, g_game_state_tick(manager),
         before.elixir_raw, after.elixir_raw, static_cast<int*>(after.hand.data)[selected_hand] != before_slot,
         matching, read_object_field<int>(queue, 0x14));
    if (enqueued != 1 || consumed_tick != tick + static_cast<int>(kLiveCommandAgeTicks) + 1 ||
        hand_cycle_tick < 0 || after.elixir_raw >= before.elixir_raw || matching < 1) return false;
  }
  LOGI("cold-clone two-owner-commands passed=1");
  return true;
}
} // namespace clone_adapter

#if defined(FIRSTLIGHT_LOCAL_APP)
#include "local_runtime_bridge.inc"
#endif
#include "cold_player_scene.inc"
#include "cold_home_scene.inc"
#if defined(FIRSTLIGHT_LOCAL_APP)
#include "local_lifecycle.inc"
#include "local_home_navigation.inc"
#include "local_app_scene.inc"
#include "local_ai_deck_button.inc"
#endif

void run_cold_clone_on_frame() {
  using namespace clone_adapter;
  sample_cold_player_scene();
#if defined(FIRSTLIGHT_LOCAL_APP)
  process_local_scene_requests();
  if (admitted || !g_local_boot_ready.load(std::memory_order_acquire) || !inspect_content_runtime()) return;
#else
  if (admitted || access(kRequest, R_OK) != 0 || !inspect_content_runtime()) return;
#endif
  admitted = true;
#if !defined(FIRSTLIGHT_LOCAL_APP)
  char* request_text = nullptr;
  std::size_t bytes = 0;
#endif
  char* fixture = nullptr;
  int steps = 0, phase = 0;
#if defined(FIRSTLIGHT_LOCAL_APP)
  { std::lock_guard<std::mutex> lock(g_local_mutex); fixture = strdup(g_local_boot_fixture.c_str()); }
  phase = 3;
#else
  if (!read_text_file(kRequest, &request_text, &bytes)) return;
  std::sscanf(request_text, "%d %d", &steps, &phase);
  std::free(request_text);
  unlink(kRequest);
  if (steps < 0 || steps > 20 || !read_text_file(kFixture, &fixture, &bytes) || bytes >= kMaxReplayConfigBytes) {
    std::free(fixture); LOGE("cold-clone invalid request"); return;
  }
#endif
  RunnerAttestationSnapshot attestation;
  resources_verified = collect_runner_attestation(&attestation) && attestation.content_ready &&
      attestation.content_assets_mapped &&
      std::strcmp(attestation.libg_sha256, "110aa2b5cac391c498645e072b0d88729428c2c2845e7e2737ca8ee979059783") == 0 &&
      std::strcmp(attestation.content_fingerprint_sha256, "e0cb2fb9eb2fdd0df1afcb00df3e3ba61cf25aa8b24e39c17cc391d952a4e339") == 0 &&
      std::strcmp(attestation.content_assets_sha256, "4b99bfd5d5fb52fe79bba74cd1990ce08fc4f46064d905c64cb5c281b6474e7b") == 0;
  owner_tid = syscall(__NR_gettid);
  deadline_ns = process_monotonic_now_ns() + 15000000000LL;
  std::int32_t location = 0;
  void* context_a = g_game_state_context_a();
  void* context_b = g_game_state_context_b();
  LOGI("cold-clone admission resources=%d isolated=%d steps=%d", resources_verified, isolated(), steps);
  if (!isolated() || !extract_location_id(fixture, &location) || !validate_empty_match_streams(fixture) ||
      !prewarm_location_tilemap(context_a, location)) { std::free(fixture); return; }
  void* parsed = parse_json_text(fixture);
  std::free(fixture);
#if defined(FIRSTLIGHT_LOCAL_APP)
  LOGI("local-app offline Home bootstrap result=%d", start_cold_home_scene(parsed,context_a,context_b));
  release_json_object(parsed);
  return;
#endif
  firstlight::cold_clone::Api api;
  api.isolated = isolated; api.copy_readable = copy; api.allocate = g_game_state_new;
  api.construct = g_game_state_constructor; api.setup = g_game_state_setup; api.original_load = g_original_game_state_load;
  api.destroy = g_game_state_destroy; api.destroy_base = g_game_state_base_destroy; api.deallocate = g_game_state_delete;
  api.snapshot = snapshot; api.delete_snapshot = g_state_snapshot_delete; api.flip_read = g_byte_stream_flip_read;
  api.rewind_read = g_byte_stream_reset_read; api.read_byte = read_byte; api.read_boolean = read_boolean;
  api.read_header = g_game_state_deserialize_header; api.read_body = g_game_state_deserialize_body;
  api.tick = g_game_state_tick; api.original_step = g_original_game_state_step; api.semantic_digest = semantic;
  api.exercise_commands = exercise_commands;
  api.progress = progress;
  firstlight::cold_clone::Request request;
  request.enabled = true; request.context_a = context_a; request.context_b = context_b;
  request.parsed_match_json = parsed; request.steps_after_source_release = steps;
  request.exercise_commands = phase == 1;
  const auto report = firstlight::cold_clone::Run(api, request);
  progress(report);
  if (phase == 2 && report.code == firstlight::cold_clone::Code::Passed) {
    LOGI("cold-scene start result=%d", start_cold_player_scene(parsed, context_a, context_b));
  }
  if (phase == 3 && report.code == firstlight::cold_clone::Code::Passed) {
    LOGI("cold-home start result=%d", start_cold_home_scene(parsed, context_a, context_b));
  }
  release_json_object(parsed);
}

using ColdCloneOwnerUpdate = void(*)(void*, float);
ColdCloneOwnerUpdate g_original_cold_owner_update = nullptr;
void cold_clone_owner_update(void* singleton, float seconds) {
  run_cold_clone_on_frame();
#if defined(FIRSTLIGHT_LOCAL_APP)
  // No server clock needs to catch up while this offline app is backgrounded.
  if(g_local_boot_ready.load(std::memory_order_acquire) && !g_local_foreground.load(std::memory_order_acquire)) seconds=0.0f;
  if(g_local_running.load(std::memory_order_acquire) && g_local_native_manager && !g_local_result_shown) {
    // Keep every five-tick FAIR/LSTM step even on slower devices. Rendering
    // stays live while the offline clock waits for its bounded consumer.
    // Service the action mailbox before waiting: an AI may be awaiting its
    // queue receipt before it asks for the next already-captured frame.
    process_local_commands(g_local_native_manager,g_game_state_tick(g_local_native_manager));
    std::lock_guard<std::mutex> lock(g_local_mutex);
    if(g_local_frames.size()>=2) {
      seconds=0.0f;
      static unsigned paced_frames=0;
      if(++paced_frames%300==1) LOGI("local-app pacing tick=%d backlog=%zu",g_game_state_tick(g_local_native_manager),g_local_frames.size());
    }
  }
#endif
  g_original_cold_owner_update(singleton, seconds);
}
void install_cold_clone_owner_hook(std::uintptr_t base) {
#if defined(FIRSTLIGHT_LOCAL_APP)
  install_local_offline_ui(base);
  install_local_lifecycle(base);
  install_local_battle_identity(base);
  install_local_home_navigation(base);
  install_local_ai_deck_button(base);
  install_local_command_accounting(base);
#endif
  install_cold_scene_authority(base);
  install_cold_home_update(base);
  static constexpr std::uint8_t prologue[16] = {
    0xe9,0x23,0xbc,0x6d,0xfd,0x7b,0x01,0xa9,0xf5,0x13,0x00,0xf9,0xf4,0x4f,0x03,0xa9
  };
  const bool ok = install_inline_hook(base, 0xc8da38, prologue,
      reinterpret_cast<const void*>(&cold_clone_owner_update),
      reinterpret_cast<void**>(&g_original_cold_owner_update), "cold-clone logic owner update");
  LOGI("cold-clone logic owner hook installed=%d", ok);
}
