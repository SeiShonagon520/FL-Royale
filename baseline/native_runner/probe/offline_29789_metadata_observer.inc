// Optional, read-only summary of the pinned v15 29789 item array. Include
// before the existing inbound dispatcher hook and invoke before original
// dispatch only when that hook has already established ID 29789. No item IDs,
// bytes, pointers, or account values are logged.

namespace offline_29789_metadata_observer {

std::atomic<std::uint32_t> g_events{0};

void observe(void *message) {
  const std::uint32_t event = g_events.fetch_add(1, std::memory_order_relaxed) + 1;
  if (event > 2) {
    return;
  }
  if (!process_range_is_readable(message, 0xa0)) {
    LOGI("offline-home-list metadata event=%u id=29789 validMessage=0", event);
    return;
  }
  void *const array = read_object_field<void *>(message, 0x98);
  if (!process_range_is_readable(array, 0x10)) {
    LOGI("offline-home-list metadata event=%u id=29789 validArray=0", event);
    return;
  }
  const std::int32_t count = read_object_field<std::int32_t>(array, 0xc);
  void *const entries = read_object_field<void *>(array, 0);
  if (count < 0 || count > 512 ||
      (count != 0 && !process_range_is_readable(entries, static_cast<std::size_t>(count) * sizeof(void *)))) {
    LOGI("offline-home-list metadata event=%u id=29789 count=%d validEntries=0", event, count);
    return;
  }

  std::uint32_t type_counts[28] = {};
  std::uint32_t unreadable = 0;
  std::uint32_t other_type = 0;
  for (std::int32_t index = 0; index < count; ++index) {
    void *const item = read_object_field<void *>(entries, static_cast<std::size_t>(index) * sizeof(void *));
    if (!process_range_is_readable(item, sizeof(void *))) {
      ++unreadable;
      continue;
    }
    void *const vtable = read_object_field<void *>(item, 0);
    if (!process_range_is_readable(vtable, 0x28)) {
      ++unreadable;
      continue;
    }
    void *const getter = read_object_field<void *>(vtable, 0x20);
    const std::uintptr_t getter_address = reinterpret_cast<std::uintptr_t>(getter);
    // The stock handler invokes this exact virtual getter after taking each
    // item from the array. Keep our call inside the pinned libg executable
    // address range and skip any unexpected object layout.
    if (getter_address < g_libg_base + 0x1000 || getter_address >= g_libg_base + 0x17e0000 ||
        !process_range_is_readable(getter, 4)) {
      ++unreadable;
      continue;
    }
    const std::int32_t type = reinterpret_cast<std::int32_t (*)(void *)>(getter)(item);
    if (type >= 1 && type <= 27) {
      ++type_counts[type];
    } else {
      ++other_type;
    }
  }

  char histogram[256] = {};
  std::size_t used = 0;
  for (std::int32_t type = 1; type <= 27; ++type) {
    if (type_counts[type] == 0) {
      continue;
    }
    const int written = std::snprintf(histogram + used, sizeof(histogram) - used,
                                      "%s%d:%u", used == 0 ? "" : ",", type, type_counts[type]);
    if (written <= 0 || static_cast<std::size_t>(written) >= sizeof(histogram) - used) {
      break;
    }
    used += static_cast<std::size_t>(written);
  }
  LOGI("offline-home-list metadata event=%u id=29789 count=%d unreadable=%u otherType=%u typeCounts=%s",
       event, count, unreadable, other_type, histogram);
}

}  // namespace offline_29789_metadata_observer
