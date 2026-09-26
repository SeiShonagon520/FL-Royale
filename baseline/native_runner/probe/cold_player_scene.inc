// Diagnostic next phase after independent cold-clone and two-owner tests.
// The stock subtype-1 controller constructs its own manager; no transplantation
// or replay JSON setter is used. The wrapper goes through its stock consumer.
void* g_cold_scene_controller = nullptr;
void* g_cold_scene_manager = nullptr;
std::uint32_t g_cold_scene_frames = 0;
std::uint64_t g_cold_scene_generation = 1;

[[maybe_unused]] void record_cold_player_frame(void* manager, int tick) {
  if (manager != g_cold_scene_manager || tick < 0 || tick > 300 || tick%5 != 0) return;
  static int previous = -1;
  if (tick == previous) return;
  previous = tick;
  static std::unique_ptr<char[]> buffer(new char[kFullObservationResponseBytes]);
  pthread_mutex_lock(&g_control_mutex);
  const bool captured = build_normal_tick_atomic_json_locked(manager,1,tick,buffer.get(),kFullObservationResponseBytes);
  pthread_mutex_unlock(&g_control_mutex);
  if (captured) {
    mkdir("/data/data/" FIRSTLIGHT_PACKAGE_NAME "/files/cold-capture",0700);
    char path[160]{};
    std::snprintf(path,sizeof(path),"/data/data/" FIRSTLIGHT_PACKAGE_NAME "/files/cold-capture/tick-%04d.json",tick);
    FILE* file=std::fopen(path,"wb");
    if(file) { std::fwrite(buffer.get(),1,std::strlen(buffer.get()),file); std::fclose(file); }
  }
  if (tick%50 == 0 || !captured) LOGI("cold-scene capture tick=%d success=%d bytes=%zu",tick,captured,std::strlen(buffer.get()));
  if (tick == 200) {
    PlayerStateView player;
    void* world = read_object_field<void*>(manager,0xa8);
    DeployRequest deploy;
    if (read_player_state(world,1,&player)) {
      for(int hand=0; hand<4; ++hand) {
        if (resolve_hand_deploy_request(manager,1,hand,3500,22000,&deploy) &&
            deploy.root_card_id/1000000 == 26 && deploy.cost*10000 <= player.elixir_raw) {
          char command[1024]{};
          const bool ok = format_deploy_command(deploy,tick,command,sizeof(command)) && inject_logic_command(manager,command);
          LOGI("cold-scene enemy command queued=%d card=%d hand=%d observed=%d due=%d",ok,deploy.root_card_id,hand,tick,tick+21);
          break;
        }
      }
    }
  }
}

// Equivalent pinned stock predicate, with one explicit local-authority owner.
// Its original prologue contains a PC-relative branch, so the unused copied
// trampoline must never be invoked. Other managers retain the exact native
// branch behavior at 1062f6c..1062fd4.
std::int32_t cold_scene_local_authority(void* manager) {
  if (manager && manager == g_cold_scene_manager) return 1;
  void* world = read_object_field<void*>(manager, 0xa8);
  if (world) {
    const int state = read_object_field<int>(world, 0x18);
    if (state == 3) return 1;
    if (state != 1) return 0;
    void* mode = read_object_field<void*>(world, 0x1b0);
    const int kind = read_object_field<int>(mode, 8);
    return kind == 15 || kind == 6;
  }
  void* alternate = read_object_field<void*>(manager, 0x218);
  if (!alternate) return 1;
  void* state = reinterpret_cast<void*(*)(void*)>(g_libg_base + 0x107c8a4)(alternate);
  return read_object_field<int>(state, 0x68) != 0;
}

void install_cold_scene_authority(std::uintptr_t base) {
  static constexpr std::uint8_t prologue[16] = {
    0x08,0x54,0x40,0xf9,0x88,0x01,0x00,0xb4,0x09,0x19,0x40,0xb9,0x3f,0x0d,0x00,0x71
  };
  void* unused_trampoline = nullptr;
  const bool ok = install_inline_hook(base, 0x1062f6c, prologue,
      reinterpret_cast<const void*>(&cold_scene_local_authority), &unused_trampoline,
      "cold-scene local authority predicate");
  LOGI("cold-scene local authority installed=%d", ok);
}

