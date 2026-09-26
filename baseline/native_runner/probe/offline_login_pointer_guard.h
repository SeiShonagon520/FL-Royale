#pragma once

#include <cstddef>
#include <cstdint>
#include <cstring>

namespace offline_login_pointer_guard {

// These GOT entries address singleton pointer storage, not the live object.
constexpr std::uintptr_t kGlobalAManagerGot = 0x018e58e8;
constexpr std::uintptr_t kHomeWrapperGot = 0x018e5968;
constexpr std::uintptr_t kDispatcherGot = 0x018e5928;

inline void *read_live_object(std::uintptr_t base, std::uintptr_t got_rva,
                              bool (*readable)(const void *, std::size_t)) {
  const void *const got_slot = reinterpret_cast<const void *>(base + got_rva);
  if (!readable(got_slot, sizeof(void *))) {
    return nullptr;
  }
  void *storage = nullptr;
  std::memcpy(&storage, got_slot, sizeof(storage));
  if (storage == nullptr || !readable(storage, sizeof(void *))) {
    return nullptr;
  }
  void *object = nullptr;
  std::memcpy(&object, storage, sizeof(object));
  return object;
}

}  // namespace offline_login_pointer_guard
