// Exact-build, behavior-preserving origin probe for the live battle-context
// mode-4 path. The c8dfd0 setter is only eight bytes and cannot take the
// existing sixteen-byte inline trampoline. Its immediate next call from
// c90c3c reaches c8e2c4 with LR=c90c40; replay JSON setup uses a different
// path. This hook only logs bounded metadata and always forwards.
constexpr std::uintptr_t kNormalMode4OriginOffset = 0x00c8e2c4;
constexpr std::uint8_t kNormalMode4OriginPrologue[16] = {
    0xfd, 0x7b, 0xbe, 0xa9, 0xf3, 0x0b, 0x00, 0xf9,
    0xfd, 0x03, 0x00, 0x91, 0xf3, 0x03, 0x00, 0xaa,
};
using NormalMode4Origin = void (*)(void *);
NormalMode4Origin g_original_normal_mode4_origin = nullptr;
std::atomic<std::uint32_t> g_normal_mode4_origin_events{0};

void normal_mode4_origin_observer_hook(void *singleton) {
  NormalMode4Origin original = g_original_normal_mode4_origin;
  if (original == nullptr) {
    return;
  }
  const std::uint32_t event = g_normal_mode4_origin_events.fetch_add(1, std::memory_order_relaxed) + 1;
  const std::uintptr_t return_address = reinterpret_cast<std::uintptr_t>(__builtin_return_address(0));
  const std::uintptr_t caller_rva = return_address >= g_libg_base ? return_address - g_libg_base
                                                                  : static_cast<std::uintptr_t>(-1);
  const bool readable_before = event <= 16 && process_range_is_readable(singleton, 0x80);
  const std::int32_t pending_before = readable_before ? read_object_field<std::int32_t>(singleton, 0x34) : -1;
  const bool context_before = readable_before &&
                              read_object_field<void *>(singleton, 0x48) != nullptr;
  const bool replay_before = readable_before &&
                             read_object_field<void *>(singleton, 0x78) != nullptr;
  original(singleton);
  if (event <= 16) {
    const bool readable_after = process_range_is_readable(singleton, 0x80);
    LOGI("normal-mode4-origin event=%u callerRva=0x%zx singleton=%p pendingBefore=%d "
         "contextBefore=%d replayBefore=%d pendingAfter=%d controllerAfter=%p tid=%ld",
         event, static_cast<std::size_t>(caller_rva), singleton, pending_before,
         context_before ? 1 : 0, replay_before ? 1 : 0,
         readable_after ? read_object_field<std::int32_t>(singleton, 0x34) : -1,
         readable_after ? read_object_field<void *>(singleton, 0x20) : nullptr,
         static_cast<long>(syscall(__NR_gettid)));
  }
}
