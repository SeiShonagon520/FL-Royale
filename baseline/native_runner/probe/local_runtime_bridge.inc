// JNI transfers immutable frames and requests; only the native game thread
// reads or changes libg objects. No host transport participates in a battle.
struct LocalFrame { int tick=-1; bool terminal=false; std::string json; };
struct LocalCommand {
  std::uint64_t generation=0;
  int observed=-1, due=-1, hand=-1, expected_card=0, x=0, y=0, object_index=0, secondary_index=0;
  bool ability=false, complete=false;
  std::atomic<bool> cancelled{false};
  std::string result;
};
std::mutex g_local_mutex;
std::condition_variable g_local_condition;
std::deque<LocalFrame> g_local_frames;
std::deque<std::shared_ptr<LocalCommand>> g_local_commands;
std::atomic<bool> g_local_boot_ready{false};
std::string g_local_boot_fixture, g_local_pending_fixture, g_local_pending_name;
std::uint64_t g_local_generation=0;
std::atomic<bool> g_local_running{false}, g_local_home_requested{false};
std::atomic<bool> g_local_foreground{true};
std::atomic<int> g_local_terminal_tick{-1};
bool g_local_result_shown=false;
jclass g_local_runtime_class=nullptr;
jmethodID g_local_prepare_match=nullptr, g_local_battle_created=nullptr, g_local_show_result=nullptr;
void* g_local_native_manager=nullptr;
int g_local_last_capture=-1;
LocalEventDelta g_local_event_delta;

void local_bad_connection_show(void* root) {
  if(g_local_boot_ready.load(std::memory_order_acquire)) return;
  // Exact stock body at 887510, retained outside an initialized local app.
  void* clip=read_object_field<void*>(root,0x188);
  if(clip) {
    static_cast<std::uint8_t*>(clip)[8]=1;
    const float duration=1.0f;
    std::memcpy(static_cast<std::uint8_t*>(root)+0x190,&duration,4);
  }
}
void install_local_offline_ui(std::uintptr_t base) {
  static constexpr std::uint8_t prologue[16]={0x08,0xc4,0x40,0xf9,0xa8,0x00,0x00,0xb4,0x29,0x00,0x80,0x52,0x0a,0xf0,0xa7,0x52};
  void* unused=nullptr;
  install_inline_hook(base,0x887510,prologue,reinterpret_cast<void*>(&local_bad_connection_show),&unused,"local offline connection indicator");
}

struct LocalJavaEnv {
  JNIEnv* env=nullptr; bool attached=false;
  LocalJavaEnv() {
    if(g_java_vm && g_java_vm->GetEnv(reinterpret_cast<void**>(&env),JNI_VERSION_1_6)==JNI_EDETACHED)
      attached=g_java_vm->AttachCurrentThread(&env,nullptr)==JNI_OK;
  }
  ~LocalJavaEnv() {
    if(env && env->ExceptionCheck()) { env->ExceptionDescribe(); env->ExceptionClear(); LOGE("local-app Java callback failed"); }
    if(attached) g_java_vm->DetachCurrentThread();
  }
};