void sample_cold_player_scene() {
  if (!g_cold_scene_controller || (++g_cold_scene_frames % 60) != 1) return;
  void* singleton = g_replay_manager_getter();
  void* controller = read_object_field<void*>(singleton, 0x20);
  if (controller != g_cold_scene_controller || !process_range_is_readable(controller, 0x330)) {
    LOGE("cold-scene controller replaced"); g_cold_scene_controller = nullptr; return;
  }
  void* manager = read_object_field<void*>(controller, 0x90);
  void* world = process_range_is_readable(manager, 0x230) ? read_object_field<void*>(manager, 0xa8) : nullptr;
  PlayerStateView p[2];
  const bool valid = process_range_is_readable(world, 0x300) &&
                     read_player_state(world, 0, &p[0]) && read_player_state(world, 1, &p[1]);
  LOGI("cold-scene frame=%u singletonMode=%d subtype=%d state=%d tick=%d worldState=%d "
       "valid=%d hand0=%d hand1=%d elixir0=%d elixir1=%d localHi=%u localLo=%u",
       g_cold_scene_frames, read_object_field<int>(singleton, 0x30), read_object_field<int>(controller, 0x58),
       read_object_field<int>(controller, 0x30), manager ? g_game_state_tick(manager) : -1,
       world ? read_object_field<int>(world, 0x18) : -1, valid,
       valid ? p[0].hand.count : -1, valid ? p[1].hand.count : -1,
       valid ? p[0].elixir_raw : -1, valid ? p[1].elixir_raw : -1,
       valid ? read_object_field<unsigned>(world, 0x2e0) : 0,
       valid ? read_object_field<unsigned>(world, 0x2e4) : 0);
}

bool start_cold_player_scene(void* parsed, void* context_a, void* context_b) {
#if !defined(FIRSTLIGHT_LOCAL_APP)
  if (!clone_adapter::isolated()) return false;
#endif
  void* singleton = g_replay_manager_getter();
  void* messaging = read_object_field<void*>(reinterpret_cast<void*>(g_libg_base + 0x19b3ba8), 0);
  if (!process_range_is_readable(messaging, 0x40)) {
    LOGE("cold-scene messaging identity unavailable"); return false;
  }
  // Stock identity setter updates both integer components and its text cache.
  reinterpret_cast<void(*)(void*,int,int)>(g_libg_base + 0xad9478)(messaging, 0, 1);
  void* source = g_game_state_new(0x230);
  g_game_state_constructor(source, 1, context_a, context_b, 0);
  g_game_state_setup(source, 1);
  g_original_game_state_load(source, parsed, nullptr, nullptr);
  void* packet = nullptr;
  std::uint64_t digest = 0;
  std::uint32_t bytes = 0;
  if (!create_state_snapshot(source, &packet, &digest, &bytes)) return false;
  g_byte_stream_flip_read(static_cast<std::uint8_t*>(packet) + 8);
  g_offline_control_session_active.store(true, std::memory_order_release);
  LOGI("cold-scene creating subtype=1 from local snapshot bytes=%u priorMode=%d", bytes,
       read_object_field<int>(singleton, 0x30));
  // Main Battle's stock preparation (bd6ff4) chooses start kind 4, which
  // enters the snapshot-ready state rather than the JSON-replay state 3.
  reinterpret_cast<void(*)(void*)>(g_libg_base + 0xc8e258)(singleton);
  const std::int32_t battle_mode = 4;
  std::memcpy(static_cast<std::uint8_t*>(singleton) + 8, &battle_mode, sizeof(battle_mode));
  reinterpret_cast<void(*)(void*,int,int)>(g_libg_base + 0xc8dfd0)(singleton, 4, 0);
  g_original_normal_start_controller_transition(singleton);
  void* controller = read_object_field<void*>(singleton, 0x20);
  if (!process_range_is_readable(controller, 0x330) || read_object_field<int>(controller, 0x58) != 1) {
    LOGE("cold-scene controller construction failed"); return false;
  }
  void* manager = read_object_field<void*>(controller, 0x90);
  void* channel = read_object_field<void*>(controller, 0x80);
  LOGI("cold-scene constructed manager=%d channel=%d state=%d",
       process_range_is_readable(manager, 0x230), process_range_is_readable(channel, 0x30),
       read_object_field<int>(controller, 0x30));
  if (!process_range_is_readable(manager, 0x230) || !process_range_is_readable(channel, 0x30)) return false;
  // Bind all semantic hook rings to the destination before its first objects
  // are deserialized. The detached fixture manager never owns live telemetry.
  begin_runtime_telemetry_epoch(manager,g_cold_scene_generation,g_cold_scene_generation);
  const int accepted = reinterpret_cast<int(*)(void*,void*)>(g_libg_base + 0xccea2c)(channel, packet);
  bind_combat_event_object_manager(manager);
  LOGI("cold-scene decoded accepted=%d tick=%d ready=%d", accepted, g_game_state_tick(manager),
       read_object_field<std::uint8_t>(manager, 0x1ec));
  // The JSON source describes replay world 3. A player world is 0; local
  // authority is now supplied independently by the manager-scoped predicate,
  // retaining the stock player HUD, touch validation, command append and clock.
  void* world = read_object_field<void*>(manager, 0xa8);
  reinterpret_cast<void(*)(void*,int)>(g_libg_base+0xcf6e18)(world,0);
  g_cold_scene_manager = manager;
  reinterpret_cast<void(*)(void*)>(g_libg_base + 0xc8e258)(singleton);
  g_state_snapshot_delete(packet);
  g_game_state_destroy(source); g_game_state_base_destroy(source); g_game_state_delete(source);
  g_cold_scene_controller = controller;
  LOGI("cold-scene source released; stock lifecycle owns scene and clock");
  return accepted != 0;
}
