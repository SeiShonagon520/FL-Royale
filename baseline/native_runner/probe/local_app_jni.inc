extern "C" JNIEXPORT void JNICALL Java_com_firstlight_offline_LocalBattleRuntime_nativeAttach(JNIEnv* env,jclass type) {
  if(!g_local_runtime_class) g_local_runtime_class=static_cast<jclass>(env->NewGlobalRef(type));
  g_local_prepare_match=env->GetStaticMethodID(type,"prepareMatch","(JLjava/lang/String;Ljava/lang/String;)V");
  g_local_battle_created=env->GetStaticMethodID(type,"onNativeBattleCreated","(J)V");
  g_local_show_result=env->GetStaticMethodID(type,"showResult","(JI)V");
  g_local_home_status=env->GetStaticMethodID(type,"onHomeBootStatus","(II)V");
}
extern "C" JNIEXPORT void JNICALL Java_com_firstlight_offline_LocalBattleRuntime_nativeBootReady(JNIEnv* env,jclass,jstring value) {
  const char* text=env->GetStringUTFChars(value,nullptr);
  if(!text) return;
  { std::lock_guard<std::mutex> lock(g_local_mutex); g_local_boot_fixture=text; }
  env->ReleaseStringUTFChars(value,text);
  g_local_boot_ready.store(true,std::memory_order_release);
}
extern "C" JNIEXPORT void JNICALL Java_com_firstlight_offline_LocalBattleRuntime_nativeSubmitMatch(JNIEnv* env,jclass,jlong request,jstring value) {
  const char* text=env->GetStringUTFChars(value,nullptr);
  if(!text) return;
  { std::lock_guard<std::mutex> lock(g_local_mutex); if(static_cast<std::uint64_t>(request)==g_local_expected_request) g_local_pending_fixture=text; }
  env->ReleaseStringUTFChars(value,text);
}
extern "C" JNIEXPORT void JNICALL Java_com_firstlight_offline_LocalBattleRuntime_nativeRenamePlayer(JNIEnv* env,jclass,jstring value) {
  const char* text=env->GetStringUTFChars(value,nullptr);
  if(!text) return;
  if(*text && std::strlen(text)<=128) {
    std::lock_guard<std::mutex> lock(g_local_mutex);
    g_local_pending_name=text;
  }
  env->ReleaseStringUTFChars(value,text);
}
extern "C" JNIEXPORT jstring JNICALL Java_com_firstlight_offline_LocalBattleRuntime_nextFrame(JNIEnv* env,jclass,jlong generation,jint tick) {
  std::unique_lock<std::mutex> lock(g_local_mutex);
  bool ready=false;
  do { ready=g_local_condition.wait_for(lock,std::chrono::seconds(8),[&]{
    return !g_local_running.load() || g_local_generation!=static_cast<std::uint64_t>(generation) || !g_local_frames.empty();
  }); } while(!ready && !g_local_foreground.load() && g_local_running.load());
  if(!ready || !g_local_running.load() || g_local_generation!=static_cast<std::uint64_t>(generation) || g_local_frames.empty()) return nullptr;
  LocalFrame frame=std::move(g_local_frames.front()); g_local_frames.pop_front();
  const bool initial=tick==0 && frame.tick==5;
  if(!frame.terminal && !initial && frame.tick!=tick) { LOGE("local-app missing capture wanted=%d actual=%d",tick,frame.tick); return nullptr; }
  lock.unlock();
  return env->NewStringUTF(frame.json.c_str());
}
extern "C" JNIEXPORT jstring JNICALL Java_com_firstlight_offline_LocalBattleRuntime_queueActionForOwner(JNIEnv* env,jclass,jlong generation,
    jint observed,jint due,jint hand,jint card,jint x,jint y,jboolean ability,jint object,jint secondary,jint owner) {
  auto request=std::make_shared<LocalCommand>();
  request->generation=generation; request->owner=owner; request->observed=observed; request->due=due; request->hand=hand; request->expected_card=card;
  request->x=x; request->y=y; request->ability=ability; request->object_index=object; request->secondary_index=secondary;
  std::unique_lock<std::mutex> lock(g_local_mutex);
  const int terminal_tick=g_local_terminal_tick.load(std::memory_order_acquire);
  if(g_local_generation==static_cast<std::uint64_t>(generation) && terminal_tick>=0) {
    const std::string response="{\"ok\":true,\"terminalCanceled\":true,\"executeTick\":"+std::to_string(terminal_tick)+"}";
    return env->NewStringUTF(response.c_str());
  }
  if(!g_local_running.load() || g_local_generation!=static_cast<std::uint64_t>(generation) || g_local_commands.size()>=16)
    return env->NewStringUTF("{\"ok\":false,\"error\":\"inactive battle\"}");
  g_local_commands.push_back(request);
  bool done=false;
  do { done=g_local_condition.wait_for(lock,std::chrono::seconds(2),[&]{ return request->complete || !g_local_running.load(); });
  } while(!done && !g_local_foreground.load() && g_local_running.load());
  if(!done || !request->complete) { request->cancelled.store(true); return env->NewStringUTF("{\"ok\":false,\"error\":\"action mailbox timed out\"}"); }
  std::string response=request->result;
  lock.unlock();
  return env->NewStringUTF(response.c_str());
}
extern "C" JNIEXPORT jstring JNICALL Java_com_firstlight_offline_LocalBattleRuntime_queueAction(JNIEnv* env,jclass clazz,jlong generation,
    jint observed,jint due,jint hand,jint card,jint x,jint y,jboolean ability,jint object,jint secondary) {
  return Java_com_firstlight_offline_LocalBattleRuntime_queueActionForOwner(env, clazz, generation, observed, due, hand, card, x, y, ability, object, secondary, 1);
}
// Schedule one card deploy for an absolute future tick. This is the PvE wave
// path: it resolves the card by id (no hand slot), charges no elixir, and may
// target any future tick, so a scripted wave table can be armed a whole match
// in advance.
//
// It deliberately does NOT take g_local_mutex. enqueue_local_scheduled_card
// takes g_control_mutex, and process_scheduled_replay_actions takes
// g_control_mutex from the game thread; nesting the two lock orders would risk
// a deadlock. Nothing here touches libg state, so no game-thread call is made
// while any local lock is held.
extern "C" JNIEXPORT jstring JNICALL Java_com_firstlight_offline_LocalBattleRuntime_scheduleCard(JNIEnv* env,jclass,
    jint owner,jint card_id,jint x,jint y,jint execute_tick,jint level) {
  char response[512]{};
  enqueue_local_scheduled_card(static_cast<std::int32_t>(owner),static_cast<std::uint32_t>(card_id),
      static_cast<std::int32_t>(x),static_cast<std::int32_t>(y),static_cast<std::int32_t>(execute_tick),
      response,sizeof(response),static_cast<std::int32_t>(level));
  LOGI("local-app scheduleCard owner=%d card=%d x=%d y=%d tick=%d level=%d -> %s",
       owner,card_id,x,y,execute_tick,level,response);
  return env->NewStringUTF(response);
}
// Top up a seat's elixir to express an elixir-rate buff.
//
// The three-pick-one offers a permanent elixir-rate buff and the specification
// requires it to actually take effect. A card deploy cannot express a rate -- it
// moves a unit onto the board and nothing else.
//
// The player's elixir is a single integer at player+0x2f8 (`elixir_raw`, read by
// read_player_state), and the engine recomputes it every tick from its own
// per-tick rate. There is no verified field holding that rate, so rather than
// guess an offset for it, the buff is expressed the only way this codebase can
// honour honestly: each frame, credit the difference between the rate the buff
// grants and the stock 1.0x, capped at the ten-drop maximum. The player's bar
// therefore fills visibly faster and the effect is observable on screen, which is
// what the option promises.
//
// Applied every frame rather than once at pick time for the same reason: a
// one-shot credit would be spent and the buff would stop doing anything.
//
// Takes g_control_mutex only, never g_local_mutex, for the same reason
// scheduleCard does: the two lock orders must not nest.
extern "C" JNIEXPORT jstring JNICALL Java_com_firstlight_offline_LocalBattleRuntime_setPveElixirRate(JNIEnv* env,jclass,
    jint owner,jfloat multiplier) {
  char response[256]{};
  const float rate=static_cast<float>(multiplier);
  if(!(rate>1.0f) || rate>4.0f) {
    std::snprintf(response,sizeof(response),"{\"ok\":false,\"error\":\"rate out of range\"}");
    return env->NewStringUTF(response);
  }
  pthread_mutex_lock(&g_control_mutex);
  void* manager=g_local_native_manager;
  const bool live=g_local_running.load(std::memory_order_acquire) && manager!=nullptr &&
                   local_app_live_manager_matches(manager);
  if(!live) {
    pthread_mutex_unlock(&g_control_mutex);
    std::snprintf(response,sizeof(response),"{\"ok\":false,\"error\":\"no live battle\"}");
    return env->NewStringUTF(response);
  }
  void* world=read_object_field<void*>(manager,0xa8);
  PlayerStateView player;
  if(!read_player_state(world,static_cast<std::int32_t>(owner),&player)) {
    pthread_mutex_unlock(&g_control_mutex);
    std::snprintf(response,sizeof(response),"{\"ok\":false,\"error\":\"player unreadable\"}");
    return env->NewStringUTF(response);
  }
  // The extra elixir one frame of regeneration is worth, capped so a buff can
  // never push the bar past the ten-drop maximum. elixir_raw counts ten-thousandths
  // of a drop, which is the unit the deploy cost comparison in
  // resolve_card_deploy_request also uses.
  constexpr std::int32_t kElixirPerDrop = 10000;
  constexpr std::int32_t kElixirMaximum = 10 * kElixirPerDrop;
  const std::int32_t bonus = static_cast<std::int32_t>(
      kElixirPerDrop * 2.8f * (rate - 1.0f) / 8.0f);
  const std::int32_t before = player.elixir_raw;
  const std::int32_t after = before + (bonus > 0 ? bonus : 0);
  const std::int32_t capped = after > kElixirMaximum ? kElixirMaximum : after;
  std::memcpy(static_cast<std::uint8_t*>(player.player)+0x2f8,&capped,sizeof(capped));
  const std::int32_t tick=g_game_state_tick(manager);
  pthread_mutex_unlock(&g_control_mutex);
  LOGI("local-app elixir rate owner=%d rate=%.2fx before=%d after=%d tick=%d",owner,rate,before,capped,tick);
  std::snprintf(response,sizeof(response),"{\"ok\":true,\"owner\":%d,\"rate\":%.3f,\"before\":%d,\"after\":%d,\"tick\":%d}",
                owner,static_cast<double>(rate),before,capped,tick);
  return env->NewStringUTF(response);
}
extern "C" JNIEXPORT void JNICALL Java_com_firstlight_offline_LocalBattleRuntime_nativeReturnHome(JNIEnv*,jclass) {
  g_local_home_requested.store(true,std::memory_order_release);
}
extern "C" JNIEXPORT void JNICALL Java_com_firstlight_offline_LocalBattleRuntime_nativeAbort(JNIEnv*,jclass,jlong generation) {
  std::lock_guard<std::mutex> lock(g_local_mutex);
  if(g_local_generation==static_cast<std::uint64_t>(generation)) { g_local_running.store(false); g_local_condition.notify_all(); }
}
extern "C" JNIEXPORT void JNICALL Java_com_firstlight_offline_LocalBattleRuntime_nativeSetForeground(JNIEnv*,jclass,jboolean foreground) {
  g_local_foreground.store(foreground==JNI_TRUE,std::memory_order_release);
  LOGI("local-app foreground=%d",foreground==JNI_TRUE);
  g_local_condition.notify_all();
}