void process_local_commands(void* manager, int tick) {
  std::deque<std::shared_ptr<LocalCommand>> requests;
  { std::lock_guard<std::mutex> lock(g_local_mutex); requests.swap(g_local_commands); }
  for(auto& request:requests) {
    char receipt[768]{}, command[1024]{};
    const int due=std::max(request->due,tick+1);
    bool ok=!request->cancelled.load() && request->generation==g_local_generation && g_local_running.load() &&
        request->observed>=0 && request->observed<=tick && tick-request->observed<=40 && due<=tick+40;
    if(ok && !request->ability) {
      DeployRequest deploy;
      ok=resolve_hand_deploy_request(manager,1,request->hand,request->x,request->y,&deploy) &&
         deploy.root_card_id==request->expected_card &&
         format_deploy_command(deploy,due-static_cast<int>(kLiveCommandAgeTicks)-1,command,sizeof(command)) &&
         inject_logic_command(manager,command);
      if(ok) std::snprintf(receipt,sizeof(receipt),
          "{\"ok\":true,\"async\":true,\"executeTick\":%d,\"queuedTick\":%d,\"cardId\":%d,\"commandCardId\":%u,"
          "\"cardParameter\":%u,\"deckSlot\":%d,\"cost\":%d,\"formCode\":%d,\"formName\":\"%s\"}",
          due,tick,deploy.root_card_id,deploy.card_id,deploy.card_parameter,deploy.deck_slot,deploy.cost,deploy.form_code,
          combat_card_form_label(deploy.form_code));
    } else if(ok) {
      AbilityRequest ability;
      ok=resolve_ready_ability_request(manager,1,request->object_index,request->secondary_index,&ability) &&
         format_ability_command(ability,due-2,command,sizeof(command)) && inject_logic_command(manager,command);
      if(ok) std::snprintf(receipt,sizeof(receipt),"{\"ok\":true,\"async\":true,\"executeTick\":%d,\"queuedTick\":%d}",due,tick);
    }
    if(!ok) std::snprintf(receipt,sizeof(receipt),"{\"ok\":false,\"error\":\"native action rejected\",\"tick\":%d}",tick);
    LOGI("local-app command accepted=%d observed=%d queued=%d requestedDue=%d scheduledDue=%d card=%d",ok,
         request->observed,tick,request->due,due,request->expected_card);
    { std::lock_guard<std::mutex> lock(g_local_mutex); request->result=receipt; request->complete=true; }
    g_local_condition.notify_all();
  }
}

void local_runtime_before_step(void* manager) {
  if(!g_local_running.load(std::memory_order_acquire) || manager!=g_local_native_manager) return;
  // The consumer exits after the terminal frame; retain that frame until it is
  // read, but do not continue filling the queue during the result animation.
  if(g_local_result_shown) return;
  const int tick=g_game_state_tick(manager);
  process_local_commands(manager,tick);
  BattleResultView result;
  bool terminal=read_battle_result(read_object_field<void*>(manager,0xa8),&result) && result.finalized;
  if(tick==g_local_last_capture || (!terminal && tick%5!=0)) return;
  // Restored towers create their active actions in the first native step.
  // Start FAIR at the first complete five-tick boundary; keep the real clock.
  if(tick==0) return;
  if(g_local_last_capture<0) {
    g_local_event_delta={};
    LOGI("local-app initial observation tick=%d",tick);
  }
  g_local_last_capture=tick;
  static std::unique_ptr<char[]> buffer(new char[kFullObservationResponseBytes]);
  LocalEventDelta next_delta=g_local_event_delta;
  const auto capture_started=std::chrono::steady_clock::now();
  bool captured=false;
  {
    LocalEventDeltaScope scope(next_delta);
    captured=build_normal_tick_atomic_json_locked(manager,g_local_generation,tick,buffer.get(),kFullObservationResponseBytes);
  }
  if(tick%100==0) LOGI("local-app capture tick=%d bytes=%zu encode_us=%lld",tick,
      captured?std::strlen(buffer.get()):0,static_cast<long long>(std::chrono::duration_cast<std::chrono::microseconds>(
          std::chrono::steady_clock::now()-capture_started).count()));
  {
    std::lock_guard<std::mutex> lock(g_local_mutex);
    if(!captured || g_local_frames.size()>=32) {
      g_local_running.store(false);
      LOGE("local-app capture failed tick=%d encoded=%d backlog=%zu",tick,captured,g_local_frames.size());
    } else {
      g_local_frames.push_back(LocalFrame{tick,terminal,std::string(buffer.get())});
      g_local_event_delta=next_delta;
      if(terminal) {
        g_local_terminal_tick.store(tick,std::memory_order_release);
        for(auto& request:g_local_commands) {
          request->result="{\"ok\":true,\"terminalCanceled\":true,\"executeTick\":"+std::to_string(tick)+"}";
          request->complete=true;
        }
        g_local_commands.clear();
      }
    }
  }
  g_local_condition.notify_all();
  if(terminal && !g_local_result_shown) {
    g_local_result_shown=true;
    LOGI("local-app finalized generation=%llu tick=%d winner=%d",static_cast<unsigned long long>(g_local_generation),tick,result.world_result_raw);
    LocalJavaEnv java;
    if(java.env && g_local_runtime_class && g_local_show_result)
      java.env->CallStaticVoidMethod(g_local_runtime_class,g_local_show_result,static_cast<jlong>(g_local_generation),
          static_cast<jint>(result.world_result_raw));
  }
}
