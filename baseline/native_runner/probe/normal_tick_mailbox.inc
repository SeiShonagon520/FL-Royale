// Read-only target-tick capture for a stock player battle. This file is
// included after the native observation encoders and before the command
// dispatcher. The stock UI advances GameState; this mailbox never steps it.

struct NormalTickMailbox {
  std::unique_ptr<char[]> payload;
  std::size_t payload_bytes = 0;
  void *observed_manager = nullptr;
  std::uint64_t generation = 0;
  std::int32_t last_tick = -1;
  std::int64_t last_seen_ns = 0;
  std::uint64_t requested_generation = 0;  // 0 waits for the next matching scene.
  std::int32_t requested_tick = -1;
  bool tick_only = false;
  std::uint64_t captured_generation = 0;
  std::int32_t captured_tick = -1;
  std::int64_t requested_ns = 0;
  std::int64_t capture_started_ns = 0;
  std::int64_t encode_ns = 0;
  bool busy = false;
  bool armed_only = false;  // Await command has not claimed this capture yet.
  bool pending = false;
  bool ready = false;
  bool capture_ok = false;
};

NormalTickMailbox g_normal_tick_mailbox;

bool build_normal_tick_atomic_json_locked(void *manager, std::uint64_t generation,
                                          std::int32_t expected_tick,
                                          char *response, std::size_t response_size) {
  constexpr const char *kSchema = "native-normal-tick-capture.v1";
  if (manager == nullptr || response == nullptr ||
      response_size < kFullObservationResponseBytes || g_game_state_tick == nullptr ||
      g_game_state_tick(manager) != expected_tick) {
    return false;
  }
  response[0] = '\0';
  std::size_t used = 0;
  JsonWriter json(response, response_size, used);
  if (!json.append("{\"ok\":true,\"schema\":\"%s\",\"atomic\":true,"
                   "\"source\":\"normal-player\",\"maxObjects\":%d,"
                   "\"generation\":%llu,\"tick\":%d,\"ordinary\":",
                   kSchema, kMaxObservedObjects,
                   static_cast<unsigned long long>(generation), expected_tick)) {
    return false;
  }
  bool ordinary_complete = false;
  if (!build_observation_json(manager, generation, response + used,
                              response_size - used, &ordinary_complete) ||
      !ordinary_complete) {
    return false;
  }
  used += std::strlen(response + used);
  if (g_game_state_tick(manager) != expected_tick ||
      !json.append(",\"rich\":")) {
    return false;
  }
  bool rich_complete = false;
  if (!build_rich_observation_json(manager, generation, response + used,
                                   response_size - used, &rich_complete) ||
      !rich_complete) {
    return false;
  }
  used += std::strlen(response + used);
  return g_game_state_tick(manager) == expected_tick && json.append("}");
}

void normal_tick_mailbox_finish_error_locked(const char *reason) {
  NormalTickMailbox &mailbox = g_normal_tick_mailbox;
  if (mailbox.payload != nullptr) {
    std::snprintf(mailbox.payload.get(), mailbox.payload_bytes,
                  "{\"ok\":false,\"schema\":\"native-normal-tick-capture.v1\","
                  "\"error\":\"%s\",\"generation\":%llu,\"tick\":%d}",
                  reason, static_cast<unsigned long long>(mailbox.generation),
                  mailbox.last_tick);
  }
  mailbox.capture_ok = false;
  mailbox.pending = false;
  mailbox.ready = true;
  pthread_cond_broadcast(&g_control_cond);
}

bool parse_normal_tick_target(const char *arguments,
                              unsigned long long *generation,
                              std::int32_t *tick) {
  if (arguments == nullptr || generation == nullptr || tick == nullptr) {
    return false;
  }
  const char *first = arguments;
  while (*first == ' ' || *first == '\t') ++first;
  if (*first == '-') return false;
  int consumed = 0;
  const int fields =
      std::sscanf(arguments, "%llu %d%n", generation, tick, &consumed);
  const char *trailing = fields == 2 ? arguments + consumed : "x";
  while (*trailing == ' ' || *trailing == '\t') ++trailing;
  return fields == 2 && consumed > 0 && *trailing == '\0' &&
         *tick >= 0 && *tick % 5 == 0;
}

