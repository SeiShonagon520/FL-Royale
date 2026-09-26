std::uint64_t g_local_expected_request=0;

using LocalTextSet=void*(*)(void*,void*,int);
LocalTextSet g_local_clan_text=nullptr;
void* local_clan_text(void* object,void* value,int flags) {
  // This exact call sets the live opponent header's clan label. Home and
  // profile labels use other call sites and keep their native text.
  if(reinterpret_cast<std::uintptr_t>(__builtin_return_address(0))==g_libg_base+0x8c8288 &&
      g_local_running.load(std::memory_order_acquire)) {
    LibgString clan;
    if(initialize_libg_string("Firstlight CR",&clan)) {
      LOGI("local-app AI clan displayed=Firstlight CR");
      return g_local_clan_text(object,&clan,flags);
    }
  }
  return g_local_clan_text(object,value,flags);
}
void install_local_battle_identity(std::uintptr_t base) {
  static constexpr std::uint8_t text_prologue[16]={0xfd,0x7b,0xbd,0xa9,0xf5,0x0b,0x00,0xf9,0xf4,0x4f,0x02,0xa9,0xfd,0x03,0x00,0x91};
  install_inline_hook(base,0x88a1b8,text_prologue,reinterpret_cast<void*>(&local_clan_text),
      reinterpret_cast<void**>(&g_local_clan_text),"local AI clan display");
}

void settle_local_command_reservation(void* manager,void* command,int id) {
  if(!g_local_running.load() || manager!=g_local_native_manager || id<=0) return;
  PlayerStateView player;
  if(!read_player_state(read_object_field<void*>(manager,0xa8),0,&player)) return;
  if(read_object_field<std::uint32_t>(command,0x14)!=player.account_id_high ||
     read_object_field<std::uint32_t>(command,0x18)!=player.account_id_low) return;
  void* prediction=read_object_field<void*>(player.player,0x248);
  if(!process_range_is_readable(prediction,0x30)) return;
  // Native acknowledgement only removes the active (+10) record. Input can
  // still have its record in the staged (+20) vector, promoted later during
  // this same step. Retire this command's cost in either vector before its
  // authoritative execution; other queued cards retain their reservations.
  // Keep all native hand/ghost records and their ownership/lifetime intact.
  for(std::size_t offset:{0x10u,0x20u}) {
    const int count=read_object_field<int>(prediction,offset+0xc);
    auto records=read_object_field<void**>(prediction,offset);
    if(count<1 || count>64 || !process_range_is_readable(records,count*8)) continue;
    for(int i=0;i<count;++i) {
      void* record=records[i];
      if(!process_range_is_readable(record,0x34) || read_object_field<int>(record,0)!=id) continue;
      const int cost=read_object_field<int>(record,0x10);
      if(cost<=0 || cost>20) continue;
      const int zero=0;
      std::memcpy(static_cast<std::uint8_t*>(record)+0x10,&zero,4);
      LOGI("local-app mana acknowledged tick=%d id=%d cost=%d raw=%d staged=%d",g_game_state_tick(manager),
          id,cost,player.elixir_raw,offset==0x20);
    }
  }
}

