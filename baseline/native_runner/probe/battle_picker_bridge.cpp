#include "battle_picker_bridge.h"

#include <android/log.h>
#include <cinttypes>
#include <cstring>
#include <limits>
#include <mutex>

namespace {

constexpr const char *kTag = "FirstlightBattleBridge";
constexpr const char *kRouterClass = "com/firstlight/offline/BattlePickerRouter";
constexpr const char *kOpenSignature = "(J)Z";
std::mutex g_mutex;
JavaVM *g_vm = nullptr;
jclass g_router_class = nullptr;
jmethodID g_request_method = nullptr;
std::uint64_t g_next_request_id = 0;
std::uint64_t g_active_request_id = 0;
bool g_ready_queued = false;
firstlight::ReadyBattleRequest g_ready;

bool ValidAiId(const char *id) {
  return id != nullptr &&
         (std::strcmp(id, "general") == 0 || std::strcmp(id, "il") == 0 ||
          std::strcmp(id, "active_il") == 0 ||
          std::strcmp(id, "hog26_proactive") == 0 ||
          std::strcmp(id, "hog26_measured") == 0);
}

void ClearJavaException(JNIEnv *env, const char *operation) {
  if (env->ExceptionCheck()) {
    env->ExceptionClear();
    __android_log_print(ANDROID_LOG_ERROR, kTag, "Java exception in %s", operation);
  }
}

}  // namespace

namespace firstlight {

bool InitBattlePickerBridge(JavaVM *vm, JNIEnv *env) {
  if (vm == nullptr || env == nullptr) return false;
  std::lock_guard<std::mutex> guard(g_mutex);
  if (g_router_class != nullptr) return g_vm == vm && g_request_method != nullptr;
  jclass local = env->FindClass(kRouterClass);
  if (local == nullptr) {
    ClearJavaException(env, "FindClass(BattlePickerRouter)");
    return false;
  }
  jmethodID method = env->GetStaticMethodID(local, "requestFromNative", kOpenSignature);
  if (method == nullptr) {
    ClearJavaException(env, "GetStaticMethodID(requestFromNative)");
    env->DeleteLocalRef(local);
    return false;
  }
  jclass global = static_cast<jclass>(env->NewGlobalRef(local));
  env->DeleteLocalRef(local);
  if (global == nullptr) {
    ClearJavaException(env, "NewGlobalRef(BattlePickerRouter)");
    return false;
  }
  g_vm = vm;
  g_router_class = global;
  g_request_method = method;
  __android_log_print(ANDROID_LOG_INFO, kTag, "Battle picker JNI route ready");
  return true;
}

bool RequestBattlePickerFromGameThread() {
  JavaVM *vm = nullptr;
  jclass router = nullptr;
  jmethodID method = nullptr;
  std::uint64_t request_id = 0;
  {
    std::lock_guard<std::mutex> guard(g_mutex);
    if (g_ready_queued || g_active_request_id != 0) return true;
    if (g_vm == nullptr || g_router_class == nullptr || g_request_method == nullptr) return false;
    if (g_next_request_id == static_cast<std::uint64_t>(std::numeric_limits<jlong>::max())) {
      return false;
    }
    request_id = ++g_next_request_id;
    g_active_request_id = request_id;
    vm = g_vm;
    router = g_router_class;
    method = g_request_method;
  }

  JNIEnv *env = nullptr;
  bool attached = false;
  jint status = vm->GetEnv(reinterpret_cast<void **>(&env), JNI_VERSION_1_6);
  if (status == JNI_EDETACHED) {
    if (vm->AttachCurrentThread(&env, nullptr) != JNI_OK) {
      ForgetBattlePickerRequest();
      return false;
    }
    attached = true;
  } else if (status != JNI_OK || env == nullptr) {
    ForgetBattlePickerRequest();
    return false;
  }
  const jboolean accepted = env->CallStaticBooleanMethod(router, method, static_cast<jlong>(request_id));
  const bool failed = env->ExceptionCheck();
  ClearJavaException(env, "requestFromNative");
  if (attached) vm->DetachCurrentThread();
  if (failed || accepted != JNI_TRUE) {
    std::lock_guard<std::mutex> guard(g_mutex);
    if (g_active_request_id == request_id) g_active_request_id = 0;
    return false;
  }
  __android_log_print(ANDROID_LOG_INFO, kTag, "Picker accepted request=%" PRIu64, request_id);
  return true;
}

bool TakeReadyBattleRequest(ReadyBattleRequest *out) {
  if (out == nullptr) return false;
  std::lock_guard<std::mutex> guard(g_mutex);
  if (!g_ready_queued) return false;
  *out = g_ready;
  g_ready = ReadyBattleRequest{};
  g_ready_queued = false;
  return true;
}

void ForgetBattlePickerRequest() {
  std::lock_guard<std::mutex> guard(g_mutex);
  g_active_request_id = 0;
  g_ready_queued = false;
  g_ready = ReadyBattleRequest{};
}

}  // namespace firstlight

extern "C" JNIEXPORT void JNICALL
Java_com_firstlight_offline_BattlePickerRouter_nativeOnBattleReady(
    JNIEnv *env, jclass, jlong request_id, jstring ai_id, jlong model_generation) {
  if (env == nullptr || request_id <= 0 || ai_id == nullptr || model_generation <= 0) return;
  const char *id = env->GetStringUTFChars(ai_id, nullptr);
  if (id == nullptr) {
    ClearJavaException(env, "GetStringUTFChars(aiId)");
    return;
  }
  if (ValidAiId(id)) {
    std::lock_guard<std::mutex> guard(g_mutex);
    if (g_active_request_id == static_cast<std::uint64_t>(request_id) && !g_ready_queued) {
      g_ready.request_id = static_cast<std::uint64_t>(request_id);
      g_ready.model_generation = static_cast<std::int64_t>(model_generation);
      std::strncpy(g_ready.ai_id, id, sizeof(g_ready.ai_id) - 1);
      g_ready_queued = true;
      g_active_request_id = 0;
      __android_log_print(ANDROID_LOG_INFO, kTag,
                          "AI ready request=%" PRIu64 " generation=%" PRId64 " id=%s",
                          g_ready.request_id, g_ready.model_generation, g_ready.ai_id);
    }
  }
  env->ReleaseStringUTFChars(ai_id, id);
}

extern "C" JNIEXPORT void JNICALL
Java_com_firstlight_offline_BattlePickerRouter_nativeOnBattleCancelled(
    JNIEnv *, jclass, jlong request_id) {
  if (request_id <= 0) return;
  std::lock_guard<std::mutex> guard(g_mutex);
  if (g_active_request_id == static_cast<std::uint64_t>(request_id)) {
    g_active_request_id = 0;
    __android_log_print(ANDROID_LOG_INFO, kTag, "Picker cancelled request=%" PRIu64,
                        static_cast<std::uint64_t>(request_id));
  }
}
