extern "C" JNIEXPORT void JNICALL Java_com_firstlight_offline_LocalBattleRuntime_nativeAttach(JNIEnv* env,jclass type) {
  if(!g_local_runtime_class) g_local_runtime_class=static_cast<jclass>(env->NewGlobalRef(type));
  g_local_prepare_match=env->GetStaticMethodID(type,"prepareMatch","(JLjava/lang/String;Ljava/lang/String;)V");
  g_local_battle_created=env->GetStaticMethodID(type,"onNativeBattleCreated","(J)V");
  g_local_show_result=env->GetStaticMethodID(type,"showResult","(JI)V");
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
extern "C" JNIEXPORT jstring JNICALL Java_com_firstlight_offline_LocalBattleRuntime_queueAction(JNIEnv* env,jclass,jlong generation,
    jint observed,jint due,jint hand,jint card,jint x,jint y,jboolean ability,jint object,jint secondary) {
  auto request=std::make_shared<LocalCommand>();
  request->generation=generation; request->observed=observed; request->due=due; request->hand=hand; request->expected_card=card;
  request->x=x; request->y=y; request->ability=ability; request->object_index=object; request->secondary_index=secondary;
  std::unique_lock<std::mutex> lock(g_local_mutex);
  const int terminal_tick=g_local_terminal_tick.load(std::memory_order_acquire);
  if(g_local_generation==static_cast<std::uint64_t>(generation) && terminal_tick>=0) {
    const std::string response="{\"ok\":true,\"terminalCanceled\":true,\"executeTick\":"+std::to_string(terminal_tick)+"}";
    return env->NewStringUTF(response.c_str());
  }
  if(!g_local_running.load() || g_local_generation!=static_cast<std::uint64_t>(generation) || g_local_commands.size()>=4)
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