// Caller holds g_control_mutex. The lock is released before socket IO, and
// busy remains set until that IO completes so no capture can reuse payload.
void normal_tick_mailbox_await_locked(int socket_fd) {
  NormalTickMailbox &mailbox = g_normal_tick_mailbox;
  timespec deadline = {};
  clock_gettime(CLOCK_REALTIME, &deadline);
  deadline.tv_sec += 8;
  int wait_status = 0;
  while (!mailbox.ready && wait_status == 0) {
    wait_status = pthread_cond_timedwait(&g_control_cond, &g_control_mutex,
                                         &deadline);
  }
  if (!mailbox.ready) {
    normal_tick_mailbox_finish_error_locked("normal target tick timed out");
  }
  const char *const response = mailbox.payload.get();
  pthread_mutex_unlock(&g_control_mutex);
  send_control_response(socket_fd, response);
  pthread_mutex_lock(&g_control_mutex);
  mailbox.pending = false;
  mailbox.armed_only = false;
  mailbox.busy = false;
  pthread_mutex_unlock(&g_control_mutex);
}

void normal_tick_mailbox_on_native_boundary(void *manager, std::int32_t tick) {
  if (tick < 0 || g_replay_manager_getter == nullptr ||
      g_runner_mode.load(std::memory_order_acquire) !=
          static_cast<std::int32_t>(RunnerMode::Headless)) {
    return;
  }
  void *const singleton = g_replay_manager_getter();
  void *const controller = process_range_is_readable(singleton, 0x28)
                               ? read_object_field<void *>(singleton, 0x20)
                               : nullptr;
  // The online mode-4 controller can tick while manager+0xa8 is not yet a
  // readable battle world. The tick-only mailbox checks identity and clock;
  // the full capture additionally requires the FAIR object graph below.
  if (!process_range_is_readable(singleton, 0x40) ||
      !process_range_is_readable(controller, 0x98) ||
      read_object_field<void *>(singleton, 0x20) != controller ||
      (read_object_field<std::int32_t>(singleton, 0x30) != 3 &&
       read_object_field<std::int32_t>(singleton, 0x30) != 4) ||
      read_object_field<std::int32_t>(controller, 0x30) != 8 ||
      read_object_field<void *>(controller, 0x90) != manager ||
      !process_range_is_readable(manager, 0xac) ||
      g_game_state_tick == nullptr || g_game_state_tick(manager) != tick) {
    return;
  }
  pthread_mutex_lock(&g_control_mutex);
  NormalTickMailbox &mailbox = g_normal_tick_mailbox;
  if (g_controlled_manager != nullptr || g_resident_mode_active) {
    pthread_mutex_unlock(&g_control_mutex);
    return;
  }
  if (manager != mailbox.observed_manager || tick < mailbox.last_tick) {
    mailbox.observed_manager = manager;
    ++mailbox.generation;
    if (mailbox.generation == 0) ++mailbox.generation;
  }
  mailbox.last_tick = tick;
  mailbox.last_seen_ns = process_monotonic_now_ns();
  if (!mailbox.pending) {
    pthread_mutex_unlock(&g_control_mutex);
    return;
  }
  if (mailbox.requested_generation != 0 &&
      mailbox.generation < mailbox.requested_generation) {
    pthread_mutex_unlock(&g_control_mutex);
    return;
  }
  if (mailbox.requested_generation != 0 &&
      mailbox.generation > mailbox.requested_generation) {
    normal_tick_mailbox_finish_error_locked("requested battle generation passed");
  } else if (tick > mailbox.requested_tick) {
    normal_tick_mailbox_finish_error_locked("requested native tick was missed");
  } else if (tick == mailbox.requested_tick) {
    mailbox.capture_started_ns = process_monotonic_now_ns();
    mailbox.captured_generation = mailbox.generation;
    mailbox.captured_tick = tick;
    if (mailbox.tick_only) {
      void *const world = read_object_field<void *>(manager, 0xa8);
      const bool world_readable = process_range_is_readable(world, 0x1c);
      const std::int32_t world_state = world_readable
                                           ? read_object_field<std::int32_t>(world, 0x18)
                                           : -1;
      std::snprintf(mailbox.payload.get(), mailbox.payload_bytes,
                    "{\"ok\":true,\"schema\":\"normal-tick-sample.v1\","
                    "\"generation\":%llu,\"tick\":%d,\"worldReadable\":%s,"
                    "\"worldState\":%d,\"managerState\":%d,"
                    "\"captureOnNativeStepThread\":true}",
                    static_cast<unsigned long long>(mailbox.generation), tick,
                    world_readable ? "true" : "false", world_state,
                    read_object_field<std::int32_t>(manager, 0x24));
      mailbox.capture_ok = true;
    } else {
      PlayerLocalSceneView scene;
      mailbox.capture_ok =
          read_player_local_scene(singleton, controller, manager, &scene,
                                  /*require_command_age=*/false) &&
          scene.tick == tick &&
          build_normal_tick_atomic_json_locked(
              manager, mailbox.generation, tick, mailbox.payload.get(),
              mailbox.payload_bytes);
    }
    mailbox.encode_ns = process_monotonic_now_ns() - mailbox.capture_started_ns;
    if (!mailbox.capture_ok) {
      normal_tick_mailbox_finish_error_locked(
          mailbox.tick_only ? "normal tick sample failed"
                            : "normal FAIR object graph or atomic capture failed");
    } else {
      mailbox.pending = false;
      mailbox.ready = true;
      pthread_cond_broadcast(&g_control_cond);
    }
  }
  pthread_mutex_unlock(&g_control_mutex);
}

