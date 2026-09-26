// Exact-build, behavior-preserving BattlePage click observer. Keep this
// separate from the experimental local authority path and Java UI bridge.
constexpr std::uintptr_t kMainBattlePageClickOffset = 0x00bd5f34;
constexpr std::uint8_t kMainBattlePageClickPrologue[16] = {
    0xff, 0x83, 0x01, 0xd1, 0xfd, 0x7b, 0x02, 0xa9, 0xf8, 0x5f, 0x03, 0xa9, 0xf6, 0x57, 0x04, 0xa9,
};
using MainBattlePageClick = void (*)(void *, void *);
MainBattlePageClick g_original_main_battle_page_click = nullptr;
std::atomic<std::uint32_t> g_main_battle_page_click_events{0};

void main_battle_page_click_observer_hook(void *page, void *clicked) {
  MainBattlePageClick original = g_original_main_battle_page_click;
  if (original == nullptr) {
    return;
  }
#if defined(FIRSTLIGHT_LOCAL_APP)
  if (g_local_boot_ready.load(std::memory_order_acquire) && g_local_deck_storage &&
      process_range_is_readable(page,0x138)) {
    void* wrapper = read_object_field<void*>(page,0x130);
    if (process_range_is_readable(wrapper,0x30) && read_object_field<void*>(wrapper,0x28)==clicked) {
      firstlight::RequestBattlePickerFromGameThread();
      return;
    }
  }
#endif
  const std::uint32_t event = g_main_battle_page_click_events.fetch_add(1, std::memory_order_relaxed) + 1;
  if (event <= 16) {
    void *button = nullptr;
    bool wrapper_present = false;
    if (process_range_is_readable(page, 0x138)) {
      void *const wrapper = read_object_field<void *>(page, 0x130);
      wrapper_present = process_range_is_readable(wrapper, 0x30);
      if (wrapper_present) {
        button = read_object_field<void *>(wrapper, 0x28);
      }
    }
    LOGI("main-battle-click event=%u wrapper=%d button=%d matched=%d tid=%ld", event,
         wrapper_present ? 1 : 0, button != nullptr ? 1 : 0,
         button != nullptr && button == clicked ? 1 : 0, static_cast<long>(syscall(__NR_gettid)));
  }
  original(page, clicked);
}
