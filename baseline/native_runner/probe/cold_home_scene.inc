// Disposable constructor-first Home experiment. No online payload is reused.
#include "local_card_catalog.inc"
#include "local_deck_storage.inc"
#include "local_cosmetics.inc"
using ColdHomeUpdate = void(*)(void*, float, float);
ColdHomeUpdate g_original_cold_home_update = nullptr;
bool g_local_home_initializing=false;
void cold_home_update(void* scene, float first, float second) {
  if (g_offline_control_session_active.load(std::memory_order_acquire)) {
    // Skip the online terms prompt without accepting terms or sending its command.
    static_cast<std::uint8_t*>(scene)[0x49a] = 1;
  }
  g_original_cold_home_update(scene, first, second);
  if(g_local_home_initializing && g_local_cosmetic_home) {
    // First-Home setup grants the default Princess again after the constructor.
    // Normalize that native grant only after setup, alongside restored decks.
    void* collection=read_object_field<void*>(read_object_field<void*>(g_local_cosmetic_home,0x28),0);
    auto supports=read_object_field<void**>(collection,0x20);
    const int count=read_object_field<int>(collection,0x2c);
    for(int i=0;i<count && i<16;++i) {
      void* support=supports[i];
      void* asset=read_object_field<void*>(support,0x18);
      for(const auto& card:kLocalSupportCards) if(card.id==read_object_field<int>(asset,0x40)) {
        LOGI("local-home fixed tower id=%d level=%d -> %d",card.id,read_object_field<int>(support,0x20),card.level);
        std::memcpy(static_cast<std::uint8_t*>(support)+0x20,&card.level,4);
      }
    }
    g_local_home_initializing=false;
  }
  g_local_decks_restoring=false;
  static unsigned save_frames = 0;
  if (g_offline_control_session_active.load(std::memory_order_acquire) && ++save_frames % 60 == 0) {
    save_local_decks(); save_local_cosmetics();
  }
}
void install_cold_home_update(std::uintptr_t base) {
  static constexpr std::uint8_t prologue[16] = {
    0xff,0x83,0x06,0xd1,0xec,0x8b,0x00,0xfd,0xeb,0x2b,0x12,0x6d,0xe9,0x23,0x13,0x6d
  };
  const bool ok = install_inline_hook(base, 0xbbda68, prologue,
      reinterpret_cast<const void*>(&cold_home_update),
      reinterpret_cast<void**>(&g_original_cold_home_update), "cold-home local update");
  LOGI("cold-home update hook installed=%d", ok);
}
void fill_cold_home_deck(void* deck, void* collection, void* context) {
  const int count = 8;
  // Matches the first decoded count in fb07d0, backed by 40 inline slots.
  std::memcpy(static_cast<std::uint8_t*>(deck) + 0x20, &count, sizeof(count));
  auto collection_vtable = read_object_field<void**>(collection, 0);
  constexpr int cards[] = {26000000,26000001,26000005,28000001,28000000,26000003,26000014,26000018};
  for (int slot = 0; slot != count; ++slot) {
    void* card = reinterpret_cast<void*(*)(void*,void*)>(collection_vtable[0x20/8])(collection,g_logic_data_by_global_id(context,cards[slot]));
    void* clone = reinterpret_cast<void*(*)(void*)>(g_libg_base + 0xfef7ac)(card);
    reinterpret_cast<void(*)(void*,int,void*)>(g_libg_base + 0xfb2950)(deck, slot, clone);
  }
  void* support = g_game_state_new(0x30);
  reinterpret_cast<void(*)(void*,void*)>(g_libg_base + 0xff10bc)(support, context);
  void* json = parse_json_text("{\"d\":159000000,\"l\":10,\"t\":0,\"c\":0,\"newu\":false,\"newf\":false}");
  reinterpret_cast<void(*)(void*,void*)>(g_libg_base + 0xff15e8)(support, json);
  release_json_object(json);
  reinterpret_cast<void(*)(void*,int,void*)>(g_libg_base + 0xfb2ac8)(deck, 0, support);
}