using LocalCommandExecute=int(*)(void*,void*,int,int);
LocalCommandExecute g_local_card_execute=nullptr,g_local_ability_execute=nullptr;
int local_card_execute(void* command,void* manager,int phase,int feedback) {
  if(phase&2) settle_local_command_reservation(manager,command,read_object_field<int>(command,0x50));
  return g_local_card_execute(command,manager,phase,feedback);
}
int local_ability_execute(void* command,void* manager,int phase,int feedback) {
  const bool record=(phase&2) && g_local_running.load() && manager==g_local_native_manager;
  const int id=record?read_object_field<int>(command,0x34):0;
  const int tick=record?g_game_state_tick(manager):-1;
  const int due=record?read_object_field<int>(command,0xc):-1;
  if(phase&2) settle_local_command_reservation(manager,command,read_object_field<int>(command,0x34));
  const int result=g_local_ability_execute(command,manager,phase,feedback);
  if(record) LOGI("local-app ability executed id=%d tick=%d dueTick=%d phase=%d result=%d executeNs=%lld",
      id,tick,due,phase,result,static_cast<long long>(process_monotonic_now_ns()));
  return result;
}
void install_local_command_accounting(std::uintptr_t base) {
  static constexpr std::uint8_t card[16]={0xff,0x43,0x02,0xd1,0xfd,0x7b,0x03,0xa9,0xfc,0x6f,0x04,0xa9,0xfa,0x67,0x05,0xa9};
  install_inline_hook(base,0xd30a00,card,reinterpret_cast<void*>(&local_card_execute),reinterpret_cast<void**>(&g_local_card_execute),"local card reservation acknowledgement");
  static constexpr std::uint8_t ability[16]={0xfd,0x7b,0xba,0xa9,0xfc,0x6f,0x01,0xa9,0xfa,0x67,0x02,0xa9,0xf8,0x5f,0x03,0xa9};
  install_inline_hook(base,0xd32a08,ability,reinterpret_cast<void*>(&local_ability_execute),reinterpret_cast<void**>(&g_local_ability_execute),"local ability reservation acknowledgement");
}

void normalize_local_touch_command(void* controller,void** owner) {
  if(!g_local_running.load() || controller!=g_cold_scene_controller || !owner || !*owner ||
     read_object_field<void*>(controller,0x90)!=g_local_native_manager) return;
  void* command=*owner;
  void* queue=read_object_field<void*>(g_local_native_manager,0x38);
  const int count=read_object_field<int>(queue,0x14);
  auto items=read_object_field<void**>(queue,8);
  bool queued=false;
  for(int index=0; index<count && index<128; ++index) if(items[index]==command) queued=true;
  if(!queued) return;
  auto table=read_object_field<void**>(command,0);
  const int kind=reinterpret_cast<int(*)(void*)>(table[0x20/8])(command);
  if(kind!=86 && kind!=2) return;
  const int tick=g_game_state_tick(g_local_native_manager);
  // Preserve the stock twenty-tick (one-second) boundary for both human
  // cards and abilities. AI commands use their own mailbox.
  if(read_object_field<int>(command,0xc)!=tick+20) return;
  LOGI("local-app human command kind=%d id=%d releasedTick=%d dueTick=%d releaseNs=%lld",kind,
      read_object_field<int>(command,kind==86?0x50:0x34),tick,read_object_field<int>(command,0xc),
      static_cast<long long>(process_monotonic_now_ns()));
}

