// Bounded read-only observer for the pinned v15 connection tick at 0xadcb10.
// It samples at entry only, then invokes the original exactly once. Sampling
// after the original would risk dereferencing a connection it released.

namespace offline_login_state_tick_observer {

struct Snapshot {
  std::int32_t application = -1;
  std::int32_t transport = -1;
  std::int32_t transport_failure = -1;
  std::int32_t handshake_ready = -1;
};

std::atomic<std::uint32_t> g_calls{0};
std::atomic<std::uint32_t> g_events{0};
std::atomic<std::int32_t> g_last_application{-1000000};
std::atomic<std::int32_t> g_last_transport{-1000000};
std::atomic<std::int32_t> g_last_transport_failure{-1000000};
std::atomic<std::int32_t> g_last_handshake_ready{-1000000};

Snapshot read_snapshot(void *connection) {
  Snapshot result;
  if (!process_range_is_readable(connection, 0xc9)) {
    return result;
  }
  result.application = __atomic_load_n(
      reinterpret_cast<std::int32_t *>(static_cast<std::uint8_t *>(connection) + 8), __ATOMIC_ACQUIRE);
  result.handshake_ready = __atomic_load_n(
      reinterpret_cast<std::uint8_t *>(static_cast<std::uint8_t *>(connection) + 0xc8), __ATOMIC_ACQUIRE) != 0
                               ? 1
                               : 0;
  void *const transport = read_object_field<void *>(connection, 0x10);
  if (!process_range_is_readable(transport, 0xac)) {
    return result;
  }
  result.transport = __atomic_load_n(
      reinterpret_cast<std::int32_t *>(static_cast<std::uint8_t *>(transport) + 0xa8), __ATOMIC_ACQUIRE);
  result.transport_failure = read_object_field<std::uint8_t>(transport, 8) != 0 ? 1 : 0;
  return result;
}

void hook(void *connection, float delta_seconds) {
  NormalStartLoginTick original = g_original_normal_start_login_tick;
  if (original == nullptr) {
    return;
  }
  const std::uint32_t call = g_calls.fetch_add(1, std::memory_order_relaxed) + 1;
  const Snapshot current = read_snapshot(connection);
  const bool application_changed =
      g_last_application.exchange(current.application, std::memory_order_acq_rel) != current.application;
  const bool transport_changed =
      g_last_transport.exchange(current.transport, std::memory_order_acq_rel) != current.transport;
  const bool failure_changed =
      g_last_transport_failure.exchange(current.transport_failure, std::memory_order_acq_rel) !=
      current.transport_failure;
  const bool handshake_changed =
      g_last_handshake_ready.exchange(current.handshake_ready, std::memory_order_acq_rel) !=
      current.handshake_ready;
  const bool changed = application_changed || transport_changed || failure_changed || handshake_changed;
  if ((call <= 8 || changed || call % 120 == 0) &&
      g_events.fetch_add(1, std::memory_order_relaxed) < 64) {
    LOGI("offline-login tick call=%u appState=%d transportState=%d transportFailure=%d handshakeReady=%d "
         "changed=%d tid=%ld",
         call, current.application, current.transport, current.transport_failure, current.handshake_ready,
         changed ? 1 : 0, static_cast<long>(syscall(__NR_gettid)));
  }
  original(connection, delta_seconds);
}

}  // namespace offline_login_state_tick_observer