void fill_cold_home_deck_proxy(void* proxy, int count, void* collection, void* context) {
  reinterpret_cast<void(*)(void*,int)>(g_libg_base + 0xfcc2c8)(static_cast<std::uint8_t*>(proxy)+8, count);
  auto slots = read_object_field<void**>(proxy, 8);
  for (int i = 0; i != count; ++i) {
    void* deck = g_game_state_new(0x1b0);
    reinterpret_cast<void(*)(void*,void*)>(g_libg_base + 0xfafe48)(deck, context);
    fill_cold_home_deck(deck, collection, context);
    slots[i] = deck;
  }
  std::memcpy(static_cast<std::uint8_t*>(proxy)+0x14, &count, sizeof(count));
}

void fill_cold_saved_decks(void* storage, void* context) {
  void* modes = read_object_field<void*>(storage, 0x10);
  const int count = read_object_field<int>(modes, 0xc);
  auto items = read_object_field<void**>(modes, 0);
  LOGI("cold-home native deck modes=%d", count);
  constexpr int cards[] = {26000000,26000001,26000005,28000001,28000000,26000003,26000014,26000018};
  for (int i = 0; i < count && i < 64; ++i) {
    void* rule = reinterpret_cast<void*(*)(void*)>(g_libg_base+0xff24f8)(items[i]);
    if (!rule) continue;
    auto vtable = read_object_field<void**>(rule, 0);
    void* key = reinterpret_cast<void*(*)(void*)>(vtable[0x88/8])(rule);
    void* selected_key = reinterpret_cast<void*(*)(void*)>(vtable[0x90/8])(rule);
    char name[129]{}; std::size_t length = 0;
    read_bounded_libg_string_utf8(key, name, sizeof(name), &length);
    void* existing = reinterpret_cast<void*(*)(void*,void*)>(g_libg_base+0xfc6b28)(static_cast<std::uint8_t*>(storage)+0x28,key);
    LOGI("cold-home mode=%d deckKey=%s existing=%d", i, name, existing != nullptr);
    if (existing) continue;
    void* deck = g_game_state_new(0x1c8);
    reinterpret_cast<void(*)(void*,void*,void*)>(g_libg_base+0xf8beac)(deck,context,rule);
    const int card_count = 8;
    std::memcpy(static_cast<std::uint8_t*>(deck)+0x148,&card_count,4);
    for (int slot = 0; slot != 8; ++slot) {
      void* asset = g_logic_data_by_global_id(context,cards[slot]);
      std::memcpy(static_cast<std::uint8_t*>(deck)+8+slot*8,&asset,8);
    }
    auto supports = read_object_field<void**>(deck,0x150);
    if (supports && read_object_field<int>(deck,0x15c)>0) supports[0] = g_logic_data_by_global_id(context,159000000);
    void* vector = g_game_state_new(0x10);
    void* data = g_game_state_new(8);
    std::memcpy(data,&deck,8);
    std::memcpy(vector,&data,8);
    const int one = 1;
    std::memcpy(static_cast<std::uint8_t*>(vector)+8,&one,4);
    std::memcpy(static_cast<std::uint8_t*>(vector)+12,&one,4);
    void* key_pointer = key; std::uint8_t inserted = 0;
    using Insert = void*(*)(void*,void*,void*,void**,std::uint8_t*);
    void* node = reinterpret_cast<Insert>(g_libg_base+0xfc7b38)(static_cast<std::uint8_t*>(storage)+0x28,key,
        reinterpret_cast<void*>(g_libg_base+0x33f9b2),&key_pointer,&inserted);
    std::memcpy(static_cast<std::uint8_t*>(node)+0x20,&vector,8);
    key_pointer = selected_key;
    node = reinterpret_cast<Insert>(g_libg_base+0xc9f290)(static_cast<std::uint8_t*>(storage)+0x50,selected_key,
        reinterpret_cast<void*>(g_libg_base+0x33f9b2),&key_pointer,&inserted);
    const int selected = 0;
    std::memcpy(static_cast<std::uint8_t*>(node)+0x20,&selected,4);
  }
}