std::string current_local_deck_json() {
  void* storage=g_local_deck_storage;
  if(!storage) return {};
  void* modes=read_object_field<void*>(storage,0x10);
  if(read_object_field<int>(modes,0xc)<1) return {};
  void* mode=read_object_field<void**>(modes,0)[0];
  void* rule=reinterpret_cast<void*(*)(void*)>(g_libg_base+0xff24f8)(mode);
  auto vtable=read_object_field<void**>(rule,0);
  void* key=reinterpret_cast<void*(*)(void*)>(vtable[0x88/8])(rule);
  void* selected_key=reinterpret_cast<void*(*)(void*)>(vtable[0x90/8])(rule);
  void* node=reinterpret_cast<void*(*)(void*,void*)>(g_libg_base+0xfc6b28)(static_cast<std::uint8_t*>(storage)+0x28,key);
  if(!node) return {};
  void* decks=read_object_field<void*>(node,0x20);
  void* selected=reinterpret_cast<void*(*)(void*,void*)>(g_libg_base+0xc9f138)(static_cast<std::uint8_t*>(storage)+0x50,selected_key);
  int index=selected?read_object_field<int>(selected,0x20):0;
  if(index<0 || index>=read_object_field<int>(decks,0xc)) return {};
  void* deck=read_object_field<void**>(decks,0)[index];
  if(read_object_field<int>(deck,0x148)!=8) return {};
  // The Home controller binds form-slot rules on its first update after a
  // restore. Do not publish a transient all-basic copy before that binding.
  if(read_object_field<int>(deck,0x16c)<8) return {};
  int ids[8]{};
  std::string cards="[", forms="[", active="[", names="[", choices="{";
  auto deck_vtable=read_object_field<void**>(deck,0);
  for(int slot=0;slot<8;++slot) {
    void* asset=read_object_field<void*>(deck,8+slot*8);
    if(!asset) return {};
    ids[slot]=read_object_field<int>(asset,0x40);
    int availability=-1;
    for(const auto& card:kLocalCards) if(card.id==ids[slot]) availability=card.forms;
    if(availability<0) return {};
    for(int earlier=0;earlier<slot;++earlier) if(ids[slot]==ids[earlier]) return {};
    char name[129]{}; std::size_t length=0;
    void* native_name=reinterpret_cast<void*(*)(void*)>(g_libg_base+0xdc25ec)(asset);
    if(!read_bounded_libg_string_utf8(native_name,name,sizeof(name),&length)) return {};
    for(std::size_t i=0;i<length;++i) if(name[i]=='"' || name[i]=='\\' || static_cast<unsigned char>(name[i])<32) return {};
    const int form=reinterpret_cast<int(*)(void*,int,void*)>(deck_vtable[0x168/8])(deck,slot,nullptr);
    const int enabled=((form==1 && (availability&1)) || (form==2 && (availability&2)))?form:0;
    if(slot) { cards+=","; forms+=","; active+=","; names+=","; choices+=","; }
    cards+=std::to_string(ids[slot]); forms+=std::to_string(availability); active+=std::to_string(enabled);
    names+="\""+std::string(name)+"\"";
    choices+="\""+std::string(name)+"\":"+std::to_string(form==2?2:1);
  }
  if(read_object_field<int>(deck,0x15c)!=1) return {};
  void* tower=read_object_field<void**>(deck,0x150)[0];
  const int tower_id=read_object_field<int>(tower,0x40);
  bool supported=false;
  for(const auto& card:kLocalSupportCards) if(card.id==tower_id) supported=true;
  if(!supported) return {};
  return "{\"cards\":"+cards+"],\"forms\":"+forms+"],\"active\":"+active+"],\"names\":"+names+
      "],\"choices\":"+choices+"},\"slot\":"+std::to_string(index+1)+
      ",\"tower\":"+std::to_string(tower_id)+",\"cosmetics\":"+current_local_cosmetics_json()+"}";
}

