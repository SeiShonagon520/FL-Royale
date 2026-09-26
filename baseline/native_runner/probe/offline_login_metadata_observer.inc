// Deferred, read-only observer for the pinned v15 20963 message. Include this
// before normal_start_inbound_dispatch_hook() and call observe(message) only
// when that existing hook has determined ID 20963, before original dispatch.
// No second inline hook is installed. Never access the message after dispatch.

#include "offline_login_pointer_guard.h"

namespace offline_login_metadata_observer {

std::atomic<std::uint32_t> g_events{0};

// -1 means null; -2 means a nonnull but unreadable field. Only lengths are
// observed; never materialize the underlying string bytes.
std::int32_t string_length(void *message, std::size_t field_offset) {
  void *const value = read_object_field<void *>(message, field_offset);
  if (value == nullptr) {
    return -1;
  }
  if (!process_range_is_readable(value, 8)) {
    return -2;
  }
  const std::int32_t length = read_object_field<std::int32_t>(value, 4);
  const std::int32_t maximum = field_offset == 0xf0 || field_offset == 0xf8 ? 50 : 900000;
  return length >= 0 && length <= maximum ? length : -3;
}

std::int32_t array_count(void *message, std::size_t field_offset) {
  void *const value = read_object_field<void *>(message, field_offset);
  if (value == nullptr) {
    return -1;
  }
  if (!process_range_is_readable(value, 0x10)) {
    return -2;
  }
  const std::int32_t count = read_object_field<std::int32_t>(value, 0xc);
  return count >= 0 && count <= 100 ? count : -3;
}

std::int32_t tri_state_presence(const void *pointer, std::size_t bytes = 1) {
  if (pointer == nullptr) {
    return 0;
  }
  return process_range_is_readable(pointer, bytes) ? 1 : -1;
}

void observe(void *message) {
  const std::uint32_t event = g_events.fetch_add(1, std::memory_order_relaxed) + 1;
  if (event > 4) {
    return;
  }
  if (!process_range_is_readable(message, 0x1b0) ||
      read_object_field<void *>(message, 0) != reinterpret_cast<void *>(g_libg_base + 0x018a4c70)) {
    LOGI("offline-login metadata event=%u id=20963 validMessage=0", event);
    return;
  }

  const std::int32_t version = read_object_field<std::int32_t>(message, 0x88);
  const std::int32_t consumed = read_object_field<std::int32_t>(message, 0x1c);
  const std::int32_t bytes = read_object_field<std::int32_t>(message, 0x20);
  const std::int32_t inline_length = read_object_field<std::int32_t>(message, 0x154);
  LOGI("offline-login metadata event=%u id=20963 version=%d consumed=%d bytes=%d idsPresent=%d,%d "
       "fixedStringLengths=%d,%d,%d,%d,%d,%d,%d inlineStringLength=%d",
       event, version, consumed, bytes,
       read_object_field<void *>(message, 0x90) != nullptr ? 1 : 0,
       read_object_field<void *>(message, 0x98) != nullptr ? 1 : 0,
       string_length(message, 0xa0), string_length(message, 0xa8), string_length(message, 0xb0),
       string_length(message, 0xd0), string_length(message, 0xb8), string_length(message, 0xf0),
       string_length(message, 0xf8), inline_length);
  LOGI("offline-login metadata event=%u extendedStringLengths=%d,%d,%d,%d,%d tailStringLengths=%d,%d,%d,%d,%d,%d,%d,%d,%d "
       "arrayCounts=%d,%d optionalBlock=%d optionalObject=%d flags=%d,%d,%d",
       event,
       string_length(message, 0x100), string_length(message, 0x108), string_length(message, 0x110),
       string_length(message, 0x118), string_length(message, 0xe8),
       string_length(message, 0x160), string_length(message, 0x168), string_length(message, 0x170),
       string_length(message, 0x178), string_length(message, 0x180), string_length(message, 0x188),
       string_length(message, 0x190), string_length(message, 0x198), string_length(message, 0x1a0),
       array_count(message, 0x130), array_count(message, 0x138),
       read_object_field<void *>(message, 0x140) != nullptr ? 1 : 0,
       read_object_field<void *>(message, 0x1a8) != nullptr ? 1 : 0,
       read_object_field<std::uint8_t>(message, 0x148) != 0 ? 1 : 0,
       read_object_field<std::uint8_t>(message, 0x149) != 0 ? 1 : 0,
       read_object_field<std::uint8_t>(message, 0x14a) != 0 ? 1 : 0);

  void *connection = nullptr;
  std::int32_t login_state = -1;
  std::int32_t connection_slot = -1;
  void *const root_slot = reinterpret_cast<void *>(g_libg_base + kNormalStartLoginStateRootOffset);
  if (process_range_is_readable(root_slot, sizeof(void *))) {
    void *const root = read_object_field<void *>(root_slot, 0);
    if (process_range_is_readable(root, sizeof(void *))) {
      connection = read_object_field<void *>(root, 0);
      if (process_range_is_readable(connection, 0x70)) {
        login_state = read_object_field<std::int32_t>(connection, 8);
        const std::int32_t index = read_object_field<std::int32_t>(connection, 0x58);
        const std::uintptr_t begin = reinterpret_cast<std::uintptr_t>(
            read_object_field<void *>(connection, 0x60));
        const std::uintptr_t end = reinterpret_cast<std::uintptr_t>(
            read_object_field<void *>(connection, 0x68));
        if (begin != 0 && end >= begin && end - begin <= 0x400 &&
            (end - begin) % 0x20 == 0 && index >= 0 &&
            static_cast<std::uintptr_t>(index) < (end - begin) / 0x20) {
          connection_slot = process_range_is_readable(
                                reinterpret_cast<void *>(begin + static_cast<std::uintptr_t>(index) * 0x20), 0x20)
                                ? 1 : 0;
        }
      }
    }
  }
  void *const game_app_slot = reinterpret_cast<void *>(g_libg_base + 0x01997cf0);
  void *const game_app = process_range_is_readable(game_app_slot, sizeof(void *))
                             ? read_object_field<void *>(game_app_slot, 0)
                             : nullptr;
  const std::int32_t game_app_context = process_range_is_readable(game_app, 0x518)
                                            ? tri_state_presence(read_object_field<void *>(game_app, 0x510))
                                            : -1;
  void *const global_a = offline_login_pointer_guard::read_live_object(
      g_libg_base, offline_login_pointer_guard::kGlobalAManagerGot, process_range_is_readable);
  void *const global_b = offline_login_pointer_guard::read_live_object(
      g_libg_base, offline_login_pointer_guard::kHomeWrapperGot, process_range_is_readable);
  const std::int32_t global_b_child = process_range_is_readable(global_b, 0x20)
                                          ? tri_state_presence(read_object_field<void *>(global_b, 0x18))
                                          : -1;
  void *const flag_slot = reinterpret_cast<void *>(g_libg_base + 0x018e5df8);
  void *const flag = process_range_is_readable(flag_slot, sizeof(void *))
                         ? read_object_field<void *>(flag_slot, 0)
                         : nullptr;
  const std::int32_t flag_zero = process_range_is_readable(flag, 1)
                                     ? (read_object_field<std::uint8_t>(flag, 0) == 0 ? 1 : 0)
                                     : -1;
  void *const dispatch_owner = offline_login_pointer_guard::read_live_object(
      g_libg_base, offline_login_pointer_guard::kDispatcherGot, process_range_is_readable);
  LOGI("offline-login metadata event=%u preDispatchState=%d indexedSlot=%d gameAppContext=%d globalA=%d "
       "globalB=%d globalBChild=%d loginFlagZero=%d dispatchOwnerReadable=%d",
       event, login_state, connection_slot, game_app_context, tri_state_presence(global_a, 0x41),
       tri_state_presence(global_b, 0x20), global_b_child, flag_zero,
       tri_state_presence(dispatch_owner, sizeof(void *)));
}

}  // namespace offline_login_metadata_observer