// Reason codes reported to the Java layer so a device that cannot build the
// offline Home scene shows a real message instead of an endless 96% screen.
enum : std::int32_t {
  kColdHomeOk = 0,
  kColdHomeNotIsolated = 1,
  kColdHomeMessagingUnreadable = 2,
  kColdHomeProfileUnreadable = 3,
  kColdHomeCardCreationFailed = 4,
  kColdHomeDegraded = 5,
};
std::atomic<std::int32_t> g_cold_home_status{kColdHomeOk};
std::atomic<std::int32_t> g_cold_home_attempts{0};
// After this many failed attempts the scene is built with a core-only card
// collection. A slow device then reaches the lobby instead of retrying forever.
constexpr std::int32_t kColdHomeDegradeAfterAttempts = 3;
constexpr std::size_t kColdHomeCoreCardCount = 24;

// Publishes the reason to Java exactly once per state change so the UI can
// surface it without being called every frame.
void publish_cold_home_status(std::int32_t status) {
  const std::int32_t previous = g_cold_home_status.exchange(status, std::memory_order_release);
  if (previous == status) return;
  LOGI("cold-home status=%d attempts=%d", status,
       g_cold_home_attempts.load(std::memory_order_relaxed));
  LocalJavaEnv java;
  if (java.env && g_local_runtime_class && g_local_home_status)
    java.env->CallStaticVoidMethod(g_local_runtime_class, g_local_home_status,
                                   static_cast<jint>(status),
                                   static_cast<jint>(g_cold_home_attempts.load(std::memory_order_relaxed)));
}