void process_local_scene_requests() {
  if(g_local_home_requested.exchange(false)) {
    g_local_running.store(false);
    g_local_native_manager=nullptr;
    g_cold_scene_manager=nullptr;
    g_local_deck_storage=nullptr;
    g_local_cosmetic_home=nullptr;
    end_combat_event_epoch(nullptr);
    g_local_condition.notify_all();
    std::string fixture;
    { std::lock_guard<std::mutex> lock(g_local_mutex); fixture=g_local_boot_fixture; }
    void* parsed=parse_json_text(fixture.c_str());
    const bool ok=start_cold_home_scene(parsed,g_game_state_context_a(),g_game_state_context_b());
    release_json_object(parsed);
    firstlight::ForgetBattlePickerRequest();
    LOGI("local-app return Home result=%d",ok);
  }
  if(!g_local_deck_storage) return;
  std::string name;
  { std::lock_guard<std::mutex> lock(g_local_mutex); name.swap(g_local_pending_name); }
  if(!name.empty()) {
    void* client=reinterpret_cast<void*(*)()>(g_libg_base+0xc85c28)();
    void* profile=client?reinterpret_cast<void*(*)(void*)>(g_libg_base+0xc89c10)(client):nullptr;
    LibgString value;
    if(process_range_is_readable(profile,0x88) && initialize_libg_string(name.c_str(),&value)) {
      // Native avatar name setter copies the string. The Home header compares
      // and refreshes this value each frame (c0c4b4); no scene is reconstructed.
      reinterpret_cast<void(*)(void*,void*)>(g_libg_base+0xcdf848)(profile,&value);
      LOGI("local-app player name applied bytes=%zu",name.size());
    } else LOGE("local-app player name target unavailable");
  }
  static unsigned publish_frames=0;
  static std::string published_deck;
  if(++publish_frames%30==0) {
    const std::string deck=current_local_deck_json();
    if(deck!=published_deck) {
      LocalJavaEnv java;
      if(java.env && g_local_runtime_class) {
        auto method=java.env->GetStaticMethodID(g_local_runtime_class,"updatePlayerDeck","(Ljava/lang/String;)V");
        if(method) {
          jstring value=java.env->NewStringUTF(deck.c_str());
          java.env->CallStaticVoidMethod(g_local_runtime_class,method,value);
          java.env->DeleteLocalRef(value);
          published_deck=deck;
          LOGI("local-app selected deck=%s}",deck.substr(0,deck.find(",\"cosmetics\":")).c_str());
        }
      }
    }
  }
  firstlight::ReadyBattleRequest ready;
  if(firstlight::TakeReadyBattleRequest(&ready)) {
    const std::string deck=current_local_deck_json();
    if(deck.empty()) {
      LocalJavaEnv java;
      if(java.env) java.env->CallStaticVoidMethod(g_local_runtime_class,g_local_show_result,static_cast<jlong>(0),static_cast<jint>(-3));
    } else {
      { std::lock_guard<std::mutex> lock(g_local_mutex); g_local_expected_request=ready.request_id; }
      LocalJavaEnv java;
      if(java.env) {
        jstring id=java.env->NewStringUTF(ready.ai_id), cards=java.env->NewStringUTF(deck.c_str());
        java.env->CallStaticVoidMethod(g_local_runtime_class,g_local_prepare_match,static_cast<jlong>(ready.request_id),id,cards);
        java.env->DeleteLocalRef(id); java.env->DeleteLocalRef(cards);
      }
    }
  }
  std::string fixture;
  { std::lock_guard<std::mutex> lock(g_local_mutex); fixture.swap(g_local_pending_fixture); }
  if(fixture.empty()) return;
  int location=0;
  if(!validate_empty_match_streams(fixture.c_str()) || !extract_location_id(fixture.c_str(),&location) ||
     !prewarm_location_tilemap(g_game_state_context_a(),location)) { LOGE("local-app invalid match fixture"); return; }
  save_local_decks();
  save_local_cosmetics();
  g_local_deck_storage=nullptr;
  g_local_cosmetic_home=nullptr;
  {
    std::lock_guard<std::mutex> lock(g_local_mutex);
    ++g_local_generation;
    g_cold_scene_generation=g_local_generation;
    g_local_frames.clear(); g_local_commands.clear(); g_local_result_shown=false; g_local_last_capture=-1;
    g_local_terminal_tick.store(-1,std::memory_order_release);
  }
  void* parsed=parse_json_text(fixture.c_str());
  bool ok=start_cold_player_scene(parsed,g_game_state_context_a(),g_game_state_context_b());
  release_json_object(parsed);
  if(ok) {
    g_local_native_manager=g_cold_scene_manager;
    g_local_running.store(true,std::memory_order_release);
    LocalJavaEnv java;
    if(java.env) java.env->CallStaticVoidMethod(g_local_runtime_class,g_local_battle_created,static_cast<jlong>(g_local_generation));
  } else g_local_home_requested.store(true);
  LOGI("local-app battle started=%d generation=%llu",ok,static_cast<unsigned long long>(g_local_generation));
}