bool handle_normal_tick_mailbox_command(int socket_fd, const char *command) {
  if (std::strcmp(command, "normal-observe-status") == 0) {
    char response[384] = {};
    pthread_mutex_lock(&g_control_mutex);
    const NormalTickMailbox &mailbox = g_normal_tick_mailbox;
    const std::int64_t wait_ns =
        mailbox.capture_started_ns > 0 && mailbox.requested_ns > 0
            ? mailbox.capture_started_ns - mailbox.requested_ns
            : -1;
    const std::int64_t age_ns = mailbox.last_seen_ns > 0
                                    ? process_monotonic_now_ns() -
                                          mailbox.last_seen_ns
                                    : -1;
    std::snprintf(response, sizeof(response),
                  "{\"ok\":true,\"schema\":\"normal-observe-status.v1\","
                  "\"generation\":%llu,\"tick\":%d,\"active\":%s,"
                  "\"lastSeenAgeNs\":%lld,\"busy\":%s,\"armed\":%s,"
                  "\"capturedGeneration\":%llu,\"capturedTick\":%d,"
                  "\"captureOk\":%s,\"requestToCaptureNs\":%lld,"
                  "\"encodeNs\":%lld}",
                  static_cast<unsigned long long>(mailbox.generation),
                  mailbox.last_tick,
                  age_ns >= 0 && age_ns < 1000000000LL ? "true" : "false",
                  static_cast<long long>(age_ns),
                  mailbox.busy ? "true" : "false",
                  mailbox.armed_only ? "true" : "false",
                  static_cast<unsigned long long>(mailbox.captured_generation),
                  mailbox.captured_tick, mailbox.capture_ok ? "true" : "false",
                  static_cast<long long>(wait_ns),
                  static_cast<long long>(mailbox.encode_ns));
    pthread_mutex_unlock(&g_control_mutex);
    send_control_response(socket_fd, response);
    return true;
  }
  constexpr const char *kArmPrefix = "arm-normal-at ";
  constexpr const char *kAwaitPrefix = "await-normal-capture ";
  constexpr const char *kCancelPrefix = "cancel-normal-capture ";
  constexpr const char *kFullPrefix = "observe-normal-at ";
  constexpr const char *kTickPrefix = "observe-normal-tick-at ";
  constexpr std::size_t kArmPrefixSize = sizeof("arm-normal-at ") - 1;
  constexpr std::size_t kAwaitPrefixSize = sizeof("await-normal-capture ") - 1;
  constexpr std::size_t kCancelPrefixSize = sizeof("cancel-normal-capture ") - 1;
  constexpr std::size_t kFullPrefixSize = sizeof("observe-normal-at ") - 1;
  constexpr std::size_t kTickPrefixSize = sizeof("observe-normal-tick-at ") - 1;
  const bool arm_only =
      std::strncmp(command, kArmPrefix, kArmPrefixSize) == 0;
  const bool await_armed =
      std::strncmp(command, kAwaitPrefix, kAwaitPrefixSize) == 0;
  const bool cancel_armed =
      std::strncmp(command, kCancelPrefix, kCancelPrefixSize) == 0;
  const bool tick_only =
      std::strncmp(command, kTickPrefix, kTickPrefixSize) == 0;
  const bool full =
      std::strncmp(command, kFullPrefix, kFullPrefixSize) == 0;
  if (!arm_only && !await_armed && !cancel_armed && !tick_only && !full) {
    return false;
  }
  const std::size_t prefix_size = arm_only      ? kArmPrefixSize
                                  : await_armed ? kAwaitPrefixSize
                                  : cancel_armed ? kCancelPrefixSize
                                  : tick_only    ? kTickPrefixSize
                                                 : kFullPrefixSize;
  unsigned long long requested_generation = 0;
  std::int32_t requested_tick = -1;
  if (!parse_normal_tick_target(command + prefix_size,
                                &requested_generation, &requested_tick)) {
    send_control_error(socket_fd,
                       "normal capture expects GENERATION TARGET_TICK divisible by five");
    return true;
  }
  pthread_mutex_lock(&g_control_mutex);
  NormalTickMailbox &mailbox = g_normal_tick_mailbox;
  if (await_armed || cancel_armed) {
    if (!mailbox.busy || !mailbox.armed_only ||
        mailbox.requested_generation != requested_generation ||
        mailbox.requested_tick != requested_tick) {
      pthread_mutex_unlock(&g_control_mutex);
      send_control_error(socket_fd, "matching armed normal capture is unavailable");
      return true;
    }
    if (cancel_armed) {
      mailbox.pending = false;
      mailbox.ready = false;
      mailbox.armed_only = false;
      mailbox.busy = false;
      pthread_mutex_unlock(&g_control_mutex);
      send_control_response(socket_fd,
                            "{\"ok\":true,\"schema\":\"normal-capture-cancelled.v1\"}");
      return true;
    }
    mailbox.armed_only = false;
    normal_tick_mailbox_await_locked(socket_fd);
    return true;
  }
  if (mailbox.busy && mailbox.armed_only && mailbox.requested_ns > 0 &&
      process_monotonic_now_ns() - mailbox.requested_ns > 10000000000LL) {
    mailbox.pending = false;
    mailbox.ready = false;
    mailbox.armed_only = false;
    mailbox.busy = false;
  }
  if (mailbox.busy) {
    pthread_mutex_unlock(&g_control_mutex);
    send_control_error(socket_fd, "another normal capture is pending");
    return true;
  }
  if (requested_generation != 0 &&
      mailbox.generation > requested_generation) {
    pthread_mutex_unlock(&g_control_mutex);
    send_control_error(socket_fd, "requested battle generation passed");
    return true;
  }
  const bool current_scene_active =
      mailbox.last_seen_ns > 0 &&
      process_monotonic_now_ns() - mailbox.last_seen_ns < 1000000000LL;
  const bool target_already_passed =
      mailbox.observed_manager != nullptr &&
      mailbox.last_tick >= requested_tick &&
      (requested_generation == 0
           ? current_scene_active
           : mailbox.generation == requested_generation);
  if (target_already_passed) {
    pthread_mutex_unlock(&g_control_mutex);
    send_control_error(socket_fd, "requested native tick already passed");
    return true;
  }
  const std::size_t required_bytes =
      tick_only ? 4096 : kFullObservationResponseBytes;
  if (mailbox.payload_bytes < required_bytes) {
    std::unique_ptr<char[]> allocation(new (std::nothrow)
                                            char[required_bytes]);
    if (allocation == nullptr) {
      pthread_mutex_unlock(&g_control_mutex);
      send_control_error(socket_fd, "normal capture workspace unavailable");
      return true;
    }
    mailbox.payload = std::move(allocation);
    mailbox.payload_bytes = required_bytes;
  }
  mailbox.payload[0] = '\0';
  mailbox.requested_generation = requested_generation;
  mailbox.requested_tick = requested_tick;
  mailbox.tick_only = tick_only;
  mailbox.requested_ns = process_monotonic_now_ns();
  mailbox.capture_started_ns = 0;
  mailbox.encode_ns = 0;
  mailbox.captured_generation = 0;
  mailbox.captured_tick = -1;
  mailbox.capture_ok = false;
  mailbox.pending = true;
  mailbox.ready = false;
  mailbox.busy = true;
  mailbox.armed_only = arm_only;
  if (arm_only) {
    pthread_mutex_unlock(&g_control_mutex);
    char response[160] = {};
    std::snprintf(response, sizeof(response),
                  "{\"ok\":true,\"schema\":\"normal-capture-armed.v1\","
                  "\"generation\":%llu,\"tick\":%d}",
                  requested_generation, requested_tick);
    send_control_response(socket_fd, response);
    return true;
  }
  normal_tick_mailbox_await_locked(socket_fd);
  return true;
}