bool start_cold_home_scene(void* parsed, void* context_a, void* context_b) {
  g_cold_home_attempts.fetch_add(1, std::memory_order_relaxed);
  g_cold_home_status.store(-1, std::memory_order_release);
#if !defined(FIRSTLIGHT_LOCAL_APP)
  if (!clone_adapter::isolated()) { publish_cold_home_status(kColdHomeNotIsolated); return false; }
#endif
  void* singleton = g_replay_manager_getter();
  void* messaging = read_object_field<void*>(reinterpret_cast<void*>(g_libg_base + 0x19b3ba8), 0);
  if (!process_range_is_readable(messaging, 0x40)) {
    LOGE("cold-home aborted: messaging object unreadable base=%lu",
         static_cast<unsigned long>(g_libg_base));
    publish_cold_home_status(kColdHomeMessagingUnreadable);
    return false;
  }
  reinterpret_cast<void(*)(void*,int,int)>(g_libg_base + 0xad9478)(messaging, 0, 1);
  void* source = g_game_state_new(0x230);
  g_game_state_constructor(source, 1, context_a, context_b, 0);
  g_game_state_setup(source, 1);
  g_original_game_state_load(source, parsed, nullptr, nullptr);
  void* profile = read_object_field<void*>(source, 0xd8);
  if (!process_range_is_readable(profile, 0x208)) {
    LOGE("cold-home aborted: profile object unreadable profile=%p", profile);
    publish_cold_home_status(kColdHomeProfileUnreadable);
    return false;
  }
  reinterpret_cast<void(*)(void*,int)>(g_libg_base+0xce15c0)(profile, 1); // name chosen
  const int tutorial_matches = reinterpret_cast<int(*)(void*,int)>(g_libg_base+0x1160c7c)(context_a, read_object_field<int>(profile, 0x188));
  reinterpret_cast<void(*)(void*,int)>(g_libg_base+0xce386c)(profile, tutorial_matches);
  reinterpret_cast<void(*)(void*)>(g_libg_base+0xce65e4)(profile);
  LOGI("cold-home named profile tutorialMatches=%d", tutorial_matches);
  void* arena = read_object_field<void*>(profile, 0x160);
  LOGI("cold-home profile arena=%d tutorialDone=%d trophies=%d level=%d", arena ? read_object_field<int>(arena, 0x40) : -1,
       reinterpret_cast<int(*)(void*)>(g_libg_base+0xce77bc)(profile),
       read_object_field<int>(profile, 0x88), read_object_field<int>(profile, 0xe8));
  void* home = g_game_state_new(0x570);
  reinterpret_cast<void(*)(void*,void*,void*)>(g_libg_base + 0xf948a4)(home, context_a, context_b);
  // Stock account-promotion dismissal flag, set locally before any Home owner
  // exists. This does not link an account or issue a native network command.
  reinterpret_cast<void(*)(void*,int,int)>(g_libg_base+0xf9cbbc)(home,11,1);
  LOGI("cold-home constructed home=1 profile=1 homeId=%d", read_object_field<void*>(home, 0x20) != nullptr);
  // Populate the same owned collection class used by f96f48. Card JSON
  // parsing is native feedb8, using the starting_home card schema.
  //
  // Building all 122 cards is the heaviest step on a slow device. A card that
  // cannot be created is skipped rather than aborting the whole scene, and once
  // several attempts have failed the collection is limited to the core cards so
  // a weak device still reaches the lobby instead of retrying forever.
  const std::int32_t attempt = g_cold_home_attempts.load(std::memory_order_relaxed);
  constexpr std::size_t kLocalCardTotal = sizeof(kLocalCards) / sizeof(kLocalCards[0]);
  const bool degraded = attempt > kColdHomeDegradeAfterAttempts;
  const std::size_t card_limit = degraded
      ? (kLocalCardTotal < kColdHomeCoreCardCount ? kLocalCardTotal : kColdHomeCoreCardCount)
      : kLocalCardTotal;
  std::int32_t skipped = 0;
  void* collection = g_game_state_new(0x48);
  reinterpret_cast<void(*)(void*,void*,void*)>(g_libg_base + 0xfcc3f0)(collection, context_a, nullptr);
  void* collection_owner = read_object_field<void*>(home, 0x28);
  reinterpret_cast<void(*)(void*,void*)>(g_libg_base + 0xf8f1f0)(collection_owner, collection);
  std::size_t built = 0;
  for (const auto& entry : kLocalCards) {
    if (built >= card_limit) break;
    const int card_id = entry.id;
    char text[192]{};
    std::snprintf(text, sizeof(text), "{\"d\":%d,\"t\":0,\"l\":%d,\"el\":%d,\"c\":0}", card_id, entry.level, entry.forms);
    void* json = parse_json_text(text);
    void* card = reinterpret_cast<void*(*)(void*,void*)>(g_libg_base + 0xfeedb8)(context_a, json);
    release_json_object(json);
    if (!card || !reinterpret_cast<int(*)(void*,void*)>(g_libg_base + 0xfcc634)(collection, card)) {
      LOGE("cold-home card creation failed id=%d attempt=%d", card_id, attempt);
      ++skipped;
      publish_cold_home_status(kColdHomeCardCreationFailed);
      if (!degraded) {
        g_game_state_delete(collection);
        g_game_state_delete(home);
      }
      if (!degraded) return false;
      continue;
    }
    ++built;
  }
  if (skipped > 0 && built > 0) {
    // Some cards are missing but the scene is usable: tell the UI so the
    // player learns about it instead of wondering why a card is absent.
    publish_cold_home_status(kColdHomeDegraded);
  }
  LOGI("cold-home collection cards=%d limit=%zu degraded=%d skipped=%d",
       read_object_field<int>(collection, 0x1c), card_limit, degraded ? 1 : 0, skipped);
  // Stock Home decode always reads seven optional restriction documents.
  // Decode seven absent entries to build the native neutral rule objects;
  // the bare constructor leaves the indexed rule array empty.
  void* stream = g_game_state_new(0x80);
  reinterpret_cast<void(*)(void*,int)>(g_libg_base + 0x1211ef4)(stream, 128);
  void* stream_vtable = read_object_field<void*>(stream, 0);
  auto write_bool = reinterpret_cast<void(*)(void*,int)>(read_object_field<void*>(stream_vtable, 0x40));
  for (int i = 0; i != 7; ++i) write_bool(stream, 0);
  g_byte_stream_flip_read(stream);
  void* restrictions = read_object_field<void*>(home, 0x1f8);
  reinterpret_cast<void(*)(void*,void*)>(g_libg_base + 0xec9520)(restrictions, stream);
  reinterpret_cast<void(*)(void*)>(g_libg_base + 0x1212088)(stream);
  g_game_state_delete(stream);
  LOGI("cold-home neutral rule objects=%d", read_object_field<int>(restrictions, 0x14));
  for(const auto& entry:kLocalSupportCards) {
    auto table=read_object_field<void**>(collection,0);
    void* support=reinterpret_cast<void*(*)(void*,void*)>(table[0x28/8])(
        collection,g_logic_data_by_global_id(context_a,entry.id));
    const bool existing=support!=nullptr;
    if(!existing) {
      support=g_game_state_new(0x30);
      reinterpret_cast<void(*)(void*,void*)>(g_libg_base+0xff10bc)(support,context_a);
    }
    char text[128]; std::snprintf(text,sizeof(text),"{\"d\":%d,\"l\":%d,\"t\":0,\"c\":0,\"newu\":false,\"newf\":false}",entry.id,entry.level);
    void* json=parse_json_text(text);
    reinterpret_cast<void(*)(void*,void*)>(g_libg_base+0xff15e8)(support,json);
    release_json_object(json);
    if(!existing) reinterpret_cast<int(*)(void*,void*)>(g_libg_base+0xfcc79c)(collection,support);
  }
  LOGI("cold-home support cards=%d", read_object_field<int>(collection, 0x2c));
  unlock_local_cosmetics(home,context_a);
  void* deck_storage = read_object_field<void*>(home, 0x4c0);
  if (!load_local_decks(deck_storage)) {
    fill_cold_home_deck(read_object_field<void*>(deck_storage, 0x20), collection, context_a);
    fill_cold_home_deck_proxy(read_object_field<void*>(deck_storage, 0xa0), 1, collection, context_a);
    void* four_decks = read_object_field<void*>(deck_storage, 0xa8);
    fill_cold_home_deck_proxy(read_object_field<void*>(four_decks, 0x18), 4, collection, context_a);
    fill_cold_saved_decks(deck_storage, context_a);
  }
  g_local_deck_storage = deck_storage;
  g_local_home_initializing=true;
  LOGI("cold-home default and four-deck UI slots populated");
  // Home's Long ID is owned and deleted by its stock destructor.
  if (!read_object_field<void*>(home, 0x20)) {
    void* identity = g_game_state_new(8);
    const std::uint32_t words[2] = {0,1};
    std::memcpy(identity, words, sizeof(words));
    std::memcpy(static_cast<std::uint8_t*>(home) + 0x20, &identity, sizeof(identity));
  }
  void* empty = nullptr;
  std::memcpy(static_cast<std::uint8_t*>(source) + 0xd8, &empty, sizeof(empty));
  g_game_state_destroy(source); g_game_state_base_destroy(source); g_game_state_delete(source);
  g_offline_control_session_active.store(true, std::memory_order_release);
  publish_cold_home_status(kColdHomeOk);
  const std::int32_t home_mode = 3;
  std::memcpy(static_cast<std::uint8_t*>(singleton) + 8, &home_mode, sizeof(home_mode));
  using Setup = void(*)(void*,void**,void**,int,int,int,int,int,int);
  reinterpret_cast<Setup>(g_libg_base + 0xc8e054)(singleton, &home, &profile, 0, 1, 0, 0, 0, 0);
  reinterpret_cast<void(*)(void*,int,int)>(g_libg_base + 0xc8dfd0)(singleton, 3, 0);
  LOGI("cold-home starting stock mode=3");
  g_original_normal_start_controller_transition(singleton);
  g_cold_scene_controller = read_object_field<void*>(singleton, 0x20);
  LOGI("cold-home controller created");
  return true;
}
