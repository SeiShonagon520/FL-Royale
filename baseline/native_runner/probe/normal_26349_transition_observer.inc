// Behavior-preserving observer for the 26349 message path that can set pending
// mode 4 and immediately create a controller at ad9190..ad91ac. This function is
// also used by a delayed-dispatch path, so record the caller RVA at entry.
constexpr std::uintptr_t kNormal26349TransitionOffset = 0x00ad9070;
constexpr std::uint8_t kNormal26349TransitionPrologue[16] = {
    0xe8, 0x0f, 0x1c, 0xfc, 0xfd, 0x7b, 0x01, 0xa9,
    0xf6, 0x57, 0x02, 0xa9, 0xf4, 0x4f, 0x03, 0xa9,
};
using Normal26349Transition = void (*)(void *, void *);
Normal26349Transition g_original_normal_26349_transition = nullptr;
std::atomic<std::uint32_t> g_normal_26349_transition_events{0};

void normal_26349_transition_observer_hook(void *owner, void *message) {
  Normal26349Transition original = g_original_normal_26349_transition;
  if (original == nullptr) {
    return;
  }
  const std::uint32_t event = g_normal_26349_transition_events.fetch_add(1, std::memory_order_relaxed) + 1;
  const std::uintptr_t return_address = reinterpret_cast<std::uintptr_t>(__builtin_return_address(0));
  const std::uintptr_t caller_rva = return_address >= g_libg_base ? return_address - g_libg_base
                                                                  : static_cast<std::uintptr_t>(-1);
  void *const singleton_before = g_replay_manager_getter != nullptr ? g_replay_manager_getter() : nullptr;
  const bool readable_before = event <= 16 && process_range_is_readable(singleton_before, 0x50);
  const std::int32_t mode_before = readable_before ? read_object_field<std::int32_t>(singleton_before, 0x30) : -1;
  const std::int32_t pending_before = readable_before ? read_object_field<std::int32_t>(singleton_before, 0x34) : -1;
  original(owner, message);
  if (event <= 16) {
    void *const singleton_after = g_replay_manager_getter != nullptr ? g_replay_manager_getter() : nullptr;
    const bool readable_after = process_range_is_readable(singleton_after, 0x50);
    void *const controller = readable_after ? read_object_field<void *>(singleton_after, 0x20) : nullptr;
    const bool controller_readable = process_range_is_readable(controller, 0x98);
    LOGI("normal-26349-transition event=%u callerRva=0x%zx owner=%p message=%p "
         "singletonBefore=%p modeBefore=%d pendingBefore=%d singletonAfter=%p "
         "modeAfter=%d controller=%p controllerSubtype=%d battleManager=%p tid=%ld",
         event, static_cast<std::size_t>(caller_rva), owner, message, singleton_before, mode_before,
         pending_before, singleton_after,
         readable_after ? read_object_field<std::int32_t>(singleton_after, 0x30) : -1,
         controller, controller_readable ? read_object_field<std::int32_t>(controller, 0x58) : -1,
         controller_readable ? read_object_field<void *>(controller, 0x90) : nullptr,
         static_cast<long>(syscall(__NR_gettid)));
  }
}
